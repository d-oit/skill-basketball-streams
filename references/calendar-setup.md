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

### Color Coding

Color selection is handled by the `get_color_id(league, event_type)` function in `scripts/color_mapping.py`. This provides deterministic, testable color assignment.

| colorId | Color | Use |
|---------|-------|-----|
| `"6"` | Tangerine/Orange | Default for all basketball events (BBL, EuroLeague regular season, etc.) |
| `"11"` | Tomato/Red | EuroLeague finals, Basketball Champions League finals, special events |
| `"2"` | Sage/Green | FIBA international games (World Cup, EuroBasket, etc.) |

**Function reference:** Use `get_color_id(league, event_type)` where:
- `league`: The league name (e.g., "BBL", "EuroLeague", "FIBA")
- `event_type`: The event type (e.g., "regular", "final", "playoff"). Defaults to "regular" if omitted.

See `scripts/color_mapping.py` for the complete mapping table and `tests/test_color_mapping.py` for unit test coverage.

### Visibility

- Always set `visibility: "public"` for all events

## Duplicate Detection

### Check Parameters

```json
{
  "calendarId": "f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com",
  "startTime": "[event start - 30 minutes]",
  "endTime": "[event start + 30 minutes]",
  "fullText": "[team names or league keywords]"
}
```

### Duplicate Criteria

A duplicate exists if ANY of the following match:
- Same date/time within ±30 minutes
- Same team names in title
- Similar title containing 'BBL', 'Finals', or team names

**Note:** The `fullText` parameter in duplicate check should use team/league keywords (e.g., `"{{team1}} {{team2}}"`) rather than a blank string to properly detect duplicates. See Issue #14 in the repository.

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

## API Tool Usage

### List Events (Duplicate Check)

```typescript
// Import calendar config at runtime
const { get_calendar_id, get_timezone } = require('./scripts/calendar_config');

// Or use environment variable directly
const calendarId = process.env.BASKETBALL_CALENDAR_ID || require('./config/calendar.json').calendarId;

await googleCalendarListEvents({
  calendarId: calendarId,  // From config/calendar.json or BASKETBALL_CALENDAR_ID env var
  startTime: startMinus30Min,
  endTime: startPlus30Min,
  fullText: `${team1} ${team2}`  // FIXED: Use team keywords, not blank string
});
```

### Create Event

```typescript
// Import calendar config at runtime
const { get_calendar_id, get_timezone, get_visibility, get_default_color_id } = require('./scripts/calendar_config');

// Or use config values directly
const config = require('./config/calendar.json');
const calendarId = process.env.BASKETBALL_CALENDAR_ID || config.calendarId;

await googleCalendarCreateEvent({
  calendarId: calendarId,  // From config/calendar.json or BASKETBALL_CALENDAR_ID env var
  summary: `${league} ${team1} vs ${team2} - FREE Live Stream`,
  startTime: isoStartTime,
  endTime: isoEndTime,
  timeZone: config.timezone || 'Europe/Berlin',
  description: buildDescription(game),
  colorId: get_color_id(league, event_type),  // From scripts/color_mapping.py
  visibility: config.visibility || 'public'
});
```

**Note:** For Python-based implementations, use `scripts/calendar_config.py`:
```python
from scripts.calendar_config import get_calendar_id, get_timezone, get_visibility
from scripts.color_mapping import get_color_id

calendar_id = get_calendar_id()
timezone = get_timezone()
visibility = get_visibility()
color_id = get_color_id(league, event_type)
```
