# Basketball Streams Finder & Calendar Manager

> **CRITICAL WARNING**: Previous runs created events with broken YouTube links. This version enforces strict validation. **NEVER create a calendar event without:**
> 1. Testing every URL with `openUrl`
> 2. Reading page content to verify live stream exists
> 3. Including source reference in event description
> 4. Passing all 7 mandatory validation checks

## Purpose

Search for FREE basketball live streams in Germany from official sources, validate them, and add them to Google Calendar `f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com`.

## When to Load

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

## Search & Validation Workflow

### Step 1: Determine Date Range

- Today through next 7 days (inclusive)
- Use Europe/Berlin timezone (CET/CEST)
- Current date: Check with `new Date().toISOString()`

### Step 2: Search for Streams

For each approved source, search for basketball live streams:

**Search Queries:**
```
- "Dyn Sport Mix Basketball live stream today"
- "MagentaSport free EuroLeague live stream"
- "Sportschau Basketball live ARD"
- "MDR Basketball live stream"
- "BR24 Basketball live"
- "RBB24 Basketball live"
- "ALBA Berlin live stream free"
- "FC Bayern München Basketball live free"
- "ratiopharm Ulm live stream"
- "Basketball Bundesliga live YouTube"
- "EuroLeague live YouTube official"
- "BBL live stream free Germany"
```

Use `webSearch` with:
- `limit: 20` per query
- Filter by date range when possible
- Focus on official domain results

### Step 2.5: Extract and Validate Direct Streaming URLs

**CRITICAL: Before processing any search result, you MUST:**

1. **Extract the actual streaming URL** from each search result
2. **Validate the URL works** using `openUrl` - must return accessible page (not 404)
3. **For YouTube URLs specifically**:
   - ✅ **PREFERRED**: Modern handle format: `youtube.com/@handle` or `youtube.com/@handle/live`
   - ✅ **ACCEPTABLE**: Legacy `/user/` URLs **ONLY** if they match approved official channels:
     - `youtube.com/user/TheDBBTV` (DBB - Deutscher Basketball Bund)
   - ❌ **REJECT**: All other `/user/` URLs (not in approved list)
   - ❌ **REJECT**: Generic channel URLs: `youtube.com/channel/UC...`
   - Must verify the page contains "Live" or "LIVE" text
4. **For non-YouTube URLs**:
   - Must load successfully (not 404, not 500, not redirecting to paywall)
   - Must contain basketball live stream content (verify by reading page)

**YouTube URL Validation Rules:**

- Valid patterns: `youtube.com/@fiba`, `youtube.com/@fiba/live`, `youtube.com/user/TheDBBTV`
- Invalid patterns: `youtube.com/@FIBAWorld` (**NOT a valid channel**), `youtube.com/user/randomuser` (not approved), `youtube.com/channel/UC...`
- Must verify the actual page contains "Live" or "LIVE" text

**Direct Link Testing:**

```typescript
async function validateStreamUrl(url: string): Promise<{ valid: boolean, reason?: string, finalUrl?: string }> {
  // ⚠️ CRITICAL: @FIBAWorld is NOT a valid YouTube channel
  if (url.includes('@FIBAWorld')) {
    return { valid: false, reason: '@FIBAWorld is NOT a valid YouTube channel. Only @fiba is the official FIBA channel.' };
  }
  // Reject non-approved /user/ URLs immediately
  if (url.includes('/user/') && !url.includes('/user/TheDBBTV')) {
    return { valid: false, reason: 'YouTube /user/ URLs are deprecated. Only /user/TheDBBTV is accepted as approved official channel.' };
  }
  // Reject /channel/ URLs
  if (url.includes('/channel/')) {
    return { valid: false, reason: 'YouTube /channel/ URLs are not direct live streams' };
  }
  try {
    const response = await openUrl({ url });
    if (!response || response.status >= 400) {
      return { valid: false, reason: `HTTP ${response?.status || 'error'}` };
    }
    // For YouTube, verify it's actually a live stream page
    if (url.includes('youtube.com') || url.includes('youtu.be')) {
      const isLiveUrl = url.includes('/live') || url.includes('live=') || url.toLowerCase().includes('/@');
      if (!isLiveUrl) {
        return { valid: false, reason: 'YouTube URL does not point to a live stream' };
      }
    }
    return { valid: true, finalUrl: response.url || url };
  } catch (error) {
    return { valid: false, reason: `URL test failed: ${error}` };
  }
}
```

