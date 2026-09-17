#!/usr/bin/env python3
"""calendar_io.py — the only place that talks to the calendar.

Phase 1 of `live-stream-runtime-spec.md`. Splitting the calendar call out of the
workflow shell does two things that a `curl` in YAML cannot:

1. **The read path feeds the planner.** `list` returns events already normalised
   into the shape `scripts/upsert_events.py` matches on — including the
   verification `state`, read back out of the title prefix via
   `verification.state_from_title`. Without that, the planner cannot tell a
   confirmed event from an uncertain one, and "never touch a verified event"
   becomes unenforceable.
2. **A bare invocation cannot write.** `apply` performs no HTTP at all unless
   `--live` is passed, so the safe behaviour is the default and the dangerous one
   has to be spelled out. The workflow gates on `ENABLE_CALENDAR_WRITES` *and*
   passes `--live`; a developer running the command by hand gets a plan.

**Transport: Composio, not the Calendar API directly.** There is no Google Cloud
project, no service account and no OAuth token in this repo: the calls go to
Composio's tool-execution endpoint, which holds the Google credential for a
connected account. That removes the Workload Identity Federation setup the
earlier version of this file needed, at the cost of the calendar credential
living with a third party — see `docs/runtime.md`.

The interface is deliberately unchanged. Same modes, same flags, same output
files, same exit codes; `parse_event`, `list_events`' pagination and
`apply_plan`'s dry-run guarantee are the same code. Only `_execute_tool` and the
argument names differ, because Composio returns the Google response body but
addresses it with its own parameter names.

Auth is `COMPOSIO_API_KEY` (or `--api-key`), read but never logged, plus the
account the call is scoped to: `COMPOSIO_USER_ID` (or `--user-id`), or
`--connected-account-id`. All four are required only to read or to write — **not**
for a dry run.

Five behaviours were verified against the live API rather than assumed, and all
five are load-bearing:

- `CREATE_EVENT` **ignores `color_id`** (it is not in that tool's schema). An
  event is created in the default colour and the intended one is applied by a
  follow-up `PATCH_EVENT`, so a create is two calls. A colour patch that fails is
  reported as a failure even though the event exists, because an event in the
  wrong colour is a wrong label on a public calendar.
- `CREATE_EVENT` **adds a Google Meet link** unless `create_meeting_room` is
  `false`, and **adds the connected user as an attendee** unless
  `exclude_organizer` is `true`. Both were observed in a throwaway event during
  development; neither belongs on a public streams calendar.
- `CREATE_EVENT` nests the created event under `data.response_data`, while
  `EVENTS_LIST` returns the Google body directly under `data`. `_payload()`
  handles both.
- `data` is documented as a string. It is a dict on v3.1. Both are accepted,
  because the documentation and the wire disagree and only one of them is reality.
- **`visibility` is a real argument** on both `CREATE_EVENT` and `PATCH_EVENT`
  (`VISIBILITY_VALUES` in `calendar_config.py`), and it is now sent. It was
  the one setting in `config/calendar.json` that everything read and nothing
  wrote: `calendar_config.get_visibility()` existed, `SETUP.md` documented it, and
  no code path applied it, so "events are created public" rested on a reader
  nobody called. It travels on updates as well as creates, because a PATCH that
  omitted it would clear the value a previous run set.

Usage:
    python3 scripts/calendar_io.py list  --out existing.json --time-min 2026-09-14T00:00:00Z --time-max 2026-09-22T00:00:00Z
    python3 scripts/calendar_io.py apply --plan plan.json          # dry run, no HTTP
    python3 scripts/calendar_io.py apply --plan plan.json --live    # performs the writes

Exit codes:
    0  PASS — the request(s) succeeded, or a dry run listed what it would do
    1  FAIL — the API rejected a request, or there was nothing to apply
    2  USAGE — bad arguments, missing credential, or unreadable input
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # direct CLI execution: `python3 scripts/calendar_io.py`
    from calendar_config import VISIBILITY_VALUES, get_visibility
    from stream_links import (
        links_from_description,
        render_block,
        source_reference_from_description,
        validated_at_from_description,
        validation_notes_from_description,
    )
    from verification import STATE_PREFIXES, STATE_VERIFIED, state_from_title
except ImportError:  # imported as a package module, e.g. scripts.calendar_io
    from scripts.calendar_config import (  # type: ignore[no-redef]
        VISIBILITY_VALUES,
        get_visibility,
    )
    from scripts.stream_links import (  # type: ignore[no-redef]
        links_from_description,
        render_block,
        source_reference_from_description,
        validated_at_from_description,
        validation_notes_from_description,
    )
    from scripts.verification import (  # type: ignore[no-redef]
        STATE_PREFIXES,
        STATE_VERIFIED,
        state_from_title,
    )

COMPOSIO_ROOT = "https://backend.composio.dev/api/v3.1"
# `latest` rather than a pinned toolkit version, deliberately. Composio's own
# guide records that "older pinned Google Calendar toolkit versions can drop or
# remap filters such as `timeMin` and `timeMax` before the request reaches
# Google" — and the window filter is the whole point of the read path here. A
# pin would be a silent, partial loss of the dedupe window.
COMPOSIO_TOOLKIT_VERSION = "latest"
TOOL_EVENTS_LIST = "GOOGLECALENDAR_EVENTS_LIST"
TOOL_CREATE_EVENT = "GOOGLECALENDAR_CREATE_EVENT"
TOOL_PATCH_EVENT = "GOOGLECALENDAR_PATCH_EVENT"
TIMEOUT_SECONDS = 30
DEFAULT_TIMEZONE = "Europe/Berlin"
# `VISIBILITY_VALUES` comes from `calendar_config.py`, which owns the field: the
# enum `CREATE_EVENT`/`PATCH_EVENT` accept, recorded from the live tool schema.
# Validated rather than forwarded, because an unaccepted value is rejected by
# Google and the rejection names the API's parameter rather than the config file
# that supplied the typo.
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_SKIP = "skip"


class CalendarError(RuntimeError):
    """Raised when the API rejects a request, carrying the status and body."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def _parse_dt(value: object) -> datetime | None:
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


