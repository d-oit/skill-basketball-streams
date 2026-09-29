# MagentaTV (`magenta.tv`) — Dynamic, Bot-Blocked Streaming Platform

Canonical playbook for the MagentaSport/MagentaTV rule
(see `references/validation-workflow.md` → *MagentaSport/MagentaTV*).
**`magenta.tv` announces *and* streams** — the free ("kostenlos") listing is
read off the rendered page itself. `magentasport.de` is corroboration only
(changed 2026-09-29, operator).

**The listings are EuroLeague basketball.** Rendered 2026-09-29, the section
"KOSTENLOS & OHNE LOGIN: LIVE-EVENTS VON MAGENTA SPORT" held:

```
Euroleague - LIVE: Panathinaikos AKTOR Athen - ASVEL Villeurbanne   20:00 - 23:05
Euroleague - LIVE: FC Bayern München - Partizan Mozzart Bet Belgrad  Fr. 02.10. 19:30
Euroleague - LIVE: Olympiakos Piräus - Anadolu Efes Istanbul       Fr. 09.10. 20:00
```

**The listing must be classified as basketball by research, not by keyword.**
The same block carries volleyball, football and darts, and the page never
writes the word *basketball* — so search each entry (`"<teams>"` + competition)
and accept only what research confirms: **EuroLeague, EuroCup, any national
team, any league, any nation**. The league comes from each **entry**, never from
the absence of a word: two traps here were read backwards while making this
change — "no BBL" read as "no basketball", when the page simply never labels
the sport. An absent string is not an absent game.

**A renderer is mandatory, and the hosted one fails here.** `patchright`
rendered it (10,085 chars; `KOSTENLOS` ×11, `OHNE LOGIN` ×10). Firecrawl with a
valid key returned `SCRAPE_ALL_ENGINES_FAILED` on the same URL — the page
blocks it — so a local rung is the working path, not the hosted one.

**Proven end to end, not just rendered.** Dispatch `36599529155` (2026-09-29)
planned both games as `VERIFIED` — `Panathinaikos AKTOR Athen vs ASVEL
Villeurbanne` (30.09. 20:00) and `FC Bayern München vs Partizan Mozzart Bet
Belgrad` (02.10. 19:30) — with `Bayern - Partizan` having been rejected by the
three previous runs as *"no per-game free indication found"*. The agent reached
the page with `webfetch`, so the renderer must be present in the job for this
to keep working: without it `webfetch` returns the 858-byte shell and the run
reverts to reporting a quiet day. Independently corroborated — the Seawolves'
own site states *"Das Spiel wird live auf MagentaSport übertragen"* for their
EuroCup game, which is exactly the corroboration the rule treats as optional and
never as a requirement.

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

## Cross-reference rule (Check 1 — **changed 2026-09-29, operator**)

The free-access indication now lives **here**, on `magenta.tv` — it is no longer
announced on `magentasport.de`:

```
https://www.magenta.tv/tv/live-basketball-euroleague-88213   # the game, rendered
```

Read the rendered page (Step 2.8 ladder — a plain GET returns the app shell) and
look for the per-matchday free indication:

PASS markers: `kostenlos für alle`, `ohne Abo`, `ohne Login`,
`für alle zugänglich`, `Jeden Spieltag eine Partie kostenlos`.
FAIL markers: `mit MagentaSport Abo`, `nur für Abonnenten`,
`Login erforderlich`, `kostenpflichtig`.

The indication must name **this** game. No indication for that game → treat as
subscription-gated → REJECT at Check 1, and log the discrepancy.

`magentasport.de` and the official social accounts (`x.com/MagentaSport`,
`facebook.com/MagentaSport`) are **corroboration only**. Their agreement raises
confidence; **their silence is not evidence against free access** and must never
trigger a rejection.

**Why this changed.** The old rule required an announcement on
`magentasport.de` and rejected without one. On 2026-09-29 that rule rejected two
real EuroLeague games — *"the Magenta two-domain rule requires a matching
per-game free announcement — none found"* — because the announcement had moved.
A rule that rejects valid games is worse than no rule: it looks like correct
caution and silently shrinks coverage.

## Worked validation log — magenta.tv accepted

```
Stream URL: https://www.magenta.tv/tv/live-basketball-euroleague-88213
Backend: firecrawl scrape (1 credit)
Source: the same rendered magenta.tv page (free indication lives there)
Timestamp: 2026-09-14T09:12:00Z
Check 1 - Free Access: PASS — "kostenlos für alle" naming THIS game on the
  rendered magenta.tv page; magentasport.de carried no announcement and that
  changed nothing.
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
| "magentasport.de is silent, so the game is paid" | Since 2026-09-29 `magentasport.de` is **corroboration only**. Silence is not evidence against free access; the indication is read off the rendered `magenta.tv` page. |