### Step 3: Validate Each Found Stream

**MANDATORY Checks (ALL must pass):**

⚠️ IMPORTANT: If ANY check fails, the stream is REJECTED. Never create a calendar event for an invalid stream.

1. **Free Access**:
   - No paywall mentioned
   - No subscription required
   - Explicitly states "free", "kostenlos", or no payment terms
   - Exclude: "Prime", "subscription", "Abo", "pay-per-view", "kostenpflichtig"

2. **Live Content**:
   - Must be live or upcoming
   - Exclude: "highlights", "replay", "zusammenfassung", "on demand", "wiederholung"
   - Look for: "live", "Live", "LIVE", "live stream", "live übertragung"
   - **For YouTube**: Page must contain "Live" or "LIVE" text (not just a channel page)

3. **Official Source**:
   - URL domain matches approved sources:
     - `amazon.de` / `primevideo.com` (Dyn Sport Mix)
     - `magentasport.de`
     - `sportschau.de` / `ard.de`
     - `mdr.de`, `br.de/br24`, `rbb-online.de/rbb24`
     - `albaberlin.de`, `fcbayern.com`, `ratiopharm-ulm.de`
     - `youtube.com` (official channels only)
   - For YouTube: Verify channel is official (league, club, or federation)

4. **Basketball-Specific**:
   - Must mention basketball, BBL, EuroLeague, or specific team names
   - Exclude other sports (football, handball, etc.)

5. **Date/Time Within Range**:
   - Event date is today through next 7 days
   - Time is reasonable for live sports (typically 14:00–23:00 CET)

6. **Working Link**:
   - Test URL accessibility with `openUrl`
   - Must return HTTP 200 or similar success code
   - Exclude broken links, 404 errors, or redirects to paywalls
   - **For YouTube**: Must NOT use deprecated `/user/` URLs except for approved official channels

7. **Direct Stream Verification** (CRITICAL):
   - Read the actual page content using `openUrl`
   - Verify the page contains an active video player or live stream
   - For YouTube: URL must include `/live` or be a verified live video, OR be an approved official channel
   - For other sources: Page must show live streaming content
   - **If you cannot verify a live stream exists on the page, REJECT the stream**

### Step 4: Extract Game Details

For each VALIDATED stream, extract:

- **League**: BBL, EuroLeague, Basketball Champions League, etc.
- **Teams**: Home vs Away (e.g., "ALBA Berlin vs FC Bayern München")
- **Date/Time**: In CET timezone (convert from UTC if needed)
- **Direct Link**: The actual streaming URL (MUST be validated and working)
- **Access Requirements**: "Free", "Registration required", "No login", etc.
- **Source Reference**: The original search result URL or source page where the stream was found
- **Validation Timestamp**: When the stream was validated

⚠️ CRITICAL RULE: If you cannot extract a working direct streaming URL, DO NOT create the calendar event.

### Step 5: Check Google Calendar for Duplicates

Use `googleCalendarListEvents` on calendar ID: `f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com`

Search parameters:
- `startTime`: Event start time - 30 minutes
- `endTime`: Event start time + 30 minutes
- `fullText`: Search for team names and league keywords

**Duplicate Criteria (ANY match = duplicate):**
- Same date/time (±30 minutes)
- Same teams (ALBA Berlin, FC Bayern München, etc.)
- Similar title (contains 'BBL', 'Finals', team names)

