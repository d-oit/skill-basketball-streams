#!/usr/bin/env python3
"""audit_events.py — Phase 3: post-hoc verdicts and the promotion sweep.

A false positive can only be confirmed *after* the broadcast ends. This module
runs the next day over events that have finished and resolves each to one of:

| Verdict | When | Effect |
|---|---|---|
| `VERIFIED` | free access **and** a live stream confirmed | colour 5 → 6, prefix dropped |
| `WRONG` | paid, or provably never live | `[WRONG]` prefix, colour `7` |
| `INCONCLUSIVE` | not finished yet, or evidence missing | **untouched** |

Three rules keep this honest:

1. **Absence of evidence is not evidence.** A missing key yields `INCONCLUSIVE`;
   only an explicit `live_confirmed: false` on a finished broadcast yields
   `WRONG`. Reporting a game as wrong because a scraper failed would poison the
   audit and generate bogus eval cases.
2. **The sweep is idempotent.** Re-running over the same day produces the same
   verdicts, and `WRONG` is terminal — it is never promoted back.
3. **Nothing is deleted.** A `WRONG` verdict relabels; the event and its history
   stay, which is the only way the audit trail survives.

Usage:
    python3 scripts/audit_events.py --events events.json --evidence evidence.json
    python3 scripts/audit_events.py --events events.json --evidence evidence.json --now 2026-09-15T08:30:00Z --json
    python3 scripts/audit_events.py --events events.json --evidence evidence.json --out telemetry/audit.jsonl --run-id 2026-09-15T08:30Z

`--out` appends one JSONL row per verdict (including `INCONCLUSIVE`, so the
record shows what was looked at and deliberately left alone). Rows carry `ts`
and `run_id` first, matching the telemetry schema in
`live-stream-runtime-spec.md` §10.1.

Exit codes:
    0  PASS — at least one actionable verdict
    1  FAIL — every event was inconclusive
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:  # direct CLI execution
    from verification import (
        DEFAULT_LEAGUE_COLOR_ID,
        STATE_UNVERIFIED,
        STATE_VERIFIED,
        STATE_WRONG,
        color_for,
        normalise_state,
        title_for,
    )
except ImportError:  # imported as a package module
    from scripts.verification import (  # type: ignore
        DEFAULT_LEAGUE_COLOR_ID,
        STATE_UNVERIFIED,
        STATE_VERIFIED,
        STATE_WRONG,
        color_for,
        normalise_state,
        title_for,
    )

VERDICT_VERIFIED = "VERIFIED"
VERDICT_WRONG = "WRONG"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICTS = (VERDICT_VERIFIED, VERDICT_WRONG, VERDICT_INCONCLUSIVE)


def parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def audit_event(
    event: dict, evidence: dict | None, now: datetime | None = None
) -> dict:
    """Resolve one event to a verdict. Never mutates the event."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    event_id = str(event.get("event_id") or event.get("id") or "")
    prior_state = normalise_state(event.get("state"), default=STATE_UNVERIFIED)
    league_color = str(event.get("league_color_id") or DEFAULT_LEAGUE_COLOR_ID)
    summary = str(event.get("summary") or "")

    def verdict(kind: str, reason: str) -> dict:
        state = {
            VERDICT_VERIFIED: STATE_VERIFIED,
            VERDICT_WRONG: STATE_WRONG,
            VERDICT_INCONCLUSIVE: prior_state,
        }[kind]
        actionable = kind != VERDICT_INCONCLUSIVE and state != prior_state
        return {
            "event_id": event_id,
            "game_key": str(event.get("game_key") or ""),
            "prior_state": prior_state,
            "verdict": kind,
            "reason": reason,
            "actionable": actionable,
            "new_state": state,
            # INCONCLUSIVE leaves the event exactly as it was.
            "new_title": title_for(state, summary) if actionable else "",
            "new_color_id": color_for(state, league_color) if actionable else "",
        }

    # Terminal state: an audit verdict is never revised by a later run.
    if prior_state == STATE_WRONG:
        return verdict(VERDICT_INCONCLUSIVE, "already WRONG — terminal verdict")

    end = parse_dt(event.get("end") or event.get("endTime"))
    if end is not None and end > now:
        return verdict(VERDICT_INCONCLUSIVE, "broadcast has not finished yet")
    start = parse_dt(event.get("start") or event.get("startTime"))
    if end is None and start is not None and start > now:
        return verdict(VERDICT_INCONCLUSIVE, "broadcast has not started yet")

    if not isinstance(evidence, dict) or not evidence:
        return verdict(VERDICT_INCONCLUSIVE, "no evidence recorded")

    if evidence.get("paid") is True:
        return verdict(VERDICT_WRONG, "audit found paid access, not a free stream")

    if evidence.get("live_confirmed") is False:
        return verdict(VERDICT_WRONG, "audit found no live broadcast")

    if "live_confirmed" not in evidence:
        return verdict(VERDICT_INCONCLUSIVE, "live status unknown for this event")

    if evidence.get("live_confirmed") is True and evidence.get("free_confirmed") is True:
        return verdict(
            VERDICT_VERIFIED, "free access and live stream both confirmed"
        )

    return verdict(
        VERDICT_INCONCLUSIVE, "live confirmed but free access still unproven"
    )


