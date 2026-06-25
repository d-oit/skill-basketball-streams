# skill-basketball-streams

AI agent skill: Search, validate, and add FREE basketball live streams in Germany from official sources to Google Calendar.

## Description

Use this skill to search, validate, and add FREE basketball live streams in Germany from official sources to Google Calendar. Triggers on requests for free basketball streams, live game schedules, or calendar updates for German basketball.

## File Structure

```
├── SKILL.md                         # Main skill instructions
├── evals/
│   └── evals.json                   # Evaluation test cases
└── references/
    ├── approved-sources.md          # Approved streaming sources
    ├── calendar-setup.md            # Google Calendar configuration
    └── validation-workflow.md       # Detailed validation workflow
```

## Key Features

- Searches approved German basketball streaming sources
- Validates streams with 7 mandatory checks
- Prevents duplicate calendar events
- Enforces strict YouTube URL validation
- Logs all validation decisions with timestamps

## Approved Sources

- Dyn Sport Mix (Joyn, Pluto TV, Zattoo)
- MagentaSport (free EuroLeague game per matchday)
- Sportschau / ARD
- Regional: MDR, BR24, RBB24
- Official club websites & YouTube channels

## Calendar

Target calendar ID: `f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com`