If duplicate found:
- Log: `"Event already exists: [Event ID]"`
- Status: `skipped`
- Skip to next stream

### Step 6: Add to Google Calendar (If No Duplicate)

⚠️ BEFORE CREATING: Double-check that ALL validation passed, especially:
- Direct streaming URL is valid and working (not 404)
- Stream is confirmed to be live (not highlights/replay)
- Source is official and approved

Use `googleCalendarCreateEvent` with:

**Required Fields:**
- `summary`: "[League] [Team1] vs [Team2] - FREE Live Stream"
- `startTime`: ISO 8601 format with Europe/Berlin timezone
- `endTime`: ISO 8601 format (estimate ~2.5 hours for basketball games)
- `calendarId`: `f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com`
- `timeZone`: `Europe/Berlin`

**Additional Fields:**
- `description`: Structured info including:
  ```
  League: [League Name]
  Teams: [Team1] vs [Team2]
  Date/Time: [CET Date/Time]
  FREE STREAM LINKS:
  - [Source1]: [Direct Link1]
  - [Source2]: [Direct Link2]
  ...
  Access: [Access Requirements]
  SOURCE REFERENCE:
  - Found at: [Original search result URL]
  - Validated: [ISO timestamp of validation]
  - Validation Notes: [Any important notes about validation]
  ```
- `colorId`: `"6"` (Tangerine/Orange - for sports events)
- `visibility`: `"public"`

⚠️ NEVER CREATE EVENT IF:
- Direct link is not validated and working
- Stream is not confirmed to be live
- Any of the 7 mandatory checks failed

### Step 7: Output Results

Return a structured list of all VALIDATED games processed:

```markdown
## Validated Basketball Streams

| Status | League | Teams | Date/Time (CET) | Direct Link | Calendar Event |
|--------|--------|-------|----------------|-------------|----------------|
| created | BBL | ALBA Berlin vs Bayern | 2026-06-20 19:30 | [link] | [event link] |
| skipped | EuroLeague | Real Madrid vs Barcelona | 2026-06-21 20:00 | [link] | Event already exists: abc123 |
```

Each row includes:
- Status: `created` or `skipped`
- League name
- Teams
- Date/Time in CET
- Direct link (clickable)
- Calendar event link or skip reason

## Implementation Notes

### Time Handling

- All times in Europe/Berlin (CET/CEST)
- Current time: Use `new Date().toISOString()` and convert to CET
- Date range: Today (00:00 CET) to Today + 7 days (23:59 CET)

### URL Validation

Use `openUrl` to test each direct link:

```typescript
const response = await openUrl({ url: streamUrl });
// Check if response contains error or redirect to paywall
```

### Team Name Variations

- ALBA Berlin / Alba Berlin / ALBA
- FC Bayern München / Bayern Munich / FC Bayern / Bayern
- ratiopharm Ulm / Ulm / Ratiopharm Ulm
- Other BBL teams: ratiopharm ulm, MHP Riesen Ludwigsburg, etc.

### League Keywords

- BBL (Basketball Bundesliga)
- EuroLeague
- Basketball Champions League
- FIBA
- ProA, ProB (German second/third divisions)

### Exclusion Keywords

- "kostenpflichtig" (paid)
- "Abo" (subscription)
- "Prime" (Amazon Prime - unless Dyn Sport Mix is free)
- "Sky" (Sky Sport - paid)
- "DAZN" (paid)
- "Pay-per-view"
- "highlights"
- "replay"
- "zusammenfassung"
- "wiederholung"

### Sports Exclusion

- Exclude: Fußball (football/soccer), Handball, Eishockey (ice hockey), Tennis, etc.
- Only include: Basketball, BBL, EuroLeague, etc.

## Example Workflow Execution

