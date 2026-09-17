#!/usr/bin/env python3
"""event_ledger.py — Phase 2 writes down what it actually put in the calendar.

`live-stream-runtime-spec.md` §10.1 lists `events.jsonl` (created/updated events
keyed by `event_id`) and `docs/runtime.md` documents `evidence.json`
(`{event_id: {live_confirmed, free_confirmed, paid}}`). Phase 3 audits against
both — the audit job's own comment in `runtime-daily.yml` says so — but nothing
ever wrote them:

    telemetry/events.jsonl   created/updated events by event_id   <- no producer
    telemetry/evidence.json  the evidence each event was judged on <- no producer

So `.tmp/telemetry/events.jsonl` never existed, the audit job found its inputs
absent, printed a notice, and did nothing. That is the whole of Phase 3: no
verdict was ever recorded, `metrics.json` reported precision `n/a` forever, the
§17 target "precision ≥ 0.95" was unmeasurable, and `self-improve`'s audit ledger
stayed empty. A green workflow that could not observe anything.

This script is the missing writer. It is deliberately downstream of the *apply
result* rather than of the plan, because they answer different questions:

    the plan     what we intended to write
    the result   what the calendar now holds, and under which id

Four rules, each one a way this could quietly go wrong:

1. **A dry run records nothing.** It wrote nothing, so there is no event for a
   verdict to be about; recording one would have the audit judge a game that was
   never on the calendar. It exits 0 and says why.
2. **An event with no id is refused, not recorded.** Phase 3 resolves a verdict
   back to an event *by id*, so an id-less row is a finding that can never be
   applied to anything. A write the API accepted but we cannot name is worse
   than a red step, so it exits 1.
3. **Evidence is copied, never derived.** The flags come from the candidate's
   explicit `evidence` fields, carried on the plan row. Nothing here defaults a
   key to `false`: `audit_events` reads a *missing* key as INCONCLUSIVE and an
   explicit `false` as WRONG, so a default would relabel live games as wrong.
4. **The ledger is append-only; the audit resolves the latest row.** A re-run of
   the same `run_id` appends a second row rather than rewriting the file, which
   keeps the "never pruned, this is the evidence trail" promise in §10.3
   literally true. `audit_events` takes the last row per `event_id`, so the
   duplicate cannot double-judge an event.

`evidence.json` is the exception, and deliberately: it is a derived map, so an
event this run recorded has its entry **replaced** by this run's finding rather
than merged with it. A union would let yesterday's `live_confirmed: true` outlive
today's observation and be read as if this run had confirmed it. Entries for
events this run did not touch are preserved.

Usage:
    python3 scripts/event_ledger.py record \\
        --plan .tmp/plan.json --applied .tmp/applied.json \\
        --dest .tmp --run-id 2026-09-15T08:30Z
    python3 scripts/event_ledger.py record --plan p.json --applied a.json --json

Exit codes:
    0  PASS — rows recorded, or a dry run with nothing to record
    1  FAIL — a written event could not be keyed (no event_id)
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evidence import coerce_evidence  # noqa: E402
from verification import (  # noqa: E402
    DEFAULT_LEAGUE_COLOR_ID,
    normalise_state,
    strip_prefix,
)

LEDGER_NAME = "events.jsonl"
EVIDENCE_NAME = "evidence.json"
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
WRITTEN_ACTIONS = (ACTION_CREATE, ACTION_UPDATE)


def utc_iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def load_plan(path: Path) -> list[dict]:
    """The planner's rows, or `[]`. Raises `ValueError` on an unusable shape."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("plan") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError('plan must be a list or {"plan": [...]}')
    return [row for row in rows if isinstance(row, dict)]


