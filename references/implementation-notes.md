# Implementation Notes

Search-query templates, time/team/vocab conventions, and code for `validateStreamUrl` and the end-to-end workflow. Referenced from SKILL.md Steps 1, 2, 2.5, and 5.

## Time Handling

- Timezone: **`Europe/Berlin`** (CEST = UTC+2 Apr–Oct, CET = UTC+1 Nov–Mar)
- Anchor "now" with `new Date().toISOString()` then convert to CET
- Date range: today (00:00 CET) through today + 7 days (23:59 CET)
- Emit ISO 8601 with offset, e.g. `2026-06-27T19:00:00+02:00`

## Canonical Search Queries

For `webSearch` with `limit: 20`:

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

Focus on official domains in approved-sources.md; reject anything else.

## Team Name Variations

- ALBA Berlin / Alba Berlin / ALBA
- FC Bayern München / Bayern Munich / FC Bayern / Bayern
- ratiopharm Ulm / Ulm / Ratiopharm Ulm
- Other BBL teams: ratiopharm ulm, MHP Riesen Ludwigsburg, MLP Academics Heidelberg, etc.

## League Keywords (accept)

- BBL (Basketball Bundesliga)
- EuroLeague
- Basketball Champions League
- FIBA
- ProA, ProB (German second/third divisions)

## Exclusion Keywords (reject)

- `kostenpflichtig`, `Abo`, `subscription`, `pay-per-view` → Check 1 fail
- `Sky`, `DAZN`, `Prime` (unless Dyn Sport Mix free tier) → Check 3 fail
- `highlights`, `replay`, `zusammenfassung`, `on demand`, `wiederholung` → Check 2 fail

## Sports Exclusion

Reject: Fußball, Handball, Eishockey, Tennis, Volleyball. Accept: Basketball, BBL, EuroLeague, FIBA.

## URL Validation Helper

```typescript
async function validateStreamUrl(url: string): Promise<{ valid: boolean, reason?: string, finalUrl?: string }> {
  // @FIBAWorld is NOT a valid YouTube channel — only @fiba is official
  if (url.includes('@FIBAWorld')) {
    return { valid: false, reason: '@FIBAWorld is NOT a valid YouTube channel. Only @fiba is the official FIBA channel.' };
  }
  // Only /user/TheDBBTV among legacy /user/ URLs is approved
  if (url.includes('/user/') && !url.includes('/user/TheDBBTV')) {
    return { valid: false, reason: 'YouTube /user/ URLs are deprecated. Only /user/TheDBBTV is accepted as approved official channel.' };
  }
  // /channel/ URLs are not direct live streams
  if (url.includes('/channel/')) {
    return { valid: false, reason: 'YouTube /channel/ URLs are not direct live streams' };
  }
  try {
    const response = await openUrl({ url });
    if (!response || response.status >= 400) {
      return { valid: false, reason: `HTTP ${response?.status || 'error'}` };
    }
    // For YouTube, the URL must point at a live stream page
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

## End-to-End Workflow (TypeScript sketch)

```typescript
// 1. Range
const now = new Date();
const startDate = new Date(now); startDate.setHours(0, 0, 0, 0);
const endDate = new Date(now); endDate.setDate(endDate.getDate() + 7); endDate.setHours(23, 59, 59, 999);

// 2. Search each approved source
const sources = [
  { name: 'Dyn Sport Mix',  query: 'Dyn Sport Mix Basketball live stream site:amazon.de OR site:joyn.de OR site:pluto.tv OR site:zattoo.com' },
  { name: 'MagentaSport',  query: 'MagentaSport free EuroLeague live stream site:magentasport.de' },
  { name: 'Sportschau',    query: 'Sportschau Basketball live site:sportschau.de OR site:ard.de' },
  // ... see full query list above
];

// 2.5. Filter via validateStreamUrl before Step 3
const validated = [];
for (const result of searchResults) {
  const v = await validateStreamUrl(result.url);
  if (!v.valid) { console.log(`Rejected ${result.url}: ${v.reason}`); continue; }
  validated.push({ ...result, finalUrl: v.finalUrl });
}

// 3–4. Run the 7-check pipeline; extract game details for survivors.
//       See references/validation-workflow.md for Check definitions.

// 5. Duplicate check
for (const game of validatedGames) {
  const dup = await googleCalendarListEvents({
    calendarId: CALENDAR_ID,
    startTime: subtract(game.startTime, 30, 'minutes'),
    endTime:   add(game.startTime, 30, 'minutes'),
    fullText: `${game.team1} ${game.team2}`,
  });
  if (dup?.length) { logSkipped(game, dup[0].id); continue; }

  // 6. Create event
  const event = await googleCalendarCreateEvent({
    calendarId: CALENDAR_ID,
    summary: `${game.league} ${game.team1} vs ${game.team2} - FREE Live Stream`,
    startTime: game.startTime,
    endTime:   game.endTime,
    timeZone:  'Europe/Berlin',
    description: buildDescription(game),
    colorId: game.colorId || '6',
    visibility: 'public',
  });
  logCreated(game, event.id, event.htmlLink);
}

// 7. Emit the results table (see SKILL.md Step 7)
```
