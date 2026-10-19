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
import time
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
    from verification import (
        STATE_PREFIXES,
        STATE_VERIFIED,
        state_from_title,
        title_for,
    )
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
        title_for,
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
TOOL_DELETE_EVENT = "GOOGLECALENDAR_DELETE_EVENT"
TIMEOUT_SECONDS = 30
DEFAULT_TIMEZONE = "Europe/Berlin"
# A retry is only safe when it cannot create a second event. `CREATE_EVENT` is
# therefore retried on 429 (the request was refused before it reached Google) but
# never on a 5xx or a dropped connection: those may have created the event, and a
# retry would produce a duplicate on a public calendar. READs, PATCHes and
# DELETEs are idempotent, so they may be retried on the gateway family too.
RETRYABLE_HTTP_STATUS = (429, 500, 502, 503, 504)
IDEMPOTENT_TOOLS = (
    "GOOGLECALENDAR_EVENTS_LIST",
    "GOOGLECALENDAR_EVENTS_GET",
    "GOOGLECALENDAR_PATCH_EVENT",
    "GOOGLECALENDAR_DELETE_EVENT",
)
MAX_RETRIES = 2
RETRY_BASE_SECONDS = 0.5
# `VISIBILITY_VALUES` comes from `calendar_config.py`, which owns the field: the
# enum `CREATE_EVENT`/`PATCH_EVENT` accept, recorded from the live tool schema.
# Validated rather than forwarded, because an unaccepted value is rejected by
# Google and the rejection names the API's parameter rather than the config file
# that supplied the typo.
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_SKIP = "skip"
# A fourth planner outcome, and the only one that is a *non-write* for a matched
# event: `update` when any field the calendar holds would change, `unchanged`
# when the stored event already carries exactly what a PATCH would send. It
# exists because the old planner re-PATCHed every unverified match on every run
# — a real HTTP write and an `action: "update"` ledger row claiming a change —
# even when nothing about the game had changed. See `upsert_events.plan_upsert`.
ACTION_UNCHANGED = "unchanged"
# Removes a stored event. Only `apply_plan` executes it; the planner does not yet
# emit it, so nothing reaches a public calendar as a delete until a policy row
# asks for one.
ACTION_DELETE = "delete"


class CalendarError(RuntimeError):
    """Raised when the API rejects a request, carrying the status and body.

    `slug`, `attempts` and `request_id` are for the log, not for control flow:
    a failed create must be diagnosable from `applied.json` alone, without the
    operator having to guess which tool or how many tries produced the row.
    """

    def __init__(
        self,
        status: int,
        message: str,
        *,
        slug: str = "",
        attempts: int = 1,
        request_id: str = "",
    ) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.slug = slug
        self.attempts = attempts
        self.request_id = request_id


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


