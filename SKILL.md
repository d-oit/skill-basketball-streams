---
name: skill-basketball-streams
description: Search for FREE basketball live streams in Germany from approved official sources (BBL, EuroLeague, FIBA, Basketball Champions League, MagentaSport/MagentaTV, public broadcasters, clubs, YouTube channels), validate them with a 7-check pipeline (URL, page content, source allow-list, basketball-specific, date range, live proof, source reference), then add confirmed streams to Google Calendar. Renders the JS-rendered, bot-blocked magenta.tv via a fetch ladder, searches YouTube live-only under a strict start-datetime-greater-than-now gate, and revalidates stored links while discovering new sources through an append-only run log. Use when the user wants free basketball streams in Germany on a calendar, needs a stream URL validated, or reports broken links in past events. Triggers - "find basketball streams", "free BBL stream", "any free EuroLeague game today?", "validate this basketball URL", "add stream to basketball calendar". Not for paid broadcasters (Sky/DAZN/Prime), highlight reels, or non-basketball sports.
version: "1.2.0"
category: workflow
license: MIT
allowed-tools: webSearch openUrl webFetch firecrawlScrape tinyfishFetch youtubeLiveSearch linkCheck GOOGLECALENDAR_EVENTS_LIST GOOGLECALENDAR_CREATE_EVENT GOOGLECALENDAR_PATCH_EVENT
compatibility: Requires internet access (webSearch + openUrl HTTP probe + an optional browser-rendering backend for magenta.tv) and calendar access through **Composio tool execution** (`GOOGLECALENDAR_EVENTS_LIST` + `GOOGLECALENDAR_CREATE_EVENT`, credential `COMPOSIO_API_KEY`, account `COMPOSIO_USER_ID`). Timezone configurable via `config/calendar.json` (default: Europe/Berlin). Free search/LLM backends: see `references/search-backends.md`.
---

# Basketball Streams Finder & Calendar Manager

> **CRITICAL**: A previous incident created calendar events with broken YouTube links. Every event MUST pass all 7 checks — URL tested with `openUrl`, page content read, live stream verified, source reference recorded. See `references/validation-workflow.md` for the canonical 7-check pipeline and `references/lessons-learned.md` for the incident post-mortem.
>
> **Three hard gates that are easy to get wrong:** (1) `magenta.tv` is a JS-rendered, bot-blocked SPA — a 200 with no readable body is NOT a working stream (`references/magenta-tv.md`); (2) a YouTube hit must be a real live broadcast whose start datetime is **greater than now** — never a VOD, replay or ended stream (`references/youtube-live-search.md`); (3) Basketball Champions League games are free **per game, not per source** — always check the site plus `@BasketballCL` and the BCL Facebook page.

## Purpose

Search for FREE basketball live streams in Germany from official sources, validate them, and add confirmed streams to Google Calendar (Calendar ID configured in `config/calendar.json`, override via `BASKETBALL_CALENDAR_ID` environment variable).

## When to Use

- User requests **free** basketball live streams in Germany
- User wants to find official basketball broadcasts (BBL / EuroLeague / FIBA / club channels)
- User asks to update the basketball calendar with free streams
- User asks to validate a candidate basketball streaming URL
- User reports broken YouTube links in a previous run

## Approved Sources

Full list and YouTube URL rules: `references/approved-sources.md`; machine-readable mirror in `config/sources.json`. Summary: Dyn Sport Mix (Joyn / Pluto TV / Zattoo free tier only), MagentaSport / MagentaTV (one free EuroLeague game per matchday), Sportschau / ARD, regional broadcasters (MDR, BR24, RBB24), **Basketball Champions League (`championsleague.basketball`, selected games only)**, official BBL club websites, and official YouTube channels only. **Accepted YouTube URL patterns:** `youtube.com/@handle/live`, `youtube.com/live/<id>`, `youtube.com/watch?v=<id>` (live/upcoming only), `youtube.com/@handle`, `youtube.com/user/TheDBBTV`. **Rejected:** `youtube.com/@FIBAWorld`, any `youtube.com/user/[*]` except `TheDBBTV`, any `youtube.com/channel/UC…`, `/playlist`, `/results`, `/shorts`.

