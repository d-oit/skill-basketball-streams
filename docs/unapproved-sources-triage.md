# Unapproved source triage (2026-11-27)

**Advisory snapshot, not a policy.** It groups the hosts `telemetry_db.py report`
calls unapproved and proposes approve-or-retire for each group. Nothing here
changes `config/sources.json`: retirement still means an `excluded` entry or a
`NON_SOURCE_DOMAINS` addition in a reviewed change, and approval still needs the
normal free-access + live-broadcast evidence.

Reproduce it from a telemetry checkout (the worktree holding the committed
`run-log.jsonl`):

```
python3 scripts/telemetry_db.py build --ledgers <telemetry-dir> --db .tmp/real.db \
    --sources config/sources.json
python3 scripts/telemetry_db.py report --db .tmp/real.db --min-attempts 10 \
    --out .tmp/report.json
```

then group `report.json`'s `unapproved_sources` by hostname family. The groups
are name-based heuristics: the "official basketball" shortlist still needs a
per-host free-access check, and the unclassified remainder needs a human. Re-run
before acting; the ledger grows every day.

## Snapshot: 2026-11-27

Derived from `telemetry_db.py report` over the committed 142,290-row run log (`config/sources.json` applied). **505 unapproved hosts, 78,124 attempts, 0 creates** — none of them has ever put a game on the calendar, so retiring costs no recall and approving still requires the normal free+live evidence path.

| Group | Hosts | Attempts | Proposal |
| --- | ---: | ---: | --- |
| retire: TV guide / EPG | 14 | 14,460 | Retire: "where to watch" pages, not broadcasts. |
| retire: betting / tips | 8 | 3,710 | Retire: never a stream; add to `NON_SOURCE_DOMAINS`. |
| retire: news / portal | 32 | 13,854 | Retire: articles about games, not games. |
| retire: paid platform | 12 | 9,405 | Retire: add to `excluded[].domains` with the pricing evidence. |
| retire: social / app store | 9 | 2,958 | Retire: plumbing/app store/social, never a stream. |
| retire: stats / results | 10 | 2,819 | Retire: fixture/score evidence at most, never a directLink. |
| review: official basketball | 74 | 9,288 | Review: plausible federation/club/broadcaster hosts — verify free access before approving (Tier 1/3/5). |
| review: other (unclassified) | 346 | 21,630 | Review: classify by hand; anything paid or plumbing retires. |

## Review shortlist (official basketball, by attempts)

- `bblprofis.de` — 1775 attempts, 1775 skips
- `about.fiba.basketball` — 968 attempts, 968 skips
- `web.basketball.scb.world` — 649 attempts, 649 skips
- `nbbl-basketball.de` — 633 attempts, 633 skips
- `basketballengland.co.uk` — 401 attempts, 401 skips
- `telekom-baskets-bonn.de` — 367 attempts, 367 skips
- `usbasket.com` — 308 attempts, 308 skips
- `tiempodebasket.com` — 275 attempts, 275 skips
- `obasketu.cz` — 274 attempts, 274 skips
- `belarus.basketball` — 261 attempts, 261 skips
- `live.2basketballbundesliga.de` — 233 attempts, 233 skips
- `basketnews.com` — 224 attempts, 224 skips
- `wheretowatchbasketball.com` — 224 attempts, 224 skips
- `nbl.basketball` — 209 attempts, 209 skips
- `eagles-basketball.de` — 178 attempts, 178 skips
- `ts-jahn-basketball.de` — 168 attempts, 168 skips
- `nznbl.basketball` — 150 attempts, 150 skips
- `probasket.asport.tv` — 145 attempts, 145 skips
- `euroleague.pro` — 130 attempts, 130 skips
- `basketball.usbasket.com` — 120 attempts, 120 skips
- `basketballireland.tv` — 120 attempts, 120 skips
- `basketball-bargteheide.de` — 115 attempts, 115 skips
- `basketballontv.com` — 113 attempts, 113 skips
- `sportando.basketball` — 110 attempts, 110 skips
- `courtside1891.basketball` — 106 attempts, 106 skips
- `basketballaustria.tv` — 92 attempts, 92 skips
- `ttl-basketball-bamberg.de` — 81 attempts, 81 skips
- `nobackboardbasketballgym.com` — 79 attempts, 79 skips
- `live.basketball-bundesliga.de` — 75 attempts, 75 skips
- `id.euroleague.net` — 65 attempts, 65 skips

