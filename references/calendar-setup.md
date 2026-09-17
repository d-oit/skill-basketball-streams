# Google Calendar Setup

## Target Calendar

Calendar configuration is centralized in `config/calendar.json`. The `BASKETBALL_CALENDAR_ID` environment variable can override the `calendarId` field for testing/CI purposes.

- **Calendar ID**: Configured in `config/calendar.json` (field: `calendarId`)
- **Purpose**: Free basketball live streams in Germany
- **Visibility**: Configured in `config/calendar.json` (field: `visibility`, default: `"public"`)
- **Timezone**: Configured in `config/calendar.json` (field: `timezone`, default: `"Europe/Berlin"`)

**Setup Instructions:** See `SETUP.md` for step-by-step guide on creating a Google Calendar and retrieving your Calendar ID.

## Event Schema

### Required Fields

```json
{
  "summary": "[League] [Team1] vs [Team2] - FREE Live Stream",
  "startTime": "2026-06-27T19:00:00+02:00",
  "endTime": "2026-06-27T21:30:00+02:00",
  "calendarId": "{{calendarId}}",  // From config/calendar.json or BASKETBALL_CALENDAR_ID env var
  "timeZone": "{{timezone}}"       // From config/calendar.json (default: Europe/Berlin)
}
```

**Note:** The `{{calendarId}}` and `{{timezone}}` placeholders should be replaced with actual values from `config/calendar.json` at runtime. The `scripts/calendar_config.py` module provides helper functions for this.

### Full Event Description Template

```
League: [League Name]
Teams: [Team1] vs [Team2]
Date/Time: [CET Date/Time]

FREE STREAM LINKS:
- [Source1]: [Direct Link1]
- [Source2]: [Direct Link2]

Access: [Free / Registration required / No login needed]

SOURCE REFERENCE:
- Found at: [Original search result URL]
- Validated: [ISO timestamp of validation]
- Validation Notes: [Any important notes]
```

**This block is written by `scripts/calendar_io.py`, not by hand.**
`description_for(row)` renders it from the plan row, and
`scripts/stream_links.py` owns the format in both directions, so the writer and
the reader cannot drift apart.

The link half reaches the row because `upsert_events._event_fields` carries it
from the candidate: `directLink` (or `directLinks`, a list of `{source, url}`),
`sourceReference`, `access`, `validationTimestamp` and `validationNotes`. Both the
camelCase spellings above and snake_case are accepted. Only the parts a row
actually has are rendered, so a row that carries no link block produces exactly
the `League:`/`Teams:` lines it always did.

Three things read it back, which is why it has to be written rather than
described: `parse_event` exposes `links`/`source_reference`/`validated_at` on
every stored event, `scripts/link_inventory.py` turns those into the `links.json`
that `scripts/link_check.py --input` consumes, and `references/self-learning.md`'s
revalidation loop needs the stored link in order to quarantine it. All of that was
specified here for months while nothing wrote a single one of the four link lines.

### Color Coding

Two **orthogonal** dimensions decide the colour: the league/event type, and the
verification state. `get_color_id(league, event_type, state=None)` combines them;
`scripts/verification.py` owns the state model.

**Dimension 1 — league / event type** (unchanged defaults):

| colorId | Color | Use |
|---------|-------|-----|
| `"6"` | Tangerine/Orange | Default for all basketball events (BBL, EuroLeague regular season, etc.) |
| `"11"` | Tomato/Red | EuroLeague finals, Basketball Champions League finals, special events |
| `"2"` | Sage/Green | FIBA international games (World Cup, EuroBasket, etc.) |

**Dimension 2 — verification state.** Precedence rule: a non-`VERIFIED` state
**wins** over the league colour.

| State | `colorId` | Colour | Title prefix | Meaning |
|---|---|---|---|---|
| `VERIFIED` | league colour (`6`/`11`/`2`) | — | *(none)* | Free access **and** a live stream confirmed |
| `UNVERIFIED` | `5` | Banana (amber = caution) | `[UNVERIFIED] ` | Live stream plausible, free access **not** confirmed |
| `WRONG` | `7` | Peacock | `[WRONG] ` | The audit proved it was paid, or never live |

An uncertain event **never** keeps the league colour. Losing a final's Tomato red
because colour `11` was "reserved" would hide exactly the uncertainty the state
exists to expose. `VERIFIED` keeps Tomato (`11`) for finals and Sage (`2`) for
FIBA once access is confirmed.

`WRONG` is set only by `scripts/audit_events.py` — never by a human edit, and
never by deleting an event. Prefixing is idempotent, so re-labelling the same
event twice cannot produce `[UNVERIFIED] [UNVERIFIED]`.

