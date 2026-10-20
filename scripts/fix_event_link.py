#!/usr/bin/env python3
"""Correct one dead stream link on one calendar event.

A one-off repair, run only by the manually dispatched `fix-event-link`
workflow. It is a dry run unless `--live` is passed, and it refuses to write
unless the old link appears verbatim in the event's description, so it can only
ever change the event it identified and the text it was told to change.

The write uses the same Composio `PATCH_EVENT` path as `calendar_io.py apply`,
with only `description` sent, because PATCH replaces the fields it is given.
After the write the event is read back and must contain the new link.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time, timedelta, timezone

try:  # direct CLI execution: `python3 scripts/fix_event_link.py`
    import calendar_io as cio
except ImportError:
    from scripts import calendar_io as cio


def replace_link(description: str, old: str, new: str) -> str | None:
    """`description` with `old` replaced by `new`, or None if `old` is absent."""
    if not old or old not in description:
        return None
    return description.replace(old, new)


def day_window(day: date) -> tuple[str, str]:
    """The UTC window [day-1, day+2) that holds an event starting on `day`."""
    start = datetime.combine(day - timedelta(days=1), time(0), tzinfo=timezone.utc)
    end = datetime.combine(day + timedelta(days=2), time(0), tzinfo=timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


def find_event(events: list[dict], event_id: str) -> dict | None:
    for event in events:
        if str(event.get("event_id") or "") == event_id:
            return event
    return None


def _fail(message: str) -> int:
    print(f"FAIL: fix_event_link: {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--old-url", required=True)
    parser.add_argument("--new-url", required=True)
    parser.add_argument("--day", required=True, type=date.fromisoformat,
                        help="the day the event starts, YYYY-MM-DD (UTC window around it)")
    parser.add_argument("--live", action="store_true", help="write; without it, dry run")
    parser.add_argument("--calendar-id", default=os.environ.get("BASKETBALL_CALENDAR_ID", ""))
    parser.add_argument("--api-key", default=os.environ.get("COMPOSIO_API_KEY", ""))
    parser.add_argument("--user-id", default=os.environ.get("COMPOSIO_USER_ID", ""))
    args = parser.parse_args(argv)

    if not args.new_url.startswith("https://"):
        return _fail("--new-url must be an https URL")
    if not (args.calendar_id and args.api_key and args.user_id):
        return _fail("needs BASKETBALL_CALENDAR_ID, COMPOSIO_API_KEY and COMPOSIO_USER_ID")

    scope = cio.scope_arguments(args.user_id, "")
    time_min, time_max = day_window(args.day)
    try:
        events = cio.list_events(
            args.api_key, scope, args.calendar_id, time_min=time_min, time_max=time_max
        )
        event = find_event(events, args.event_id)
        if event is None:
            return _fail(f"event {args.event_id} not found between {time_min} and {time_max}")
        new_description = replace_link(
            str(event.get("description") or ""), args.old_url, args.new_url
        )
        if new_description is None:
            print(f"Description of {args.event_id}:\n{event.get('description')}", file=sys.stderr)
            return _fail("the old link is not in the event description; refusing to write")

        print(f"event {args.event_id}: {event.get('summary')} (start {event.get('start')})")
        print(f"  old: {args.old_url}")
        print(f"  new: {args.new_url}")
        if not args.live:
            print("DRY RUN: nothing written. Re-run with --live to patch the event.")
            return 0

        cio._execute_tool(
            cio.TOOL_PATCH_EVENT,
            {
                "calendar_id": args.calendar_id,
                "event_id": args.event_id,
                "description": new_description,
                "send_updates": "none",
            },
            api_key=args.api_key,
            scope=scope,
        )
        after = cio.list_events(
            args.api_key, scope, args.calendar_id, time_min=time_min, time_max=time_max
        )
        check = find_event(after, args.event_id)
        if check is None:
            return _fail("event missing after the patch; read-back failed")
        description = str(check.get("description") or "")
        if args.new_url not in description or args.old_url in description:
            return _fail("read-back does not show the new link; check the calendar")
        # PATCH replaces the fields it is given: prove the rest of the event survived.
        if (check.get("summary"), check.get("start")) != (event.get("summary"), event.get("start")):
            return _fail("read-back shows the title or start changed; check the calendar")
    except cio.CalendarError as exc:
        return _fail(f"calendar call failed: {exc}")

    print(f"OK: fix_event_link: {args.event_id} now links {args.new_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
