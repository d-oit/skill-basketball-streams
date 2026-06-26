---
name: skill-basketball-streams
description: Search for FREE basketball live streams in Germany from approved official sources, validate them with a 7-check pipeline (URL, page content, source allow-list, basketball-specific, date range, live proof, source reference), and add confirmed streams to a Google Calendar. Use this skill when the user requests free basketball live streams in Germany, asks to find official basketball broadcasts, wants to update a basketball calendar with free streams, asks to validate basketball streaming sources, or reports broken YouTube links in past basketball calendar events. Triggers: "find basketball streams", "free BBL stream", "any free EuroLeague game today?", "validate this basketball URL", "add stream to basketball calendar". Not for paid broadcaster streams (Sky/DAZN/Prime), highlight reels, or non-basketball sports.
version: "1.1.0"
category: workflow
license: MIT
allowed-tools: webSearch openUrl webFetch googleCalendarListEvents googleCalendarCreateEvent
compatibility: Requires internet access (webSearch + openUrl HTTP probe) and Google Calendar API access (listEvents + createEvent). Timezone: Europe/Berlin.
---

# Basketball Streams Finder & Calendar Manager

> **CRITICAL**: A previous incident created calendar events with broken YouTube links. Every event MUST pass all 7 checks — URL tested with `openUrl`, page content read, live stream verified, source reference recorded. See `references/validation-workflow.md` for the canonical 7-check pipeline and `references/lessons-learned.md` for the incident post-mortem.

## Purpose

Search for FREE basketball live streams in Germany from official sources, validate them, and add confirmed streams to Google Calendar `f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com`.

## When to Use

- User requests **free** basketball live streams in Germany
- User wants to find official basketball broadcasts (BBL / EuroLeague / FIBA / club channels)
- User asks to update the basketball calendar with free streams
- User asks to validate a candidate basketball streaming URL
- User reports broken YouTube links in a previous run

## Approved Sources

Full list and YouTube URL rules: `references/approved-sources.md`. Summary: Dyn Sport Mix (Joyn / Pluto TV / Zattoo free tier only), MagentaSport (one free EuroLeague game per matchday), Sportschau / ARD, regional broadcasters (MDR, BR24, RBB24), official BBL club websites, and official YouTube channels only. **Accepted YouTube URL patterns:** `youtube.com/@handle`, `youtube.com/@handle/live`, `youtube.com/user/TheDBBTV`. **Rejected:** `youtube.com/@FIBAWorld`, any `youtube.com/user/[*]` except `TheDBBTV`, any `youtube.com/channel/UC…`.

## Process

### Step 1 — Determine Date Range
Today (00:00 `Europe/Berlin`) through today + 7 days (23:59 `Europe/Berlin`). Convert times to ISO 8601 with `+02:00` (CEST) or `+01:00` (CET).

### Step 2 — Search for Streams
Run `webSearch` against each approved source with `limit: 20`, focusing on official domains. Canonical query list in `references/implementation-notes.md`.

### Step 2.5 — Validate Direct URLs
For every result, call `validateStreamUrl(url)` (TypeScript in `references/implementation-notes.md`). It rejects `@FIBAWorld`, every `/user/[name]` except `TheDBBTV`, every `/channel/UC…`, and any URL that fails an HTTP probe via `openUrl`. **Rejected URLs never reach Step 3.**

### Step 3 — Run the 7-Check Pipeline
Full definitions in `references/validation-workflow.md`. **ANY failure → REJECT**, no calendar event:

1. **Free Access** — no `Abo`, `kostenpflichtig`, `pay-per-view`, or subscription
2. **Live Content** — page contains `live`/`Live`/`LIVE`; no `highlights`/`replay`/`zusammenfassung`/`on demand`
3. **Official Source** — domain in approved list
4. **Basketball-Specific** — mentions BBL, EuroLeague, FIBA, or approved club name
5. **Date/Time Within Range** — today…today+7d, 14:00–23:00 CET
6. **Working Link** — `openUrl` returns HTTP 200 (not 404 / 500 / paywall redirect)
7. **Direct Stream Verification** — page content contains active video player and / or "Live" badge text

### Step 4 — Extract Game Details
For each surviving stream: league, teams, ISO 8601 date/time in CET, direct link, access requirements, `sourceReference` URL, validation timestamp.

### Step 5 — Check Google Calendar for Duplicates
Call `googleCalendarListEvents` on `f8a14c...@group.calendar.google.com` with `startTime = eventStart - 30 min`, `endTime = eventStart + 30 min`, `fullText = "<team1> <team2> <league>"`. Duplicate if any of: same ±30-min slot, identical team pair, or title contains same BBL/EuroLeague/team substring.