**Social media is validation evidence, never a source link.** `@BasketballCL` / `facebook.com/BasketballCL` (mandatory per BCL game), `@MagentaSport` / `facebook.com/MagentaSport`, `@EuroLeague`, `@BBLofficial` may confirm free access for Check 1; a social URL is never stored as the `directLink`.

> **IMPORTANT MAGENTA NOTE:** `magentasport.de` is the **content/announcement provider** (where free games are announced); `magenta.tv` is the **streaming platform** (where the actual live streams play). Free streams may appear on `magenta.tv` with dynamic URLs (e.g. `magenta.tv/tv/live-[game-slug]/[dynamic-id]`) that are not indexed by search engines. Always check **both domains** and verify free access via an official MagentaSport announcement on `magentasport.de` or official MagentaSport social media before treating any stream as free. **`magenta.tv` is a JavaScript-rendered SPA and blocks plain HTTP clients — a bare 200 returns an app shell with no readable text, so Checks 6 and 7 need a browser-rendering backend.** See `references/validation-workflow.md` → MagentaSport Special Case for the two-step search strategy and `references/magenta-tv.md` for the fetch ladder, URL shapes and worked validation logs.

## Process

### Step 1 — Determine Date Range

Today (00:00, timezone from `config/calendar.json` default `Europe/Berlin`) through today + 7 days (23:59). Convert times to ISO 8601 with `+02:00` (CEST) or `+01:00` (CET).

### Step 2 — Search for Streams

Run `webSearch` against each approved source with `limit: 20`, focusing on official domains. **Search in tier order (Tier 1 → Tier 6)** as defined in `references/approved-sources.md`. If a stream is confirmed from a higher tier, skip lower tiers for that same event to reduce redundant searches. Canonical query list in `references/implementation-notes.md`.

### Step 2.3 — MagentaSport/MagentaTV Special Handling

**CRITICAL:** MagentaSport and MagentaTV require a mandatory two-step search due to their split-domain structure:

1. **Domain Separation**: `magentasport.de` = Content/announcement site (where free games are announced); `magenta.tv` = Streaming platform (where the actual live streams play).
2. **Announcement Search**: Search `site:magentasport.de`, `site:facebook.com/magentasport`, and `site:twitter.com/MagentaSport` for keywords like `kostenlos` or `kostenlos für alle` to find the official free-game announcement.
3. **Stream Search**: Search `site:magenta.tv/tv/live*` for the matching game content. URLs are dynamic and often not indexed — cross-reference with the announcement.
4. **Free Stream Indicators**: Look for `"kostenlos für alle"`, `"ohne Abo"`, `"ohne Login"`, `"für alle zugänglich"`, `"Jeden Spieltag eine Partie kostenlos"`. **Reject** streams marked `"mit MagentaSport Abo"`, `"nur für Abonnenten"`, or `"Login erforderlich"`.
5. **Cross-reference Rule**: A `magenta.tv` stream URL is only valid if a matching official free-access announcement exists on `magentasport.de` or official MagentaSport social media. No announcement = REJECT.

### Step 2.5 — Validate Direct URLs

For every result, call `validateStreamUrl(url)` (TypeScript in `references/implementation-notes.md`). It rejects `@FIBAWorld`, every `/user/[name]` except `TheDBBTV`, every `/channel/UC…`, and any URL that fails an HTTP probe via `openUrl`. **Rejected URLs never reach Step 3.**

### Step 2.6 — Basketball Champions League (Selected Games Only)

`championsleague.basketball` is approved, but **free access is per game, not per source**. Some BCL games are free, others are not. Run the mandatory triple check for **every** BCL game:

1. Game page on `championsleague.basketball`
2. `x.com/BasketballCL` (official handle `@BasketballCL`)
3. `facebook.com/BasketballCL` (page: *Basketball Champions League*)