def _request_id(detail: str) -> str:
    """Composio's own handle for a failed call, when the body carries one."""
    try:
        payload = json.loads(detail)
    except (TypeError, ValueError):
        return ""
    if isinstance(payload, dict):
        return str(payload.get("log_id") or payload.get("request_id") or "")
    return ""


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Honour `Retry-After`, otherwise exponential backoff."""
    try:
        value = str((exc.headers or {}).get("Retry-After") or "").strip()
        if value:
            return max(0.0, float(value))
    except (AttributeError, TypeError, ValueError):
        pass
    return RETRY_BASE_SECONDS * (2**attempt)


def _execute_tool(
    slug: str,
    arguments: dict,
    *,
    api_key: str,
    scope: dict,
    timeout: float = TIMEOUT_SECONDS,
) -> dict:
    """One Composio tool execution, with a retry policy that cannot duplicate.

    Raises `CalendarError` for a transport failure **and** for a tool that
    reported `successful: false` — a 200 carrying a refusal is the failure mode
    that would otherwise be read as an empty result, and an empty result is
    indistinguishable from "no events today".

    Retries exist because a single 429 or gateway 5xx used to fail the whole
    row. They are bounded and **capability-aware**: a create is retried only on
    429 (refused before Google saw it), while reads, patches and deletes — which
    are idempotent — may also be retried on 5xx and dropped connections. Retrying
    a create on an ambiguous failure is how a public calendar gets two copies of
    the same game.
    """
    body = {**scope, "arguments": arguments, "version": COMPOSIO_TOOLKIT_VERSION}
    last_error: CalendarError | None = None
    for attempt in range(MAX_RETRIES + 1):
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
            # `exc.read()` is `bytes` for a real HTTP response, but a *synthetic*
            # `HTTPError` — one constructed with `fp=None`, which the test suite
            # raises and some stdlib builds back with a `StringIO` — returns `str`.
            # `.decode` on that is an `AttributeError` that replaces the calendar
            # error with a crash, which is the failure the caller can least act on.
            raw = exc.read() if exc.fp else b""
            detail = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            detail = detail or str(exc.reason or "")
            retryable = exc.code in RETRYABLE_HTTP_STATUS and (
                exc.code == 429 or slug in IDEMPOTENT_TOOLS
            )
            last_error = CalendarError(
                exc.code,
                detail[:500] or exc.reason,
                slug=slug,
                attempts=attempt + 1,
                request_id=_request_id(detail),
            )
            if retryable and attempt < MAX_RETRIES:
                time.sleep(_retry_delay(exc, attempt))
                continue
            raise last_error from exc
        except (urllib.error.URLError, OSError) as exc:
            # A dropped connection on a create is ambiguous: the event may exist.
            # Only idempotent tools may repeat it.
            last_error = CalendarError(
                0, f"network error: {exc}", slug=slug, attempts=attempt + 1
            )
            if slug in IDEMPOTENT_TOOLS and attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_SECONDS * (2**attempt))
                continue
            raise last_error from exc
        except ValueError as exc:
            # A malformed URL is a programming error, not a transient one.
            raise CalendarError(
                0, f"network error: {exc}", slug=slug, attempts=attempt + 1
            ) from exc
        break
    else:  # pragma: no cover - the loop always returns or raises
        raise last_error or CalendarError(0, "request exhausted", slug=slug)
    if not payload.strip():
        return {}
    try:
        envelope = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CalendarError(
            0, f"non-JSON response: {exc}", slug=slug, attempts=attempt + 1
        ) from exc
    if isinstance(envelope, dict) and envelope.get("successful") is False:
        # `log_id` is Composio's own handle for the call, so a failure that
        # needs their support does not require guessing which request it was.
        request_id = str(envelope.get("log_id") or "")
        raise CalendarError(
            0,
            f"{slug} reported failure: {envelope.get('error') or 'no error message'} "
            f"(log_id={request_id or 'n/a'})",
            slug=slug,
            attempts=attempt + 1,
            request_id=request_id,
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
    properties = raw.get("extendedProperties") or raw.get("extended_properties") or {}
    private = properties.get("private") if isinstance(properties, dict) else {}
    stored_key = private.get("game_key") if isinstance(private, dict) else ""
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
        # The exact identity written into private extended properties. Old
        # events have none, so this is additive and `events_match` falls back to
        # the slot/teams rule for them.
        "game_key": str(stored_key or raw.get("game_key") or ""),
        "transparency": str(raw.get("transparency") or "opaque"),
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

    Two fields make the stored entry more useful than its text alone. The plan
    row's `game_key` goes into private extended properties, so the next run can
    match the event exactly instead of relying on the ±30-minute slot and the
    two sources' club spellings. `transparency: transparent` keeps a public
    stream listing from making every subscriber look busy; it is an appointment,
    not a meeting.
    """
    start = str(row.get("start") or "")
    end = str(row.get("end") or "")
    if not end and start:
        moment = _parse_dt(start)
        if moment is not None:
            end = (moment + timedelta(hours=default_duration_hours)).isoformat()
    game_key = str(row.get("game_key") or "").strip()
    body: dict = {
        "summary": str(row.get("title") or ""),
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
        "colorId": str(row.get("color_id") or ""),
        "description": description,
        "visibility": visibility,
        # A stream listing is an appointment, not a meeting: marking it
        # transparent keeps a public subscription calendar from making every
        # subscriber look busy. CREATE and PATCH both accept the field.
        "transparency": "transparent",
    }
    if game_key:
        # The identity the description only approximates. A later run can match
        # this exactly instead of relying on the ±30-minute slot and the way two
        # sources spell a club. It is private metadata, not shown to subscribers.
        body["extendedProperties"] = {"private": {"game_key": game_key}}
    return body


def _same_instant(left: object, right: object) -> bool:
    """Whether two date-times name the same moment.

    Compared as *instants*, not as strings. The planner takes the stored event's
    own start on an update, but Google may echo a `dateTime` with an offset the
    source did not spell (`…T19:00:00+02:00` vs `…T17:00:00Z`), and a byte
    comparison would then report a change on every run — re-introducing exactly
    the pointless PATCH this comparison exists to remove.

    A blank on either side is never a match. A stored event with no usable time
    is a legacy or half-written one, and it needs the update that fills it in;
    treating "missing" as "equal to anything" is the trap `events_match` already
    documents for the teams line.
    """
    left_text, right_text = str(left or ""), str(right or "")
    if not left_text or not right_text:
        return False
    left_dt, right_dt = _parse_dt(left_text), _parse_dt(right_text)
    if left_dt is None or right_dt is None:
        return left_text == right_text
    return left_dt == right_dt


