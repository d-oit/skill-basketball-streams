# Validation Workflow

Detailed step-by-step validation process for every potential stream.

## Overview

Every stream found must pass **all 7 checks** before a calendar event is created. Failing any single check results in immediate rejection.

## Check 1: Free Access

**Purpose**: Ensure no payment is required.

**Pass criteria**:
- Page or description explicitly says "free", "kostenlos", "gratis"
- No mention of paywall, subscription, or payment
- No keywords: "Abo", "kostenpflichtig", "pay-per-view", "subscription"

**Fail criteria**:
- Mentions "Sky", "DAZN", "Prime" (unless Dyn Sport Mix free tier)
- Requires registration/payment before viewing
- Contains pricing information

## Check 2: Live Content

**Purpose**: Ensure it is a live stream, not a replay or highlight reel.

**Pass criteria**:
- Contains: "live", "Live", "LIVE", "live stream", "live übertragung"
- For YouTube: Page must contain "Live" or "LIVE" badge text
- Scheduled future event with live stream planned

**Fail criteria**:
- Contains: "highlights", "replay", "zusammenfassung", "on demand", "wiederholung"
- Past event without live component
- YouTube channel page with no active/scheduled live stream

## Check 3: Official Source

**Purpose**: Ensure the stream is from an approved official source.

**Pass criteria**:
- URL domain is in the approved sources list (see `references/approved-sources.md`)
- For YouTube: Channel is an official league, club, or federation channel

**Fail criteria**:
- Domain not in approved list
- `@FIBAWorld` — NOT a valid YouTube channel (only `@fiba` is official)
- Third-party aggregator or pirate site
- Unofficial fan channel

## Check 4: Basketball-Specific

**Purpose**: Prevent non-basketball content from being added.

**Pass criteria**:
- Mentions: basketball, BBL, EuroLeague, Basketball Champions League, FIBA
- Mentions specific BBL or EuroLeague team names

**Fail criteria**:
- Other sports: Fußball, Handball, Eishockey, Tennis, Volleyball
- Ambiguous content that could be another sport

## Check 5: Date/Time Within Range

**Purpose**: Only add streams within the next 7 days.

**Pass criteria**:
- Event date is between today (00:00 CET) and today + 7 days (23:59 CET)
- Kickoff time is reasonable for live sports (typically 14:00–23:00 CET)

**Fail criteria**:
- Event is in the past
- Event is more than 7 days in the future
- Date/time cannot be determined

## Check 6: Working Link

**Purpose**: Verify the URL is accessible.

**Pass criteria**:
- `openUrl` returns HTTP 200
- Page loads without errors
- Not redirected to a 404, 500, or paywall page

**Fail criteria**:
- HTTP 404, 500, or other error
- URL redirects to a subscription/paywall page
- For YouTube: `/user/` URLs (except `TheDBBTV`), `/channel/` URLs — always rejected

## Check 7: Direct Stream Verification (CRITICAL)

**Purpose**: Confirm a live stream actually exists on the page.

**Pass criteria**:
- Page content contains active video player
- YouTube pages contain "Live" or "LIVE" text
- For `/live` URLs: page shows live or upcoming live stream
- For approved channels: scheduled live stream is listed

**Fail criteria**:
- Page is just a channel homepage with no live content
- No video player detected
- Only archived/past content visible
- Cannot read page content

## Decision Logic

```
Checks 1–7: ALL must PASS → Decision: CREATE calendar event
Any check FAILS:          → Decision: SKIP
                          → Log reason for rejection
                          → Continue to next stream
```

## Validation Log Format

```
Stream URL: [url]
Source: [where found]
Timestamp: [ISO timestamp]
Check 1 - Free Access: [PASS/FAIL] - [reason]
Check 2 - Live Content: [PASS/FAIL] - [reason]
Check 3 - Official Source: [PASS/FAIL] - [reason]
Check 4 - Basketball-Specific: [PASS/FAIL] - [reason]
Check 5 - Date/Time Range: [PASS/FAIL] - [reason]
Check 6 - Working Link: [PASS/FAIL] - [reason]
Check 7 - Direct Stream Verification: [PASS/FAIL] - [reason]
Final Decision: [CREATE/SKIP]
Calendar Event ID: [id or N/A]
```

## Special Cases

### MagentaSport/MagentaTV Special Case

> **IMPORTANT:** MagentaSport (`magentasport.de`) and MagentaTV (`magenta.tv`) are two separate domains with distinct roles. Handling them correctly is mandatory.

**Domain Separation:**
- `magentasport.de` = **Content/announcement provider** — where free games are announced, schedules are published, and "kostenlos für alle" confirmations appear.
- `magenta.tv` = **Streaming platform** — where the actual live streams play. URLs follow the pattern `magenta.tv/tv/live-[game-slug]/[dynamic-id]` and are often **not indexed** by search engines.

**Free Stream Policy:**
- MagentaSport confirms: *"Jeden Spieltag außerdem eine Partie kostenlos für alle"* (one game per matchday free for everyone).
- Only ONE game per matchday is free. All other games require a MagentaSport subscription → REJECT.

**Mandatory Two-Step Search Strategy:**
1. **Announcement Search** — Search `site:magentasport.de`, `site:facebook.com/magentasport`, and `site:twitter.com/MagentaSport` for the free-game announcement using keywords: `kostenlos`, `kostenlos für alle`, `ohne Abo`, `für alle`.
2. **Stream Search** — Search `site:magenta.tv/tv/live*` for the matching game content. Cross-reference the slug/game title with the announcement found in step 1.
3. **Cross-reference Rule** — A `magenta.tv` stream URL is **only valid** if a matching official free-access announcement exists on `magentasport.de` or official MagentaSport social media. **No announcement = REJECT.**

**Free Stream Indicators (PASS):**
- `"kostenlos für alle"`
- `"ohne Abo"`
- `"ohne Login"`
- `"für alle zugänglich"`
- `"Jeden Spieltag eine Partie kostenlos"`

**Subscription/Paid Indicators (FAIL → REJECT):**
- `"mit MagentaSport Abo"`
- `"nur für Abonnenten"`
- `"Login erforderlich"`
- `"kostenpflichtig"`

**Validation Note for Check 1 (Free Access):**
If a `magenta.tv` URL passes HTTP 200 but no official free-access announcement can be found on `magentasport.de`, the stream must be treated as **subscription-gated** and **REJECTED** at Check 1. Document the discrepancy in the validation log.

### Dyn Sport Mix

- Free via Joyn, Pluto TV, Zattoo (no subscription required)
- NOT free via Amazon Prime (requires Prime subscription)
- Verify which platform is being used

### YouTube Live Verification

- A channel URL alone (`youtube.com/@fiba`) does NOT pass Check 7
- Must be `youtube.com/@fiba/live` OR the channel must have an active/upcoming live stream listed
- Read the actual page to confirm "LIVE" badge or scheduled live event

### Duplicate Handling

If a stream is found from multiple sources (e.g., both Sportschau and MagentaSport cover the same game):
- Create ONE calendar event with BOTH stream links in the description
- Check for calendar duplicates before creating