def load_applied(path: Path) -> dict:
    """The result of `calendar_io.py apply`."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("applied result must be an object")
    return payload


def ledger_row(
    plan_row: dict,
    action: dict,
    *,
    run_id: str,
    now: datetime | None = None,
) -> dict:
    """One `events.jsonl` row: the event as the calendar now holds it.

    Time and summary come from the applied *body* rather than from the plan row,
    because the body is what was sent — a missing `end` is filled there from the
    documented default duration, and the audit decides "has this broadcast
    finished?" from that end time. Reading it from the plan instead would have
    every event look like it ends when it starts.
    """
    body = action.get("body") if isinstance(action.get("body"), dict) else {}
    start = body.get("start") if isinstance(body.get("start"), dict) else {}
    end = body.get("end") if isinstance(body.get("end"), dict) else {}
    state = normalise_state(plan_row.get("state"))
    # The league colour a `VERIFIED` promotion restores — the shared constant, and
    # deliberately not `color_for(state, ...)`: see its definition in
    # `verification.py` for why the *state* colour would repaint a freshly
    # promoted event amber.
    league_color = str(plan_row.get("league_color_id") or DEFAULT_LEAGUE_COLOR_ID)
    return {
        "ts": utc_iso(now),
        "run_id": run_id,
        "event_id": str(action.get("event_id") or ""),
        "game_key": str(plan_row.get("game_key") or action.get("game_key") or ""),
        "action": str(action.get("action") or ""),
        "state": state,
        # Unprefixed, so re-applying a prefix is idempotent and a reader sees the
        # game's name rather than its label. `audit_events` rebuilds the title
        # with `title_for`, which strips a known prefix anyway.
        "summary": strip_prefix(str(body.get("summary") or plan_row.get("title") or "")),
        "league_color_id": league_color,
        "start": str(start.get("dateTime") or start.get("date") or ""),
        "end": str(end.get("dateTime") or end.get("date") or ""),
        "dry_run": False,
    }


def build_rows(
    plan: list[dict], applied: dict, *, run_id: str, now: datetime | None = None
) -> tuple[list[dict], list[str], str]:
    """(rows, refusals, reason-there-are-no-rows). Never invents a row."""
    if applied.get("dry_run"):
        return [], [], "dry run — nothing was written, so there is nothing to audit"

    by_key = {str(row.get("game_key") or ""): row for row in plan}
    rows: list[dict] = []
    refusals: list[str] = []
    for action in applied.get("actions") or []:
        if not isinstance(action, dict):
            continue
        if str(action.get("action") or "") not in WRITTEN_ACTIONS:
            continue
        game_key = str(action.get("game_key") or "")
        plan_row = by_key.get(game_key, {})
        row = ledger_row(plan_row, action, run_id=run_id, now=now)
        if not row["event_id"]:
            # Refused rather than written with an empty key: the audit joins on
            # event_id, so this row could only ever be an unappliable verdict.
            refusals.append(
                f"{game_key or '(no game_key)'}: {row['action']} has no event_id "
                "(the API returned none, or the plan came from a dry run)"
            )
            continue
        rows.append(row)
    if not rows and not refusals:
        return [], [], "no create/update action was applied"
    return rows, refusals, ""


def evidence_for(rows: list[dict], plan: list[dict]) -> dict:
    """`{event_id: {...}}` for this run's events, from explicit flags only."""
    by_key = {
        str(row.get("game_key") or ""): row
        for row in plan
        if isinstance(row, dict)
    }
    evidence: dict = {}
    for row in rows:
        plan_row = by_key.get(row["game_key"], {})
        flags = coerce_evidence(plan_row)
        if flags:
            evidence[row["event_id"]] = flags
    return evidence