**Accept** only when at least one of the three states free access for that specific game (matching teams + date). **Reject** when none states it (silence ≠ free), when the BCL page only links out to a broadcaster, or when the free claim is for a different game that matchday. Record in `Validation Notes` which source carried the announcement. Social accounts are validation evidence only — never the `directLink`.

### Step 2.7 — YouTube Live-Only Search (real live, start datetime > now)

A YouTube hit is promotable only when it is a **real live broadcast** AND its start datetime is **greater than now** (or it is live at this moment with no end timestamp). Full contract: `references/youtube-live-search.md`. Gate every candidate with `scripts/youtube_live.py`.

1. **Search live-only** — either the HTML live filter `https://www.youtube.com/results?search_query=<q>&sp=EgJAAQ%3D%3D`, or the Data API `search.list` with `eventType=live&type=video` (free: 10,000 units/day, 100 units per search).
2. **Normalize** each hit into the candidate schema (field mapping in the reference).
3. **Gate** with `classify_stream`: `@handle/live`, `/live/<id>` or `/watch?v=<id>` URLs only; reject `liveBroadcastContent == none`, a set `actualEndTime` (ended broadcast → archived VOD), a fixed `duration_seconds` while not live now, and any `scheduled_start` that is not strictly greater than now.
4. Only `LIVE` and `SCHEDULED` decisions continue to Step 3. Never invent or reuse a `/live` URL from a finished broadcast.

### Step 2.8 — Fetch Ladder for Dynamic / Bot-Blocked Pages

`magenta.tv` is a JavaScript-rendered SPA that blocks plain HTTP clients: a 200 returns an app shell with **no readable text**. A bare 200 on the shell does **not** satisfy Checks 6 or 7. Climb the fetch ladder until the page yields stream evidence, and log which rung was used: Firecrawl scrape → TinyFish Fetch → Exa `search_and_contents` → Tavily `extract` → headless browser. Acceptance markers, URL shapes and worked logs: `references/magenta-tv.md`. Backend keys and MCP wiring: `references/search-backends.md`. If every rung fails → UNVERIFIED → no event. Never treat `401/403/429/451` as "broken": those mean *blocked*, so climb the ladder rather than rejecting the game.

### Step 2.9 — Revalidate Links, Log the Run, Discover New Sources

Self-learning is conservative: the skill never promotes a source or deletes an event by itself. See `references/self-learning.md`.

1. **Link revalidation** — `scripts/link_check.py` classifies stored links: `OK`, `BROKEN` (404/410 → quarantine the link), `BLOCKED` (401/403/429/451 → climb the fetch ladder, keep the event), `ERROR` (5xx → retry, do not delete), `UNREACHABLE`, `INVALID`. Revalidate daily for events inside today…today+7d and again ~2 h before start.
2. **Run log** — append one row per candidate with `scripts/source_learning.py record` (outcome `create|skip|reject|blocked|error`).
3. **Source scoring** — `scripts/source_learning.py score` reports per-source hit rate; use ≥10 attempts before re-ordering tiers.
4. **New-source discovery** — `scripts/source_learning.py candidates` writes unapproved domains as **quarantined**. Promotion requires a PR that adds the tier row, the `config/sources.json` entry, and one accept + one reject eval case. Until then a URL from that domain is rejected at Check 3.

### Step 3 — Run the 7-Check Pipeline

Full definitions in `references/validation-workflow.md`. **ANY failure → REJECT**, no calendar event:

1. **Free Access** — no `Abo`, `kostenpflichtig`, `pay-per-view`, or subscription. For BCL, a per-game free statement from Step 2.6.
2. **Live Content** — page contains `live`/`Live`/`LIVE`; no `highlights`/`replay`/`zusammenfassung`/`on demand`. YouTube items must pass the Step 2.7 live/`actualEndTime` rules.
3. **Official Source** — domain in approved list; quarantined domains fail.
4. **Basketball-Specific** — mentions BBL, EuroLeague, FIBA, Basketball Champions League, or approved club name
5. **Date/Time Within Range** — today…today+7d, 14:00–23:00 CET
6. **Working Link** — HTTP 200 **with readable page content** (not 404 / 500 / paywall redirect / empty SPA shell)
7. **Direct Stream Verification** — page content contains active video player and / or "Live" badge text; for `magenta.tv`, the Step 2.8 render markers

