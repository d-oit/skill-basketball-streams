---
name: basketball-streams
description: Use this skill to search, validate, and add FREE basketball live streams in Germany from official sources to Google Calendar. Triggers on requests for free basketball streams, live game schedules, or calendar updates for German basketball. Not for paid streams, highlights, replays, or non-basketball sports.
license: MIT
version: "1.0.0"
category: sports
allowed-tools: webSearch openUrl googleCalendarListEvents googleCalendarCreateEvent
---

# Basketball Streams Finder & Calendar Manager

> **CRITICAL WARNING**: Previous runs created events with broken YouTube links. This version enforces strict validation. **NEVER create a calendar event without:**
> 1. Testing every URL with `openUrl`
> 2. Reading page content to verify live stream exists
> 3. Including source reference in event description
> 4. Passing all 7 mandatory validation checks

## When to Use

- User requests free basketball live streams in Germany
- User wants to find official basketball broadcasts
- User asks to update basketball calendar with free streams
- User requests validation of basketball streaming sources

## Approved Sources (ONLY these)

1. **Dyn Sport Mix** (via Amazon Prime Video, Joyn, Pluto TV, Zattoo, ASTRA satellite)
2. **MagentaSport** (free EuroLeague game per matchday)
3. **Sportschau** (ARD)
4. **Regional broadcasters**: MDR, BR24, RBB24
5. **Club websites/social media**: ALBA BERLIN, BMA365 Bamberg Baskets, Basketball Löwen Braunschweig, EWE Baskets Oldenburg, FC Bayern München Basketball, FIT/One Würzburg Baskets, MHP RIESEN Ludwigsburg, MLP Academics Heidelberg, NINERS Chemnitz, RASTA Vechta, ratiopharm ulm, ROSTOCK SEAWOLVES, Science City Jena, SKYLINERS, SYNTAINICS MBC, Telekom Baskets Bonn, Veolia Towers Hamburg, VET-CONCEPT Gladiators Trier
6. **YouTube** (official channels of leagues, clubs, or federations)

## Core Workflow

### Step 1: Determine Date Range
- Today through next 7 days (inclusive)
- Use Europe/Berlin timezone (CET/CEST)
- Current date: Check with `new Date().toISOString()`

### Step 2: Search for Streams

Search queries (use `webSearch` with `limit: 20`):
```
- "Dyn Sport Mix Basketball live stream today"
- "MagentaSport free EuroLeague live stream"
- "Sportschau Basketball live ARD"
- "MDR Basketball live stream"
- "BR24 Basketball live"
- "RBB24 Basketball live"
- "ALBA Berlin live stream free"
- "FC Bayern München Basketball live free"
- "BBL live stream free Germany"
- "EuroLeague live YouTube official"
```

### Step 2.5: Extract and Validate Direct Streaming URLs

**CRITICAL — for every URL found:**
1. Extract the actual streaming URL from the search result
2. Validate with `openUrl` — must return accessible page (not 404)
3. **YouTube URL rules:**
   - ✅ PREFERRED: `youtube.com/@handle` or `youtube.com/@handle/live`
   - ✅ ACCEPTABLE: `youtube.com/user/TheDBBTV` only
   - ❌ REJECT: All other `/user/` URLs
   - ❌ REJECT: All `/channel/UC...` URLs
   - ❌ REJECT: `@FIBAWorld` — NOT a valid channel; only `@fiba` is official
4. Page must contain "Live" or "LIVE" text for YouTube URLs

### Step 3: Validate Each Stream (ALL 7 must pass)

| # | Check | Criteria |
|---|-------|----------|
| 1 | Free Access | No paywall, no subscription, no "Prime"/"Abo"/"DAZN"/"Sky" |
| 2 | Live Content | Is live/upcoming — not highlights/replay/zusammenfassung |
| 3 | Official Source | Domain matches approved sources list |
| 4 | Basketball-Specific | Mentions BBL, EuroLeague, or specific team names |
| 5 | Date/Time Range | Within today + 7 days, CET |
| 6 | Working Link | HTTP 200, no redirect to paywall |
| 7 | Direct Stream Verification | Page content confirms active video/live stream |

> If ANY check fails → **REJECT**. Never create a calendar event for an invalid stream.

See `references/validation-workflow.md` for full validation pseudocode and log template.

### Step 4: Extract Game Details

For each validated stream extract: League, Teams, Date/Time (CET), Direct Link, Access Requirements, Source Reference URL, Validation Timestamp.

> **CRITICAL RULE**: If you cannot extract a working direct streaming URL, DO NOT create the calendar event.

### Step 5: Check Calendar for Duplicates

Use `googleCalendarListEvents` on:
```
f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com
```
Search ±30 min window around event start time. Skip if duplicate found (same teams + date).

### Step 6: Create Calendar Event

Use `googleCalendarCreateEvent`:
- `summary`: `"[League] [Team1] vs [Team2] - FREE Live Stream"`
- `startTime` / `endTime`: ISO 8601, `Europe/Berlin`, ~2.5h duration
- `colorId`: `"6"` (Tangerine/Orange)
- `visibility`: `"public"`
- `description`: Include league, teams, free stream links, source reference, and validation timestamp

### Step 7: Output Results

```markdown
## Validated Basketball Streams
| Status | League | Teams | Date/Time (CET) | Direct Link | Calendar Event |
|--------|--------|-------|----------------|-------------|----------------|
| created | BBL | ALBA Berlin vs Bayern | 2026-06-20 19:30 | [link] | [event link] |
| skipped | EuroLeague | Real Madrid vs Barcelona | 2026-06-21 20:00 | [link] | Already exists |
```

## Implementation Notes

- All times in Europe/Berlin (CET/CEST)
- Team name variants: ALBA Berlin / Alba / ALBA; FC Bayern München / Bayern Munich / Bayern
- League keywords: BBL, EuroLeague, Basketball Champions League, FIBA, ProA, ProB
- Exclusion keywords: kostenpflichtig, Abo, Sky, DAZN, Pay-per-view, highlights, replay, zusammenfassung, wiederholung
- Exclude other sports: Fußball, Handball, Eishockey, Tennis
- If a source website is down, log and continue
- If Google Calendar API fails, retry once then log error
- If no streams found: return "No free basketball live streams found in the next 7 days from official sources."

## Rationalizations

| Rationalization | Reality |
|-----------------|---------|
| "The URL looks valid, I don't need to test it" | Broken URLs (e.g. `/user/FIBA`) have caused calendar spam before. Every URL must be tested with `openUrl`. |
| "I'll add the source reference later" | Without a source reference, validation cannot be audited. Add it in the same step as creation. |
| "This stream is probably free" | Guess work creates paid-content events. All 7 checks must pass explicitly. |
| "I'll skip the duplicate check to save time" | Duplicate events clutter the calendar and break user trust. Always check. |

## Red Flags

- [ ] Creating a calendar event without testing the direct streaming URL with `openUrl`
- [ ] Using `/user/` YouTube URLs other than `/user/TheDBBTV`
- [ ] Using `@FIBAWorld` — only `@fiba` is the official FIBA channel
- [ ] Creating an event when page content could not be read to verify live stream
- [ ] Missing source reference or validation timestamp in event description
- [ ] Skipping the duplicate check step

## References

- `references/approved-sources.md` — Full list of approved domains and channels
- `references/validation-workflow.md` — Detailed pseudocode, log template, and examples
- `references/calendar-setup.md` — Calendar ID and event field reference
- `evals/evals.json` — Test cases for skill evaluation
