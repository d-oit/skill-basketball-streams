# Free Search Backends, Free LLM APIs, and the Google Calendar Path

The skill needs three capabilities that are all available at $0:

1. **Search** — find candidate URLs (`webSearch`).
2. **Fetch/render** — read a page, including JS-rendered, bot-blocked ones
   (`magenta.tv`). See `references/magenta-tv.md`.
3. **Decide** — apply the 7 checks and extract game details (an LLM step).

**An LLM API cannot search the web by itself.** OpenCode Zen (or any other free
LLM endpoint) replaces the *decide* layer only. The *search* and *render* layers
come from the backends below.

## Search backends (free tiers)

| Backend | Free allowance | JS render | MCP | Best for |
|---|---|---|---|---|
| **TinyFish** | Search + Fetch **free at any balance**; 5,000 req/month allowance, 30 req/min (Search), 150 URLs/min (Fetch) | Fetch yes; Browser tool for hard pages | — | Primary search: `after_date` / `before_date` / `recency_minutes` filters map directly onto the "datetime > now" gate |
| **Firecrawl** | 1,000 credits/month, no card; keyless mode (no API key) for search + scrape | **Yes** (`waitFor`, `onlyMainContent`) | `firecrawl-mcp` | Rendering `magenta.tv` and other SPAs; the paid-plan escape hatch for anti-bot pages |
| **Tavily** | 1,000 credits/month, no card | via `extract` | `tavily-mcp` | `/search`, `/extract`, `/crawl` for announcement pages |
| **Exa** | MCP server is **free, keyless, rate-limited** (`https://mcp.exa.ai/mcp`); sign-up adds $20 credits (~2,800 searches) | `search_and_contents` returns page text | `exa-mcp-server`; hosted `mcp.exa.ai/mcp` | Semantic discovery of unindexed dynamic slugs; the keyless rung is Phase 0's zero-secret default |
| **YouTube Data API v3** | 10,000 units/day; `search.list` = 100 units (~100 live searches/day) | n/a | — | `eventType=live` live-only search (see `references/youtube-live-search.md`) |
| **Composio (Google Calendar toolkit)** | Free Hobby tier, 100K tool calls/month | n/a | Composio's own MCP server | Reading and writing the events — no Google Cloud project, no service account |

Recommended default ladder: **TinyFish** (search) → **Firecrawl** (render) →
**Exa** (semantic fallback) → **Tavily** (extract fallback). If the agent runtime
already exposes `webSearch` + `openUrl`, use those first and treat the backends
above as the fallback rungs for dynamic/blocked pages.

### The keyless Exa MCP rung (Phase 0's zero-secret default)

