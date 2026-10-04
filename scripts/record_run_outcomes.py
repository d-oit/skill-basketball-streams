#!/usr/bin/env python3
"""record_run_outcomes.py — the caller `source_learning.py record` never had.

`references/self-learning.md` documents five loops. The first is the run log:
`scripts/source_learning.py record` appends one row per source per run to
`run-log.jsonl`; `score` reads it into per-source hit rates; `candidates` reads
it for domains that produced hits but are not approved. All three are
implemented. **Nothing calls `record` on any automated path** — `grep
source_learning .github/workflows/` finds a single `candidates --dry-run` in
`validate.yml`. So on the runtime path the log is never created by CI, `score`
exits 1 on the empty file, and `candidates` reads nothing. That is `AGENTS.md`'s
"a documented artifact with no producer", one level down: the writer exists and
the caller does not.

There is a second, sharper version of the same defect to avoid here.
`references/self-learning.md` says the log lives at `logs/run-log.jsonl`, and
`logs/` is gitignored (`.gitignore:12`). A CI job that appended there would write
a file no CI job can ever read — which is exactly how the retired
`source-registry` sensor came to pass vacuously on a runner where the file could
not exist. So the rows are written through `source_learning.py record --dest
<telemetry dir>`, the `--dest` that module already documents for this ("so the
daily runtime can target a telemetry worktree (spec §10.4) without a second
writer implementation"). The result is `run-log.jsonl` **on the `telemetry`
branch**, beside `candidates.jsonl`, `fetch-attempts.jsonl` and
`rung-attempts.jsonl` — committed, diffable, and readable by the next job.

The inputs are the ledgers that already exist. No new data source is invented:

    candidates.jsonl      Phase 0 search hits, including which backend found each
    fetch-attempts.jsonl  league fixture sources that were blocked/gone/5xx —
                          "an issue found during the run"
    rung-attempts.jsonl   render rungs that failed or are parked
    events.jsonl          what reached the calendar

`record` derives one run-log row per input row and hands each to
`source_learning.mode_record` — the real writer, imported, not a second
implementation of the append.

`check` is the gate, and it is deliberately a gate on **committed evidence**.
It re-derives from a directory of ledgers, asserts the stored `run-log.jsonl`
equals the derivation, that a blocked fetch attempt and a dying rung each left a
row, and that the stored log is readable by `source_learning.read_log` and
`discover_candidates`. A log that is missing, stale, or blind to the blocked and
parked signals fails — which is the whole defect this file exists to make
observable.

Usage:
    python3 scripts/record_run_outcomes.py record --dest .tmp/telemetry \\
        --run-id 2026-09-15T08:30Z
    python3 scripts/record_run_outcomes.py check \\
        --ledgers tests/fixtures/run_outcomes

Exit codes:
    0  PASS — rows recorded (or none to record, stated), / the log matches evidence
    1  FAIL — the log could not be written, / the committed log does not match
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import source_learning  # noqa: E402  (the writer this script gives a caller to)

LOG_NAME = "run-log.jsonl"
LEDGER_NAMES = ("candidates", "fetch-attempts", "rung-attempts", "events")
# The keys `source_learning.mode_record` emits only when they carry a value.
OPTIONAL_KEYS = ("game_key", "backend", "rung", "llm", "state", "color_id", "event_id")
# A fetch attempt with one of these statuses is an issue found during the run.
FETCH_OUTCOMES = {"blocked": "blocked", "not_found": "reject"}
# `rung_health.FAILURE_STATES` — a rung in one of these is dying, and `parked`
# says it has been dying long enough to raise an issue.
RUNG_FAILURE_STATES = ("blocked", "failed")


def read_jsonl(path: Path) -> list[dict]:
    """JSONL objects, blank and malformed lines skipped — `source_learning`'s rule."""
    return source_learning.read_log(path)


def entry_for(spec: dict) -> dict:
    """The exact row `source_learning.mode_record` writes for a spec.

    This mirrors that function's construction because the stored log is compared
    against it: if the two ever drift, `check` fails on the committed fixture,
    which is regenerated through the real `record` path. The row shape therefore
    has one tester even though the construction is written down twice.
    """
    entry = {
        "ts": source_learning._parse_time(spec.get("now")).isoformat(),
        "run_id": spec.get("run_id") or "unassigned",
        "source": spec.get("source") or "",
        "tier": spec.get("tier") or 0,
        "url": spec.get("url") or "",
        "outcome": spec.get("outcome") or "",
        "reason": spec.get("reason") or "",
    }
    for key in OPTIONAL_KEYS:
        value = spec.get(key)
        if value:
            entry[key] = value
    return entry


def _outcome_for_disposition(disposition: str) -> str:
    """Map a Phase 0 disposition onto a run-log `outcome`.

    `unverifiable` becomes `skip`, not `reject`: the run did not reach a verdict
    on it, and calling that a rejection would score a source down for a fact
    nobody established. `created` and `skipped_duplicate` are the dispositions
    that correspond to a decision; `rejected_<check>` is the one case the run
    genuinely refused.
    """
    if disposition == "created":
        return "create"
    if disposition == "skipped_duplicate":
        return "skip"
    if disposition.startswith("rejected_"):
        return "reject"
    return "skip"