def append_rows(dest: Path, rows: list[dict]) -> int:
    if not rows:
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    with (dest / LEDGER_NAME).open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def read_evidence(dest: Path) -> dict:
    try:
        payload = json.loads((dest / EVIDENCE_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def merge_evidence(stored: dict, fresh: dict) -> dict:
    """Replace each touched event's entry; keep every other event's."""
    merged = dict(stored)
    merged.update(fresh)
    return {key: merged[key] for key in sorted(merged)}


def write_evidence(dest: Path, merged: dict) -> Path:
    path = dest / EVIDENCE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def render_markdown(rows: list[dict], evidence: dict, *, reason: str = "") -> str:
    """The job-summary view. One renderer for the artifact, not two."""
    lines = [
        "| | |",
        "|---|---|",
        f"| Events recorded | {len(rows)} |",
        f"| Evidence entries | {len(evidence)} |",
        "",
    ]
    if not rows:
        lines.append(f"No ledger rows: {reason or 'nothing was written'}.")
        lines.append(
            "Phase 3 needs both files; without them the audit judges nothing and "
            "precision stays `n/a`."
        )
        return "\n".join(lines)
    lines += ["| Event id | State | Start | Game |", "|---|---|---|---|"]
    for row in rows:
        lines.append(
            f"| `{row['event_id']}` | {row['state']} | {row['start']} "
            f"| {row['summary']} |"
        )
    missing = [row["event_id"] for row in rows if row["event_id"] not in evidence]
    if missing:
        lines += [
            "",
            f"{len(missing)} event(s) carry no evidence flags at all. The audit "
            "reads a missing key as INCONCLUSIVE and an explicit `false` as a "
            "`WRONG` verdict, so a silent gap here is a game that can never be "
            "confirmed — never a game that gets wrongly condemned.",
        ]
    else:
        lines += [
            "",
            "Every recorded event carries at least one explicit evidence flag, "
            "so the audit can reach a verdict on each.",
        ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("record",))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--applied", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True,
                        help="telemetry directory for events.jsonl / evidence.json")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--now", help="ISO-8601 override (for tests)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report without writing")
    parser.add_argument("--markdown", action="store_true",
                        help="print the job-summary view and exit 0")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        plan = load_plan(args.plan)
        applied = load_applied(args.applied)
    except FileNotFoundError as exc:
        print(f"FAIL: event_ledger: {exc.filename}: file not found", file=sys.stderr)
        sys.exit(2)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"FAIL: event_ledger: {exc}", file=sys.stderr)
        sys.exit(2)

    now = None
    if args.now:
        try:
            now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        except ValueError:
            print(
                f"FAIL: event_ledger: --now {args.now!r} is not ISO-8601",
                file=sys.stderr,
            )
            sys.exit(2)

    rows, refusals, reason = build_rows(plan, applied, run_id=args.run_id, now=now)

    if args.markdown:
        print(render_markdown(rows, evidence_for(rows, plan), reason=reason))
        return

    if refusals:
        # Loud, and before anything is written: a partially recorded run is
        # exactly the state where the audit looks complete and is not.
        for refusal in refusals:
            print(f"FAIL: event_ledger: {refusal}", file=sys.stderr)
        sys.exit(1)

    evidence = evidence_for(rows, plan)
    if args.dry_run:
        print(f"OK: event_ledger: would record {len(rows)} event(s), "
              f"{len(evidence)} evidence entry(ies) — dry run, nothing written")
        return

    written = append_rows(args.dest, rows)
    if written:
        merged = merge_evidence(read_evidence(args.dest), evidence)
        write_evidence(args.dest, merged)
        evidence = {key: merged[key] for key in evidence}

    if args.json:
        print(json.dumps({"rows": rows, "evidence": evidence, "written": written},
                         indent=2))
        return

    if not written:
        print(f"OK: event_ledger: nothing recorded — {reason}")
        return
    print(f"OK: event_ledger: recorded {written} event(s), "
          f"{len(evidence)} evidence entry(ies) -> {args.dest / LEDGER_NAME}")
    if len(evidence) < written:
        print(
            f"NOTE: event_ledger: {written - len(evidence)} event(s) carry no "
            "evidence flags, so the audit can only return INCONCLUSIVE for them "
            "(that is the safe direction: a missing key is never a WRONG verdict)."
        )


if __name__ == "__main__":
    main()
