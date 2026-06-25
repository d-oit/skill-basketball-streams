# Lessons Learned — Broken YouTube Links Incident

Referenced from SKILL.md's opening CRITICAL banner. This page is the canonical "why" behind every Check in `references/validation-workflow.md` and the rationale for `## Rationalizations` / `## Red Flags` in SKILL.md.

## Incident Summary

A prior run created 3 FIBA U20 calendar events with non-working YouTube links (`/user/FIBA`, `/user/TheDBBTV`) that returned 404 or were deprecated URLs. The events went out to subscribers with broken `description` links.

## Root Causes

1. **No direct URL validation** — URLs were extracted from search results but never tested with `openUrl`.
2. **Assumed YouTube `/user/` URLs work** — legacy `/user/` format was assumed valid without verification.
3. **No page content reading** — pages weren't fetched to confirm the stream actually existed there.
4. **Missing references** — no tracking of where streams were found or when they were validated.
5. **No live verification** — calendar events were created without confirming an active live stream.

## What Went Wrong

| Event | URL | Outcome |
|---|---|---|
| FIBA U20 (Game 1) | `youtube.com/@FIBAWorld` | **404** — `@FIBAWorld` is NOT a valid YouTube channel. Only `@fiba` is official. |
| FIBA U20 (Game 2) | `youtube.com/user/FIBA` | Deprecated/broken — `/user/FIBA` is no longer the official FIBA URL. |
| FIBA U20 (Game 3) | `youtube.com/user/TheDBBTV` | Worked *eventually*, but was never URL-tested before the event shipped; subjectively risky. |

## ✅ Prevention Checklist — Run BEFORE Any Calendar Event

### URL Validation

- Every URL tested with `openUrl`. **No exceptions.**
- HTTP status **200** required (reject 404, 500, paywall redirects).
- YouTube URLs must follow the **accepted** patterns in `references/approved-sources.md`:
  - ✅ `youtube.com/@handle` (modern handle)
  - ✅ `youtube.com/@handle/live` (preferred)
  - ✅ `youtube.com/user/TheDBBTV` (only legacy `/user/` URL accepted)
  - ❌ All other `youtube.com/user/…` URLs → reject
  - ❌ All `youtube.com/channel/UC…` URLs → reject

### Live Stream Verification

- Page contains `"Live"` or `"LIVE"` text (for YouTube / video platforms).
- Active `<video>` player element detected on the page.
- Reject channel homepages with no scheduled or active live stream.
- Reject highlight reels / replay pages.

### Source Tracking

- Original search result URL saved as `sourceReference`.
- Validation timestamp recorded in ISO 8601.
- Validation notes included in the calendar event description.

### Quality Gates

- All 7 checks pass (not just 6).
- Direct streaming URL confirmed working (tested, not assumed).
- Live stream exists on the page (content verified).
- No broken links in the event description.

### Final Pre-Creation Gate

```typescript
if (!stream.directLink)            REJECT: 'No direct streaming URL found';
if (!stream.urlValidation?.valid)  REJECT: 'URL not validated: ' + stream.urlValidation.reason;
if (!stream.liveVerification?.confirmed) REJECT: 'Live stream not verified on page';
if (!stream.sourceReference)       REJECT: 'Missing source reference';
// Only then: createCalendarEvent()
```

## 📋 Validation Log Template

Log one entry per candidate stream so the run is auditable.

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

### Worked Example — CREATE

```
Stream URL: https://www.youtube.com/@fiba/live
Source: https://google.com/search?q=FIBA+U20+live
Timestamp: 2026-06-24T12:00:00Z
Check 1 - Free Access: PASS — FIBA official channel (@fiba is the ONLY valid FIBA channel)
Check 2 - Live Content: PASS — URL contains /live, page shows LIVE badge
Check 3 - Official Source: PASS — youtube.com, @fiba is approved FIBA channel
Check 4 - Basketball-Specific: PASS — FIBA basketball content
Check 5 - Date/Time Range: PASS — Event is 2026-06-27, within 7 days
Check 6 - Working Link: PASS — HTTP 200, page loads successfully
Check 7 - Direct Stream Verification: PASS — Page content contains "LIVE" text
Final Decision: CREATE
Calendar Event ID: xyz123
```

### Worked Example — SKIP (deprecated `/user/FIBA`)

```
Stream URL: https://www.youtube.com/user/FIBA
Source: https://google.com/search?q=basketball+stream
Timestamp: 2026-06-24T12:05:00Z
Check 1 - Free Access: PASS — FIBA is official
Check 2 - Live Content: FAIL — /user/ URL not in approved list
Check 3 - Official Source: PASS — youtube.com domain
Check 4 - Basketball-Specific: PASS — FIBA content
Check 5 - Date/Time Range: PASS — Within range
Check 6 - Working Link: FAIL — /user/FIBA is deprecated, not approved
Check 7 - Direct Stream Verification: N/A — Failed earlier check
Final Decision: SKIP
Reason: YouTube /user/ URLs are deprecated. Only /user/TheDBBTV is accepted.
```

### Worked Example — SKIP (`@FIBAWorld`)

```
Stream URL: https://www.youtube.com/@FIBAWorld/live
Source: https://google.com/search?q=FIBA+live
Timestamp: 2026-06-24T12:10:00Z
Check 1 - Free Access: PASS — FIBA is official
Check 2 - Live Content: PASS — URL contains /live
Check 3 - Official Source: FAIL — @FIBAWorld is NOT a valid YouTube channel
Check 4 - Basketball-Specific: PASS — FIBA content
Check 5 - Date/Time Range: PASS — Within range
Check 6 - Working Link: FAIL — @FIBAWorld does not exist on YouTube
Check 7 - Direct Stream Verification: N/A — Failed earlier check
Final Decision: SKIP
Reason: @FIBAWorld is NOT a valid YouTube channel. Only @fiba is the official FIBA channel.
```