## Retire lists

### retire: TV guide / EPG (14)

- `netzwelt.de` (2376 attempts)
- `sports.justwatch.com` (2366 attempts)
- `dslweb.de` (2181 attempts)
- `basketballimtv.de` (1606 attempts)
- `liveimtv.de` (1386 attempts)
- `livesportsontv.com` (1262 attempts)
- `astra.de` (621 attempts)
- `calovo.de` (589 attempts)
- `sport-tv-guide.live` (459 attempts)
- `gestreamt.de` (458 attempts)
- `dyn-sport-live-auf-abruf.en.softonic.com` (323 attempts)
- `calovo.com` (319 attempts)
- `digitalfernsehen.de` (289 attempts)
- `wowi.astra.de` (225 attempts)

### retire: betting / tips (8)

- `tipico.de` (1188 attempts)
- `sportsgambler.com` (909 attempts)
- `freetips.com` (623 attempts)
- `wetten.com` (389 attempts)
- `leon.bet` (270 attempts)
- `1xbet.com` (175 attempts)
- `legalbet.es` (86 attempts)
- `wettfreunde.net` (70 attempts)

### retire: news / portal (32)

- `news.de` (2178 attempts)
- `basketball-world.news` (2037 attempts)
- `sportbild.bild.de` (1524 attempts)
- `sport.de` (1406 attempts)
- `presseportal.de` (1165 attempts)
- `swp.de` (839 attempts)
- `ruhrnachrichten.de` (829 attempts)
- `basketballsphere.com` (727 attempts)
- `eurosport.de` (513 attempts)
- `sportpresseportal.de` (511 attempts)
- `squawka.com` (446 attempts)
- `bild.de` (389 attempts)
- `cms.magentasport.de` (381 attempts)
- `sports.yahoo.com` (275 attempts)
- `t-online.de` (229 attempts)
- `rnd.de` (135 attempts)
- `live-sport-online.com` (50 attempts)
- `sport1.de` (43 attempts)
- `ws05.news.de` (40 attempts)
- `tagesschau.de` (25 attempts)
- `hockeyweb.de` (20 attempts)
- `sueddeutsche.de` (16 attempts)
- `merkur.de` (15 attempts)
- `topfreizeit.de` (15 attempts)
- `sportdaten.wr.de` (10 attempts)
- `swr.de` (6 attempts)
- `freiepresse.de` (5 attempts)
- `liveticker.abendblatt.de` (5 attempts)
- `sport.bild.de` (5 attempts)
- `sportdaten.ikz-online.de` (5 attempts)
- `sportdaten.waz.de` (5 attempts)
- `welt.de` (5 attempts)

### retire: paid platform (12)

- `fubo.tv` (2720 attempts)
- `telekom.com` (1153 attempts)
- `telekom.de` (885 attempts)
- `fibatv.com` (841 attempts)
- `tv.youtube.com` (782 attempts)
- `euroleague.tv` (677 attempts)
- `dosbnewmedia.de` (617 attempts)
- `de2.sportplus.tv` (583 attempts)
- `flohoops.com` (578 attempts)
- `espn.com` (347 attempts)
- `secure.fibatv.com` (123 attempts)
- `tur.sportplus.tv` (99 attempts)

### retire: social / app store (9)

- `spox.com` (726 attempts)
- `twitch.tv` (652 attempts)
- `apps.apple.com` (561 attempts)
- `linkedin.com` (443 attempts)
- `forums.vmix.com` (301 attempts)
- `de.linkedin.com` (179 attempts)
- `m.twitch.tv` (84 attempts)
- `open.spotify.com` (10 attempts)
- `sporx.com` (2 attempts)

### retire: stats / results (10)

- `flashscoreusa.com` (1802 attempts)
- `sofascore.com` (566 attempts)
- `eurobasket.com` (308 attempts)
- `flashscore.com` (71 attempts)
- `livescorehunter.com` (52 attempts)
- `flashscore.co.uk` (6 attempts)
- `livescore.com` (6 attempts)
- `proballers.com` (4 attempts)
- `basketball.realgm.com` (2 attempts)
- `livescoreapi.com` (2 attempts)

## Unclassified