### Step 4 — Extract Game Details

For each surviving stream: league, teams, ISO 8601 date/time in CET, direct link, access requirements, `sourceReference` URL, validation timestamp.

### Step 5 — Check Google Calendar for Duplicates

**Dedupe before any write.** Call `GOOGLECALENDAR_EVENTS_LIST` on calendarId from `config/calendar.json` (or `BASKETBALL_CALENDAR_ID` env var) over the **whole window** `today 00:00 … today+7d 23:59`, then match in memory: same ±30-min slot **and** the team pair **and** a compatible league. Short and long club names are the same club (`ALBA` = `ALBA Berlin`, `Telekom Baskets Bonn` = `Bonn`); `scripts/upsert_events.py` implements this planner and prints `create`/`update`/`skip` rows **without touching the calendar**.

### Step 6 — Create or Update Event (Only If No Duplicate AND All 7 Checks Passed)

Every event carries one of **three verification states**, and the state — not a boolean — decides title and colour:

| State | Meaning | Title | `colorId` |
|---|---|---|---|
| `VERIFIED` | free access **and** a live stream both confirmed by ≥1 approved source and Check 7 | unchanged | league colour (`6`, or `2`/`11`) |
| `UNVERIFIED` | live stream plausible, free access **not** confirmed | `[UNVERIFIED] …` | `5` Banana (amber = caution) |
| `WRONG` | the audit proved it was paid, or never live | `[WRONG] …` | `7` Peacock |

An uncertain state **overrides** the league colour: a final whose free access is unconfirmed must not look like a confirmed final. `VERIFIED` keeps Tomato (`11`) for finals and Sage (`2`) for FIBA. `scripts/verification.py` is the single source of truth; `get_color_id(league, event_type, state)` adds the dimension.

Then `GOOGLECALENDAR_CREATE_EVENT` with required arguments (`summary`, `start_datetime`, `end_datetime`, `calendar_id`, `timezone="Europe/Berlin"`) and a `description` containing League / Teams / Date/Time / FREE STREAM LINKS / Access / SOURCE REFERENCE-with-ISO-timestamp / Validation Notes. Default duration 2.5h regular season / 3h playoff. Two arguments are mandatory because their defaults are wrong for a public streams calendar: `create_meeting_room: false` (otherwise a Google Meet link is added) and `exclude_organizer: true` (otherwise the connected user is added as an attendee). The colour is a **follow-up** `GOOGLECALENDAR_PATCH_EVENT`, because `CREATE_EVENT` ignores `color_id`. `scripts/calendar_io.py` is the reference implementation of all three calls. A re-run over the same window must create **zero** duplicates and leave `VERIFIED` events byte-identical. See `references/calendar-setup.md`.

### Step 6.5 — Post-hoc Audit and Promotion Sweep

Run `scripts/audit_events.py` over events that have **finished** since the last run. Verdicts: `VERIFIED` (free + live confirmed → colour 5→6, prefix dropped), `WRONG` (paid, or provably never live → `[WRONG]`, colour 7), `INCONCLUSIVE` (**untouched** — not finished yet, or evidence missing). Three rules:

- **Absence of evidence is not evidence.** Only an explicit `live_confirmed: false` on a *finished* broadcast is `WRONG`; a missing key is `INCONCLUSIVE`. Marking a game wrong because a scraper failed would poison the audit and then be "learned" from.
- **`WRONG` is terminal** — a later run never promotes it back.
- **Nothing is deleted.** A `WRONG` verdict relabels the event; it never removes it, so the audit trail survives.

### Step 7 — Output Results