def _unwrap(value: object) -> object:
    """A payload that may be the object itself or a JSON string of it.

    Composio's docs type `data` as a string; v3.1 returns an object. Both are
    accepted rather than trusting either, because a version that switches back
    would otherwise present as "the calendar is empty" instead of an error.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": value}
    return value


def _payload(envelope: object) -> dict:
    """The tool's own result, unwrapped from the execution envelope.

    Two shapes, both verified against the live API. `EVENTS_LIST` puts the Google
    response body straight under `data`. `CREATE_EVENT` wraps it one level
    further, under `data.response_data`, alongside `display_url` and a nullable
    `composio_execution_message`. Reading only `data` on a create finds no `id`,
    and the id is the one thing the audit needs.
    """
    if not isinstance(envelope, dict):
        return {}
    data = _unwrap(envelope.get("data"))
    if isinstance(data, dict):
        nested = _unwrap(data.get("response_data"))
        if isinstance(nested, dict):
            return nested
        return data
    # No usable `data`: fall back to the envelope's own top level rather than
    # returning `{}`. An empty dict here is indistinguishable from "the calendar
    # has no events", which is the one reading of a failure that this system
    # must never produce — it looks exactly like a quiet day.
    return envelope


def scope_arguments(user_id: str, connected_account_id: str) -> dict:
    """Which account to act as, as Composio wants it.

    A `user_id` and a `connected_account_id` are *not* interchangeable: they go
    in different fields, and a connected-account id sent as a `user_id` fails as
    "no connected account", which reads like a broken credential rather than a
    misplaced argument. One of the two is set, never both — a request carrying
    both is ambiguous about which account the write belongs to.
    """
    if connected_account_id:
        return {"connected_account_id": connected_account_id}
    return {"user_id": user_id}


def _execute_tool(
    slug: str,
    arguments: dict,
    *,
    api_key: str,
    scope: dict,
    timeout: float = TIMEOUT_SECONDS,
) -> dict:
    """One Composio tool execution. Never logs the API key.

    Raises `CalendarError` for a transport failure **and** for a tool that
    reported `successful: false` — a 200 carrying a refusal is the failure mode
    that would otherwise be read as an empty result, and an empty result is
    indistinguishable from "no events today".
    """
    body = {**scope, "arguments": arguments, "version": COMPOSIO_TOOLKIT_VERSION}
    request = urllib.request.Request(
        f"{COMPOSIO_ROOT}/tools/execute/{slug}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace") if exc.fp else ""
        # Composio's error body carries its own reason and a request id; the API
        # key never appears in it.
        raise CalendarError(exc.code, detail[:500] or exc.reason) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CalendarError(0, f"network error: {exc}") from exc
    if not payload.strip():
        return {}
    try:
        envelope = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CalendarError(0, f"non-JSON response: {exc}") from exc
    if isinstance(envelope, dict) and envelope.get("successful") is False:
        # `log_id` is Composio's own handle for the call, so a failure that
        # needs their support does not require guessing which request it was.
        raise CalendarError(
            0,
            f"{slug} reported failure: {envelope.get('error') or 'no error message'} "
            f"(log_id={envelope.get('log_id') or 'n/a'})",
        )
    return _payload(envelope)


def _description_field(description: str, key: str) -> str:
    """The value of a `Key: value` line, or `""`."""
    for line in (description or "").splitlines():
        text = line.strip()
        if text.lower().startswith(f"{key.lower()}:"):
            return text.split(":", 1)[1].strip()
    return ""


def teams_from_description(description: str) -> list[str]:
    """Read the `Teams: A vs B` line out of an event description.

    This is the *primary* identity a stored event carries, and the one this
    system writes (`description_for`), so it is what `events_match` prefers. It is
    not the only one: measured on the live calendar on 2026-09-15, 78 of 102
    events had no `Teams:` line at all — most of the calendar predates the
    writer — so an event that records nothing here still has to be identified by
    what it *says*. `upsert_events.events_match` does that fallback, against the
    event's own text, rather than this function guessing from a `summary` whose
    title convention is not ours to define.
    """
    value = _description_field(description, "Teams")
    parts = [part.strip() for part in value.split(" vs ") if part.strip()]
    return parts if len(parts) == 2 else []


def league_from_description(description: str) -> str:
    """Read the `League:` line, the counterpart `description_for` writes.

    Without it a stored event carries no league at all, so `events_match`'s
    league check is skipped for every event the runtime wrote.
    """
    return _description_field(description, "League")


def description_for(row: dict) -> str:
    """The documented `League:`/`Teams:` block for a plan row.

    `references/calendar-setup.md` specifies this block, `list_events` reads the
    `Teams:` line back out, and `upsert_events.events_match` matches on it — but
    nothing ever *wrote* one: every event the runtime created carried
    `description: ""`. `events_match` used to skip the teams check whenever either
    side was empty, so a stored event with no description matched **any** candidate
    within the 30-minute window, including a different pairing. Both halves are now
    closed: this function is the writer, and `events_match` no longer treats an
    absent `Teams:` line as permission to match anything.

    Derived from the plan row rather than required from the caller: the row
    already carries `league` and `teams` (they exist for exactly this), and a
    caller that has to remember to build a description is one that will not.

    The link half (`Date/Time:`, `FREE STREAM LINKS:`, `Access:`, `SOURCE
    REFERENCE:`) comes from `stream_links.render_block`, which renders only the
    parts the row actually has — so a row with no link information produces
    exactly the two lines it always did. That is what keeps the whole
    specification in one place instead of in this function and a parser
    somewhere else.
    """
    league = str(row.get("league") or "").strip()
    teams = [str(part).strip() for part in (row.get("teams") or []) if str(part).strip()]
    start = str(row.get("start") or row.get("startTime") or "").strip()
    lines = []
    if league:
        lines.append(f"League: {league}")
    if len(teams) == 2:
        lines.append(f"Teams: {' vs '.join(teams)}")
    # Only once the entry can already identify the game. A timestamp on its own
    # names no event, so a row with no league and no teams still gets no
    # description at all — which is the property `created` events were relying on
    # before this line existed.
    if lines and start:
        lines.append(f"Date/Time: {start}")
    extra = render_block(row)
    if not lines and extra and extra[0] == "":
        # No identity block to separate the sections from, so the leading blank
        # line would be the first character of the description.
        extra = extra[1:]
    lines += extra
    return "\n".join(lines) + ("\n" if lines else "")


def _day_of(value: object) -> str:
    """The date-time of a Google `start`/`end` object, or of an already-named one.

    Both shapes arrive in practice: an API event carries
    `{"dateTime": "…", "timeZone": "…"}`, and this function's own output carries
    the flattened string. Reading only the object shape made `parse_event`
    **crash on its own output** (`str` has no `.get`), which is the shape
    `calendar_io list > events.json` writes — and that file is the documented
    input of `link_inventory.py`, so the round trip had to be closed here rather
    than special-cased at the caller.
    """
    if isinstance(value, dict):
        return str(value.get("dateTime") or value.get("date") or "")
    return str(value or "")


def parse_event(raw: dict) -> dict:
    """Normalise an API event into the shape `upsert_events` matches on.

    Idempotent: feeding it the dict it returned yields the same dict, so an export
    written by `calendar_io list` can be read back by anything that expects an API
    event.
    """
    if not isinstance(raw, dict):
        raise ValueError("event must be an object")
    summary = str(raw.get("summary") or "")
    description = str(raw.get("description") or "")
    return {
        "event_id": str(raw.get("id") or raw.get("event_id") or ""),
        "summary": summary,
        "description": description,
        "start": _day_of(raw.get("start")),
        "end": _day_of(raw.get("end")),
        "teams": teams_from_description(description),
        "league": league_from_description(description),
        "state": state_from_title(summary),
        "league_color_id": str(raw.get("colorId") or raw.get("league_color_id") or ""),
        "status": str(raw.get("status") or "confirmed"),
        # Read back out of the description — the round trip that makes the link
        # revalidation loop possible. `scripts/link_inventory.py` turns these into
        # the `links.json` that `link_check.py --input` has always been documented
        # to take, and until `description_for` wrote them there was nothing to
        # read: the stored link existed only as prose in a reference document.
        "links": links_from_description(description),
        "source_reference": source_reference_from_description(description),
        "validated_at": validated_at_from_description(description),
        "validation_notes": validation_notes_from_description(description),
    }


def list_events(
    api_key: str,
    scope: dict,
    calendar_id: str,
    *,
    time_min: str,
    time_max: str,
    max_results: int = 250,
) -> list[dict]:
    """Every confirmed event in [time_min, time_max), normalised. Paginates.

    `EVENTS_LIST` passes the Google query through, so the filters keep Google's
    camelCase names while the *tool* arguments are typed JSON — `True`, not the
    string `"true"` the direct API wanted. The response body is Google's, which
    is why `items` and `nextPageToken` are read exactly as before.
    """
    events: list[dict] = []
    page_token = ""
    while True:
        arguments: dict = {
            "calendarId": calendar_id,
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": max_results,
            "showDeleted": False,
        }
        if page_token:
            arguments["pageToken"] = page_token
        payload = _execute_tool(
            TOOL_EVENTS_LIST, arguments, api_key=api_key, scope=scope
        )
        for item in payload.get("items") or []:
            if isinstance(item, dict):
                events.append(parse_event(item))
        page_token = str(payload.get("nextPageToken") or "")
        if not page_token:
            return events


def build_event_body(
    row: dict,
    *,
    timezone: str = DEFAULT_TIMEZONE,
    description: str = "",
    default_duration_hours: float = 2.5,
    visibility: str = "",
) -> dict:
    """Request body for a plan row.

    `summary` comes from the planner already carrying the right state prefix, and
    `colorId` from the state machine — this function must not re-derive either,
    or the state would have two owners.

    A missing `end` is filled from the documented default duration rather than
    sent empty, because the Calendar API rejects an event whose end precedes or
    equals its start, and a rejected write is a silently missing game.

    `visibility` is carried here because it was the one setting in
    `config/calendar.json` with **no writer at all**: `calendar_config.py`
    returned it, `SETUP.md` and `references/calendar-setup.md` both documented
    it as the intended event visibility, and no code path ever sent it — so the
    promise "events are public" came entirely from a reader nobody called. An
    empty value is left empty and the tool's own default applies; `visibility`
    is *not* re-derived from the row, for the same reason `summary` is not.
    """
    start = str(row.get("start") or "")
    end = str(row.get("end") or "")
    if not end and start:
        moment = _parse_dt(start)
        if moment is not None:
            end = (moment + timedelta(hours=default_duration_hours)).isoformat()
    return {
        "summary": str(row.get("title") or ""),
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
        "colorId": str(row.get("color_id") or ""),
        "description": description,
        "visibility": visibility,
    }


def create_arguments(body: dict, calendar_id: str, timezone: str) -> dict:
    """`build_event_body`'s output, addressed the way `CREATE_EVENT` wants it.

    Two of these keys are not cosmetic. `create_meeting_room` and
    `exclude_organizer` default to the opposite of what this product wants, and
    both were observed doing it: a throwaway event created during development
    came back with a Google Meet link nobody asked for and the connected user
    added as an attendee. On a public calendar of free-to-watch streams, an
    event that invites its owner and advertises a video call is wrong in a way
    no test would have caught, because the *tool* considered it a success.

    `color_id` is deliberately absent: this tool ignores it. See
    `apply_plan`, which patches the colour after the create.

    `visibility` **is** accepted by this tool — it is in the live schema, as the
    enum `default` / `public` / `private` / `confidential` — and it is sent only
    when a caller supplied one, because the empty string is not a member of that
    enum and an invalid value is a rejected write rather than a default.
    """
    arguments = {
        "calendar_id": calendar_id,
        "summary": str(body.get("summary") or ""),
        "description": str(body.get("description") or ""),
        "start_datetime": str((body.get("start") or {}).get("dateTime") or ""),
        "end_datetime": str((body.get("end") or {}).get("dateTime") or ""),
        "timezone": timezone,
        "create_meeting_room": False,
        "exclude_organizer": True,
        "send_updates": "none",
    }
    visibility = str(body.get("visibility") or "")
    if visibility:
        arguments["visibility"] = visibility
    return arguments


def patch_arguments(
    body: dict, calendar_id: str, event_id: str, timezone: str
) -> dict:
    """The same body, addressed the way `PATCH_EVENT` wants it.

    The two tools disagree on their own parameter names — `start_datetime` on
    CREATE,    `start_time` on PATCH — so this mapping is the one place that
    difference is allowed to exist. `color_id` is here and only here, because
    PATCH is the only one of the two that honours it.

    `visibility` travels on an update too, and that is not cosmetic: PATCH
    replaces the fields it is given, so an update that omitted it would clear the
    visibility a previous run set and let the event fall back to the calendar's
    default — a *later* run silently undoing the label, which no create-time test
    would notice.
    """
    arguments = {
        "calendar_id": calendar_id,
        "event_id": event_id,
        "summary": str(body.get("summary") or ""),
        "description": str(body.get("description") or ""),
        "start_time": str((body.get("start") or {}).get("dateTime") or ""),
        "end_time": str((body.get("end") or {}).get("dateTime") or ""),
        "timezone": timezone,
        "send_updates": "none",
    }
    colour = str(body.get("colorId") or "")
    if colour:
        arguments["color_id"] = colour
    visibility = str(body.get("visibility") or "")
    if visibility:
        arguments["visibility"] = visibility
    return arguments


def apply_plan(
    plan: list[dict],
    *,
    api_key: str = "",
    scope: dict | None = None,
    calendar_id: str = "",
    timezone: str = DEFAULT_TIMEZONE,
    live: bool = False,
    descriptions: dict | None = None,
    visibility: str = "",
) -> dict:
    """Execute a planner's create/update rows. `skip` rows send nothing.

    With `live=False` this performs **no HTTP at all** — the safe behaviour is
    the default, and "did we write to a public calendar?" must be answerable by
    reading the command line rather than by trusting a code path.
    """
    descriptions = descriptions or {}
    scope = scope or {}
    result = {
        "dry_run": not live,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "failed": [],
        "actions": [],
    }
    for row in plan or []:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "")
        if action == ACTION_SKIP:
            result["skipped"] += 1
            continue
        if action not in (ACTION_CREATE, ACTION_UPDATE):
            result["failed"].append(
                {"game_key": str(row.get("game_key") or ""), "reason": "unknown action"}
            )
            continue

        # An explicit description wins; otherwise it is derived from the row.
        # Sending `""` was the old behaviour and it silently dropped the only
        # field the next run can match a stored event by.
        description = descriptions.get(str(row.get("game_key") or "")) or description_for(row)
        body = build_event_body(
            row,
            timezone=timezone,
            description=description,
            visibility=visibility,
        )

        if not live:
            result["actions"].append(
                {
                    "action": action,
                    "game_key": row.get("game_key"),
                    # Empty, deliberately: nothing was written, so there is no
                    # event for a Phase 3 verdict to be about. The ledger writer
                    # refuses an id-less row rather than keying one to nothing.
                    "event_id": "",
                    "body": body,
                }
            )
            result["created" if action == ACTION_CREATE else "updated"] += 1
            continue

        try:
            if action == ACTION_CREATE:
                created = _execute_tool(
                    TOOL_CREATE_EVENT,
                    create_arguments(body, calendar_id, timezone),
                    api_key=api_key,
                    scope=scope,
                )
                # The API is the only authority on a new event's id. Recording
                # it is what makes the write auditable at all: the audit
                # resolves a verdict back to an event *by id*, so a discarded
                # id left `events.jsonl` unkeyable and Phase 3 dead.
                event_id = str((created or {}).get("id") or "")
                if not event_id:
                    # Refused rather than counted: an event exists somewhere and
                    # this run cannot say where, so calling it created would put
                    # an unkeyable row in the audit's ledger.
                    result["failed"].append(
                        {
                            "game_key": str(row.get("game_key") or ""),
                            "reason": (
                                "create returned no event id — the event may exist "
                                "and cannot be resolved by the audit"
                            ),
                        }
                    )
                    continue
                result["created"] += 1
                colour = str(row.get("color_id") or "")
                if colour:
                    # CREATE ignores `color_id`, so the colour is a second call.
                    # Verified, not assumed: a create that passed `color_id`
                    # returned an event with no `colorId` at all.
                    #
                    # The event now exists in the default colour, so a failure
                    # here is recorded as well as the action: the audit must
                    # still learn the event's id (it happened), and the run must
                    # still fail (its label is wrong).
                    try:
                        _execute_tool(
                            TOOL_PATCH_EVENT,
                            {
                                "calendar_id": calendar_id,
                                "event_id": event_id,
                                "color_id": colour,
                                "send_updates": "none",
                            },
                            api_key=api_key,
                            scope=scope,
                        )
                    except CalendarError as exc:
                        result["failed"].append(
                            {
                                "game_key": str(row.get("game_key") or ""),
                                "reason": (
                                    f"event {event_id} was created but its colour "
                                    f"could not be set — {exc}"
                                ),
                                "status": exc.status,
                            }
                        )
            else:
                event_id = str(row.get("event_id") or "")
                if not event_id:
                    result["failed"].append(
                        {
                            "game_key": str(row.get("game_key") or ""),
                            "reason": "update row without an event_id",
                        }
                    )
                    continue
                patched = _execute_tool(
                    TOOL_PATCH_EVENT,
                    patch_arguments(body, calendar_id, event_id, timezone),
                    api_key=api_key,
                    scope=scope,
                )
                result["updated"] += 1
                # A response that echoes the event wins; the plan's own id is
                # the fallback for a reply that carries no body.
                event_id = str((patched or {}).get("id") or event_id)
            result["actions"].append(
                {
                    "action": action,
                    "game_key": row.get("game_key"),
                    "event_id": event_id,
                    "body": body,
                }
            )
        except CalendarError as exc:
            result["failed"].append(
                {
                    "game_key": str(row.get("game_key") or ""),
                    "reason": str(exc),
                    "status": exc.status,
                }
            )
    return result


def _window(days: int, *, now: datetime | None = None) -> tuple[str, str]:
    """[today 00:00Z, today+days 00:00Z) — the same window the planner matches on.

    Boundary-inclusive in the calendar's timezone in practice, but computed in
    UTC here and widened on purpose: listing a slightly larger window costs one
    API call and can only ever *add* candidates for dedupe to find. A narrower
    window would risk a duplicate event, which is the failure that matters.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days + 1)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def _load_json(path: Path, key: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: calendar_io: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"FAIL: calendar_io: {path}: invalid JSON ({exc})", file=sys.stderr)
        sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List and write Google Calendar events for the streams runtime.",
    )
    parser.add_argument("mode", choices=("list", "apply"))
    parser.add_argument("--calendar-id", default=os.environ.get("BASKETBALL_CALENDAR_ID", ""))
    parser.add_argument("--api-key", default=os.environ.get("COMPOSIO_API_KEY", ""))
    parser.add_argument("--user-id", default=os.environ.get("COMPOSIO_USER_ID", ""))
    parser.add_argument(
        "--connected-account-id",
        default=os.environ.get("COMPOSIO_CONNECTED_ACCOUNT_ID", ""),
        help="scope the call to one connected account instead of a user id",
    )
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    # From `config/calendar.json` via `calendar_config`, not a literal: this is
    # the field that used to be read by everyone and written by nobody, so the
    # default has to come from the file that documents it or the loop stays open.
    parser.add_argument("--visibility", default=get_visibility())
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--time-min")
    parser.add_argument("--time-max")
    parser.add_argument("--now", help="ISO-8601 override (for tests)")
    parser.add_argument("--out")
    parser.add_argument("--plan")
    parser.add_argument(
        "--live",
        action="store_true",
        help="actually write. Without it, apply performs no HTTP at all",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.now and _parse_dt(args.now) is None:
        print(f"FAIL: calendar_io: --now {args.now!r} is not ISO-8601", file=sys.stderr)
        sys.exit(2)

    # Fail-closed and *named*: an unaccepted value is rejected by the API, which
    # reports its own enum rather than the config file that supplied the typo.
    if args.visibility and args.visibility not in VISIBILITY_VALUES:
        print(
            f"FAIL: calendar_io: --visibility {args.visibility!r} is not one of "
            f"{', '.join(VISIBILITY_VALUES)}",
            file=sys.stderr,
        )
        sys.exit(2)

    if not args.calendar_id:
        print(
            "FAIL: calendar_io: no calendar id (--calendar-id or "
            "BASKETBALL_CALENDAR_ID)",
            file=sys.stderr,
        )
        sys.exit(2)

    api_key = args.api_key or os.environ.get("COMPOSIO_API_KEY", "")
    # A `user_id` is the documented default because it is the one that survives a
    # reconnect; the connected-account id is the override. Passing both is
    # refused rather than resolved, because the request would be ambiguous about
    # which account a write belongs to.
    if args.user_id and args.connected_account_id:
        print(
            "FAIL: calendar_io: pass --user-id or --connected-account-id, not "
            "both — the request would be ambiguous about whose calendar it is",
            file=sys.stderr,
        )
        sys.exit(2)
    # A credential is needed to read, and to write — but **not** for a dry run, so
    # `apply` on a plan stays inspectable offline with no credentials at all.
    # That is the property the CI smoke step and every test in the suite rely on.
    needs_credential = args.mode == "list" or args.live
    if needs_credential and not api_key:
        print(
            "FAIL: calendar_io: no Composio API key (--api-key or COMPOSIO_API_KEY)",
            file=sys.stderr,
        )
        sys.exit(2)
    if needs_credential and not (args.user_id or args.connected_account_id):
        print(
            "FAIL: calendar_io: no account to act as (--user-id or "
            "COMPOSIO_USER_ID, or --connected-account-id)",
            file=sys.stderr,
        )
        sys.exit(2)
    scope = scope_arguments(args.user_id, args.connected_account_id)

    if args.mode == "list":
        time_min, time_max = (args.time_min, args.time_max)
        if not (time_min and time_max):
            time_min, time_max = _window(
                args.days, now=_parse_dt(args.now) if args.now else None
            )
        try:
            events = list_events(
                api_key, scope, args.calendar_id, time_min=time_min, time_max=time_max
            )
        except CalendarError as exc:
            print(f"FAIL: calendar_io: list failed — {exc}", file=sys.stderr)
            sys.exit(1)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(
                json.dumps({"events": events}, indent=2) + "\n", encoding="utf-8"
            )
        if args.json:
            print(json.dumps({"events": events, "time_min": time_min, "time_max": time_max}, indent=2))
        else:
            for event in events:
                print(
                    f"OK: calendar_io: {event['event_id']} {event['state']} "
                    f"{event['start']} {event['summary']!r}"
                )
            print(
                f"OK: calendar_io: {len(events)} events in "
                f"[{time_min}, {time_max})"
            )
        return

    if not args.plan:
        print("FAIL: calendar_io: apply requires --plan", file=sys.stderr)
        sys.exit(2)
    payload = _load_json(Path(args.plan), "plan")
    plan = payload.get("plan") if isinstance(payload, dict) else payload
    if not isinstance(plan, list):
        print(
            'FAIL: calendar_io: --plan must be a list or {"plan": [...]}',
            file=sys.stderr,
        )
        sys.exit(2)

    result = apply_plan(
        plan,
        api_key=api_key,
        scope=scope,
        calendar_id=args.calendar_id,
        timezone=args.timezone,
        live=args.live,
        visibility=args.visibility,
    )

    # The applied *result* is the record of what the calendar now holds — which
    # event ids were written, and which writes failed. Phase 3 keys its verdicts
    # to those ids, so this file is the audit's evidence and is written here
    # rather than re-derived from the plan (a plan says what we intended).
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for item in result["failed"]:
            print(f"FAIL: calendar_io: {item['game_key']}: {item['reason']}", file=sys.stderr)
        if result["dry_run"]:
            for item in result["actions"]:
                print(f"WOULD {item['action']:6s} {item['game_key']}")
        # Formatted positionally: `result` carries a `failed` list, so unpacking
        # it into .format() would collide with the count.
        print(
            "OK: calendar_io: dry_run={} created={} updated={} skipped={} "
            "failed={}".format(
                result["dry_run"],
                result["created"],
                result["updated"],
                result["skipped"],
                len(result["failed"]),
            )
        )

    if result["failed"]:
        sys.exit(1)
    if result["dry_run"] and not result["actions"]:
        print(
            "FAIL: calendar_io: the plan had nothing to create or update",
            file=sys.stderr,
        )
        sys.exit(1)
    if not result["dry_run"] and not (result["created"] or result["updated"]):
        print("FAIL: calendar_io: no writes were performed", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
