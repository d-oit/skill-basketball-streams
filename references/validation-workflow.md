# Validation Workflow

Detailed step-by-step validation process for every potential stream.

## Overview

Every stream found must pass **all 7 checks** before a calendar event is created. Failing any single check results in immediate rejection.

Each check has an **id** in its heading below (`freeAccess`, `liveContent`, …).
That id is the token the runtime's graders match a transcript on, so when a
validation result is reported as `checks: <id>=PASS|FAIL`, the id names the
check — the prose titles and the log format's `Check N - Name` lines refer to
the same checks.

## Check 1: Free Access (check id: `freeAccess`)

**Purpose**: Ensure no payment is required.

**Pass criteria**:
- Page or description explicitly says "free", "kostenlos", "gratis"
- No mention of paywall, subscription, or payment
- No keywords: "Abo", "kostenpflichtig", "pay-per-view", "subscription"

**Fail criteria**:
- Mentions "Sky", "DAZN", "Prime" (unless Dyn Sport Mix free tier)
- Requires registration/payment before viewing
- Contains pricing information

## Check 2: Live Content (check id: `liveContent`)

**Purpose**: Ensure it is a live stream, not a replay or highlight reel.

**Pass criteria**:
- Contains: "live", "Live", "LIVE", "live stream", "live übertragung"
- For YouTube: `liveBroadcastContent` is `live`/`upcoming`, an active "LIVE"
  badge, or an upcoming live broadcast listed on the page
- Scheduled future event with live stream planned

**Fail criteria**:
- Contains: "highlights", "replay", "zusammenfassung", "on demand", "wiederholung"
- Past event without live component
- YouTube channel page with no active/scheduled live stream
- YouTube `liveBroadcastContent` is `none`, `actualEndTime` is set, or the item
  has a fixed `duration_seconds` while not live now → VOD, not a live stream
  (see `references/youtube-live-search.md`)

## Check 3: Official Source (check id: `officialSource`)

**Purpose**: Ensure the stream is from an approved official source.

**Pass criteria**:
- URL domain is in the approved sources list (see `references/approved-sources.md`)
- For YouTube: Channel is an official league, club, or federation channel

**Fail criteria**:
- Domain not in approved list
- `@FIBAWorld` — NOT a valid YouTube channel (only `@fiba` is official)
- Third-party aggregator or pirate site
- Unofficial fan channel

## Check 4: Basketball-Specific (check id: `basketballSpecific`)

**Purpose**: Prevent non-basketball content from being added.

**Pass criteria**:
- Mentions: basketball, BBL, EuroLeague, Basketball Champions League, FIBA
- Mentions specific BBL or EuroLeague team names

**Fail criteria**:
- Other sports: Fußball, Handball, Eishockey, Tennis, Volleyball
- Ambiguous content that could be another sport

## Check 5: Date/Time Within Range (check id: `dateTimeRange`)

**Purpose**: Only add streams within the next 7 days.

**Pass criteria**:
- Event date is between today (00:00 CET) and today + 7 days (23:59 CET)
- Kickoff time is reasonable for live sports (typically 14:00–23:00 CET)

**Fail criteria**:
- Event is in the past
- Event is more than 7 days in the future
- Date/time cannot be determined

## Check 6: Working Link (check id: `workingLink`)

**Purpose**: Verify the URL is accessible.

**Pass criteria**:
- HTTP 200 **with readable page content**
- Page loads without errors
- Not redirected to a 404, 500, or paywall page

**Fail criteria**:
- HTTP 404, 500, or other error
- URL redirects to a subscription/paywall page
- HTTP 200 but an empty/JS app shell (no readable body) → **UNVERIFIED**, not a pass
- For YouTube: `/user/` URLs (except `TheDBBTV`), `/channel/`, `/playlist`, `/results`, `/shorts` — always rejected

**Anti-bot statuses**: `401/403/429/451` mean *blocked*, not *broken*. Do not
reject the game — climb the fetch ladder in `references/magenta-tv.md` and retry
with a browser-rendering backend. Classify stored links with
`scripts/link_check.py` (`OK` / `BROKEN` / `BLOCKED` / `ERROR` / `UNREACHABLE` /
`INVALID`) and see `references/self-learning.md` → Link revalidation for the
quarantine cadence.

## Check 7: Direct Stream Verification (check id: `directStreamVerification`, CRITICAL)

**Purpose**: Confirm a live stream actually exists on the page.

**Pass criteria**:
- Page content contains active video player
- YouTube pages contain "Live" or "LIVE" text
- For `/live` URLs: page shows live or upcoming live stream
- For approved channels: scheduled live stream is listed