Markdown table with columns `Status | League | Teams | Date/Time (CET) | Direct Link | Calendar Event`. Row formats: `created | BBL | ALBA Berlin vs Bayern | 2026-06-20 19:30 | [link] | [event link]` and `skipped | EuroLeague | Real Madrid vs Barcelona | 2026-06-21 20:00 | [link] | Event already exists: abc123`.

## Important Constraints

1. **Only approved sources** — no exceptions, even if a search result looks official.
2. **All 7 checks must PASS** — including Direct Stream Verification (page read).
3. **No paid content** — Sky / DAZN / general Prime → REJECT, even if domain is on the allow-list.
4. **No unofficial streams** — no pirate sites, third-party aggregators, fan-run YouTube.
5. **Date accuracy** — every event must fall in today…today+7d.
6. **Link testing** — every direct link tested with `openUrl` and confirmed working.
7. **Live verification** — read page content to confirm an active live stream exists.
8. **YouTube URL rules** — only `@handle`, `@handle/live`, and `/user/TheDBBTV` are accepted.
9. **Include references** — every event description must contain `sourceReference` and validation timestamp.
10. **No event without a live stream** — Check 7 must pass; if the live content itself is unverified there is no event. Free access that is merely *unconfirmed* is different: it is still created, but labelled `[UNVERIFIED]` (colour `5`) instead of being rejected.
11. **Magenta two-domain rule** — a `magenta.tv` stream URL requires a matching official free-access announcement on `magentasport.de` or MagentaSport social media; without it the game may only be created as `UNVERIFIED`, never as `VERIFIED`.
12. **Magenta render rule** — a `magenta.tv` URL only counts as *working* after a browser-rendering backend (Step 2.8) returns player + live markers. A plain GET returning 200 on the app shell fails Check 6/7 and creates no event.
13. **BCL per-game rule** — Basketball Champions League free access is decided per game via site + `@BasketballCL` + BCL Facebook; silence is not consent.
14. **YouTube live-only rule** — only real live broadcasts with `scheduled_start` strictly greater than now (or a live-now stream with no `actualEndTime`) may be promoted. VODs, recordings, replays and ended broadcasts never are.
15. **Social media is evidence, not a source** — official social accounts may satisfy Check 1; they are never stored as the `directLink`.
16. **Quarantine, never delete** — a failed revalidation removes the dead link and records the reason in `Validation Notes`; calendar events are never deleted on a link failure, and new sources stay quarantined until a human promotes them.
17. **Verification-state rule** — every event carries `VERIFIED`/`UNVERIFIED`/`WRONG`; title prefix and colour come from `scripts/verification.py`, never from ad-hoc formatting.
18. **Dedupe-before-write rule** — `GOOGLECALENDAR_EVENTS_LIST` over the full window runs *before* any write, and a `VERIFIED` event is never modified by a later run.
19. **Audit-only demotion** — only `scripts/audit_events.py` may set `WRONG`, and it relabels; it never deletes an event.

## Output Format

Always emit the Step 7 table. On total failure return `"Found [N] potential streams, but none passed validation."`. On zero hits: `"No free basketball live streams found in the next 7 days from official sources."`. On tool errors: log and continue with the next source.

## Available scripts

All scripts are stdlib-only on Python 3.8+ and share the same exit codes: `0` PASS, `1` FAIL, `2` USAGE. `OK: …` goes to stdout, `FAIL: …` to stderr.

