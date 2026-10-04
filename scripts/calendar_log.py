#!/usr/bin/env python3
"""calendar_log.py — one row per calendar decision, including the ones nothing acts on.

`.tmp/plan.json` holds the planner's full decision — which game was considered,
what matched it, what changed and why — and it is scratch in the runner's working
directory: never uploaded, discarded when the job ends. `events.jsonl`
(`scripts/event_ledger.py`) records only the `create`/`update` rows that reached
the calendar, so a **skip is recorded nowhere**. The fine-grained `reason`
strings ("verified event exists", "audit verdict WRONG stands", "refreshed
unverified event") only ever existed in a file that no longer exists a minute
later. That is the same shape as the defects in `AGENTS.md`: a decision that is
made every run and observable in no artifact.

This is the missing producer. It reads the plan and the apply result and appends
one JSONL row per **game in the plan** to `calendar-log.jsonl`, including skips
and `unchanged` rows, recording the action that was taken and the planner's own
`reason` verbatim.

Why the *plan* is the row source and not the applied result: `calendar_io
.apply_plan` sends nothing for a skip or an unchanged row, so those games appear
in no other artifact. The applied result is still consulted, but for two things
only — the event id of a row that was written, and whether the run was a dry run.
Reading the plan for the rest is what makes the decision observable.

Six rules, each one a way this could quietly go wrong:

1. **Every plan row becomes a row, whatever its action.** Filtering to
   `create`/`update` is exactly the hole this file closes.
2. **An event id is never invented.** A create in a dry run has no id, and one is
   not conjured for it; a skip carries the id of the event the planner matched,
   because the planner read it from the calendar rather than guessing.
3. **`dry_run` is carried through, not assumed.** It comes from the apply
   result's own `dry_run`. A dry run's rows say `dry_run: true` and no `created`
   fact is recorded anywhere.
4. **`reason` is the planner's string, copied.** Re-deriving it here would give
   the reason two owners and they would drift.
5. **`fields_changed` is only what was actually compared.** With `--existing`
   (the `calendar_io list` snapshot the runtime already writes to
   `.tmp/existing.json`) each row whose event was found in the snapshot is
   diffed against it, and the list names the payload fields that differ — an
   empty list means compared and identical. `null` means **no comparison was
   made**: no `--existing` snapshot at all, or no stored event matching this
   row's id (a create). A `skip` row proposes no title or colour, so those are
   not compared; its `state` is, when the matched event is in the snapshot.
6. **Append-only.** One JSON object per line, opened in append mode, so
   concurrent writers interleave whole lines rather than truncating each other.

`AGENTS.md`'s "a rule that reads a field nothing writes" cuts the other way too:
a field that is always `null` is decoration. That is why rule 5 has a producer
for the comparison rather than an invented empty list.

Usage:
    python3 scripts/calendar_log.py record \\
        --plan .tmp/plan.json --applied .tmp/applied.json --existing .tmp/existing.json \\
        --dest .tmp --run-id 2026-09-15T08:30Z
    python3 scripts/calendar_log.py record --plan p.json --applied a.json \\
        --dest .tmp --json

Exit codes:
    0  PASS — rows recorded, or a run with nothing to record
    1  FAIL — a live write carries no event id (recorded, and named)
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stream_links import normalise_links  # noqa: E402
from verification import normalise_state  # noqa: E402

LOG_NAME = "calendar-log.jsonl"
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_SKIP = "skip"
ACTION_UNCHANGED = "unchanged"
WRITTEN_ACTIONS = (ACTION_CREATE, ACTION_UPDATE)

# The payload fields the log compares against the stored event. Kept to what
# both sides of the round trip carry: the plan row and `calendar_io.parse_event`
# both name these, so the diff is a comparison and not a re-derivation.
COMPARED_FIELDS = ("summary", "state", "color_id", "start", "end", "teams", "links")


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


def load_existing(path: Path) -> list[dict]:
    """The `calendar_io.py list` snapshot (`{"events": [...]}` or a bare list)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError('existing events must be a list or {"events": [...]}')
    return [row for row in rows if isinstance(row, dict)]


def _teams(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(" vs ") if part.strip()]
    return []


def _body_of(action: dict) -> dict:
    body = action.get("body")
    return body if isinstance(body, dict) else {}


def _body_day(body: dict, key: str) -> str:
    value = body.get(key)
    if isinstance(value, dict):
        return str(value.get("dateTime") or value.get("date") or "")
    return str(value or "")


