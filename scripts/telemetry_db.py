#!/usr/bin/env python3
"""telemetry_db.py — a derived SQLite read model over the run log.

The ledgers on the `telemetry` branch are append-only JSONL. `run-log.jsonl`
carries one row per source per run, and every scorer reads it by scanning the
whole file: `source_learning.py score` can say "source X hit 40%", but nothing
answers "which tier should be reordered, and on how many attempts?" without a
fresh scan and an ad-hoc script per question.

This module adds no writer and no new truth. It builds a SQLite file **derived**
from the committed JSONL and reports from that:

* `build` — read `<ledgers>/run-log.jsonl`, replace the database, and write one
  `outcomes` row per run-log row with a `reward` column. The database is
  derived: rebuilding is always safe, and nothing else reads or writes it.
* `report` — per-source attempts/creates/hit rate/mean reward, plus advisory
  tier reorder suggestions behind a minimum-attempts floor (`--min-attempts`,
  default 10). Below the floor the sample is too small to be a claim, so no
  suggestion is made rather than one being guessed.

A row's `source` is normally a host, but not every row is a source observation:
render rungs (`tinyfish`), libraries (`urllib`) and the league labels older event
rows were charged to also reach the log. Those are reported as
`plumbing_sources` rather than `unapproved_sources`: the right reaction to an
unapproved *source* is a `candidates` review, and there is nothing to approve in
a rung's name.

The `tier` column is resolved, not trusted. Every row the runtime writes today
carries `tier: 0` — `record_run_outcomes.derive_specs` hardcodes it, and the
committed log's 142k rows all say so — so a report that read the log's own
field would be structurally unable to propose anything, which is the
"rule that reads a field nothing writes" trap. A positive logged tier wins;
otherwise the source is looked up in `config/sources.json` (the approved
registry, the only place a tier is actually maintained) and recorded as
`tier_source: "registry"`. A source the registry does not carry stays
`tier: NULL` — it is *unapproved*, not tier 0 — and is never reordered, because
its right reaction is a `candidates` review, not a tier move.

A host is also resolved through its approved parent when it is a subdomain
(`about.fiba.basketball` is the approved `fiba.basketball`), which is the same
rule discovery applies; an `excluded` host wins over that fallback, so
`tv.youtube.com` stays unapproved even though `youtube.com` is tier 6. Those two
answers — approved and excluded — are reported apart (`tier_source`), never
scored as tier 0.

Sources are also folded to one identity per site (`www.` stripped, lowercased),
the same rule the registry uses. The log records the host as fetched, so the
real ledger carries 1,479 rows for `euroleaguebasketball.net` and 405 blocked
rows for `www.euroleaguebasketball.net`; scoring those separately proposes a
demotion for an alias while the site itself was answering.

`reward` is a judgement about one row, kept beside the raw `outcome` rather than
replacing it: `create` +1 (a game reached the calendar), `skip` 0 (no verdict
was reached — not a failure), `reject` -0.5 (the run refused it), `blocked` /
`error` -1 (the source never answered). It is computed **here**, in the read
model, not written into the append-only log: no stored row changes shape, and
`record_run_outcomes.py check` keeps comparing bytes.

Promotion is never a write. A suggestion names a source, a tier, and the
evidence behind it, for a human/PR to act on; `config/sources.json` is not
touched by any code path in this file.

Usage:
    python3 scripts/telemetry_db.py build --ledgers .tmp/telemetry --db .tmp/telemetry.db
    python3 scripts/telemetry_db.py report --db .tmp/telemetry.db --min-attempts 10 --json

Exit codes:
    0  PASS — rows written / report produced
    1  FAIL — nothing to build from / no evidence to report
    2  USAGE — bad arguments or an unreadable database
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

LOG_NAME = "run-log.jsonl"
DEFAULT_MIN_ATTEMPTS = 10
MIN_TIER = 1
MAX_TIER = 7
REGISTRY_NAME = "sources.json"

# One row's worth, by outcome. `skip` is 0 on purpose: the run reached no
# verdict, and scoring that as a failure would punish a source for a fact
# nobody established (the same rule `record_run_outcomes` follows when it maps
# `unverifiable` to `skip`).
REWARDS = {
    "create": 1.0,
    "skip": 0.0,
    "reject": -0.5,
    "blocked": -1.0,
    "error": -1.0,
}

# The run-log keys carried into the table; anything else is not queryable.
FIELDS = (
    "ts", "run_id", "source", "tier", "url", "outcome", "reason",
    "game_key", "backend", "query_id", "query_family",
)

SCHEMA = """
CREATE TABLE outcomes (
    id INTEGER PRIMARY KEY,
    ts TEXT,
    run_id TEXT,
    source TEXT NOT NULL,
    tier INTEGER,
    tier_source TEXT,
    url TEXT,
    outcome TEXT NOT NULL,
    reason TEXT,
    game_key TEXT,
    backend TEXT,
    query_id TEXT,
    query_family TEXT,
    experiment INTEGER NOT NULL,
    reward REAL NOT NULL
);
CREATE INDEX outcomes_source ON outcomes (source);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def reward_for(row: dict) -> float:
    """What one outcome row is worth. Unknown outcomes score 0, never a guess."""
    return REWARDS.get(str(row.get("outcome") or "").strip().lower(), 0.0)