- **`scripts/validate.py`** — self-contained validator. `python3 scripts/validate.py --root .` runs every check: `--check {all,evals,skill,references,calendar-config,smoke-test}`. Wire into CI so a failing validation never lands in the basketball calendar.
- **`scripts/youtube_live.py`** — the live-only, future-only gate (Step 2.7). `--input candidates.json [--now ISO] [--json]` classifies candidates as `LIVE`/`SCHEDULED`/`REJECT`; `--print-urls QUERY` emits the `sp=EgJAAQ%3D%3D` HTML URL and the `eventType=live` API URL. Exits `1` when nothing is promotable.
- **`scripts/link_check.py`** — revalidates stored links (Step 2.9). `--input links.json [--dry-run] [--out report.json]`; `--dry-run` makes no network requests. Classifies `OK`/`BROKEN`/`BLOCKED`/`ERROR`/`UNREACHABLE`/`INVALID`.
- **`scripts/source_learning.py`** — self-learning (Step 2.9). `record` appends a JSONL run-log row, `score` reports per-source hit rates, `candidates` quarantines newly discovered domains into `logs/source-candidates.json`. Never promotes a source.
- **`scripts/calendar_config.py`** — reads `config/calendar.json` with the `BASKETBALL_CALENDAR_ID` override.
- **`scripts/color_mapping.py`** — `get_color_id(league, event_type, state)` for the calendar `colorId`. The league and verification-state dimensions are orthogonal.
- **`scripts/verification.py`** — the verification state machine (`VERIFIED`/`UNVERIFIED`/`WRONG`): prefixes, colour precedence, idempotent `title_for`.
- **`scripts/upsert_events.py`** — dedupe-then-replace planner (Step 5/6). `--existing existing.json --candidates candidates.json [--json]` prints the plan; it never calls the calendar API.
- **`scripts/audit_events.py`** — post-hoc verdicts and the promotion sweep (Step 6.5). `--events events.json --evidence evidence.json [--now ISO] [--out audit.jsonl]`; `--events` accepts JSONL as well as JSON.
- **`scripts/extract_candidates.py`** — the transcript → candidate seam for Steps 5/6. The runtime agent has no file-write tool, so this is its only channel; it refuses `state=WRONG` (an audit verdict, never a proposal) and any candidate without two teams and a parseable start.
- **`scripts/calendar_io.py`** — the only module that reads or writes the calendar, through Composio tool execution (`COMPOSIO_API_KEY`; no Google project). `list` recovers the verification state from the title prefix, which is what lets Step 6 honour "never touch a verified event"; `apply` makes **no HTTP request at all** unless `--live` is passed.
- **`scripts/fixtures.py`** — official league fixtures → `game_key`s, so recall can be measured against something other than our own search results. JSON-LD first, microdata fallback, and a fixture that fails to parse is dropped, never guessed.
- **`scripts/synthesise_eval_case.py`** — audit verdict → regression eval case (Step 6.5 feeds it). `--verify` asserts every assertion needle resolves, so no case can be filed that grades nothing.
- **`scripts/bump_version.py`** — keeps `SKILL.md` and `CHANGELOG.md` versions in sync.

## Rationalizations

| Excuse | Why it's wrong |
|---|---|
| "The URL came from a search result so it's safe" | Even official searches return `/user/FIBA` and `/channel/UC…` 404s; every URL must clear `validateStreamUrl` first. |
| "Domain matches the allow-list, no need to test" | Allow-list match can't detect a paywall inside the page; `openUrl` + page read is required. |
| "Page returned HTTP 200, so it's live" | A 200 can be a channel homepage with no active stream; Check 7 requires reading the body. |
| "Highlights are 'live content' enough" | Replays and highlight reels explicitly FAIL Check 2 (`highlights`, `replay`, `zusammenfassung`). |
| "The user can filter Sky / DAZN themselves" | Rejecting paid broadcasters is the skill's contract; don't pass the decision upstream. |
| "The magenta.tv URL looks valid so no announcement needed" | `magenta.tv` streams require a matching official free-access announcement on `magentasport.de`; URL alone is never sufficient. |
| "The magenta.tv URL returns 200, so it works" | The SPA shell always returns 200 with no readable body. Stream evidence must come from a rendered page (Step 2.8). |
| "Firecrawl hit a 403 so the game isn't free" | 403 is anti-bot, not a paywall. Climb the fetch ladder before concluding anything. |
| "The BCL site lists the game, so it's free" | BCL free access is per game. Silence on the site, X and Facebook means NOT free. |
| "It's a YouTube live URL, so it's a live stream" | `/live` URLs and `isLiveContent` survive after a broadcast ends. Without an `actualEndTime` check you would ship a link to a finished game. |
| "The highlights video is live enough" | Rejected at Check 2: `liveBroadcastContent == none` or a fixed duration means a recording. |