def _url_set(links: object) -> list[str]:
    """The URL identities in a link field, sorted, for a shape-free comparison.

    The two sides spell the field differently by design: the plan row goes
    through `stream_links.normalise_links` (`{"direct_links": [{"source", "url"}]}`)
    and the stored event through `links_from_description` (a bare list of the
    same pairs). Comparing the pair dicts verbatim would report a change when
    only the `source` label moved, so the comparison is over the URLs.
    """
    urls: list[str] = []
    if isinstance(links, dict):
        for entry in links.get("direct_links") or []:
            if isinstance(entry, dict) and entry.get("url"):
                urls.append(str(entry["url"]))
            elif isinstance(entry, str) and entry:
                urls.append(entry)
    elif isinstance(links, list):
        for entry in links:
            if isinstance(entry, dict) and entry.get("url"):
                urls.append(str(entry["url"]))
            elif isinstance(entry, str) and entry:
                urls.append(entry)
    return sorted(set(urls))


def _decided(plan_row: dict, applied_action: dict | None) -> dict:
    """The payload this row decided, from the applied body where there is one.

    The body is what was sent: a missing `end` is filled there from the
    documented default duration, so reading the plan instead would record an
    event that ends when it starts. A `skip` row has no body and, by the
    planner's own construction, carries no proposed title or colour — those are
    recorded as `""`, and rule 5 means an empty field is not compared.
    """
    body = _body_of(applied_action) if applied_action else {}
    teams = _teams(plan_row.get("teams"))
    return {
        "summary": str(body.get("summary") or plan_row.get("title") or ""),
        "state": normalise_state(plan_row.get("state")),
        "color_id": str(body.get("colorId") or plan_row.get("color_id") or ""),
        "start": _body_day(body, "start") or str(plan_row.get("start") or ""),
        "end": _body_day(body, "end") or str(plan_row.get("end") or ""),
        "teams": teams,
        "links": normalise_links(plan_row),
    }


def _stored(stored_event: dict) -> dict:
    """The same fields as `_decided`, read off a `calendar_io.parse_event` row.

    `parse_event` recovers the stored `colorId` under `league_color_id`, and the
    stored state from the title prefix — both are that function's documented
    round trip, so this reader does not re-parse anything itself.
    """
    return {
        "summary": str(stored_event.get("summary") or ""),
        "state": normalise_state(stored_event.get("state")),
        "color_id": str(stored_event.get("league_color_id") or stored_event.get("colorId") or ""),
        "start": str(stored_event.get("start") or ""),
        "end": str(stored_event.get("end") or ""),
        "teams": _teams(stored_event.get("teams")),
        "links": stored_event.get("links") or [],
    }


def _comparable(field: str, value: object) -> bool:
    """Did this row actually decide a value for `field`?

    A skipped row carries `title: ""` and `color_id: ""` on purpose (nothing is
    sent), so treating an empty value as "the new value is empty" would report a
    change against a stored event that was never touched. Only a field the row
    supplies is compared.
    """
    if field == "state":
        return bool(value)
    if field in ("teams", "links"):
        return bool(value)
    return bool(str(value or ""))


def _same(field: str, new: object, old: object) -> bool:
    if field == "teams":
        return sorted(str(t).lower() for t in new) == sorted(str(t).lower() for t in old)
    if field == "links":
        return _url_set(new) == _url_set(old)
    if field == "state":
        return normalise_state(new) == normalise_state(old)
    return str(new or "") == str(old or "")


def fields_changed(plan_row: dict, applied_action: dict | None, stored_event: dict | None) -> list[str] | None:
    """Which decided payload fields differ from the stored event, or `None`.

    `None` means **no comparison was possible**, which is not the same claim as
    `[]` ("compared and nothing differs"): a create has no stored event, and a
    row with no `--existing` snapshot has nothing to compare against at all.
    """
    if stored_event is None:
        return None
    decided = _decided(plan_row, applied_action)
    stored = _stored(stored_event)
    changed = [
        field
        for field in COMPARED_FIELDS
        if _comparable(field, decided[field]) and not _same(field, decided[field], stored[field])
    ]
    return changed