### Step 6 — Create Event (Only If No Duplicate AND All 7 Checks Passed)
`googleCalendarCreateEvent` with required fields (`summary`, `startTime`, `endTime`, `calendarId`, `timeZone="Europe/Berlin"`), a `description` containing League / Teams / Date/Time / FREE STREAM LINKS / Access / SOURCE REFERENCE-with-ISO-timestamp / Validation Notes, `colorId: "6"` (Tangerine/Orange; `"11"` Red for EuroLeague finals, `"2"` Green for FIBA internationals), `visibility: "public"`. Default duration 2.5h regular season / 3h playoff. See `references/calendar-setup.md`.

### Step 7 — Output Results
Markdown table with columns `Status | League | Teams | Date/Time (CET) | Direct Link | Calendar Event`. Row formats: `created | BBL | ALBA Berlin vs Bayern | 2026-06-20 19:30 | [link] | [event link]` and `skipped | EuroLeague | Real Madrid vs Barcelona | 2026-06-21 20:00 | [link] | Event already exists: abc123`.

## Important Constraints

1. **Only approved sources** — no exceptions, even if a search result looks official.
2. **All 7 checks must PASS** — including Direct Stream Verification (page read).
3. **No paid content** — Sky / DAZN / general Prime → REJECT, even if domain is on the allow-list.
4. **No unofficial streams** — no pirate sites, third-party aggregators, fan-run YouTube.
5. **Date accuracy** — every event must fall in today…today+7d.
6. **Link testing** — every direct link tested with `openUrl` and confirmed working.
7. **Live verification** — read page content to confirm an active live stream exists.
8. **YouTube URL rules** — only `@handle`, `@handle/live`, and `/user/TheDBBTV` are accepted.
9. **Include references** — every event description must contain `sourceReference` and validation timestamp.
10. **No event without a live stream** — unverified → no event.

## Output Format

Always emit the Step 7 table. On total failure return `"Found [N] potential streams, but none passed validation."`. On zero hits: `"No free basketball live streams found in the next 7 days from official sources."`. On tool errors: log and continue with the next source.

## Available scripts

- **`scripts/validate.py`** — self-contained validator (stdlib only). Run from the skill root to confirm the project passes all three checks: `python3 scripts/validate.py --root .`. Supports `--check {all,evals,skill,references}` to limit to a single check. Exit code `0` = PASS, `1` = FAIL, `2` = USAGE. Wire this into CI so a failing validation never lands in the basketball calendar.

## Rationalizations

| Excuse | Why it's wrong |
|---|---|
| "The URL came from a search result so it's safe" | Even official searches return `/user/FIBA` and `/channel/UC…` 404s; every URL must clear `validateStreamUrl` first. |
| "Domain matches the allow-list, no need to test" | Allow-list match can't detect a paywall inside the page; `openUrl` + page read is required. |
| "Page returned HTTP 200, so it's live" | A 200 can be a channel homepage with no active stream; Check 7 requires reading the body. |
| "Highlights are 'live content' enough" | Replays and highlight reels explicitly FAIL Check 2 (`highlights`, `replay`, `zusammenfassung`). |
| "The user can filter Sky / DAZN themselves" | Rejecting paid broadcasters is the skill's contract; don't pass the decision upstream. |

## Red Flags

- [ ] Skipped `validateStreamUrl` before promoting a search result to Step 3.
- [ ] Accepted any `/user/` YouTube URL other than `/user/TheDBBTV`.
- [ ] Accepted `youtube.com/@FIBAWorld/...` or any `/channel/UC…` URL.
- [ ] Skipped Check 7 (Direct Stream Verification) because the URL "looks live".
- [ ] Created an event without a `sourceReference` and validation timestamp in the description.
- [ ] Used a date outside the today…today+7d window.
- [ ] Added an event without running `googleCalendarListEvents` for duplicates first.
- [ ] Logged a `CREATE` decision when any of the 7 checks were FAIL.

## References

- `references/approved-sources.md` — full approved source list and YouTube URL allow/reject table
- `references/validation-workflow.md` — 7-check pipeline, decision logic, special cases (MagentaSport 1-game rule, Dyn Sport Mix free tier, YouTube live-URL-only, duplicate handling)
- `references/calendar-setup.md` — Google Calendar event schema, color codes, duplicate detection parameters, time-handling rules
- `references/implementation-notes.md` — query templates, time-handling, team-name variants, exclusion vocabulary, full `validateStreamUrl` implementation and end-to-end workflow TypeScript
- `references/lessons-learned.md` — incident post-mortem (broken `/user/FIBA` events), root causes, prevention checklist, validation log template and worked examples
- `evals/evals.json` — 12 eval cases (driving the `skill-evaluator` rubric; see `README.md` → Self-Validation)