def body_matches_event(body: dict, stored: dict) -> bool:
    """Whether PATCHing `stored` with `body` would change the event at all.

    The planner asks this before emitting an `update`, so a quiet day sends
    nothing instead of re-writing every unverified event. It compares the **full
    payload the calendar would end up holding** — the same `body` `apply_plan`
    sends — against what the stored (already-`parse_event`-ed) event holds:

    * **summary** — normalised through `title_for` with the state read off the
      planned title, so the comparison is about the *game's name* rather than the
      exact prefix characters. A promotion changes the state, so the planned
      summary carries a different prefix and this differs (and so does the
      colour), which is what makes a promotion an `update`.
    * **start**/**end** — as instants, via `_same_instant`.
    * **description** — compared verbatim, because `description_for` is
      deterministic from the row and the round trip is exact. A stored event with
      an empty description simply is not equal to the non-empty one we would
      write, so it fails here and gets the update that fills it in. Blankness is
      never a wildcard.
    * **colour** — `colorId` as `parse_event` records it (`league_color_id`).

    `visibility` is deliberately not compared: `parse_event` does not read it
    back, so the stored side has no field to compare against, and inventing one
    here would be a rule reading a field nothing writes.
    """
    if not isinstance(body, dict) or not isinstance(stored, dict):
        return False

    planned_summary = str(body.get("summary") or "")
    # The state is read off the planned title the planner already built, so this
    # needs no second state argument and cannot drift from `title_for`.
    if title_for(state_from_title(planned_summary), str(stored.get("summary") or "")) != planned_summary:
        return False

    if not _same_instant(_day_of(body.get("start")), stored.get("start")):
        return False
    if not _same_instant(_day_of(body.get("end")), stored.get("end")):
        return False

    if str(body.get("description") or "") != str(stored.get("description") or ""):
        return False

    stored_colour = stored.get("league_color_id")
    if stored_colour is None:
        stored_colour = stored.get("colorId")
    if str(body.get("colorId") or "") != str(stored_colour or ""):
        return False
    return True


def _metadata_arguments(body: dict) -> dict:
    """The write arguments that carry identity and busy-state, when present.

    Kept in one place because CREATE and PATCH use different names for almost
    everything else but agree on these two (`extended_properties` and
    `transparency`). Omitting an empty metadata block on PATCH is deliberate:
    PATCH replaces the fields it is given, so sending `{}` would erase an
    identity a previous run wrote.
    """
    arguments: dict = {}
    properties = body.get("extendedProperties")
    private = properties.get("private") if isinstance(properties, dict) else None
    if isinstance(private, dict):
        game_key = str(private.get("game_key") or "").strip()
        if game_key:
            arguments["extended_properties"] = {"private": {"game_key": game_key}}
    transparency = str(body.get("transparency") or "")
    if transparency:
        arguments["transparency"] = transparency
    return arguments


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
    arguments.update(_metadata_arguments(body))
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
    arguments.update(_metadata_arguments(body))
    visibility = str(body.get("visibility") or "")
    if visibility:
        arguments["visibility"] = visibility
    return arguments