**Function reference:** `get_color_id(league, event_type, state=None)` where:
- `league`: The league name (e.g., "BBL", "EuroLeague", "FIBA")
- `event_type`: The event type (e.g., "regular", "final", "playoff"). Defaults to "regular" if omitted.
- `state`: Optional `"VERIFIED"`/`"UNVERIFIED"`/`"WRONG"`. Omit to preserve the pre-state behaviour (league colour only).

See `scripts/color_mapping.py` for the complete mapping table,
`scripts/verification.py` for the state machine, and `tests/test_color_mapping.py`
/ `tests/test_verification.py` for unit test coverage.

### Visibility

`visibility` is a real argument of both `GOOGLECALENDAR_CREATE_EVENT` and
`GOOGLECALENDAR_PATCH_EVENT`, with the enum `default` / `public` / `private` /
`confidential`, and it is now **sent**. The value comes from `config/calendar.json`
via `scripts/calendar_config.get_visibility()`, which is `scripts/calendar_io.py`'s
`--visibility` default, so setting the field in the config file is what decides it.

It was the one setting in that file that everything *read* and nothing *wrote*:
`get_visibility()` existed and had no caller, and this document and `SETUP.md` both
described events as public, so the promise rested on a reader nobody invoked. It is
passed on updates as well as creates, because `PATCH` replaces the fields it is
given — an update that omitted it would clear what a previous run set and let the
event fall back to the calendar's own default.

An unknown value is refused at the CLI (exit 2, naming the enum) rather than
forwarded, since the API would reject it with a message about *its* parameter
instead of the config file that supplied the typo.

This is an **event-level** property, not a substitute for the calendar's sharing
setting: an event marked `public` on a calendar that is not shared is still
unreachable. Both are needed for a reader to find the event.

### `defaultColorId` — REMOVED (2026-09-16)

Swept the repo for settings that are read but never written on 2026-09-15, and
`defaultColorId` was the one other instance of the defect `visibility` had:
`calendar_config.get_default_color_id()` existed and **no code path called it**,
while the same value was hardcoded as the literal `"6"` in four more places:
`upsert_events`, `audit_events`, `event_ledger`, and `color_mapping`'s fallback
rule. Measured, not inferred: with `config/calendar.json` set to
`"defaultColorId": "3"`, `get_default_color_id()` returned `3` and a plan row for a
league with no explicit mapping still carried `"6"`. **Changing the field changed
nothing.**

It was first left open as a documented gap, because wiring it looked like a product
decision: should the config *override* the league table, or only stand in for a
league the table does not know? It was then **deleted rather than wired**, because
that question rests on a false premise. The value is not a preference — it is
`verification.STATE_COLOR_IDS["VERIFIED"]`. The precedence rule (an uncertain state
wins over the league colour) holds only while the league default stays
distinguishable from `5` (UNVERIFIED) and `7` (WRONG), and `SETUP.md` was offering
the user all eleven colours, `"5"` and `"7"` among them. A setting that can render a
confirmed game as unconfirmed, or as proved-wrong, is not a knob to wire up — it is
a colour contract that can be made to lie.

What stands in its place: one definition, `verification.DEFAULT_LEAGUE_COLOR_ID`,
imported by all four call sites that used to carry their own literal, and
`verification.FORBIDDEN_LEAGUE_COLOR_IDS` — the colours the state machine and the
league-override rules already speak for (`2`, `5`, `7`, `11`) — which
`tests/test_color_mapping.py` asserts the default is never in, alongside a check
that fails if any module reintroduces a local copy.

## Duplicate Detection

**Dedupe before any write.** List the **whole window** in one call, then match in
memory *before* creating anything. `scripts/upsert_events.py` implements the
planner and prints `create`/`update`/`skip` rows without touching the calendar,
so the rule is testable offline.

### Check Parameters

```json
{
  "calendarId": "f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com",
  "startTime": "[today 00:00]",
  "endTime": "[today+7d 23:59]",
  "fullText": ""
}
```

### Duplicate Criteria

A duplicate exists only when **all three** agree:

- the slot overlaps within ±30 minutes, **and**
- the team pair matches — a short and a long club name are the same club
  (`ALBA` = `ALBA Berlin`, `Telekom Baskets Bonn` = `Bonn`), **and**
- the leagues are compatible (either name contains the other).

**Both** teams must match. On a double-header day two different games can share
one club, so a single overlapping name is not enough. Team order does not matter
(`A vs B` and `B vs A` are the same game).

**Note:** the `fullText` parameter should use team/league keywords
(e.g., `"{{team1}} {{team2}}"`) rather than a blank string to properly detect
duplicates. See Issue #14 in the repository.

### Upsert rules (replace-if-unverified)

| Existing event | Candidate | Action |
|---|---|---|
| nothing | any | **create** |
| `VERIFIED` | any | **skip** — a confirmed event is never touched, so re-runs are byte-identical |
| `UNVERIFIED` | `VERIFIED` | **update** — promote (colour `5` → `6`, drop the prefix) |
| `UNVERIFIED` | `UNVERIFIED` | **update** — refresh links and timestamp |
| `WRONG` | anything but `WRONG` | **skip** — an audit verdict outranks a fresh guess |

