# YouTube Live-Stream Search (Live-Only, Future-Only)

Contract for the YouTube sub-skill: **only real live broadcasts whose start
datetime is greater than now** (or that are live at this moment) may enter the
7-check pipeline. A normal upload, a highlights reel, a re-run, or an ended
broadcast is rejected **before** Check 1.

Implementation: `scripts/youtube_live.py` (`classify_stream`,
`filter_candidates`, `build_live_search_url`, `build_api_live_search_url`).
Eval cases 21–24 in `evals/evals.json` pin this behaviour.

## Two search entry points

### 1. HTML live filter — no API key, no quota

Append YouTube's "Live" search filter to a results URL:

```
https://www.youtube.com/results?search_query=<query>&sp=EgJAAQ%3D%3D
```

`sp=EgJAAQ%3D%3D` decodes to *type=video + features=live*: the result page then
contains only live-now and upcoming broadcasts. Build it with
`build_live_search_url(query)` — never hand-assemble the URL.

### 2. YouTube Data API v3 — free, 10,000 units/day

```
https://www.googleapis.com/youtube/v3/search
  ?part=snippet&type=video&eventType=live&maxResults=25&q=<query>&key=<KEY>
```

`search.list` costs **100 units**, so the free quota allows ~100 live searches
per day. `eventType=live` already excludes VODs; the datetime gate below still
runs on the returned items.

## Field mapping (raw payload → candidate schema)

| Candidate key | HTML (`ytInitialData`) | API v3 response |
|---|---|---|
| `url` | `videoRenderer.navigationEndpoint.commandMetadata.webCommandMetadata.url` → prefix `https://www.youtube.com` | `https://www.youtube.com/watch?v=` + `items[].id.videoId` |
| `video_id` | `videoRenderer.videoId` | `items[].id.videoId` |
| `title` | `videoRenderer.title.runs[0].text` | `items[].snippet.title` |
| `channel` | `videoRenderer.ownerText.runs[0].text` | `items[].snippet.channelTitle` |
| `live_broadcast_content` | `videoRenderer.badges[].metadataBadgeRenderer.label` (`LIVE`/`UPCOMING`) | `items[].snippet.liveBroadcastContent` |
| `is_live_now` | `liveBroadcastDetails.isLiveNow` (page) | `videos.list?part=liveStreamingDetails` → `liveStreamingDetails.concurrentViewers` present |
| `scheduled_start` | `liveBroadcastDetails.startTimestamp` | `liveStreamingDetails.scheduledStartTime` |
| `actual_start` / `actual_end` | `liveBroadcastDetails.startTimestamp` / page `actualEndTime` | `liveStreamingDetails.actualStartTime` / `actualEndTime` |
| `duration_seconds` | `videoRenderer.lengthText` (only present for VODs) | `videos.list?part=contentDetails` → `contentDetails.duration` |

`liveStreamingDetails` requires a **second** call (`videos.list`, 1 unit) — cheap
and worth it: `actualEndTime` is the single most reliable "this is over" signal.

## The gate (all must hold)

1. **URL shape** — `youtube.com/@handle/live`, `youtube.com/live/<id>`, or
   `youtube.com/watch?v=<id>`. `/channel/…`, `/user/…` (except `TheDBBTV`),
   `/c/…`, `/playlist`, `/results` → REJECT.
2. **Real live** — `live_broadcast_content ∈ {live, upcoming}` **or**
   `is_live_now` **or** `is_live_content`. A regular upload (`none`) → REJECT.
3. **Not over** — `actual_end` must be absent. An ended broadcast is an archived
   live even though `isLiveContent` is still true → REJECT.
4. **Not a recording** — a non-zero `duration_seconds` while not live now means
   a fixed-length upload → REJECT.
5. **Datetime > now** — for `upcoming` items `scheduled_start` must be strictly
   greater than now. Missing or past `scheduled_start` → REJECT.
   `is_live_now` items pass this gate because the broadcast is happening now
   (start ≤ now < end); an `is_live_now` item whose `actual_start` is in the
   future is an inconsistent payload → REJECT.

Decisions: `LIVE` (live right now) and `SCHEDULED` (starts in the future) are
promotable; `REJECT` is not.

## Channel allow-list

Only channels in `references/approved-sources.md` Tier 6 / `config/sources.json`:

```
youtube.com/@fiba           youtube.com/@bbl_basketball
youtube.com/@EuroLeague     youtube.com/@BasketballCL
youtube.com/user/TheDBBTV
```

A live broadcast on an unlisted channel is a new-source **candidate**
(`scripts/source_learning.py candidates`) — quarantined, never auto-promoted.

## Canonical queries

```
"<team1> vs <team2> live"         "BBL live heute"
"EuroLeague live"                 "Basketball Champions League live"
"FIBA Basketball live"            "ProA Basketball live stream"
"<club name> live"                "DBB live"
```

## Worked example — promote

```
candidate: {"url": "https://www.youtube.com/@EuroLeague/live",
            "live_broadcast_content": "upcoming",
            "scheduled_start": "2026-09-16T20:30:00+02:00"}
now:       2026-09-14T09:00:00Z
-> SCHEDULED: scheduled live broadcast starts 2026-09-16T20:30:00+02:00
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
            "actual_end": "2026-09-13T22:05:00Z"}
-> REJECT: broadcast already ended (2026-09-13T22:05:00+00:00) — archived live
```

```
candidate: {"url": "https://www.youtube.com/@fiba/live",
            "live_broadcast_content": "upcoming",
            "scheduled_start": "2026-09-14T07:00:00Z"}
now:       2026-09-14T09:00:00Z
-> REJECT: scheduled start 2026-09-14T07:00:00+00:00 is not greater than now
```
