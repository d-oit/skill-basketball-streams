# Approved Streaming Sources

Only the sources listed here may be used. No exceptions.

## Source Confidence Tiers

Sources are organized into confidence tiers to prioritize search order. **Always search Tier 1 first, then Tier 2, then Tier 3.** This reduces wasted searches against low-hit-rate sources when a higher-confidence source already confirms the event.

### Tier 1: Official League/Federation Sites (Highest Confidence)
These are the primary, most reliable sources for official basketball content.

| Source | Type | Domains | Notes |
|--------|------|---------|-------|
| FIBA | Federation | `fiba.basketball`, `fiba.com` | Official international basketball federation |
| EuroLeague | League | `euroleaguebasketball.net` | Official EuroLeague site |
| Basketball Bundesliga (BBL) | League | `easycredit-bbl.de`, `api.basketball-bundesliga.de` | Official easyCredit BBL site. **Renamed** — `basketball-bundesliga.de` no longer serves it (TLS SNI failure, DNS still resolving) and `x.com/BBLofficial` is a 404. Re-verified 2026-09-17 via `scripts/link_inventory.py` + `scripts/link_check.py`. The schedule page at `/saison/spielplaene_liga-pokalspiele/hauptrunde` is client-rendered: no JSON-LD, no microdata, and the server-rendered list reads “Keine Spiele für diese Saison gefunden”, because fixtures come from `api.basketball-bundesliga.de` (401 without a credential) — so no plain GET of the page can read it. Verified 2026-09-30. **The same host serves the league’s own iCalendar feed without a credential**: `https://api.basketball-bundesliga.de/calendar/ical/all-games` answers HTTP 200, 90,568 bytes, 321 VEVENTs, and is what `scripts/fixtures.py` now reads (`kind = "ics"`). Its `SUMMARY` lines write the competition onto the first club name — measured across all 321 events: exactly two prefixes, `easyCredit BBL Spiel` (306) and `BBL Pokal Spiel` (15), all left-hand side — and every `DTSTART` is floating with the zone declared once as `X-WR-TIMEZONE:Europe/Berlin`. A trimmed verbatim capture is in `tests/fixtures/feeds/`. Verified 2026-09-30 |
| Basketball Champions League (BCL) | League | `championsleague.basketball` | Official BCL site. **Free for selected games only** — some games are free, others are not. Always check the site *and* the official social accounts (see BCL note below). |
| BKT EuroCup | League | `eurocupbasketball.com` | Official BKT EuroCup site. **Evidence and sourceReference only** — in Germany EuroCup games sit with paid/geo-restricted broadcasters, so this is never a `directLink`. Verified live 2026-10-05. |
| 2. Basketball Bundesliga (ProA/ProB) | League | `2basketballbundesliga.de` | Official German 2nd/3rd-tier site. **Streams are PAID** — verified 2026-10-05 from the league's own pass page: *"Die Spiele der 2. Basketball Bundesliga ProA kostenpflichtig auf Sporteurope.TV"*, Einzelspiel 5,99 € (PPV), Teampass 109,99 €, All-Access 189,99 €. Announcement/schedule evidence only, never a `directLink`. |
| Damen Basketball Bundesliga (1. DBBL) | League | `toyota-dbbl.de` | Official German women's top league. **PAID from 2026/27** — the DBBL announcement (verified 2026-10-05): 2025/26 ran free on the "Dyn Basketball" YouTube channel, *"Ab der Saison 2026/27 ist die Ausstrahlung aller Frauenbundesliga-Spiele auf der Streaming-Plattform Dyn Sport geplant"*, and no DBBL game is in the current month's Dyn Free Spiele list. Evidence only, never a `directLink`. |
| Regionalliga (league organisations) | League | `rln-basketball.de`, `regionalliga-suedost.de` | Official German 4th-tier league sites (RL Nord, RL Südost). **No centralised stream** — schedules/standings only; Sporteurope.TV has an RL Südost section whose pricing needs a rendering backend to read. A Regionalliga game is free only via the **club's own** official channel with a live/upcoming broadcast (Tier 6 rules); a league-site link is never a `directLink`. Verified 2026-10-05. |
| DBB (Deutscher Basketball Bund) | Federation | `basketball-bund.de` | German Basketball Federation |