def _apply_delete(
    row: dict,
    result: dict,
    *,
    live: bool,
    calendar_id: str,
    api_key: str,
    scope: dict,
) -> None:
    """Delete one event. Dry-run records the intent and sends nothing.

    The event id is required: a delete without one would have no target, and
    refusing it is the only safe reading of that row.
    """
    game_key = str(row.get("game_key") or "")
    event_id = str(row.get("event_id") or "")
    if not event_id:
        result["failed"].append({"game_key": game_key, "reason": "delete row without an event_id"})
        return
    if not live:
        result["actions"].append(
            {"action": ACTION_DELETE, "game_key": game_key, "event_id": event_id, "body": {}}
        )
        result["deleted"] += 1
        return
    try:
        _execute_tool(
            TOOL_DELETE_EVENT,
            {"calendar_id": calendar_id, "event_id": event_id, "send_updates": "none"},
            api_key=api_key,
            scope=scope,
        )
    except CalendarError as exc:
        result["failed"].append(
            {
                "game_key": game_key,
                "reason": str(exc),
                "status": exc.status,
                "tool": exc.slug,
                "attempts": exc.attempts,
                "request_id": exc.request_id,
            }
        )
        return
    result["deleted"] += 1
    result["actions"].append(
        {"action": ACTION_DELETE, "game_key": game_key, "event_id": event_id, "body": {}}
    )


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
        "deleted": 0,
        # Additive: `.tmp/applied.json` is read by `event_ledger.py` and by
        # `candidates.py record --applied` on the real path, so nothing existing
        # is renamed or removed. An `unchanged` row sends **nothing**, exactly
        # like a skip, but it is a different fact — the event is on the calendar
        # and already carries exactly what we would have sent — so it gets its
        # own counter rather than being folded into `skipped`.
        "unchanged": 0,
        "skipped": 0,
        "failed": [],
        "actions": [],
    }
    for row in plan or []:
        if not isinstance(row, dict):
            continue
        action = str(row.get("action") or "")
        # Both of these send nothing and appear nowhere in `actions`. A skip is
        # "do not touch this at all"; an unchanged row is "a PATCH would change
        # nothing", which is only ever true because the planner compared the full
        # payload against the stored event through `body_matches_event`.
        if action in (ACTION_SKIP, ACTION_UNCHANGED):
            result["skipped" if action == ACTION_SKIP else "unchanged"] += 1
            continue
        if action == ACTION_DELETE:
            _apply_delete(row, result, live=live, calendar_id=calendar_id, api_key=api_key, scope=scope)
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
                                "tool": exc.slug,
                                "attempts": exc.attempts,
                                "request_id": exc.request_id,
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
                    "tool": exc.slug,
                    "attempts": exc.attempts,
                    "request_id": exc.request_id,
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
    # `upsert_events.py --json` emits `{"plan": [...], "summary": {...}}`. The
    # summary is the producer's own statement that it ran and counted; a bare
    # `{"plan": []}` has no such statement and is still treated as a fault below.
    planned_empty = isinstance(payload, dict) and isinstance(payload.get("summary"), dict)
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
            "OK: calendar_io: dry_run={} created={} updated={} unchanged={} "
            "skipped={} failed={}".format(
                result["dry_run"],
                result["created"],
                result["updated"],
                result["unchanged"],
                result["skipped"],
                len(result["failed"]),
            )
        )

    if result["failed"]:
        sys.exit(1)
    # **A day with nothing to write is a result, not a fault.** This used to
    # exit 1, and the first complete dry-run hit it: the agent found two real
    # BBL games, both already on the calendar as `VERIFIED`, so the planner
    # correctly emitted two skips and the step went red. A night with no free
    # game is the *most common* legitimate outcome, so a workflow that reds on it
    # is a workflow whose red is not information — and "no games tonight" reads
    # exactly like "the planner never ran", which is the fault this must still
    # catch.
    #
    # So the two are now told apart by **what the plan said**, not by whether
    # anything was written. A plan the planner emitted — even one whose every row
    # is a skip or an `unchanged` — is a decision, and it exits 0 with the reason
    # on stderr. A plan that is *empty* means the planner produced no rows at all,
    # which is a different thing: nothing decided, so nothing was reported.
    decided = (
        result["created"] + result["updated"] + result["unchanged"]
        + result["skipped"]
    )
    if not decided:
        if planned_empty:
            # A quiet day: the planner ran, counted zero candidates, and wrote
            # that count into the same document. Failing here is what reddened
            # the 2026-10-13 run after the model emitted the explicit empty
            # block the contract asks for. A plan file without a summary has no
            # such evidence and keeps the fault below.
            print(
                "OK: calendar_io: the planner produced an empty plan — no "
                "candidates this run, so there is nothing to write",
                file=sys.stderr,
            )
            return
        print(
            "FAIL: calendar_io: the plan contained no rows at all — the planner "
            "produced nothing, which is not the same as 'nothing to write'",
            file=sys.stderr,
        )
        sys.exit(1)
    if not (result["created"] or result["updated"]):
        # **Uniform across both modes, and the asymmetry was a defect.** This
        # branch previously exited 1 for a live run and 0 for a dry one, on the
        # reasoning that "writes were permitted and none happened, so a skip
        # reason is not an explanation". The first live dispatch (2026-09-29)
        # disproved that: the plan was two skips, and the skip reason *was* the
        # explanation — every planned game was already on the calendar as
        # VERIFIED. Same fact, same night, opposite exit code, which is the
        # definition of a red that carries no information.
        #
        # Enumerating every reachable tuple shows what the live branch actually
        # detected: with `failed` already handled above, it is reachable **only**
        # when the plan is all-skips. So it never caught a write problem - it
        # re-reported a dedupe decision, and without the `game_key` that
        # `result["failed"]` carries.
        #
        # The real live fault is a create/update row whose *write* was rejected,
        # and that already exits 1 with a name and a reason per event. This exit
        # code still has two jobs - an empty plan is a fault, and a rejected
        # write is a fault - and it no longer has a third that means "already
        # up to date".
        print(
            "OK: calendar_io: nothing to create or update — every planned game is "
            "already on the calendar, so this is a result rather than a failure",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