That last-but-one row is deliberate: a `WRONG` label records a conclusion that
the broadcast was not free or never live. Silently overwriting it with a new
unverified claim would erase the only record of that conclusion.

## Time Handling

### Timezone Rules

- Use timezone from `config/calendar.json` (field: `timezone`, default: `"Europe/Berlin"`)
- CEST (Central European Summer Time): UTC+2 (April–October)
- CET (Central European Time): UTC+1 (November–March)
- Convert all found times to ISO 8601 with offset: `2026-06-27T19:00:00+02:00`

### Game Duration Estimates

| Game type | Estimated duration |
|-----------|-------------------|
| Regular season BBL | 2.5 hours |
| EuroLeague | 2.5 hours |
| FIBA international | 2.5 hours |
| Playoff game | 3 hours |

## Tool Usage

Events are read and written through **Composio's Google Calendar toolkit**, not the
Calendar API directly. There is no Google Cloud project, no service account and no OAuth
token: a connected Google account holds the grant, and the caller presents
`COMPOSIO_API_KEY` plus `COMPOSIO_USER_ID` (or `COMPOSIO_CONNECTED_ACCOUNT_ID`).
`SETUP.md` has the two-secret setup.

Three quirks of this toolkit are load-bearing. Each was observed against a live account,
and none is visible in a successful run:

1. **`CREATE_EVENT` ignores `color_id`.** It is not in that tool's schema. A create
therefore produces the default colour and the intended one is applied by a follow-up
`PATCH_EVENT` — so a create with a colour is *two* calls, and a failed second call leaves
an event that exists in the wrong colour. That is reported as a failure even though the
event was created, because a wrong colour is a wrong label on a public calendar.
2. **`CREATE_EVENT` adds a Google Meet link and adds the connected user as an attendee**
unless `create_meeting_room: false` and `exclude_organizer: true` are sent. Both are
mandatory here for that reason.
3. **The two tools disagree on their own parameter names**: `start_datetime` /
`end_datetime` on CREATE, `start_time` / `end_time` on PATCH. An event addressed with the
other tool's pair is a field this one does not look for, so the mapping lives in exactly
one place — `create_arguments()` and `patch_arguments()` in `scripts/calendar_io.py` —
rather than being re-derived at each call site.
4. **`visibility` must be sent explicitly.** It is valid on both tools, and it is the
setting that decides whether a reader sees the event at all — see *Visibility* below.

### List Events (Duplicate Check)

```python
# scripts/calendar_io.py is the reference implementation of all three calls.
from scripts.calendar_io import list_events

# The window arrives with Google's own camelCase names, and the JSON types are
evidence: `singleEvents` is True, not the string "true" the direct API wanted.
events = list_events(
    api_key, scope, calendar_id,
    time_min=window_start,     # whole window in ONE call, not ±30 min per game
    time_max=window_end,
)
```

matching the tool call it makes:

```
GOOGLECALENDAR_EVENTS_LIST {
  calendarId, timeMin, timeMax, singleEvents: true, orderBy: "startTime",
  maxResults: 250, showDeleted: false, pageToken?
}
```

### Create Event

```python
from scripts.calendar_io import apply_plan

# build_event_body() derives summary (already carrying the state prefix), the
# start/end objects and the description; create_arguments() re-addresses them.
# Do not re-derive the summary or the colour here — the state machine owns both.
result = apply_plan(
    plan,
    api_key=api_key,
    scope=scope,
    calendar_id=calendar_id,
    timezone=timezone,
    live=True,
)
```

```
GOOGLECALENDAR_CREATE_EVENT {
  calendar_id, summary, description,
  start_datetime, end_datetime, timezone,
  create_meeting_room: false, exclude_organizer: true, send_updates: "none"
}
# then, only if the state machine assigned a colour:
GOOGLECALENDAR_PATCH_EVENT { calendar_id, event_id, color_id, send_updates: "none" }
```

The **event id is the return value that matters**. It is what `telemetry/events.jsonl` is
keyed by and the only way a Phase 3 verdict can be resolved back to an event, so a create
that returns no id is recorded as a failure rather than as a created event.

**Note:** config values come from `scripts/calendar_config.py`:
```python
from scripts.calendar_config import get_calendar_id, get_timezone, get_visibility
from scripts.color_mapping import get_color_id
from scripts.verification import title_for

calendar_id = get_calendar_id()
timezone = get_timezone()
visibility = get_visibility()
state = "UNVERIFIED"  # free access not confirmed
color_id = get_color_id(league, event_type, state)
summary = title_for(state, f"{league} {team1} vs {team2} - FREE Live Stream")
```