### Tier 2: National Broadcasters (High Confidence)
National public broadcasters with dedicated sports coverage.

| Source | Type | Domains | Notes |
|--------|------|---------|-------|
| Sportschau (ARD) | National Public | `sportschau.de`, `ard.de`, `ardmediathek.de` | Free public broadcaster. Streams selected **BBL top games** free: a dedicated `sportschau.de/basketball/…,livestream-…html` page *and* the ARD Mediathek, announced days ahead (worked: FC Bayern München vs ALBA BERLIN, 26.09.2026 ab 15:30 Uhr im Ersten). Both answers are Check-1 free; `sportschau.de` is the stored link. |
| ZDF | National Public | `zdf.de`, `zdfmediathek.de` | Free public broadcaster |

### Tier 3: Regional Broadcasters (Medium Confidence)
Regional public broadcasters that occasionally cover basketball.

| Broadcaster | Domains | Notes |
|-------------|---------|-------|
| MDR | `mdr.de` | Regional public broadcaster |
| BR24 | `br.de`, `br24.de` | Bavarian public broadcaster |
| RBB24 | `rbb-online.de`, `rbb24.de` | Berlin-Brandenburg public broadcaster |

### Tier 4: Streaming Platforms (Medium-High Confidence)
Platforms that carry official free basketball streams.

| Platform | Domains | Notes |
|----------|---------|-------|
| Dyn Sport Mix | `dyn.sport` (announcement/programme), `joyn.de`, `pluto.tv`, `zattoo.com` (free-tier links), `amazon.de`, `primevideo.com` | Free tier only; NOT behind Prime paywall. **Free access is decided per game**, from the official monthly free-games list on `dyn.sport/deinsender/dyn-sport-mix/` — see "Dyn Sport Mix" in `references/validation-workflow.md`. `dyn.sport` is evidence and the `sourceReference`, never the `directLink`: its own player is behind the paid Dyn service. |
| MagentaSport / MagentaTV | `magenta.tv` (free-access evidence **and** stream), `magentasport.de` (corroboration only) | One free EuroLeague game per matchday, decided **per game**; the free indication is read off the rendered `magenta.tv` page, and `magentasport.de` silence is **not** evidence against free access (changed 2026-09-29, operator). `magenta.tv` is a JS-rendered, bot-blocked SPA — Checks 6/7 need a browser-rendering backend. See `references/magenta-tv.md` |

### Tier 5: Official Club Websites (Medium Confidence)
Official websites of BBL clubs that may host live streams.

| Club | Domain | Notes |
|------|--------|-------|
| ALBA BERLIN | `albaberlin.de` | ✅ Tier 5 |
| BMA365 Bamberg Baskets | `bamberg-baskets.de` | ✅ Tier 5 |
| Basketball Löwen Braunschweig | `basketball-braunschweig.de` | ✅ Tier 5 |
| EWE Baskets Oldenburg | `ewe-baskets.de` | ✅ Tier 5 |
| FC Bayern München Basketball | `fcbayern.com` | ✅ Tier 5 |
| FIT/One Würzburg Baskets | `wuerzburg-baskets.de` | ✅ Tier 5 |
| MHP RIESEN Ludwigsburg | `mhpriesen.de` | ✅ Tier 5 |
| MLP Academics Heidelberg | `academics-basketball.de` | ✅ Tier 5 |
| NINERS Chemnitz | `niners-chemnitz.de` | ✅ Tier 5 |
| RASTA Vechta | `rasta-vechta.de` | ✅ Tier 5 |
| ratiopharm ulm | `ratiopharm-ulm.de` | ✅ Tier 5 |
| ROSTOCK SEAWOLVES | `rostock-seawolves.de` | ✅ Tier 5 |
| Science City Jena | `sciencecitybasketball.de` | ✅ Tier 5 |
| SKYLINERS | `skygermany.de` | ✅ Tier 5 |
| SYNTAINICS MBC | `mitteldeutscherbbc.de` | ✅ Tier 5 |
| Telekom Baskets Bonn | `telekom-baskets.de` | ✅ Tier 5 |
| Veolia Towers Hamburg | `towers-hamburg.de` | ✅ Tier 5 |
| VET-CONCEPT Gladiators Trier | `gladiators-trier.de` | ✅ Tier 5 |

