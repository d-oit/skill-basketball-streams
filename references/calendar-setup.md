# Google Calendar Setup

## Target Calendar

- **Calendar ID**: `f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com`
- **Purpose**: Free basketball live streams in Germany
- **Visibility**: Public
- **Timezone**: Europe/Berlin

## Event Schema

### Required Fields

```json
{
  "summary": "[League] [Team1] vs [Team2] - FREE Live Stream",
  "startTime": "2026-06-27T19:00:00+02:00",
  "endTime": "2026-06-27T21:30:00+02:00",
  "calendarId": "f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com",
  "timeZone": "Europe/Berlin"
}
```

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

| colorId | Color | Use |
|---------|-------|-----|
| `"6"` | Tangerine/Orange | Default for all basketball events |
| `"11"` | Tomato/Red | EuroLeague finals or special events |
| `"2"` | Sage/Green | FIBA international games |

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

## Time Handling

### Timezone Rules

- Always use `Europe/Berlin` timezone
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
await googleCalendarListEvents({
  calendarId: 'f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com',
  startTime: startMinus30Min,
  endTime: startPlus30Min,
  fullText: `${team1} ${team2}`
});
```

### Create Event

```typescript
await googleCalendarCreateEvent({
  calendarId: 'f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com',
  summary: `${league} ${team1} vs ${team2} - FREE Live Stream`,
  startTime: isoStartTime,
  endTime: isoEndTime,
  timeZone: 'Europe/Berlin',
  description: buildDescription(game),
  colorId: '6',
  visibility: 'public'
});
```
