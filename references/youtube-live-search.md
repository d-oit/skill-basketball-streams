# YouTube Live-Stream Search (Live-Only, Future-Only)

Contract for the YouTube sub-skill: **only real live broadcasts whose start
datetime is greater than now** (or that are live at this moment) may enter the
7-check pipeline. A normal upload, a highlights reel, a re-run, or an ended
broadcast is rejected **before** Check 1.

Implementation: `scripts/youtube_live.py` (`classify_stream`,
`filter_candidates`, `build_live_search_url`).
Eval cases 21–24 in `evals/evals.json` pin this behaviour.

## The search entry point: YouTube's Live filter

Append YouTube's "Live" search filter to a results URL:

```
https://www.youtube.com/results?search_query=<query>&sp=EgJAAQ%3D%3D
```

`sp=EgJAAQ%3D%3D` decodes to *type=video + features=live*: the result page then
contains only live-now and upcoming broadcasts. Build it with
`build_live_search_url(query)` — never hand-assemble the URL.

The daily GitHub workflow generates the query URLs from
`config/youtube-live-queries.json`, attaches them to the runtime, and instructs
it to call `webfetch` on every URL. Interactive runs use the `youtubeLiveSearch`
tool. **Never use generic `webSearch` results or the YouTube Data API for stream
discovery.** A YouTube results URL without the exact Live filter is not an
acceptable search input.

## Field mapping (raw payload → candidate schema)

| Candidate key | Live-filtered result/page field |
|---|---|
| `url` | `videoRenderer.navigationEndpoint.commandMetadata.webCommandMetadata.url` → prefix `https://www.youtube.com` |
| `video_id` | `videoRenderer.videoId` |
| `title` | `videoRenderer.title.runs[0].text` |
| `channel` | `videoRenderer.ownerText.runs[0].text` |
| `live_broadcast_content` | `videoRenderer.badges[].metadataBadgeRenderer.label` (`LIVE`/`UPCOMING`) |
| `is_live_now` | `liveBroadcastDetails.isLiveNow` (page) |
| `scheduled_start` | `liveBroadcastDetails.startTimestamp` |
| `actual_start` / `actual_end` | `liveBroadcastDetails.startTimestamp` / page `actualEndTime` |
| `duration_seconds` | `videoRenderer.lengthText` (only present for VODs) |

Open the result page when the search result does not expose a scheduled start or
end state. A missing start time is not evidence that an upcoming stream is
future-dated; reject it.

## The gate (all must hold)

Each rule has an **id** in brackets — the token the runtime's graders match a
validation result on (`checks: <id>=PASS|FAIL`). A result reported for one of
these outcomes names the outcome by its id.

1. **URL shape** — `youtube.com/@handle/live`, `youtube.com/live/<id>`, or
   `youtube.com/watch?v=<id>`. `/channel/…`, `/user/…` (except `TheDBBTV`),
   `/c/…`, `/playlist`, `/results` → REJECT.
2. **Real live** (`liveOnly`) — `live_broadcast_content ∈ {live, upcoming}` **or**
   `is_live_now` **or** `is_live_content`. A regular upload (`none`) → REJECT.
3. **Not over** (`liveOnly`) — `actual_end` must be absent. An ended broadcast is
   an archived live even though `isLiveContent` is still true → REJECT.
4. **Not a recording** (`liveOnly`) — a non-zero `duration_seconds` while not live
   now means a fixed-length upload → REJECT.
5. **Datetime > now** (`futureDatetime`) — for `upcoming` items `scheduled_start`
   must be strictly greater than now. Missing or past `scheduled_start` → REJECT.
   `is_live_now` items pass this gate because the broadcast is happening now
   (start ≤ now < end); an `is_live_now` item whose `actual_start` is in the
   future is an inconsistent payload → REJECT.

Rules 2–4 share one id: they are three ways a candidate fails the live-only
requirement. Rule 5 is the future-only requirement.

Decisions: `LIVE` (live right now) and `SCHEDULED` (starts in the future) are
promotable; `REJECT` is not.

## Channel allow-list

Only channels in `references/approved-sources.md` Tier 6 / `config/sources.json`:

```
youtube.com/@fiba           youtube.com/@basketballbundesliga
youtube.com/@EuroLeague     youtube.com/@BasketballCL
youtube.com/user/TheDBBTV   youtube.com/@tampa2films
```

A live broadcast on an unlisted channel is a new-source **candidate**
(`scripts/source_learning.py candidates`) — quarantined, shown in the daily
telemetry summary, and never auto-promoted. This is how new Regionalliga,
NBBL/JBBL, ProB and club channels can be reviewed without trusting them early.

## Canonical queries

```
"<team1> vs <team2> live"         "BBL live heute"
"EuroLeague live"                 "Basketball Champions League live"
"FIBA Basketball live"            "NBBL Basketball live stream"
"JBBL Basketball live stream"     "Basketball Regionalliga live Deutschland"
"ProB Basketball live stream"     "<club name> live"
```

The workflow's complete query set is in `config/youtube-live-queries.json`.
ProB, Regionalliga, and other unlisted league streams are discovery leads; they
still need current, event-specific free-access evidence before promotion.

## Worked example — promote

```
candidate: {"url": "https://www.youtube.com/@EuroLeague/live",
            "live_broadcast_content": "upcoming",
            "scheduled_start": "2026-10-15T20:30:00+02:00"}
now:       2026-10-13T09:00:00Z
-> SCHEDULED: scheduled live broadcast starts 2026-10-15T20:30:00+02:00
   (greater than now)  -> continue to Check 1
```

## Worked example — reject

```
candidate: {"url": "https://www.youtube.com/@fiba/live",
            "live_broadcast_content": "none",
            "duration_seconds": 6420}
now:       2026-09-14T09:00:00Z
-> REJECT: not a live broadcast (liveBroadcastContent='none') — regular video
   upload
```

```
candidate: {"url": "https://www.youtube.com/live/abc123",
            "is_live_content": true,
            "actual_end": "2026-10-12T22:05:00Z"}
-> REJECT: broadcast already ended (2026-10-12T22:05:00+00:00) — archived live
```

```
candidate: {"url": "https://www.youtube.com/@fiba/live",
            "live_broadcast_content": "upcoming",
            "scheduled_start": "2026-10-13T07:00:00Z"}
now:       2026-10-13T09:00:00Z
-> REJECT: scheduled start 2026-10-13T07:00:00+00:00 is not greater than now
```