```typescript
// 1. Get current date and calculate range
const now = new Date();
const startDate = new Date(now.setHours(0, 0, 0, 0));
const endDate = new Date(now);
endDate.setDate(endDate.getDate() + 7);
endDate.setHours(23, 59, 59, 999);

// 2. Search each approved source
const sources = [
  { name: "Dyn Sport Mix", query: "Dyn Sport Mix Basketball live stream site:amazon.de OR site:joyn.de OR site:pluto.tv OR site:zattoo.com" },
  { name: "MagentaSport", query: "MagentaSport free EuroLeague live stream site:magentasport.de" },
  { name: "Sportschau", query: "Sportschau Basketball live site:sportschau.de OR site:ard.de" },
  // ... etc
];

// 2.5. Validate URLs before processing
for (const result of searchResults) {
  const urlValidation = await validateStreamUrl(result.url);
  if (!urlValidation.valid) {
    console.log(`Rejected ${result.url}: ${urlValidation.reason}`);
    continue;
  }
  // Continue with full validation
}

// 3. For each result, validate and extract
const validatedGames = [];
for (const result of searchResults) {
  if (isValidStream(result)) {
    validatedGames.push(extractGameDetails(result));
  }
}

// 4. Check calendar and add events
for (const game of validatedGames) {
  const duplicate = await checkCalendarDuplicate(game);
  if (duplicate) {
    logSkipped(game, duplicate.id);
  } else {
    const event = await createCalendarEvent(game);
    logCreated(game, event.id, event.htmlLink);
  }
}

// 5. Return results
return formatResults(validatedGames);
```

## Important Constraints

1. **ONLY use approved sources** - No exceptions
2. **100% validation required** - All 7 checks must pass (including direct stream verification)
3. **No paid content** - Even if from approved source, if it requires payment, exclude it
4. **No unofficial streams** - No pirate sites, third-party aggregators, or unofficial YouTube channels
5. **Date accuracy** - Double-check all dates/times are within range
6. **Link testing** - Every direct link must be tested with `openUrl` and confirmed working (not 404, not broken)
7. **Live verification** - You MUST read the page content to verify a live stream actually exists
8. **YouTube URL rules** - NEVER use `/user/` or `/channel/` URLs except for approved official channels (TheDBBTV)
9. **Include references** - Every calendar event MUST include the source reference and validation timestamp in the description
10. **No event without live stream** - If you cannot verify a working live stream, DO NOT create the calendar event

## Error Handling

- If a source website is down, log and continue with other sources
- If Google Calendar API fails, retry once then log error
- If no streams found, return: "No free basketball live streams found in the next 7 days from official sources."
- If validation fails for all found streams, return: "Found [N] potential streams, but none passed validation."

## 🔴 Lessons Learned From Previous Incident

**Issue**: Calendar events were created with non-working YouTube links (`/user/FIBA`, `/user/TheDBBTV`) that returned 404 or were deprecated URLs.

### Root Causes Identified

1. **No Direct URL Validation**: URLs were extracted from search results but never tested with `openUrl`
2. **Assumed YouTube /user/ URLs Work**: Legacy `/user/` format was assumed valid without verification
3. **No Page Content Reading**: Never verified if pages actually contained live streams
4. **Missing References**: No tracking of where streams were found or when validated
5. **No Live Verification**: Created events without confirming live stream existence

### What Went Wrong

- Created 3 FIBA U20 events with URLs: `youtube.com/@FIBAWorld`, `youtube.com/user/FIBA`, `youtube.com/user/TheDBBTV`
- **`@FIBAWorld` is NOT a valid YouTube channel** - only `@fiba` is official
- `/user/FIBA` URL was deprecated/broken
- `/user/TheDBBTV` may work but wasn't verified
- No validation that these URLs actually had live content
- No source references in event descriptions

## ✅ Prevention Checklist For Next Run

**BEFORE creating ANY calendar event, verify:**

### URL Validation