## Red Flags

- [ ] Skipped `validateStreamUrl` before promoting a search result to Step 3.
- [ ] Accepted any `/user/` YouTube URL other than `/user/TheDBBTV`.
- [ ] Accepted `youtube.com/@FIBAWorld/...` or any `/channel/UC…` URL.
- [ ] Skipped Check 7 (Direct Stream Verification) because the URL "looks live".
- [ ] Created an event without a `sourceReference` and validation timestamp in the description.
- [ ] Used a date outside the today…today+7d window.
- [ ] Added an event without running `GOOGLECALENDAR_EVENTS_LIST` for duplicates first.
- [ ] Logged a `CREATE` decision when any of the 7 checks were FAIL.
- [ ] Used a `magenta.tv` stream URL without first finding a matching free-access announcement on `magentasport.de`.
- [ ] Searched only `magentasport.de` and skipped `magenta.tv` for the actual stream URL (or vice versa).
- [ ] Accepted a `magenta.tv` link on an HTTP 200 without a rendered body (Check 6/7 passed on the SPA shell).
- [ ] Promoted a YouTube item whose `scheduled_start` is not greater than now, or whose `actualEndTime` is set.
- [ ] Treated a YouTube VOD (`liveBroadcastContent == none` or a fixed duration) as a live stream.
- [ ] Accepted a BCL game without finding a per-game free statement on the site, `@BasketballCL` or the BCL Facebook page.
- [ ] Stored a social media URL as the `directLink`.
- [ ] Created an event for an unconfirmed-free game **without** the `[UNVERIFIED]` prefix and colour `5`.
- [ ] Let an uncertain game keep the league colour (Tomato/Sage) instead of the amber caution colour.
- [ ] Marked a game `WRONG` because a scraper failed or evidence was missing, instead of `INCONCLUSIVE`.
- [ ] Promoted a `WRONG` event back to `VERIFIED`, or deleted an event instead of relabelling it.
- [ ] Wrote to the calendar before listing existing events for the whole window.
- [ ] Deleted a calendar event because a link failed revalidation instead of quarantining the link.

## References

- `references/approved-sources.md` — full approved source list and YouTube URL allow/reject table
- `references/validation-workflow.md` — 7-check pipeline, decision logic, special cases (MagentaSport two-domain rule + 1-game rule, Dyn Sport Mix free tier, YouTube live-URL-only, duplicate handling)
- `references/calendar-setup.md` — Google Calendar event schema, color codes, duplicate detection parameters, time-handling rules
- `references/implementation-notes.md` — query templates, time-handling, team-name variants, exclusion vocabulary, full `validateStreamUrl` implementation and end-to-end workflow TypeScript
- `references/magenta-tv.md` — magenta.tv fetch ladder, URL shapes, acceptance markers, worked validation logs
- `references/youtube-live-search.md` — live-only/future-only gate, search entry points, payload field mapping, worked examples
- `references/search-backends.md` — free search APIs and MCPs (TinyFish, Firecrawl, Tavily, Exa; Brave is excluded by policy), free LLM IDs (OpenCode Zen), Google Calendar implementation, cost guardrails
- `references/self-learning.md` — run log, source scoring, quarantine-and-promote process, link-revalidation cadence, eval feedback loop
- `docs/runtime.md` — daily runtime operations: cadence, secrets, telemetry schema, rung health, recovery runbook, AGPL notice
- `references/lessons-learned.md` — incident post-mortem (broken `/user/FIBA` events), root causes, prevention checklist, validation log template and worked examples
- `config/sources.json` — machine-readable approved-source registry + excluded sources
- `evals/evals.json` — 36 eval cases (driving the `skill-evaluator` rubric; see `README.md` → Self-Validation)