- `ran.joyn.de` (836 attempts)
- `web.playsight.com` (621 attempts)
- `waipu.tv` (568 attempts)
- `go3.lt` (489 attempts)
- `olympics.com` (483 attempts)
- `nbl.com.au` (437 attempts)
- `de.headtopics.com` (390 attempts)
- `hakro-merlins.com` (348 attempts)
- `nbabox.co` (342 attempts)
- `tvmovie.de` (302 attempts)
- `areadvd.de` (295 attempts)
- `uplay.se` (290 attempts)
- `ubc.ms` (289 attempts)
- `frequency.com` (285 attempts)
- `sportairtime.com` (270 attempts)
- `sportingnews.com` (265 attempts)
- `eldia.es` (262 attempts)
- `ga.de` (259 attempts)
- `fanduel.com` (251 attempts)
- `ggaz.de` (250 attempts)
- `teltarif.de` (250 attempts)
- `chip.de` (249 attempts)
- `aktionen.sporteurope.tv` (235 attempts)
- `tomsguide.com` (225 attempts)
- `laprovincia.es` (221 attempts)
- `watch-live-stream.com` (208 attempts)
- `prosiebensat1.com` (197 attempts)
- `dorstenerzeitung.de` (194 attempts)
- `sportnews.eu` (189 attempts)
- `mebucom.de` (185 attempts)
- `malavida.com` (183 attempts)
- `freestreams-live1.watch` (180 attempts)
- `firmenpresse.de` (178 attempts)
- `nblaus.nbl.com.au` (178 attempts)
- `livesport.com` (177 attempts)
- `allgemeine-zeitung.de` (175 attempts)
- `broadbandtvnews.com` (175 attempts)
- `ran.de` (170 attempts)
- `remixed.nobackboard.com` (170 attempts)
- `techradar.com` (170 attempts)
- `sportimtv.net` (168 attempts)
- `kurierverlag.de` (165 attempts)
- `tvinfo.de` (160 attempts)
- `wormatia.de` (155 attempts)
- `thesportscommons.com` (152 attempts)
- `livetvworld.net` (150 attempts)
- `oneball.live` (150 attempts)
- `telecompaper.com` (150 attempts)
- `live-result.com` (145 attempts)
- `finanznachrichten.de` (140 attempts)
- `khaosod.co.th` (140 attempts)
- `meedia.de` (140 attempts)
- `protipster.com` (139 attempts)
- `berlindreams.world` (138 attempts)
- `freestreams-live1i.pk` (134 attempts)
- `nba.com` (132 attempts)
- `watchathletics.com` (131 attempts)
- `liveticker.morgenpost.de` (130 attempts)
- `ttbl.de` (127 attempts)
- `sport.es` (126 attempts)
- `de.droidinformer.org` (125 attempts)
- `tvizle.tr` (124 attempts)
- `shoppingvorteil.de` (123 attempts)
- `sportsmediawatch.com` (123 attempts)
- `streameasti.ch` (123 attempts)
- `streaming-probemonat.de` (123 attempts)
- `aiscore.com` (122 attempts)
- `9now.com.au` (121 attempts)
- `directv.com` (120 attempts)
- `nfhsnetwork.com` (119 attempts)
- `apkfab.com` (115 attempts)
- `fillingthelane.com` (110 attempts)
- `bluewin.ch` (109 attempts)
- `bcwiesbaden.de` (105 attempts)
- `canli-spor.com` (105 attempts)
- `ch.marketscreener.com` (104 attempts)
- `support.playback.tv` (100 attempts)
- `exa.ai` (99 attempts)
- `converseer.com` (95 attempts)
- `24live.com` (92 attempts)
- `rp-online.de` (92 attempts)
- `matchcast.tv` (91 attempts)
- `kulturigo.de` (90 attempts)
- `livekick.info` (90 attempts)
- `privateinternetaccess.com` (90 attempts)
- `frankfurt-skyliners.de` (89 attempts)
- `sportsweek.org` (89 attempts)
- `drakula.stream` (87 attempts)
- `khelnow.com` (85 attempts)
- `sambadigital.com` (85 attempts)
- `thaipost.net` (85 attempts)
- `tips.gg` (85 attempts)
- `365scores.com` (80 attempts)
- `freestreams-live1h.pk` (80 attempts)
- `bba-46ers.de` (79 attempts)
- `tntsports.co.uk` (77 attempts)
- `lvscore.com` (75 attempts)
- `pirly.tech` (75 attempts)
- `schoenen-dunk.de` (75 attempts)
- `hom-sistaxon-int.ibama.gov.br` (70 attempts)