**Fail criteria**:
- Page is just a channel homepage with no live content
- No video player detected
- Only archived/past content visible
- Cannot read page content

## Decision Logic

```
Checks 1–7: ALL must PASS → Decision: CREATE calendar event
Any check FAILS:          → Decision: SKIP
                          → Log reason for rejection
                          → Continue to next stream
```

## Validation Log Format

```
Stream URL: [url]
Source: [where found]
Timestamp: [ISO timestamp]
Check 1 - Free Access: [PASS/FAIL] - [reason]
Check 2 - Live Content: [PASS/FAIL] - [reason]
Check 3 - Official Source: [PASS/FAIL] - [reason]
Check 4 - Basketball-Specific: [PASS/FAIL] - [reason]
Check 5 - Date/Time Range: [PASS/FAIL] - [reason]
Check 6 - Working Link: [PASS/FAIL] - [reason]
Check 7 - Direct Stream Verification: [PASS/FAIL] - [reason]
Final Decision: [CREATE/SKIP]
Calendar Event ID: [id or N/A]
```

## Special Cases

### Basketball Champions League (BCL) — Selected Games Only

`championsleague.basketball` is approved (Tier 1), but **free access is decided
per game**, not per source. Some BCL games are free, others are not.

**Mandatory triple check (all three, per game):**

1. `championsleague.basketball` — the game page itself
2. `x.com/BasketballCL` (official handle `@BasketballCL`)
3. `facebook.com/BasketballCL` (page: *Basketball Champions League*)

**Accept** only when at least one of the three carries the free marker for that
specific game (matching teams + date). **Reject** when:

- none of the three states free access (silence ≠ free),
- the BCL page only links out to a broadcaster without a free statement, or
- the free claim is for a *different* game that matchday.

Log which of the three sources carried the announcement in
`Validation Notes`. Social accounts are validation evidence only — never the
`directLink`.

### MagentaSport/MagentaTV — the free arena is magenta.tv

> **Changed 2026-09-29 (operator, then measured):** the free basketball game is
> **no longer announced on `magentasport.de`**. Free games now live on
> **`magenta.tv/sport`**, under **"KOSTENLOS & OHNE LOGIN: LIVE-EVENTS VON
> MAGENTA SPORT"** — *live events of Magenta Sport*. That page is **in no
> web-search index** (a dynamic SPA), so the free game is found by
> **rendering the page**, never by searching for it.
> 
> **The listings are EuroLeague basketball**, rendered 2026-09-29 as:
> `Panathinaikos AKTOR Athen - ASVEL Villeurbanne` (20:00), `FC Bayern München -
> Partizan Mozzart Bet Belgrad` (Fr. 02.10. 19:30), `Olympiakos Piräus -
> Anadolu Efes Istanbul` (Fr. 09.10. 20:00). Two traps the page sets: it never
> writes the word *basketball* — it writes *Euroleague* — and it never lists the
> BBL. So the league is read from each **entry**, and an absent string is not an
> absent game. Both were read backwards once during this change, which is why
> the rendered listing is recorded here rather than only its conclusion. The previous rule required
> an announcement on `magentasport.de` and rejected anything without one, which
> produced a **false rejection** on the first live run of that rule — see
> "What changed and why" below.

**Domain Separation (current):**
- **`magenta.tv/sport` = the free arena.** The "KOSTENLOS & OHNE LOGIN: LIVE-EVENTS
  VON MAGENTA SPORT" section lists the current **free EuroLeague basketball**
  games. This is the page to render.
- `magenta.tv/tv/live-[game-slug]/[dynamic-id]` = **the stream itself**, read
  from the rendered arena page. Dynamic and usually unindexed.
- `magentasport.de` = **secondary corroboration only.** It may still carry a
  schedule or a pointer, but a free game is **not** established by its absence
  there, and its silence is **not** evidence against free access.
- Official MagentaSport social accounts remain **corroboration**, never the
  stored `directLink`.

**Free Stream Policy:**
- One game per matchday is free. All other games require a MagentaSport
  subscription → REJECT.
- Free access is decided **per game**, never per platform.

**Mandatory Four-Step Strategy:**
1. **Render `magenta.tv/sport`** — the free arena. Read the games listed under
   **"KOSTENLOS & OHNE LOGIN: LIVE-EVENTS VON MAGENTA SPORT"**. This
   step **requires** a browser-rendering backend (Firecrawl / TinyFish Fetch /
   Playwright): measured 2026-09-29, a plain GET returns **858 bytes of app
   shell** containing no `kostenlos`, no game and no media token. The page is in
   **no web-search index**, so `webSearch` cannot substitute for it. Full fetch
   ladder, acceptance markers and worked logs: `references/magenta-tv.md`.