def latest_per_event(events: list[dict]) -> tuple[list[dict], int]:
    """One row per `event_id` — the last one — plus how many were dropped.

    `events.jsonl` is append-only forever (§10.3), so an event the runtime
    updated on five days has five rows. Auditing all of them would judge the same
    event five times and append five verdicts for it, inflating `audit.jsonl` and
    the precision denominator with copies of one finding.

    Last row wins because the file is append-ordered, so the newest observation
    is simply the latest one written. Rows with no `event_id` are kept as-is: the
    audit reports them and moving on is the only honest option, whereas dropping
    them would hide a write we cannot name.
    """
    latest: dict[str, dict] = {}
    keyless: list[dict] = []
    dropped = 0
    for row in events:
        if not isinstance(row, dict):
            continue
        event_id = str(row.get("event_id") or row.get("id") or "")
        if not event_id:
            keyless.append(row)
            continue
        if event_id in latest:
            dropped += 1
        latest[event_id] = row
    return [*latest.values(), *keyless], dropped


def sweep(
    events: list[dict], evidence_by_id: dict, now: datetime | None = None
) -> list[dict]:
    """Audit every event, including UNVERIFIED promotion candidates."""
    results: list[dict] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or event.get("id") or "")
        evidence = evidence_by_id.get(event_id) if isinstance(
            evidence_by_id, dict
        ) else None
        results.append(audit_event(event, evidence, now=now))
    return results


def telemetry_rows(
    results: list[dict], *, run_id: str, now: datetime | None = None
) -> list[dict]:
    """Wrap verdicts as append-only `audit.jsonl` rows (`ts` and `run_id` first).

    `INCONCLUSIVE` rows are recorded too: the append-only log is the evidence
    trail, and "we checked and could not tell" is itself evidence — it is what
    distinguishes a quiet day from a day the audited nothing.
    """
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    rows: list[dict] = []
    for result in results:
        rows.append(
            {
                "ts": stamp,
                "run_id": run_id,
                "event_id": result["event_id"],
                "game_key": result["game_key"],
                "prior_state": result["prior_state"],
                "verdict": result["verdict"],
                "reason": result["reason"],
                "actionable": result["actionable"],
                "new_state": result["new_state"],
                "new_color_id": result["new_color_id"],
            }
        )
    return rows


def append_rows(path: Path, rows: list[dict]) -> int:
    """Append JSONL rows, creating the parent directory. Returns rows written."""
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def summarise(results: list[dict]) -> dict:
    summary = {verdict: 0 for verdict in VERDICTS}
    summary["actionable"] = 0
    for row in results:
        summary[row["verdict"]] = summary.get(row["verdict"], 0) + 1
        if row["actionable"]:
            summary["actionable"] += 1
    return summary