## Fetch pass (2026-11-27)

Each of the 449 hosts the name-based pass could not settle was fetched once (HTTPS, 6 s timeout, `<title>` + meta description; the throwaway probe is not part of the product). **383 answered, 66 did not** — an unreachable host is a transport fact, never evidence that a game was not streamed, so nothing was retired for failing to answer.

What the pages said: news 92, official basketball 60, broadcaster 51, stats 32, TV guide 22, betting 11, paid platform 4, other 111.

**Retired on the fetch evidence** (now in `config/non-source-domains.json`, which `discover_candidates` and `approve_source` both refuse): 27 unauthorised aggregators and 10 name-collision businesses. The collision list is the useful one — `alba-coffee.com`, `albabotanica.com` and `albaquincy.com` matched the `alba` query and have nothing to do with ALBA Berlin.

**Official basketball hosts that answered with a club/league page**, proposed for a documented Tier 5 addition. They are **not approved here**: each needs the free-access claim `references/approved-sources.md` requires, and a club stream is free only when the club says so.

- `bamberg.basketball` — Bamberg Baskets – Bamberg ist Basketball
- `basket.co.il` — מנהלת ליגת העל בכדורסל
- `belarus.basketball` — Белорусская федерация баскетбола
- `bilbaobasket.biz` — Bilbao Basket | Web Oficial
- `bkpardubice.cz` — BK Pardubice, a.s.
- `cholet-basket.com` — Site officiel du club de Cholet Basket
- `eagles-basketball.de` — Home – Itzehoe Eagles Basketball - 2. Basketball Bundesliga
- `frankfurt-skyliners.de` — Home - Frankfurt Skyliners
- `igokea.rs` — Igokea m:tel &#8211; Republika Srpska
- `jbbl-koeln.de` — JBBL Basketball Köln
- `manchester.basketball` — Manchester Basketball | Super League Basketball Team
- `msb.fr` — MSB.FR - Le Mans Sarthe Basket
- `nbl.com.au` — National Basketball League | NBL Australia | Australia&#x27;s Basketba
- `nbl1.com.au` — Home | NBL1 Australia
- `pallacanestroreggiana.it` — Pallacanestro Reggiana
- `peristeribc.gr` — PERISTERI BC
- `sg-rheinstars-koeln.de` — RheinStars Köln - #1 Basketball in Köln
- `telekom-baskets-bonn.de` — Telekom Baskets Bonn - Telekom Baskets Bonn
- `ts-jahn-basketball.de` — TS Jahn München Basketball - #jahnfamily
- `ttl-basketball-bamberg.de` — TTL Basketball Bamberg &#8211; Basketball in Bamberg seit 1988 – Leist
- `ubc.ms` — Startseite - UBC Münster
- `usc-eisvoegel.de` — Eisvögel USC Freiburg | USC Eisvögel
- `winnerleague.tv` — -
- `basketballaustria.tv` — -
- `basketballireland.tv` — JoymoTV
- `tv.ibasketball.co.il` — Israeli Basketball TV
- `bba-46ers.de` — BBA GIESSEN 46ers | Basketball Akademie GIESSEN 46ers
- `bcwiesbaden.de` — Basketball Club Wiesbaden &#8211; 1952 e.V.
- `tsvbreitenguessbach.de` — TSV Breitengüßbach e.V. 1923
- `probasket.asport.tv` — ProBasket | Nord-Ostschweizer Basketballverband
- `courtside1891.basketball` — (no title)

**Paid broadcasters and platforms the fetch found**, proposed for `excluded` once their pricing is checked the way `fubo.tv` was: `foxtel.com.au`, `kayosports.com.au`, `beinsports.com`, `nfhsnetwork.com`, `ballertv.com`, `fanseat.com`, `staige.tv`.

**Aggregators retired from the fetch**: `streameaste.cc`, `vipleague.ro`, `nbabox.co`, `buffsports.io`, `crackstream-au.net`, `liveleagues.me`, `oneball.live`, `mainsports.me`, `livetv-de2.com`, `stream2watch.me`, `canli-spor.com`, `tvsport24.fr`, `partidos-hoy.com`.