def _league_of(game_key: str) -> str:
    return game_key.split("|", 1)[0].strip() if game_key else ""


def _candidate_specs(rows: list[dict], fallback_now: str) -> list[dict]:
    specs: list[dict] = []
    for row in rows:
        url = str(row.get("url") or "")
        domain = source_learning._domain(url)
        if not domain:
            # No URL means no domain to score or discover; a row that names only
            # a `game_key` is not a source observation.
            continue
        disposition = str(row.get("disposition") or "")
        specs.append(
            {
                "source": domain,
                "tier": 0,
                "url": url,
                "outcome": _outcome_for_disposition(disposition),
                "reason": f"search hit ({disposition or 'unknown'})"
                + (f" via {row.get('backend')}" if row.get("backend") else ""),
                "now": str(row.get("ts") or fallback_now),
                "run_id": str(row.get("run_id") or ""),
                "backend": str(row.get("backend") or ""),
                "game_key": str(row.get("game_key") or ""),
                "state": str(row.get("state") or ""),
            }
        )
    return specs


def _fetch_specs(rows: list[dict], fallback_now: str) -> list[dict]:
    specs: list[dict] = []
    for row in rows:
        status = str(row.get("status") or "")
        if status in ("", "ok"):
            continue
        code = row.get("code")
        reason = str(row.get("why") or "") or (
            f"{status}" + (f" (HTTP {code})" if code else "")
        )
        league = str(row.get("source") or "")
        specs.append(
            {
                "source": str(
                    row.get("host")
                    or source_learning._domain(str(row.get("url") or ""))
                ),
                "tier": 0,
                "url": str(row.get("url") or ""),
                "outcome": FETCH_OUTCOMES.get(status, "error"),
                "reason": f"fixture source {league or '?'}: {reason}",
                "now": str(row.get("ts") or fallback_now),
                "run_id": str(row.get("run_id") or ""),
            }
        )
    return specs


def _rung_specs(rows: list[dict], fallback_now: str) -> list[dict]:
    specs: list[dict] = []
    for row in rows:
        state = str(row.get("state") or "")
        if not (row.get("parked") or state in RUNG_FAILURE_STATES):
            continue
        outcome = "blocked" if state == "blocked" else "error"
        detail = str(row.get("error") or "") or f"state={state or 'unknown'}"
        if row.get("parked"):
            detail = f"parked; {detail}"
        specs.append(
            {
                "source": str(row.get("rung") or ""),
                "tier": 0,
                "url": str(row.get("url") or ""),
                "outcome": outcome,
                "reason": detail,
                "now": str(row.get("ts") or fallback_now),
                "run_id": str(row.get("run_id") or ""),
                "rung": str(row.get("rung") or ""),
                "state": state,
            }
        )
    return specs


def _event_specs(rows: list[dict], fallback_now: str) -> list[dict]:
    specs: list[dict] = []
    for row in rows:
        action = str(row.get("action") or "")
        if action not in ("create", "update") or row.get("dry_run"):
            # A dry run wrote nothing, so it cannot be a `create` fact — the same
            # rule `event_ledger` follows, applied before the row reaches here.
            continue
        game_key = str(row.get("game_key") or "")
        specs.append(
            {
                "source": _league_of(game_key) or "calendar",
                "tier": 0,
                "url": "",
                "outcome": "create",
                "reason": f"event {action} reached the calendar",
                "now": str(row.get("ts") or fallback_now),
                "run_id": str(row.get("run_id") or ""),
                "game_key": game_key,
                "event_id": str(row.get("event_id") or ""),
                "state": str(row.get("state") or ""),
            }
        )
    return specs


def derive_specs(ledgers: dict[str, list[dict]], *, now: str = "") -> list[dict]:
    """One run-log spec per issue/decision in the ledgers, in a fixed order.

    The order is fixed so the committed fixture's line order is reproducible:
    candidates (what search surfaced), fetch attempts (what broke), rungs (what
    is dying), events (what reached the calendar).
    """
    return (
        _candidate_specs(ledgers.get("candidates", []), now)
        + _fetch_specs(ledgers.get("fetch-attempts", []), now)
        + _rung_specs(ledgers.get("rung-attempts", []), now)
        + _event_specs(ledgers.get("events", []), now)
    )


def load_ledgers(directory: Path) -> dict[str, list[dict]]:
    return {name: read_jsonl(directory / f"{name}.jsonl") for name in LEDGER_NAMES}


def _namespace(spec: dict, dest: Path, *, dry_run: bool) -> SimpleNamespace:
    return SimpleNamespace(
        dest=str(dest) if dest is not None else None,
        log=None,
        dry_run=dry_run,
        run_id=spec.get("run_id") or "",
        source=spec.get("source") or "",
        tier=spec.get("tier") or 0,
        url=spec.get("url") or "",
        outcome=spec.get("outcome") or "",
        reason=spec.get("reason") or "",
        now=spec.get("now"),
        **{key: spec.get(key) for key in OPTIONAL_KEYS},
    )