def load_tiers(sources_path: Path) -> dict[str, int]:
    """domain -> tier from the approved registry; `{}` when the file is absent.

    Entries carry either `domain` or `domains`, so both are read. A registry
    that exists but cannot be parsed is a usage error, not an empty map: silently
    scoring every source as unapproved would look exactly like a quiet week.
    """
    try:
        payload = json.loads(sources_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{sources_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{sources_path}: expected an object with a 'sources' list")
    tiers: dict[str, int] = {}
    for entry in payload.get("sources") or []:
        if not isinstance(entry, dict):
            continue
        try:
            tier = int(entry.get("tier"))
        except (TypeError, ValueError):
            continue
        candidates = [entry.get("domain"), *(entry.get("domains") or [])]
        for domain in candidates:
            if isinstance(domain, str) and domain.strip():
                tiers[domain.strip().lower().removeprefix("www.")] = tier
    return tiers


def normalise_source(source: str) -> str:
    """One identity per site: the registry's own `www.`-stripping rule.

    The writer records the host as it was fetched, so a bare host and its
    `www.` alias are two rows for one site. Folding them here keeps a blocked
    alias from reading as a dead source.
    """
    return str(source or "").strip().lower().removeprefix("www.")


def is_source_identity(source: str) -> bool:
    """Whether this name is something `approve_source` could ever add.

    A host (`ndr.de`), a YouTube identity (`youtube.com/@handle`) or a bare
    handle (`@handle`) is a source. `tinyfish` (a render rung), `urllib` (a
    library) and `NBBL` (a league label older event rows were charged to) are
    not, and listing them under *unapproved sources* invites a review with
    nothing to approve.
    """
    name = normalise_source(source)
    return name.startswith("@") or ("." in name and " " not in name)


def load_excluded(sources_path: Path) -> set[str]:
    """Hosts `config/sources.json` refuses outright, from `excluded[].domains`.

    `{}` when the file is absent; an unparseable registry raises, exactly as
    `load_tiers` does — silently treating every exclusion as gone would let a
    paid host resolve a tier.
    """
    try:
        payload = json.loads(sources_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{sources_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{sources_path}: expected an object with a 'sources' list")
    excluded: set[str] = set()
    for entry in payload.get("excluded") or []:
        if not isinstance(entry, dict):
            continue
        for value in (entry.get("domain"), *(entry.get("domains") or [])):
            if isinstance(value, str) and value.strip():
                excluded.add(normalise_source(value))
    return excluded


def _parents(source: str) -> list[str]:
    """Parent suffixes of a host, most specific first (`a.b.c` -> `b.c`, `c`)."""
    parts = source.split(".")
    return [".".join(parts[index:]) for index in range(1, len(parts) - 1)]


def resolve_tier(
    row: dict, tiers: dict[str, int], excluded: set[str] | None = None
) -> tuple[int | None, str | None]:
    """The tier a row is scored at, and where that number came from.

    A positive tier in the log is the observation itself and wins. The runtime
    writes `0` for "not stated", so `0` falls through to the registry. A host
    the registry excludes is `(None, "excluded")` even when a parent is
    approved (`tv.youtube.com` under `youtube.com`); a subdomain of an approved
    host resolves from that parent; anything else is `(None, None)`: unapproved.
    """
    try:
        logged = int(row.get("tier"))
    except (TypeError, ValueError):
        logged = 0
    if logged > 0:
        return logged, "log"
    source = normalise_source(row.get("source"))
    blocked = excluded or set()
    if source in blocked:
        return None, "excluded"
    if source in tiers:
        return tiers[source], "registry"
    for parent in _parents(source):
        if parent in blocked:
            return None, "excluded"
        if parent in tiers:
            return tiers[parent], "registry"
    return None, None


def read_rows(directory: Path) -> list[dict]:
    """The committed run log under `directory`, blank/malformed lines skipped."""
    path = directory / LOG_NAME
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and str(row.get("source") or "").strip():
            rows.append(row)
    return rows


def build(
    directory: Path, db_path: Path, sources_path: Path | None = None
) -> int:
    """Replace `db_path` with a database derived from `directory/run-log.jsonl`.

    `sources_path` is the approved registry used to resolve a tier the log does
    not state; without it only logged tiers are known and every runtime row is
    unapproved.
    """
    rows = read_rows(directory)
    tiers = load_tiers(sources_path) if sources_path else {}
    excluded = load_excluded(sources_path) if sources_path else set()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Derived, so the old file is discarded rather than migrated: a stale row
    # that the ledger no longer carries must not survive a rebuild.
    db_path.unlink(missing_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        resolved = [
            (
                resolve_tier(row, tiers, excluded),
                {**row, "source": normalise_source(row.get("source"))},
            )
            for row in rows
        ]
        # `tier` and `tier_source` replace the raw field, which is always 0 on
        # the runtime path: the insert names them explicitly so the value the
        # report reads is the resolved one.
        other = [key for key in FIELDS if key != "tier"]
        columns = ", ".join(["tier", "tier_source", *other])
        placeholders = ", ".join("?" for _ in range(len(other) + 2))
        conn.executemany(
            f"INSERT INTO outcomes ({columns}, experiment, reward) "
            f"VALUES ({placeholders}, ?, ?)",
            [
                (tier, tier_source)
                + tuple(row.get(key) for key in other)
                + (1 if row.get("experiment") else 0, reward_for(row))
                for (tier, tier_source), row in resolved
            ],
        )
        conn.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [("ledger", str(directory / LOG_NAME)), ("rows", str(len(rows)))],
        )
    return len(rows)


def propose_tier_change(
    stats: dict, *, min_attempts: int = DEFAULT_MIN_ATTEMPTS
) -> dict | None:
    """Advisory tier move for one source, or None below the evidence floor.

    Promotion needs a real sample and a majority of attempts reaching the
    calendar. Demotion needs a real sample in which **nothing** was created and
    at least half the attempts never reached the source at all — a source that
    is quiet is not a source that is dead.
    """
    if stats.get("tier") is None:
        # Unapproved: a tier move would legitimise a source the registry does
        # not carry. `candidates` is the path for those, not this report.
        return None
    attempts = int(stats.get("attempts") or 0)
    if attempts < max(1, min_attempts):
        return None
    tier = int(stats["tier"])
    creates = int(stats.get("creates") or 0)
    hit_rate = creates / attempts
    common = {
        "source": str(stats.get("source") or ""),
        "from": tier,
        "attempts": attempts,
        "creates": creates,
        "hit_rate": hit_rate,
        "mean_reward": float(stats.get("mean_reward") or 0.0),
        "tier_source": stats.get("tier_source") or "log",
    }
    if creates and tier > MIN_TIER and hit_rate >= 0.5:
        return {
            **common,
            "direction": "promote",
            "to": tier - 1,
            "why": f"{creates}/{attempts} attempts reached the calendar",
        }
    unreachable = int(stats.get("blocked") or 0) + int(stats.get("errors") or 0)
    if tier >= MIN_TIER and tier < MAX_TIER and creates == 0 and unreachable * 2 >= attempts:
        return {
            **common,
            "direction": "demote",
            "to": tier + 1,
            "why": f"{unreachable}/{attempts} attempts never reached the source",
        }
    return None


def report(db_path: Path, *, min_attempts: int = DEFAULT_MIN_ATTEMPTS) -> dict:
    """Per-source evidence and the tier moves it supports. Read-only."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT source,
                   MAX(tier) AS tier,
                   MAX(tier_source) AS tier_source,
                   SUM(tier_source = 'log') AS logged_tiers,
                   COUNT(*) AS attempts,
                   SUM(outcome = 'create') AS creates,
                   SUM(outcome = 'skip') AS skips,
                   SUM(outcome = 'reject') AS rejects,
                   SUM(outcome = 'blocked') AS blocked,
                   SUM(outcome = 'error') AS errors,
                   AVG(reward) AS mean_reward
            FROM outcomes
            GROUP BY source
            ORDER BY source
            """
        ).fetchall()
    finally:
        conn.close()
    sources: list[dict] = []
    for row in rows:
        entry = {key: row[key] for key in row.keys()}
        entry["attempts"] = int(entry["attempts"] or 0)
        tier = entry.pop("tier")
        entry["tier"] = None if tier is None else int(tier)
        logged = int(entry.pop("logged_tiers") or 0)
        stored = entry.pop("tier_source")
        entry["tier_source"] = (
            "log"
            if logged
            else (stored or ("registry" if entry["tier"] is not None else "unapproved"))
        )
        for key in ("creates", "skips", "rejects", "blocked", "errors"):
            entry[key] = int(entry[key] or 0)
        entry["hit_rate"] = entry["creates"] / entry["attempts"] if entry["attempts"] else 0.0
        entry["mean_reward"] = float(entry["mean_reward"] or 0.0)
        sources.append(entry)
    changes = [
        change
        for change in (propose_tier_change(stats, min_attempts=min_attempts) for stats in sources)
        if change
    ]
    return {
        "min_attempts": max(1, min_attempts),
        "sources": sources,
        "tier_changes": changes,
        "unapproved_sources": sorted(
            stats["source"]
            for stats in sources
            if stats["tier"] is None
            and stats["tier_source"] != "excluded"
            and is_source_identity(stats["source"])
        ),
        # A refused host is not a candidate: `approve_source` would reject it, so
        # offering it for review would be a question with a known answer.
        "excluded_sources": sorted(
            stats["source"]
            for stats in sources
            if stats["tier"] is None and stats["tier_source"] == "excluded"
        ),
        # Kept in the report rather than dropped: the row is real evidence about
        # the run, it is just not a source anyone could approve.
        "plumbing_sources": sorted(
            stats["source"]
            for stats in sources
            if stats["tier"] is None and not is_source_identity(stats["source"])
        ),
        "advisory": "tier moves are proposals; config/sources.json is written only by a reviewed PR",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Derived SQLite read model over the run log.")
    sub = parser.add_subparsers(dest="command", required=True)

    build_parser = sub.add_parser("build", help="rebuild the database from a ledger directory")
    build_parser.add_argument("--ledgers", type=Path, required=True,
                              help="directory holding run-log.jsonl")
    build_parser.add_argument("--db", type=Path, required=True, help="derived database path")
    build_parser.add_argument(
        "--sources", type=Path, default=None,
        help=f"approved registry used to resolve an unstated tier (e.g. config/{REGISTRY_NAME})",
    )

    report_parser = sub.add_parser("report", help="per-source evidence and tier suggestions")
    report_parser.add_argument("--db", type=Path, required=True)
    report_parser.add_argument("--min-attempts", type=int, default=DEFAULT_MIN_ATTEMPTS)
    report_parser.add_argument("--out", default=None)
    report_parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.command == "build":
        try:
            count = build(args.ledgers, args.db, args.sources)
        except ValueError as exc:
            print(f"FAIL: telemetry_db: {exc}", file=sys.stderr)
            sys.exit(2)
        except OSError as exc:
            print(f"FAIL: telemetry_db: {exc}", file=sys.stderr)
            sys.exit(2)
        if not count:
            print(
                f"FAIL: telemetry_db: no run-log rows in {args.ledgers} — the "
                "learning loop has no evidence to model",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"OK: telemetry_db: built {args.db} from {count} row(s)", file=sys.stderr)
        return

    if not args.db.is_file():
        print(f"FAIL: telemetry_db: {args.db} does not exist — run `build` first", file=sys.stderr)
        sys.exit(1)
    try:
        payload = report(args.db, min_attempts=args.min_attempts)
    except sqlite3.Error as exc:
        print(f"FAIL: telemetry_db: {args.db}: {exc}", file=sys.stderr)
        sys.exit(2)
    if not payload["sources"]:
        print(f"FAIL: telemetry_db: no outcome rows in {args.db} — nothing to report", file=sys.stderr)
        sys.exit(1)
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json:
        print(text)
    else:
        for stats in payload["sources"]:
            print(
                f"OK: telemetry_db: {stats['source']}: tier={stats['tier']} "
                f"attempts={stats['attempts']} creates={stats['creates']} "
                f"hit_rate={stats['hit_rate']:.2f} mean_reward={stats['mean_reward']:.2f}"
            )
        for change in payload["tier_changes"]:
            print(
                f"OK: telemetry_db: {change['direction']} {change['source']} "
                f"tier {change['from']} -> {change['to']} ({change['why']})"
            )
    print(
        f"OK: telemetry_db: {len(payload['tier_changes'])} tier change(s), "
        f"{len(payload['unapproved_sources'])} unapproved source(s)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