- **Every URL tested with `openUrl`** - no exceptions
- **HTTP status is 200** (not 404, 500, or redirect to error)
- **YouTube URLs follow approved patterns**:
  - ✅ `youtube.com/@handle` or `@handle/live`
  - ✅ `youtube.com/user/TheDBBTV` (only this specific /user/ URL)
  - ❌ All other `/user/` URLs rejected
  - ❌ All `/channel/` URLs rejected
- **Page content read and analyzed** (not just URL pattern matching)

### Live Stream Verification

- **Page contains "Live" or "LIVE" text** (for YouTube)
- **Video player element detected** on the page
- **Not a channel page without live content**
- **Not a highlights/replay page**

### Source Tracking

- **Original search result URL saved** as `sourceReference`
- **Validation timestamp recorded** (ISO format)
- **Validation notes included** in event description

### Quality Gates

- **All 7 mandatory checks pass** (not just 6)
- **Direct streaming URL is working** (tested, not assumed)
- **Live stream confirmed to exist** (page content verified)
- **No broken links in event description**

### Final Pre-Creation Check

```typescript
// Pseudo-code for final validation before creating event
if (!stream.directLink) { REJECT: "No direct streaming URL found" }
if (!stream.urlValidation?.valid) { REJECT: "URL not validated: " + stream.urlValidation.reason }
if (!stream.liveVerification?.confirmed) { REJECT: "Live stream not verified on page" }
if (!stream.sourceReference) { REJECT: "Missing source reference" }
// Only then: createCalendarEvent()
```

## 📋 Validation Log Template

Use this template to track every stream validation:

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

**Example of proper validation:**

```
Stream URL: https://www.youtube.com/@fiba/live
Source: https://google.com/search?q=FIBA+U20+live
Timestamp: 2026-06-24T12:00:00Z
Check 1 - Free Access: PASS - FIBA official channel (@fiba is the ONLY valid FIBA channel)
Check 2 - Live Content: PASS - URL contains /live, page shows LIVE badge
Check 3 - Official Source: PASS - youtube.com, @fiba is approved FIBA channel
Check 4 - Basketball-Specific: PASS - FIBA basketball content
Check 5 - Date/Time Range: PASS - Event is 2026-06-27, within 7 days
Check 6 - Working Link: PASS - HTTP 200, page loads successfully
Check 7 - Direct Stream Verification: PASS - Page content contains "LIVE" text
Final Decision: CREATE
Calendar Event ID: xyz123
```

**Example of rejected stream:**

```
Stream URL: https://www.youtube.com/user/FIBA
Source: https://google.com/search?q=basketball+stream
Timestamp: 2026-06-24T12:05:00Z
Check 1 - Free Access: PASS - FIBA is official
Check 2 - Live Content: FAIL - /user/ URL not in approved list
Check 3 - Official Source: PASS - youtube.com domain
Check 4 - Basketball-Specific: PASS - FIBA content
Check 5 - Date/Time Range: PASS - Within range
Check 6 - Working Link: FAIL - /user/FIBA is deprecated, not approved
Check 7 - Direct Stream Verification: N/A - Failed earlier check
Final Decision: SKIP
Reason: YouTube /user/ URLs are deprecated. Only /user/TheDBBTV is accepted.
```

**Example of rejected stream (@FIBAWorld):**

```
Stream URL: https://www.youtube.com/@FIBAWorld/live
Source: https://google.com/search?q=FIBA+live
Timestamp: 2026-06-24T12:10:00Z
Check 1 - Free Access: PASS - FIBA is official
Check 2 - Live Content: PASS - URL contains /live
Check 3 - Official Source: FAIL - @FIBAWorld is NOT a valid YouTube channel
Check 4 - Basketball-Specific: PASS - FIBA content
Check 5 - Date/Time Range: PASS - Within range
Check 6 - Working Link: FAIL - @FIBAWorld does not exist on YouTube
Check 7 - Direct Stream Verification: N/A - Failed earlier check
Final Decision: SKIP
Reason: @FIBAWorld is NOT a valid YouTube channel. Only @fiba is the official FIBA channel.
```
