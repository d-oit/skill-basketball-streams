# MagentaTV (`magenta.tv`) — Dynamic, Bot-Blocked Streaming Platform

Canonical playbook for the second half of the MagentaSport two-domain rule
(see `references/validation-workflow.md` → MagentaSport/MagentaTV Special Case).
`magentasport.de` announces; **`magenta.tv` streams**.

## Why a plain fetch fails

`https://www.magenta.tv/` is a JavaScript-rendered SPA. A plain HTTP GET returns
an app shell with **no readable text** — verified 2026-09-14:

```
$ openUrl https://www.magenta.tv/
errorMessage: "No readable text found at URL"
```

Consequences for the skill:

1. `webSearch` rarely indexes `magenta.tv/tv/live-*` URLs — they are per-matchday
   and dynamic (the `[dynamic-id]` segment changes per broadcast).
2. `openUrl` / `urllib` alone **cannot** satisfy Check 6 or Check 7 for a
   `magenta.tv` URL. A bare 200 on the shell is *not* a working stream.
3. Anything that looks like a bot (default `python-requests` UA, missing
   `Accept-Language`, no cookies) is served the shell — or a 403.

The shell is recorded byte for byte at
`tests/fixtures/pages/magenta-tv-shell.html` (942 bytes, **zero** media or
live-state markers). It is a fixture precisely because "HTTP 200 on the shell is
not evidence" needs to be a test, not a sentence.

## Fetch ladder (try in order, stop at the first that returns stream evidence)

Implemented by `scripts/render_ladder.py`; `--probe --expect-teams "A vs B"` walks it and prints
one line per rung with the reason each was unusable, then whether the page names the game.

| # | Backend | How | Cost |
|---|---------|-----|------|
| 0 | **plain stdlib GET** (`urllib`) | `render_ladder.py --probe`; last on this host because it cannot render the SPA | free, no key |
| 1 | **Firecrawl scrape** | `POST https://api.firecrawl.dev/v1/scrape` with `{"url": ..., "formats": ["markdown", "html"], "waitFor": 6000, "onlyMainContent": false}` | 1 credit (~1,000/month free, no card) |
| 2 | **TinyFish Fetch** | `GET https://api.tinyfish.ai/...` Fetch endpoint with the URL | Fetch is free (30 req/min) |
| 3 | **Exa `search_and_contents`** | semantic search for the game slug, returns page text | free-tier credits |
| 4 | **Tavily `extract`** | `POST https://api.tavily.com/extract` with the URL | 1,000 credits/month free |
| 5 | **Headless browser** | Playwright/Puppeteer (bundled in the agent runtime), wait for the player element, then read `document.body.innerText` | free, needs a browser sandbox |

If **every** rung fails, the URL is **UNVERIFIED** → no calendar event. Never
create an event from a search-result snippet alone.

Rung 0 exists so the ladder can always answer *something*: on this host it returns the app
shell, which proves the gate posture rather than evidence. Without it, a machine with no key and
no browser package reported "no rung produced" for every URL, which is indistinguishable from a
broken game.

**And the page must name the game.** A render can show a player and a live badge for a
different match; `anchor_game()` matches the expected clubs against the page's own title and
reports a multi-game title (`"A vs B | C vs D"`) as ambiguous instead of picking one. See
`tests/fixtures/pages/README.md`.

Backend selection and credentials: `references/search-backends.md`.

## What "stream evidence" means (Check 7 for magenta.tv)

A render is only accepted when the returned text/HTML contains **both**:

1. a **media** marker — an element or asset, not a word: `<video`, `.m3u8`,
   `.mpd`, `application/x-mpegurl`, `application/dash+xml`, `og:video`; and
2. a **live-state** marker — the German live badge `Jetzt live`, or a structured
   YouTube field (`"isLiveContent":true`, `"isLiveNow":true`,
   `"liveBroadcastContent":"live"`).

Plus the free-access marker from `magentasport.de` (Check 1 — see the
cross-reference rule below). Missing either marker → REJECT.

Implementation: `FetchResult.has_stream_evidence()` in `scripts/render_ladder.py`.
Media markers are matched against the document with its `<script>` blocks
**removed**; structured YouTube fields are matched against the raw document,
because that is the only place they exist.

