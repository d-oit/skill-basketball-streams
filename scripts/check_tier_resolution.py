#!/usr/bin/env python3
"""check_tier_resolution.py — the tier report must be able to propose something.

`telemetry_db.py report` proposes tier moves from the run log, and its first
version read the log's own `tier` field. Every runtime row stores `tier: 0`
(`record_run_outcomes.derive_specs` hardcodes it, and all 142k committed rows
say so), so on the real ledger the report scored every source as tier 0 and
could not promote or demote anything — while its unit tests passed, because
they supplied a tier by hand. That is AGENTS.md's "a rule that reads a field
nothing writes", with the hand-built test input as the tell.

This gate is the tripwire for it. It drives the **production row shape** — every
fixture row says `tier: 0` — through the real `config/sources.json` and requires:

1. rows that state no tier to resolve one from the approved registry;
2. both directions of the rule to fire on the fixture (a promotion and a
   demotion), because a report that can only ever return `[]` is decoration;
3. an unapproved domain to be reported and never reordered;
4. a `www.` alias to fold into the bare host instead of reading as a dead source
   (the real ledger's 405-blocked alias proposed exactly that spurious demotion);
5. every fixture row to state no tier, so the fixture cannot drift back into
   supplying the field the pipeline never writes.

    python3 scripts/check_tier_resolution.py --root .

Exit codes:
    0  PASS — the report resolves, fires both ways, and refuses an alias
    1  FAIL — the report is vacuous or the fixture no longer matches runtime
    2  USAGE — bad arguments or missing inputs
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import telemetry_db  # noqa: E402

FIXTURE_RELATIVE_PATH = "tests/fixtures/tier_resolution"
SOURCES_RELATIVE_PATH = "config/sources.json"
MIN_ATTEMPTS = 10

# What the fixture is built to prove, as (direction, source, from, to).
EXPECTED_CHANGES = {
    ("promote", "magentasport.de", 4, 3),
    ("demote", "sporteurope.tv", 4, 5),
}
UNAPPROVED = "unapproved.example"
ALIAS = "euroleaguebasketball.net"


def check(root: Path) -> int:
    ledger = root / FIXTURE_RELATIVE_PATH
    sources = root / SOURCES_RELATIVE_PATH
    if not sources.is_file():
        print(
            f"FAIL: check_tier_resolution: {SOURCES_RELATIVE_PATH} is missing — "
            "without the approved registry every source scores as unapproved and "
            "the report cannot propose anything",
            file=sys.stderr,
        )
        return 2

    try:
        rows = telemetry_db.read_rows(ledger)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "telemetry.db"
            telemetry_db.build(ledger, db, sources)
            payload = telemetry_db.report(db, min_attempts=MIN_ATTEMPTS)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"FAIL: check_tier_resolution: {exc}", file=sys.stderr)
        return 1

    problems: list[str] = []
    if not rows:
        problems.append(
            f"{FIXTURE_RELATIVE_PATH}/run-log.jsonl has no rows — this gate would "
            "assert nothing"
        )
    stated = [row for row in rows if row.get("tier")]
    if stated:
        problems.append(
            f"{len(stated)} fixture row(s) state a tier; the runtime writes "
            "`tier: 0` for every row, so a fixture that supplies one cannot catch "
            "a report that only works when the field is populated"
        )

    by_source = {entry["source"]: entry for entry in payload["sources"]}
    from_registry = [
        entry for entry in payload["sources"] if entry["tier_source"] == "registry"
    ]
    if not from_registry:
        problems.append(
            "no source resolved its tier from config/sources.json — the registry "
            "is not wired into the build, so on real rows the report can only "
            "ever score unapproved sources and propose nothing"
        )

    got = {
        (change["direction"], change["source"], change["from"], change["to"])
        for change in payload["tier_changes"]
    }
    if got != EXPECTED_CHANGES:
        problems.append(
            f"tier changes were {sorted(got)}, expected {sorted(EXPECTED_CHANGES)} "
            "— a rule that cannot fire in both directions is not a rule"
        )
    for change in payload["tier_changes"]:
        if change.get("tier_source") != "registry":
            problems.append(
                f"{change['source']} was reordered from tier_source "
                f"{change.get('tier_source')!r}; the fixture states no tier, so a "
                "move that does not cite the registry came from nowhere"
            )

    if UNAPPROVED not in payload["unapproved_sources"]:
        problems.append(
            f"{UNAPPROVED} is not reported as unapproved — a source the registry "
            "does not carry must be visible as such, not silently scored"
        )
    if UNAPPROVED in {change["source"] for change in payload["tier_changes"]}:
        problems.append(
            f"{UNAPPROVED} was reordered; an unapproved domain's right reaction is "
            "a `candidates` review, never a tier move"
        )

    merged = by_source.get(ALIAS)
    if merged is None:
        problems.append(
            f"{ALIAS} is absent; the bare host and its `www.` alias must be one "
            "source"
        )
    else:
        if merged["attempts"] != 50:
            problems.append(
                f"{ALIAS} has {merged['attempts']} attempt(s), expected 50 — the "
                "`www.` alias is being scored as a separate source, which is how "
                "the real ledger proposed demoting a site that was answering"
            )
        if merged["tier"] != 1:
            problems.append(
                f"{ALIAS} resolved to tier {merged['tier']}, expected 1 from the "
                "approved registry"
            )

    for problem in problems:
        print(f"FAIL: check_tier_resolution: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(
        "OK: check_tier_resolution: production-shaped rows (tier: 0) resolve from "
        f"{SOURCES_RELATIVE_PATH}; {len(from_registry)} source(s) tiered by the "
        f"registry, {len(got)} move(s) fired, 1 unapproved source refused, 1 "
        "`www.` alias merged"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    args = parser.parse_args(argv)
    return check(args.root.resolve())


if __name__ == "__main__":
    sys.exit(main())
