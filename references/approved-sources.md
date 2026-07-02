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
| MagentaSport | `magentasport.de`, `magenta.tv` | One free EuroLeague game per matchday; requires two-domain verification |

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

## YouTube URL Rules (STRICT)

| Pattern | Status | Reason |
|---------|--------|--------|
| `youtube.com/@handle` | ✅ ACCEPTED | Modern handle format |
| `youtube.com/@handle/live` | ✅ PREFERRED | Direct live URL |
| `youtube.com/user/TheDBBTV` | ✅ ACCEPTED | Only approved legacy URL |
| `youtube.com/@FIBAWorld` | ❌ REJECTED | NOT a valid YouTube channel |
| `youtube.com/user/FIBA` | ❌ REJECTED | Deprecated/broken |
| `youtube.com/user/[other]` | ❌ REJECTED | Only TheDBBTV is approved |
| `youtube.com/channel/UC...` | ❌ REJECTED | Not direct live streams |

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