**Why the vocabulary shrank (2026-09-15).** The earlier rule accepted bare words
(`player`, `dash`, `live`, `läuft`, `hls`, a play-button label). Those appear in
the page's *inline JS runtime*, which every modern page ships, so the gate said
"stream evidence" for pages that stream nothing. Verified against the recorded
corpus in [`tests/fixtures/pages/`](../tests/fixtures/pages/README.md): the old
rule accepted the YouTube **home page**, a **recorded YouTube video**, the
**MagentaSport announce home page** and the **Basketball Champions League home
page**. Under this rule all four are correctly rejected, and both recorded real
live pages still pass.

Do not widen these markers from memory. A new marker ships with a recorded page
that proves it discriminates — `python3 scripts/record_pages.py --refresh` —
otherwise it is a guess that the next bundle change invalidates.

**This gate ages, so it is re-checked against the live web weekly.**
`.github/workflows/corpus-refresh.yml` re-records every page and reports a page
whose *verdict* moved; a page whose bytes moved is silent, because the stored
YouTube extracts hash differently whenever the live page does. `lost-evidence`
means real streams stopped being recognised (missed games) and `gained-evidence`
means a page that streams nothing is accepted again — which is what this rule was
narrowed to prevent. The cause is one of two things, and a human decides: the
source changed (update `PAGES`/`expect`, re-record) or the gate broke (fix the
gate, leave the corpus). Never hand-edit a fixture to agree.

## URL shapes

```
https://www.magenta.tv/tv/live-<game-slug>-<dynamic-id>
https://www.magenta.tv/tv/live-<channel-slug>
https://www.magenta.tv/tv/<channel-slug>            # channel page, NOT a stream
https://www.magenta.tv/einstellungen/menu           # settings — never a stream
```

- `/tv/live-…` may be a stream → candidate.
- Any other `magenta.tv/tv/…` path is a channel/landing page → Check 7 FAIL.
- `?utm_*` / `#` fragments are stripped before storing the link in the event.

## Cross-reference rule (Check 1, unchanged and mandatory)

A `magenta.tv` URL is **only** valid when a matching official free-access
announcement exists on `magentasport.de` or official MagentaSport social media
(`x.com/MagentaSport`, `facebook.com/MagentaSport`):

PASS markers: `kostenlos für alle`, `ohne Abo`, `ohne Login`,
`für alle zugänglich`, `Jeden Spieltag eine Partie kostenlos`.
FAIL markers: `mit MagentaSport Abo`, `nur für Abonnenten`,
`Login erforderlich`, `kostenpflichtig`.

No announcement → treat as subscription-gated → REJECT at Check 1, and log the
discrepancy.

## Worked validation log — magenta.tv accepted

```
Stream URL: https://www.magenta.tv/tv/live-basketball-euroleague-88213
Backend: firecrawl scrape (1 credit)
Source: https://www.magentasport.de/live/basketball/euroleague
Timestamp: 2026-09-14T09:12:00Z
Check 1 - Free Access: PASS — "kostenlos für alle" on magentasport.de announcement
Check 2 - Live Content: PASS — "Jetzt live" present in rendered text
Check 3 - Official Source: PASS — magenta.tv in Tier 4
Check 4 - Basketball-Specific: PASS — EuroLeague, Real Madrid vs ALBA Berlin
Check 5 - Date/Time Range: PASS — 2026-09-16T20:30:00+02:00
Check 6 - Working Link: PASS — Firecrawl HTTP 200 with rendered body
Check 7 - Direct Stream Verification: PASS — <video> + "Jetzt live" markers present
Final Decision: CREATE
```

## Worked validation log — magenta.tv rejected (blocked + unverified)

```
Stream URL: https://www.magenta.tv/tv/live-basketball-euroleague-88214
Backend: openUrl (plain GET) -> app shell, no readable text
Check 6 - Working Link: FAIL — HTTP 200 but rendered body empty (SPA shell)
Check 7 - Direct Stream Verification: FAIL — no <video> / LIVE marker obtainable
Final Decision: SKIP
Reason: magenta.tv requires a browser-rendering backend; URL 200 on the shell is
not stream evidence.
```

## Rationalizations to refuse

| Excuse | Why it's wrong |
|---|---|
| "The URL returns 200, so it works" | The SPA shell always returns 200. Stream evidence must come from a rendered body. |
| "Firecrawl hit a 403, so the game isn't free" | 403 is anti-bot, not a paywall. Climb the fetch ladder before concluding anything. |
| "magentasport.de announced a free game, so every magenta.tv link today is free" | Only **one** game per matchday is free. Match the teams, not the date. |