### Tier 6: Official YouTube Channels (Medium Confidence)
Official YouTube channels for leagues, federations, and clubs.

| Channel | Handle / URL | Notes |
|---------|--------------|-------|
| FIBA (official) | `youtube.com/@fiba` | ✅ ONLY valid FIBA handle |
| DBB - Deutscher Basketball Bund | `youtube.com/user/TheDBBTV` | ✅ Only accepted `/user/` URL |
| Basketball Bundesliga | `youtube.com/@basketballbundesliga` | ✅ Verified 2026-09-17 — channel "easyCredit Basketball Bundesliga". The old `@bbl_basketball` returns 404 |
| EuroLeague | `youtube.com/@EuroLeague` | Verify |
| ALBA Berlin | Via `albaberlin.de` social links | Verify |
| FC Bayern Basketball | Via `fcbayern.com` social links | Verify |

### Tier 7: European Domestic Leagues (evidence only — never a `directLink`)

Official top-division league sites, one per country, for fixture and announcement
evidence. **Their domestic streams are paid or geo-restricted in Germany**, so
none of these can ever be a `directLink`; a link with no explicit free marker
fails Check 1. Verified live 2026-10-05 — several answer 403/connection-reset to
datacenter IPs (`lnb.fr`, `bsl.org.tr`), the documented runner-IP class, so an
unreachable host is a transport fact, not evidence against a game.

| Country | League | Domains | Notes |
|---------|--------|---------|-------|
| Spain | Liga ACB (Endesa) | `acb.com` | Evidence only |
| Italy | LBA Serie A | `legabasket.it` | Evidence only |
| France | LNB Pro A | `lnb.fr` | Evidence only; WAF-blocked from datacenter IPs |
| Adriatic | ABA League | `aba-liga.com` | Evidence only |
| Greece | Stoiximan Basket League | `esake.gr` | Evidence only |
| Türkiye | BSL | `bsl.org.tr` | Evidence only; connection reset from datacenter IPs |
| Russia/East | VTB United League | `vtb-league.com` | Evidence only |
| Belgium/Netherlands | BNXT League | `bnxtleague.com` | Evidence only |
| Austria | win2day Basketball Superliga | `basketball.at` | Evidence only; free-to-air ORF listings are outside this registry's German scope |
| Switzerland | Swiss Basketball League | `swiss.basketball` | Evidence only |
| Poland | Orlen Basket Liga | `plk.pl` | Evidence only |

### Basketball Champions League (BCL) — Selected Games Only

`championsleague.basketball` is an approved Tier 1 source, but **free access is
per-game, not per-source**. Some BCL games are free, others require a
broadcaster subscription. Never treat "it is on the official BCL site" as free
access.

Mandatory triple check for every BCL game:

1. **Site** — `championsleague.basketball` game page for a free marker
   (`free`, `kostenlos`, `watch free`, `Live auf … kostenlos`).
2. **X/Twitter** — `x.com/BasketballCL` (official handle `@BasketballCL`) for the
   per-game free announcement.
3. **Facebook** — `facebook.com/BasketballCL` (page: *Basketball Champions
   League*) for the same announcement.

A BCL game is **only** accepted when at least one of the three shows the free
marker **for that specific game** (matching teams, matching date). A BCL page
that only links to official broadcast partners is a **link-out, not a stream** →
REJECT at Check 1 unless the partner stream itself independently passes all 7
checks.

## Social Media as Validation Sources (always check)

Official social accounts are **validation evidence for Check 1 (free access)**,
never standalone stream sources. A social post can confirm that a specific game
is free; it can never be the `directLink`.