def _load(path: Path, key: str) -> object:
    """Read a JSON document, or a JSONL file.

    JSONL is accepted because the documented inputs are telemetry artefacts
    (`events.jsonl`, `audit.jsonl`), which are one object per line by design —
    requiring a caller to wrap them in `{\"events\": [...]}` first would invite
    transcripts of real data.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"FAIL: audit_events: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except OSError as exc:
        print(f"FAIL: audit_events: {path}: unreadable ({exc})", file=sys.stderr)
        sys.exit(2)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        rows: list = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(
                    f"FAIL: audit_events: {path}:{number}: invalid JSON ({exc})",
                    file=sys.stderr,
                )
                sys.exit(2)
        if not rows:
            print(
                f"FAIL: audit_events: {path}: not JSON and contains no rows",
                file=sys.stderr,
            )
            sys.exit(2)
        return rows

    if isinstance(payload, dict) and key in payload:
        return payload[key]
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post-hoc audit of finished events plus the promotion sweep.",
    )
    parser.add_argument("--events", required=True, help="events JSON")
    parser.add_argument("--evidence", required=True, help="evidence JSON by event_id")
    parser.add_argument("--now", help="ISO-8601 override (for tests)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--out", help="append verdict rows to this JSONL path (telemetry)"
    )
    parser.add_argument(
        "--run-id", default="", help="run identifier recorded on every row"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="with --out, report without writing"
    )
    args = parser.parse_args()

    events = _load(Path(args.events), "events")
    evidence = _load(Path(args.evidence), "evidence")
    if not isinstance(events, list):
        print("FAIL: audit_events: --events must be a list", file=sys.stderr)
        sys.exit(2)
    if not isinstance(evidence, dict):
        print(
            "FAIL: audit_events: --evidence must be an object keyed by event_id",
            file=sys.stderr,
        )
        sys.exit(2)

    now = parse_dt(args.now) if args.now else datetime.now(timezone.utc)
    if args.now and now is None:
        print(f"FAIL: audit_events: --now {args.now!r} is not ISO-8601", file=sys.stderr)
        sys.exit(2)

    events, dropped = latest_per_event(events)
    if dropped:
        # stderr, so `--json` leaves stdout as the payload alone.
        print(
            f"NOTE: audit_events: {dropped} superseded row(s) collapsed — the "
            "ledger keeps every write, so only the latest row per event_id is "
            "judged",
            file=sys.stderr,
        )

    results = sweep(events, evidence, now=now)
    summary = summarise(results)

    # Written before the report, and to stderr in --json mode, so stdout stays
    # parseable (the repo-wide convention; see scripts/README.md).
    if args.out:
        rows = telemetry_rows(results, run_id=args.run_id, now=now)
        sink = sys.stderr if args.json else sys.stdout
        if args.dry_run:
            print(f"OK: audit_events: dry-run — would append {len(rows)} rows", file=sink)
        else:
            written = append_rows(Path(args.out), rows)
            print(
                f"OK: audit_events: appended {written} rows to {args.out}", file=sink
            )
    if args.json:
        print(json.dumps({"verdicts": results, "summary": summary}, indent=2))
    else:
        for row in results:
            if row["verdict"] == VERDICT_INCONCLUSIVE:
                print(
                    f"OK   {row['event_id']}: INCONCLUSIVE — {row['reason']}"
                )
            else:
                print(
                    f"AUDIT {row['event_id']}: {row['verdict']} "
                    f"({row['prior_state']} -> {row['new_state']}) — {row['reason']}"
                )
        print(
            "OK: audit_events: verified={VERIFIED} wrong={WRONG} "
            "inconclusive={INCONCLUSIVE} actionable={actionable}".format(**summary)
        )
    if summary["actionable"] == 0:
        print(
            "FAIL: audit_events: no actionable verdicts "
            f"({len(results)} events audited)",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
