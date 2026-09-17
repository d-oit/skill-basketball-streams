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
| Basketball Bundesliga (BBL) | League | `basketball-bundesliga.de` | Official BBL site |
| Basketball Champions League (BCL) | League | `championsleague.basketball` | Official BCL site. **Free for selected games only** — some games are free, others are not. Always check the site *and* the official social accounts (see BCL note below). |
| DBB (Deutscher Basketball Bund) | Federation | `basketball-bund.de` | German Basketball Federation |

### Tier 2: National Broadcasters (High Confidence)
National public broadcasters with dedicated sports coverage.

| Source | Type | Domains | Notes |
|--------|------|---------|-------|
| Sportschau (ARD) | National Public | `sportschau.de`, `ard.de`, `ardmediathek.de` | Free public broadcaster |
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
| Dyn Sport Mix | `amazon.de`, `primevideo.com`, `joyn.de`, `pluto.tv`, `zattoo.com` | Free tier only; NOT behind Prime paywall |
| MagentaSport / MagentaTV | `magentasport.de`, `magenta.tv` | One free EuroLeague game per matchday; requires two-domain verification. `magenta.tv` is a JS-rendered, bot-blocked SPA — see `references/magenta-tv.md` |

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
| Basketball Bundesliga | `youtube.com/@bbl_basketball` | Verify |
| EuroLeague | `youtube.com/@EuroLeague` | Verify |
| ALBA Berlin | Via `albaberlin.de` social links | Verify |
| FC Bayern Basketball | Via `fcbayern.com` social links | Verify |

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
| `@MagentaSport` | `x.com/MagentaSport` | MagentaSport free-game announcements |
| MagentaSport | `facebook.com/MagentaSport` | MagentaSport free-game announcements |
| `@EuroLeague` | `x.com/EuroLeague` | EuroLeague broadcaster/timing confirmations |
| `@BBLofficial` | `x.com/BBLofficial` | BBL schedule/broadcast confirmations |

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
| Pirate/aggregator sites | Unofficial |
| Third-party stream sites | Unofficial |
| Social media accounts as stream sources | Validation only — never a `directLink` |