def build_rows(
    plan: list[dict],
    applied: dict,
    *,
    run_id: str,
    now: datetime | None = None,
    existing: list[dict] | None = None,
) -> tuple[list[dict], list[str], str]:
    """(rows, refusals, reason-there-are-no-rows). One row per plan game."""
    dry_run = bool(applied.get("dry_run"))
    actions = {
        str(action.get("game_key") or ""): action
        for action in applied.get("actions") or []
        if isinstance(action, dict)
    }
    stored_by_id = {
        str(event.get("event_id") or ""): event
        for event in existing or []
        if isinstance(event, dict) and event.get("event_id")
    }
    if not plan:
        return [], [], "the plan is empty — no game was considered this run"

    rows: list[dict] = []
    refusals: list[str] = []
    for plan_row in plan:
        game_key = str(plan_row.get("game_key") or "")
        applied_action = actions.get(game_key)
        action = str(
            (applied_action or {}).get("action") or plan_row.get("action") or ""
        )
        event_id = str(
            (applied_action or {}).get("event_id") or plan_row.get("event_id") or ""
        )
        decided = _decided(plan_row, applied_action)
        stored_event = stored_by_id.get(event_id)
        row = {
            "ts": utc_iso(now),
            "run_id": run_id,
            "game_key": game_key,
            "event_id": event_id,
            "action": action,
            "reason": str(plan_row.get("reason") or ""),
            "state": decided["state"],
            "league": str(plan_row.get("league") or ""),
            "teams": decided["teams"],
            "start": decided["start"],
            "end": decided["end"],
            "dry_run": dry_run,
            "fields_changed": fields_changed(plan_row, applied_action, stored_event),
            "links": decided["links"],
        }
        rows.append(row)
        if not dry_run and action in WRITTEN_ACTIONS and not event_id:
            # Recorded and named rather than withheld: these rows are decisions,
            # not the audit's verdict keys, so hiding the run's other decisions
            # would lose the observability this file exists for. `event_ledger`
            # withholds its rows in the same case because a verdict it cannot
            # key could never be acted on; that reason does not apply here.
            refusals.append(
                f"{game_key or '(no game_key)'}: {action} was applied live but carries "
                "no event_id — the calendar named no event for it"
            )
    return rows, refusals, ""


def append_rows(dest: Path, rows: list[dict]) -> int:
    if not rows:
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    with (dest / LOG_NAME).open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def render_markdown(rows: list[dict], *, reason: str = "") -> str:
    """The job-summary view. One renderer for the artifact, not two."""
    lines = ["| | |", "|---|---|", f"| Decisions recorded | {len(rows)} |", ""]
    if not rows:
        lines.append(f"No decision rows: {reason or 'nothing was considered'}.")
        return "\n".join(lines)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["action"]] = counts.get(row["action"], 0) + 1
    lines += ["| Action | Count |", "|---|---|"]
    for action in sorted(counts):
        lines.append(f"| `{action}` | {counts[action]} |")
    lines += ["", "| Game | Action | State | Event | Why |", "|---|---|---|---|---|"]
    for row in rows:
        event = row["event_id"] or "—"
        lines.append(
            f"| {row['game_key']} | {row['action']} | {row['state']} | {event} "
            f"| {row['reason']} |"
        )
    dry = sum(1 for row in rows if row["dry_run"])
    if dry:
        lines += [
            "",
            f"{dry} row(s) are `dry_run: true`: nothing was written, so no row is a "
            "`created` fact.",
        ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("record",))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--applied", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True,
                        help="telemetry directory for calendar-log.jsonl")
    parser.add_argument("--existing", type=Path,
                        help="calendar_io list snapshot, for the fields_changed diff")
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
        existing = load_existing(args.existing) if args.existing else None
    except FileNotFoundError as exc:
        print(f"FAIL: calendar_log: {exc.filename}: file not found", file=sys.stderr)
        sys.exit(2)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"FAIL: calendar_log: {exc}", file=sys.stderr)
        sys.exit(2)

    now = None
    if args.now:
        try:
            now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        except ValueError:
            print(
                f"FAIL: calendar_log: --now {args.now!r} is not ISO-8601",
                file=sys.stderr,
            )
            sys.exit(2)

    rows, refusals, reason = build_rows(
        plan, applied, run_id=args.run_id, now=now, existing=existing
    )

    if args.markdown:
        print(render_markdown(rows, reason=reason))
        return

    if args.dry_run:
        print(f"OK: calendar_log: would record {len(rows)} decision(s) — dry run, "
              "nothing written")
        return

    written = append_rows(args.dest, rows)

    if args.json:
        print(json.dumps(
            {"rows": rows, "written": written, "refusals": refusals, "reason": reason},
            indent=2,
        ))
        if refusals:
            for refusal in refusals:
                print(f"FAIL: calendar_log: {refusal}", file=sys.stderr)
            sys.exit(1)
        return

    for refusal in refusals:
        print(f"FAIL: calendar_log: {refusal}", file=sys.stderr)
    if not written:
        print(f"OK: calendar_log: nothing recorded — {reason}")
        return
    print(f"OK: calendar_log: recorded {written} decision(s) -> {args.dest / LOG_NAME}")
    if refusals:
        sys.exit(1)


if __name__ == "__main__":
    main()