2. **Classify each listing as basketball by research, not by keyword.** The block
   mixes volleyball, football and darts, and the page never writes the word
   *basketball* — so search each entry (`"<teams>"` + competition) and accept
   only what research confirms: **EuroLeague, EuroCup, any national team, any
   league, any nation**. An absent string is never an absent game.
3. **Cross-reference** — the listed game must be **this** game. A `magenta.tv`
   stream URL for a game absent from the listing is **REJECT**; the platform
   existing says nothing.
4. **Corroboration (optional)** — `site:magentasport.de` and the official social
   accounts may confirm the same game. Their agreement raises confidence; their
   silence changes nothing.

**Free Stream Indicators (PASS):**
- `"kostenlos für alle"`
- `"ohne Abo"`
- `"ohne Login"`
- `"für alle zugänglich"`
- `"Jeden Spieltag eine Partie kostenlos"`

**Subscription/Paid Indicators (FAIL → REJECT):**
- `"mit MagentaSport Abo"`
- `"nur für Abonnenten"`
- `"Login erforderlich"`
- `"kostenpflichtig"`

**Validation Note for Check 1 (Free Access):**
Record **where the free indication was found** — `magenta.tv` is the primary
source, and `magentasport.de` / social are corroboration. If a `magenta.tv` page
renders and shows **no** free indication for that game, the stream is
subscription-gated and is **REJECTED** at Check 1. Document the discrepancy in the
validation log.

**What changed and why.** The old rule was *"a `magenta.tv` URL is only valid if
a free announcement exists on `magentasport.de`; no announcement = REJECT."* On
2026-09-29 that rule rejected two real EuroLeague games — *"the Magenta two-domain
rule requires a matching per-game free announcement — none found"* — because the
announcement had moved. A rule that rejects valid games is worse than no rule:
it is invisible, it looks like correct caution, and it silently shrinks coverage.
The new rule inverts where the evidence is looked for, while keeping the rule
that actually matters — **free access is decided per game, and a game with no
free indication is rejected.**

### Dyn Sport Mix

Dyn splits the same way Magenta does, and — like the BCL — the free decision is made **per game, not per platform**.

**Domain separation:**
- `dyn.sport` = **programme/announcement site.** `dyn.sport/deinsender/dyn-sport-mix/` publishes the official per-month list *"Dyn Free Spiele" / "Basketball Free Spiele"* — every game the free linear channel carries, with date, tip-off, league and pairing. That list is what proves **this** game is free.
- `joyn.de`, `pluto.tv`, `zattoo.com` = **free-tier platforms.** The linear channel *Dyn Sport Mix* plays there with no account and no Abo. The stored `directLink` comes from one of these.
- The `dyn.sport` player itself sits behind the **paid** Dyn service: a `dyn.sport` URL is announcement evidence and the `sourceReference`, never the direct link.

**Rules:**
1. **On the list → Check 1 PASS; not on the list → Check 1 FAIL**, even when the very same game is broadcast on Dyn elsewhere (`"Alles live und auf Abruf zu sehen bei Dyn Basketball"` describes the paid platform, not a free stream).
2. The list carries a **month** of games at a time; a game whose tip-off disagrees with the league's own schedule means the list is stale — record the discrepancy, do not guess.
3. Store the **indexed channel URL** (`https://pluto.tv/gsa/live-tv/<id>`). The obvious deep links `zattoo.com/de/live-tv/sender/dyn-sport-mix` and `joyn.de/sender/dyn-sport-mix` answer **404**: that rejects the *link*, not the game — quarantine the URL and keep the event (worked, 2026-09-26).

**Worked example (2026-09-26):** FC Bayern München vs ALBA BERLIN (26.09.2026, 15:40) is announced by Dyn as *"der erste Klassiker der diesjährigen easyCredit BBL-Saison"* yet is **absent** from the month's free list → Check 1 FAIL on Dyn; the game was created from its Sportschau/ARD livestream instead. EWE Baskets Oldenburg vs Veolia Towers Hamburg (27.09.2026, 16:30) and ALBA BERLIN vs BMA365 Bamberg Baskets (03.10.2026, 18:30) **are** on the list → Check 1 PASS with the Pluto TV channel as the link.

### YouTube Live Verification

- A channel URL alone (`youtube.com/@fiba`) does NOT pass Check 7
- Must be `youtube.com/@fiba/live` OR the channel must have an active/upcoming live stream listed
- Read the actual page to confirm "LIVE" badge or scheduled live event

### Duplicate Handling

If a stream is found from multiple sources (e.g., both Sportschau and MagentaSport cover the same game):
- Create ONE calendar event with BOTH stream links in the description
- Check for calendar duplicates before creating