`run_daily.py`'s search ladder is `exa-mcp` (paid, needs `EXA_API_KEY`) →
**`exa-mcp-keyless`** (free, no key) → `tinyfish` (needs `TINYFISH_API_KEY`). The
keyless rung speaks to the hosted MCP server at `https://mcp.exa.ai/mcp` — Exa
serves it rate-limited with no API key at all (their "Keyless" auth mode,
https://exa.ai/docs/get-started/exa-mcp). Consequences:

* The Phase 0 preflight (`run_daily.py --check-backends`) passes with **zero
  secrets**: a fresh fork runs Phase 0 on day one.
* A free-tier rate limit surfaces as HTTP 429 in the run's failure detail; the
  ladder moves on, and a paid `EXA_API_KEY` remains the upgrade path.
* The keyless rung steps aside entirely when `EXA_API_KEY` is set — same
  provider, so serving twice would only duplicate rows and burn limits.
* Transport: JSON-RPC over Streamable HTTP — `initialize`, echo the returned
  `mcp-session-id`, then `tools/call` for `web_search_exa`; the answer is SSE
  with `result.content[0].text` holding `Title:`/`URL:` records split by `---`.
  The parser is unit-tested against a recorded live response
  (`tests/fixtures/exa_mcp_keyless_search.sse`).

Ordering rationale: paid Exa outranks keyless Exa (same index, better limits),
and TinyFish stays last as the *independent* index — the rung that still works
when Exa-wide is having a bad day.

**Excluded by project policy: Brave Search API.** Do not use it as a search rung,
a fallback, or an MCP, and do not add a `--backend` entry for it. `run_daily.py`
has no Brave rung and takes no `BRAVE_API_KEY`; a proposal that reintroduces it is
a regression, not a coverage improvement.

### Credentials

```
TINYFISH_API_KEY   https://agent.tinyfish.ai/api-keys
FIRECRAWL_API_KEY  https://www.firecrawl.dev/           (keyless mode works without one)
TAVILY_API_KEY     https://www.tavily.com/
EXA_API_KEY        https://dashboard.exa.ai
YOUTUBE_API_KEY    https://console.cloud.google.com/   (enable YouTube Data API v3)
COMPOSIO_API_KEY   https://composio.dev/                (Google Calendar toolkit connected)
COMPOSIO_USER_ID   the Composio user whose connected account the call acts as
```

`COMPOSIO_USER_ID` is a scoping handle rather than a credential — but the workflows
still take it from `secrets`, because this repository is public and the handle
identifies an account. It is set **instead of** `COMPOSIO_CONNECTED_ACCOUNT_ID`,
never as well as: a `user_id` survives the account being reconnected, where a pinned
connected-account id does not, and a request carrying both is refused as ambiguous
about whose calendar a write belongs to. Both are read by `scripts/calendar_io.py`,
and neither is needed for a dry run.

TinyFish Search is a single GET:

```bash
curl "https://api.search.tinyfish.ai?query=Basketball+Champions+League+live+free&after_date=2026-09-14&language=de&location=DE" \
  -H "X-API-Key: $TINYFISH_API_KEY"
```

`after_date` must be `YYYY-MM-DD`; never combine it with `recency_minutes`.

Firecrawl scrape (rendering `magenta.tv`):

```bash
curl -X POST https://api.firecrawl.dev/v1/scrape \
  -H "Authorization: Bearer $FIRECRAWL_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.magenta.tv/tv/live-basketball-euroleague-88213",
       "formats":["markdown","html"],"waitFor":6000,"onlyMainContent":false}'
```

MCP wiring (any MCP-capable runtime):

```json
{
  "mcpServers": {
    "tinyfish":   { "command": "npx", "args": ["-y", "tinyfish-mcp"] },
    "firecrawl":  { "command": "npx", "args": ["-y", "firecrawl-mcp"],
                    "env": { "FIRECRAWL_API_KEY": "..." } },
    "tavily":     { "command": "npx", "args": ["-y", "tavily-mcp"],
                    "env": { "TAVILY_API_KEY": "..." } },
    "exa":        { "command": "npx", "args": ["-y", "exa-mcp-server"],
                    "env": { "EXA_API_KEY": "..." } }
  }
}
```

Verify package names against the vendor docs before installing — treat this
block as a template, not a pinned manifest.

## Free LLM APIs (the *decide* layer)

### OpenCode Zen — has genuinely free model IDs

Base URL `https://opencode.ai/zen/v1`, OpenAI-compatible
`/chat/completions`. Free IDs observed 2026-09-14 (`GET /v1/models` returns the
live list):

```
big-pickle
mimo-v2.5-free
ling-3.0-flash-fin-free
nemotron-3-ultra-free
nemotron-3.5-lightning-free
muse-spark-1.3-contributor-free
```

Conventions:

- Config model id format is `opencode/<model-id>` (e.g. `opencode/mimo-v2.5-free`).
- Only IDs suffixed `-free` (plus `big-pickle`) are $0. The rest of the Zen
  catalogue is pay-as-you-go and needs billing details on the Zen account.
- The free catalogue rotates — re-read `GET https://opencode.ai/zen/v1/models`
  before relying on a specific ID.

```bash
curl https://opencode.ai/zen/v1/chat/completions \
  -H "Authorization: Bearer $OPENCODE_ZEN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"big-pickle","messages":[
        {"role":"user","content":"Apply the 7 checks to this payload: ..."}]}'
```

### What the LLM is allowed to do

| Allowed | Forbidden |
|---|---|
| Normalize a raw search hit into the candidate schema | Invent a stream URL |
| Score the 7 checks and emit `Decision=CREATE\|SKIP` + reasons | Override a failed check because the game "looks free" |
| Draft the calendar description from verified fields | Decide that a `magenta.tv` URL is free without the `magentasport.de` announcement |

The deterministic gates that must **not** be delegated to an LLM:
`scripts/youtube_live.py` (live + future-only), `scripts/link_check.py`
(link validity), `scripts/color_mapping.py` (colour), the Check 1/2/3/5 keyword
sets, and duplicate detection.

## Google Calendar path

The target calendar (`config/calendar.json` →
`f8a14c4037d9ab411f93f19ee369218f0ed54be7c2d88deaf09d6b76fbe72e7f@group.calendar.google.com`)
is already configured; `scripts/calendar_config.py` reads it with the
`BASKETBALL_CALENDAR_ID` override, and `scripts/validate.py --check calendar-config`
keeps it well-formed.

**One way to write events, and it is not the Calendar API.** The credential is a
Composio API key for a connected Google account; there is no Google Cloud project, no
service account and no OAuth client to configure. Two interchangeable front doors onto
the same credential:

1. **MCP tools** (what `SKILL.md` names): `GOOGLECALENDAR_EVENTS_LIST` for the ±30 min
duplicate probe, then `GOOGLECALENDAR_CREATE_EVENT` with `summary`,
`start_datetime`, `end_datetime`, `calendar_id`, `timezone`, `description` — plus
`create_meeting_room: false` and `exclude_organizer: true`, because both default the
wrong way for a public streams calendar — and a follow-up
`GOOGLECALENDAR_PATCH_EVENT` for `color_id`, which `CREATE_EVENT` ignores.
2. **Tool execution over HTTP** (scripted/CI runs): `scripts/calendar_io.py`,
which POSTs to `/api/v3.1/tools/execute/{slug}` with an `x-api-key` header and
`{user_id, arguments, version}` in the body. It passes `version: latest`
deliberately: Composio records that older pinned toolkit versions can drop or remap
`timeMin`/`timeMax` before Google sees them, and a dropped window filter returns the
wrong window silently — which is how a duplicate event gets planned.

Scopes: the toolkit needs `https://www.googleapis.com/auth/calendar.events`. Under
that scope an *enumeration* of calendars can come back empty, which is harmless here
and worth knowing before it is mistaken for a broken credential: every call in this
repository names an explicit `calendarId` from `config/calendar.json`, and none of
them needs to enumerate anything.

Every created event description must keep the `SOURCE REFERENCE` block
(`Found at:`, `Validated:`, `Validation Notes:`) — see
`references/calendar-setup.md`.

## Cost guardrails

- Prefer the free rungs before spending credits; log the backend used per
  candidate in the validation log (and in `logs/run-log.jsonl`).
- One search call per tier, `limit: 20`, stop when a higher tier confirms the
  event (see the tier order in `references/approved-sources.md`).
- `scripts/source_learning.py score` surfaces sources that burn attempts without
  producing creates, so the tier order can be re-tuned.