| Account | Domain | Used to validate |
|---------|--------|------------------|
| `@BasketballCL` | `x.com/BasketballCL` | BCL free-game announcements (mandatory per game) |
| Basketball Champions League | `facebook.com/BasketballCL` | BCL free-game announcements (mandatory per game) |
| `@MagentaSport` | `x.com/MagentaSport` | MagentaSport free-game announcements — **corroboration only** since 2026-09-29 |
| MagentaSport | `facebook.com/MagentaSport` | MagentaSport free-game announcements — **corroboration only** since 2026-09-29 |
| `@EuroLeague` | `x.com/EuroLeague` | EuroLeague broadcaster/timing confirmations |
| `@easyCreditBBL` | `x.com/easyCreditBBL` | BBL schedule/broadcast confirmations. Verified 2026-09-17 and linked from the league's own homepage; `x.com/BBLofficial` is a 404 |
| Basketball Bundesliga | `facebook.com/BBLofficial` | **Unverifiable by probe**: facebook.com answers HTTP 400 to any non-browser user agent, so check by hand |

The machine-readable mirror of this table lives in `config/sources.json`.

## YouTube URL Rules (STRICT)

| Pattern | Status | Reason |
|---------|--------|--------|
| `youtube.com/@handle/live` | ✅ PREFERRED | Direct live URL |
| `youtube.com/live/<id>` | ✅ ACCEPTED | Live broadcast permalink |
| `youtube.com/watch?v=<id>` | ✅ ACCEPTED | Only when `liveBroadcastContent` is `live`/`upcoming` |
| `youtube.com/@handle` | ✅ ACCEPTED | Channel handle — needs a listed live/upcoming broadcast (Check 7) |
| `youtube.com/user/TheDBBTV` | ✅ ACCEPTED | Only approved legacy URL |
| `youtube.com/@FIBAWorld` | ❌ REJECTED | NOT a valid YouTube channel |
| `youtube.com/user/[other]` | ❌ REJECTED | Only TheDBBTV is approved |
| `youtube.com/channel/UC...` | ❌ REJECTED | Not direct live streams |
| `youtube.com/playlist?…`, `/results?…`, `/shorts/…` | ❌ REJECTED | Not direct live streams |

**Live-only + future-only:** a YouTube hit must be a real live broadcast whose
start datetime is greater than now (or be live at this moment), never a VOD,
replay, highlights reel, or ended broadcast. Search with the live filter
(`sp=EgJAAQ%3D%3D`) or the Data API's `eventType=live`, and gate every result
with `scripts/youtube_live.py`. Full contract:
`references/youtube-live-search.md`.

## Search Order Rationale

The tier system prioritizes sources based on:

1. **Authority**: Official league/federation sites (Tier 1) are the most authoritative
2. **Reliability**: National broadcasters (Tier 2) have high reliability for official content
3. **Coverage**: Regional broadcasters (Tier 3) cover specific regions
4. **Accessibility**: Streaming platforms (Tier 4) carry official content but may have access restrictions
5. **Specificity**: Club websites (Tier 5) provide team-specific content
6. **Verification**: YouTube channels (Tier 6) require careful URL validation

**Search Strategy**: When searching for streams, always follow the tier order. If a stream is confirmed from a Tier 1 source, there's no need to search lower tiers for the same event. This reduces API calls and improves efficiency.

## Explicitly Excluded Sources

| Source | Reason |
|--------|--------|
| Sky Sport | Paid subscription |
| DAZN | Paid subscription |
| Sport1+ | Paid |
| Amazon Prime (general) | Paid (Dyn Sport Mix free tier only) |
| Sporteurope.TV | **Paid** — ProA/ProB pay-per-view passes, verified 2026-10-05 from the league's own page (Einzelspiel 5,99 €, Teampass 109,99 €, All-Access 189,99 €) |
| Pirate/aggregator sites | Unofficial |
| Third-party stream sites | Unofficial |
| Social media accounts as stream sources | Validation only — never a `directLink` |