def record(dest: Path, *, now: str = "", dry_run: bool = False) -> int:
    """Append one row per derived spec through `source_learning.mode_record`."""
    specs = derive_specs(load_ledgers(dest), now=now)
    if not specs:
        print(
            "OK: record_run_outcomes: no candidate, fetch, rung or event row was "
            f"produced under {dest} — nothing to learn from this run"
        )
        return 0
    for spec in specs:
        # `mode_record` prints `OK:` per row; send its human lines to stderr so
        # `--json` can leave stdout as the payload alone, like every other tool.
        with contextlib.redirect_stdout(sys.stderr):
            code = source_learning.mode_record(_namespace(spec, dest, dry_run=dry_run))
        if code:
            print(
                f"FAIL: record_run_outcomes: could not append a row for "
                f"{spec.get('source')}",
                file=sys.stderr,
            )
            return 1
    return 0


def _diff(expected: list[dict], stored: list[dict]) -> list[str]:
    problems: list[str] = []
    if len(expected) != len(stored):
        problems.append(
            f"run-log.jsonl has {len(stored)} row(s), the derivation has "
            f"{len(expected)} — a signal was dropped or a row was never written"
        )
    for index, (want, have) in enumerate(zip(expected, stored)):
        if want != have:
            differing = sorted(key for key in set(want) | set(have)
                               if want.get(key) != have.get(key))
            problems.append(
                f"row {index + 1} ({want.get('source')} {want.get('outcome')}) "
                f"differs on {differing or 'key order only'}"
            )
    return problems


def check(ledgers_dir: Path, root: Path) -> int:
    """Gate: the committed run-log must equal what the deriver makes of the ledgers."""
    if not ledgers_dir.is_dir():
        print(
            f"FAIL: record_run_outcomes: {ledgers_dir} is not a directory of ledgers",
            file=sys.stderr,
        )
        return 2
    try:
        ledgers = load_ledgers(ledgers_dir)
    except OSError as exc:
        print(f"FAIL: record_run_outcomes: {exc}", file=sys.stderr)
        return 2
    specs = derive_specs(ledgers)
    expected = [entry_for(spec) for spec in specs]
    stored_path = ledgers_dir / LOG_NAME
    stored = source_learning.read_log(stored_path)

    problems: list[str] = []
    if not stored_path.is_file():
        problems.append(
            f"{stored_path} does not exist — the learning loop never ran, which is "
            "the defect this gate exists to catch"
        )
    else:
        problems += _diff(expected, stored)
    if not any(row["outcome"] == "blocked" for row in expected):
        problems.append(
            "no blocked fetch attempt became a run-log row, so the 'issue found "
            "during the run' signal is invisible to the learning loop"
        )
    if not any(row.get("rung") for row in expected):
        problems.append(
            "no dying or parked rung became a run-log row, so a dead render "
            "backend is indistinguishable from a quiet day"
        )
    if stored and not source_learning.score_entries(stored):
        problems.append("the stored run log scores to nothing")
    approved = source_learning.approved_domains(source_learning.load_sources(root))
    if stored and not source_learning.discover_candidates(stored, approved):
        problems.append(
            "the stored run log yields no unapproved domain, so anything it "
            "records is already in config/sources.json and `candidates` can never "
            "surface a new source"
        )

    for problem in problems:
        print(f"FAIL: record_run_outcomes: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(
        f"OK: record_run_outcomes: {len(expected)} derived row(s) match "
        f"{stored_path}, including "
        f"{sum(1 for r in expected if r['outcome'] == 'blocked')} blocked source(s) "
        f"and {sum(1 for r in expected if r.get('rung'))} dying rung(s); readable "
        "by source_learning score/candidates"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    record_parser = sub.add_parser("record", help="append run-log rows from the ledgers")
    record_parser.add_argument("--dest", type=Path, required=True,
                               help="telemetry directory holding the ledgers")
    record_parser.add_argument("--run-id", default="")
    record_parser.add_argument("--now", help="ISO-8601 fallback timestamp")
    record_parser.add_argument("--dry-run", action="store_true",
                               help="report without appending")
    record_parser.add_argument("--json", action="store_true")

    check_parser = sub.add_parser("check", help="gate a committed run-log against its ledgers")
    check_parser.add_argument("--ledgers", type=Path, required=True,
                              help="directory holding the ledgers and run-log.jsonl")
    check_parser.add_argument("--root", type=Path, default=Path("."))

    args = parser.parse_args()
    if args.mode == "record":
        if args.json:
            try:
                specs = derive_specs(load_ledgers(args.dest), now=args.now or "")
            except OSError as exc:
                print(f"FAIL: record_run_outcomes: {exc}", file=sys.stderr)
                sys.exit(2)
            print(json.dumps({"rows": [entry_for(spec) for spec in specs]}, indent=2))
        sys.exit(record(args.dest, now=args.now or "", dry_run=args.dry_run))
    sys.exit(check(args.ledgers, args.root.resolve()))


if __name__ == "__main__":
    main()
