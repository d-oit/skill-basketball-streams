#!/usr/bin/env python3
"""fixtures.py — official league fixtures as recall ground truth.

Phase 5 of `live-stream-runtime-spec.md` (§8 item 5, §16 Phase 5).

Search-derived candidates (§8) measure how much of what we *saw* we captured.
They cannot see a game that **no** backend surfaced, so on their own they can
never report the worst failure this system has: a real free stream nobody found.
Official league fixture lists are the ground truth for that, and this script
turns one into the same `game_key` shape the ledger uses, so the two can be
joined.

Design rules, all of them about not inventing data:

1. **JSON-LD first.** League sites commonly embed `application/ld+json` with
   `SportsEvent` nodes, which is a standard with a stdlib parser. That is the
   supported path.
2. **A microdata fallback, clearly marked as a fallback.** Hand-rolled HTML
   scraping breaks whenever a site is redesigned. It is here because it costs
   little, and it returns `[]` rather than guessing.
3. **An embedded-payload rung, for a site that publishes neither.**
   `championsleague.basketball` carries no `application/ld+json` and no
   microdata anywhere — not at its game list and not on a game page, whose only
   JSON-LD node is a `BreadcrumbList` — so before this rung existed the site was
   unparseable by construction and its `fixture_recall` was `n/a` for ever. The
   game list is served by Next.js as a flight payload
   (`self.__next_f.push([1, "…"])`); joining those string literals and reading
   the `"games": [...]` arrays out of them is *decoding*, not scraping: once the
   chunks are joined the game objects are JSON.
4. **No match means no fixture, never a guess.** A partially parsed event is
   dropped, because a phantom fixture would show up as a "missed" game that does
   not exist and would quietly make recall look worse than it is.
5. **This module is stdlib-only.** Adding a real DOM parser would mean a new
   dependency in a repo that has none outside the test suite, so the honest
   trade is fewer supported sites and a captured fixture per site — see
   `tests/fixtures/README.md` for the input/output rule.
6. **Nothing here touches the network in tests.** `--input` parses a saved page,
   which is the only path CI exercises.

Usage:
    python3 scripts/fixtures.py --input page.html --source bbl --json
    python3 scripts/fixtures.py --source bbl --source bcl --out fixtures.jsonl
    python3 scripts/fixtures.py --source bbl --now 2026-09-14T08:30:00Z --days 7

Exit codes:
    0  PASS — every named source produced fixtures inside the window
    1  FAIL — a named source produced nothing (even when another source
           parsed), or no fixture fell inside the window
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

try:  # direct CLI execution: `python3 scripts/fixtures.py`
    from candidates import unseen_keys
    from upsert_events import game_key as build_game_key
except ImportError:  # imported as a package module, e.g. scripts.fixtures
    from scripts.candidates import unseen_keys  # type: ignore
    from scripts.upsert_events import game_key as build_game_key  # type: ignore

TIMEOUT_SECONDS = 20
USER_AGENT = "basketball-streams-runtime/1.0"
JSON_LD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
# Separators seen on German and international league sites.
TEAM_SEPARATORS = re.compile(
    r"\s+(?:vs\.?|gegen|-|–|—|@)\s+", re.IGNORECASE
)

# The three league sites named in the spec (§16 Phase 5). `league` is the string
# the colour map and the approved-source list already use.
DEFAULT_SOURCES: dict[str, dict] = {
    "bbl": {
        "league": "BBL",
        # `html` is the default and is stated explicitly only where it matters.
        # `ics` sources declare a feed; see parse_source for why the format is
        # declared rather than sniffed.
        "kind": "html",
        # The league moved: `basketball-bundesliga.de` fails TLS SNI
        # (`tlsv1 unrecognized name`) while DNS still resolves, so a plain GET
        # never reaches a body. This is the current official schedule page.
        #
        # It is ALSO a page none of rungs 1-3 can read, and that is a property
        # of the site rather than a bug in this file. Verified 2026-09-30
        # against the live page: HTTP 200, ~285 KB of server-rendered markup,
        # zero `application/ld+json` blocks, zero `itemtype` microdata, and the
        # server-rendered game list reads "Keine Spiele für diese Saison
        # gefunden" — the fixtures are fetched client-side from
        # `api.basketball-bundesliga.de`, which answers 401 to any caller
        # without the credential the site itself holds.
        #
        # So this source yields `[]` from a plain GET, and per rule 4 below that
        # is a FAIL, not a silent zero. Making it contribute needs a *rendered*
        # capture fed through `--input`; see `tests/fixtures/README.md`. Until
        # such a capture exists, BBL fixture recall is genuinely `n/a` and this
        # source is the reason — which is why the URL is corrected here rather
        # than left pointing at a host that cannot answer at all.
        "url": "https://www.easycredit-bbl.de/saison/spielplaene_liga-pokalspiele/hauptrunde",
    },
    "euroleague": {
        "league": "EuroLeague",
        "kind": "html",
        "url": "https://www.euroleaguebasketball.net/euroleague/game-center/",
    },
    "bcl": {
        "league": "Basketball Champions League",
        "kind": "html",
        # The game list, not the site root: the root redirects to `/en`, which is
        # a landing page carrying no fixtures at all — not in JSON-LD, not in
        # microdata, and not in the payload.
        "url": "https://www.championsleague.basketball/en/games",
    },
}
EVENT_TYPES = {"sportsevent", "event"}
# The Next.js flight payload. `"games": [` is not unique on the BCL page — it is
# sent twice, once empty — so every array is collected rather than the first
# being trusted.
FLIGHT_CALL = "self.__next_f.push("
GAMES_ARRAY = re.compile(r'"games"\s*:\s*\[')


def parse_dt(value: object) -> datetime | None:
    """Parse an ISO-8601 instant, tolerating a trailing Z. None if unusable."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def split_teams(text: str) -> list[str]:
    """Split `"A vs B"` / `"A - B"` / `"A gegen B"` into exactly two names."""
    if not isinstance(text, str):
        return []
    parts = [part.strip() for part in TEAM_SEPARATORS.split(text.strip()) if part.strip()]
    return parts if len(parts) == 2 else []


def _type_of(node: dict) -> str:
    raw = node.get("@type") or node.get("type") or ""
    if isinstance(raw, list):
        return " ".join(str(item).lower() for item in raw)
    return str(raw).lower()


def _names_of(value: object) -> list[str]:
    """Collect `name` from a competitor node, which may be a dict or a list."""
    if isinstance(value, dict):
        name = value.get("name") or value.get("legalName")
        return [str(name).strip()] if name else []
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            names.extend(_names_of(item))
        return names
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def extract_json_ld(html: str) -> list[dict]:
    """Every parsed JSON-LD node, flattening `@graph` containers."""
    nodes: list[dict] = []
    for match in JSON_LD.finditer(html or ""):
        try:
            payload = json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate.get("@graph")
            if isinstance(graph, list):
                nodes.extend(node for node in graph if isinstance(node, dict))
            else:
                nodes.append(candidate)
    return nodes


def event_to_fixture(
    node: dict, *, league: str, source: str, source_url: str = ""
) -> dict | None:
    """Normalise one SportsEvent node. Returns None if it is not usable."""
    if "sportsevent" not in _type_of(node):
        return None
    start = parse_dt(node.get("startDate"))
    if start is None:
        return None

    teams = _names_of(node.get("competitor") or node.get("competitors"))
    if len(teams) != 2:
        teams = split_teams(str(node.get("name") or ""))
    if len(teams) != 2:
        # A fixture with no identifiable pair of teams cannot be joined to a
        # candidate, and a half-parsed one would be counted as a missed game.
        return None

    return {
        "league": league,
        "teams": teams,
        "start": start.isoformat(),
        "game_key": build_game_key(league, teams, start.isoformat()),
        "source": source,
        "source_url": source_url or str(node.get("url") or ""),
    }


WANTED_ITEMPROPS = ("name", "startDate", "endDate")


class _MicrodataEvents(HTMLParser):
    """Fallback extractor for `itemtype="...SportsEvent"` microdata.

    Deliberately minimal, and a fallback rather than a scraper: it records
    `name` / `startDate` / `endDate` itemprop values inside an enclosing
    SportsEvent block and hands them to `parse_microdata`, which applies the
    same "no match, no fixture" rule as the JSON-LD path.

    Structure is tracked with a tag depth rather than tag names, because the
    container element is `div` on some sites and `li` or `article` on others —
    and a `div`-based guess closes the wrong block on real pages, which silently
    produces half-parsed events.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.open_events: list[dict] = []
        self.events: list[dict] = []
        self.pending: tuple[int, str] | None = None

    def _start(self, attributes: dict[str, str]) -> None:
        if "sportsevent" in attributes.get("itemtype", "").lower():
            self.open_events.append({"depth": self.depth, "props": {}})
        prop = attributes.get("itemprop", "")
        if prop not in WANTED_ITEMPROPS or not self.open_events:
            return
        # `<time datetime>` / `<meta content>` carry the machine value.
        value = attributes.get("datetime") or attributes.get("content")
        if value:
            self.open_events[-1]["props"].setdefault(prop, value)
        else:
            self.pending = (len(self.open_events) - 1, prop)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.depth += 1
        self._start({key: (value or "") for key, value in attrs})

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Void elements (`<meta>`, `<link>`) never get an end tag or change depth.
        self._start({key: (value or "") for key, value in attrs})

    def handle_endtag(self, tag: str) -> None:
        while self.open_events and self.open_events[-1]["depth"] >= self.depth:
            self.events.append(self.open_events.pop())
        self.depth = max(0, self.depth - 1)
        self.pending = None

    def handle_data(self, data: str) -> None:
        if self.pending is None or not data.strip():
            return
        index, prop = self.pending
        if index < len(self.open_events):
            self.open_events[index]["props"].setdefault(prop, data.strip())
        self.pending = None

    def close(self) -> None:
        super().close()
        while self.open_events:
            self.events.append(self.open_events.pop())


def parse_microdata(html: str, *, league: str, source: str, source_url: str = "") -> list[dict]:
    """Best-effort microdata fallback. Returns [] when it finds nothing usable."""
    parser = _MicrodataEvents()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is tolerant by design
        return []
    fixtures: list[dict] = []
    for event in parser.events:
        props = event.get("props") or {}
        node = {
            "@type": "SportsEvent",
            "name": props.get("name", ""),
            "startDate": props.get("startDate", ""),
        }
        fixture = event_to_fixture(
            node, league=league, source=source, source_url=source_url
        )
        if fixture is not None:
            fixtures.append(fixture)
    return fixtures


def extract_embedded_payload(html: str) -> str:
    """The joined Next.js flight payload, or `""` when the page carries none.

    Next streams server-rendered data as `self.__next_f.push([1, "<chunk>"])`
    calls whose second argument is a JS string literal. Decoding the literals
    and **concatenating** them rebuilds the stream — a value split across two
    chunks is only readable once they are joined, which is why the whole flow is
    returned rather than the chunks one at a time.
    """
    decoder = json.JSONDecoder()
    chunks: list[str] = []
    position = 0
    while True:
        call = html.find(FLIGHT_CALL, position)
        if call < 0:
            return "".join(chunks)
        cursor = call + len(FLIGHT_CALL)
        try:
            argument, end = decoder.raw_decode(html, cursor)
        except ValueError:
            # A call whose argument is not JSON — a variable, a function call —
            # is stepped over rather than guessed at.
            position = cursor
            continue
        if (
            isinstance(argument, list)
            and len(argument) > 1
            and isinstance(argument[1], str)
        ):
            chunks.append(argument[1])
        position = end


def extract_embedded_games(html: str) -> list[dict]:
    """Every game object inside a `"games": [ … ]` array of the payload."""
    flow = extract_embedded_payload(html)
    if not flow:
        return []
    decoder = json.JSONDecoder()
    games: list[dict] = []
    for match in GAMES_ARRAY.finditer(flow):
        try:
            array, _ = decoder.raw_decode(flow, match.end() - 1)
        except ValueError:
            continue
        if isinstance(array, list):
            games.extend(item for item in array if isinstance(item, dict))
    return games


def _competitor_name(team: object) -> str:
    """One side's display name, from the payload's three namings of it.

    `shortName` first, because it is what the site itself shows in the list; the
    join against the candidate ledger is an exact `game_key` string, so the name
    chosen here decides whether a game counts as surfaced.
    """
    if not isinstance(team, dict):
        return ""
    for key in ("shortName", "officialName", "code"):
        value = team.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def game_to_fixture(
    game: dict, *, league: str, source: str, source_url: str = ""
) -> dict | None:
    """Normalise one embedded game object. None when it is not usable.

    Two refusals, both because a phantom fixture is worse than a missing one —
    it is reported as a game **no backend surfaced**:

    * **No tip-off.** The list carries the whole season and 46 of its 136 rows
      are placeholders — `hasTimeGameDateTime` is `false`, the date is a
      midnight stub and one side is still `null`. No stream can ever match one.
    * **No pair of teams.** The pair is what `game_key` sorts and what the
      ledger join compares; half a pairing is not an identity.
    """
    teams = [_competitor_name(game.get("teamA")), _competitor_name(game.get("teamB"))]
    if not all(teams):
        return None
    if game.get("hasTimeGameDateTime") is False:
        return None
    # `gameDateTimeUTC` is a real instant and is preferred; `gameDateTime` is the
    # venue-local one, which `parse_dt` would otherwise read as UTC.
    start = parse_dt(game.get("gameDateTimeUTC") or game.get("gameDateTime"))
    if start is None:
        return None
    return {
        "league": league,
        "teams": teams,
        "start": start.isoformat(),
        "game_key": build_game_key(league, teams, start.isoformat()),
        "source": source,
        "source_url": source_url,
    }


# ── iCalendar (RFC 5545) ────────────────────────────────────────────────────
# A league that publishes an iCalendar feed hands us a *file*, not a page: no
# JavaScript to render, no WAF to climb, and no markup for a redesign to break.
# That is the most durable ground truth available, which is why this reader sits
# alongside the three HTML rungs rather than after them.
#
# Everything here is stdlib, `zoneinfo` included — a feed states its own
# timezone, and getting that wrong moves a tip-off by one or two hours, which
# silently breaks both the `--days` window and the `game_key` join. A time read
# as UTC when the feed said Europe/Berlin is a wrong fixture, not a formatting
# nit.
#
# The refusals are the same shape as `game_to_fixture`, and for the same reason: a
# phantom or misplaced fixture is reported as a game **no backend surfaced**,
# which makes recall look worse than reality and sends someone hunting a game
# that does not exist.
FLOATING_TIMEZONE = "Europe/Berlin"
"""A DTSTART with no `Z` and no `TZID` is a *floating* local time.

Read as the calendar's timezone, not UTC. Every league in `DEFAULT_SOURCES` is
European and `config/calendar.json` is Europe/Berlin, so UTC would be wrong by
the DST offset — 1h in winter, 2h in summer — and the error would move with the
seasons.
"""

# RFC 5545 §3.3.11 TEXT escaping, in the order the grammar requires: the
# backslash is handled last so `\\n` is a literal backslash-n and not a newline.
_ICS_UNESCAPE = re.compile(r"\\([\\;,nN])")
_ICS_ESCAPES = {"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}

# Properties inside a nested component (VALARM, VTIMEZONE) belong to that
# component, not to the event. A VALARM has no DTSTART today, but a VTIMEZONE
# does, and reading one as a tip-off would invent a fixture at 1970.
_NESTED_COMPONENTS = frozenset({"VALARM", "VTIMEZONE", "VJOURNAL", "VTODO"})


def unfold_ics(text: str) -> list[str]:
    """Undo RFC 5545 line folding, returning logical lines.

    A fold is a CRLF followed by a single space or tab, and the continuation
    carries no separator of its own. A `SUMMARY` long enough to fold is common —
    "ALBA BERLIN vs FC Bayern Muenchen - easyCredit BBL" plus a venue — so
    skipping this silently truncates the team pair and `split_teams` returns
    nothing.
    """
    if not text:
        return []
    # Normalise line endings first: a feed served with bare LF is still valid to
    # every client, and a CRLF-only fold rule would then never fire.
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw in normalised.split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def unescape_ics(value: str) -> str:
    return _ICS_UNESCAPE.sub(lambda m: _ICS_ESCAPES[m.group(1)], value)


def calendar_name(text: str) -> str:
    """`X-WR-CALNAME` / `NAME` — which feed this is, for the fetch ledger.

    The HTML fingerprint is a `<title>`; a feed has no HTML, so without this a
    mis-pointed feed would be a 31 KB line of nothing in the ledger.
    """
    for line in unfold_ics(text)[:40]:
        name, _, value = line.partition(":")
        if name.split(";")[0].strip().upper() in {"X-WR-CALNAME", "NAME"}:
            return unescape_ics(value.strip())[:120]
    return ""


def parse_ics_dt(value: str, params: str = "") -> datetime | None:
    """A DTSTART into an aware datetime. None when it is not a tip-off time.

    Handles the three forms a feed actually uses: `Z` (UTC), a `TZID` parameter,
    and a bare floating local time. An all-day `VALUE=DATE` value is refused
    rather than read as midnight: an all-day entry has no tip-off, and midnight
    is a placeholder no stream ever matches, which is the same refusal
    `game_to_fixture` makes for `hasTimeGameDateTime: false`.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    upper_params = params.upper()
    if "VALUE=DATE" in upper_params and "TIME" not in upper_params:
        return None  # all-day: no tip-off time to match a stream against
    try:
        if raw.endswith("Z"):
            return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
        naive = datetime.strptime(raw, "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    tzid = ""
    for part in params.split(";"):
        key, _, val = part.partition("=")
        if key.strip().upper() == "TZID":
            tzid = val.strip().strip('"')
            break
    if tzid:
        try:
            return naive.replace(tzinfo=ZoneInfo(tzid))
        except Exception:  # noqa: BLE001 - an unknown TZID must not crash a run
            # An unknown zone is not a reason to invent UTC: the local reading
            # is closer to right than a two-hour error in the other direction.
            return naive.replace(tzinfo=ZoneInfo(FLOATING_TIMEZONE))
    return naive.replace(tzinfo=ZoneInfo(FLOATING_TIMEZONE))


# The *unambiguous* match markers. A feed states the matchup with one of these and
# then, very often, appends context after it — "ALBA BERLIN vs FC Bayern
# Muenchen - easyCredit BBL" — so the pair has to be read around the trailing
# text rather than by splitting the whole line.
FEED_MATCH_MARKER = re.compile(r"\s+(?:vs\.?|gegen|v\.)\s+", re.IGNORECASE)


def split_feed_teams(summary: str) -> list[str]:
    """The two team names in a feed `SUMMARY`, or [] when there is no pair.

    `split_teams` demands exactly two halves, which is the right rule for a page
    that writes one pairing per cell and the wrong rule here: a feed line
    routinely carries a second separator for the competition or the matchday, so
    "A vs B - League" arrives as three parts and the naive split drops a real
    game. That is not hypothetical — it is the shape the BBL subscription writes,
    and the first version of this reader refused it.

    So the match marker is tried first and everything after the pair is dropped.
    A bare `-` is *not* treated as a match marker, because it is the one
    separator a feed uses for trailing context; a pairing written "A - B" is
    still handled, by the generic path.

    **A comma after the pair is a refusal, not a cut.** "Real Madrid vs
    Barcelona, EuroLeague" could mean a team called "Barcelona, EuroLeague" or a
    team called "Barcelona" plus context, and no rule available here can tell the
    difference — a club name may legitimately contain a comma. Cutting would
    invent a team, and a fixture with a wrong team name is reported as a game no
    backend surfaced, for ever. So the event is dropped and the limitation is
    stated here rather than papered over. One real feed settles it: if the
    provider's own `SUMMARY` format is measured, this becomes a cut rule with a
    citation instead of a refusal.
    """
    if not isinstance(summary, str):
        return []
    match = FEED_MATCH_MARKER.search(summary)
    if not match:
        return split_teams(summary)
    left = summary[: match.start()].strip()
    right = summary[match.end() :].strip()
    if "," in right or ";" in right:
        return []
    if TEAM_SEPARATORS.search(right):
        right = TEAM_SEPARATORS.split(right)[0].strip()
    return [left, right] if left and right else []


def parse_ics(text: str, *, league: str, source: str, source_url: str = "") -> list[dict]:
    """VEVENTs into the same fixture shape `parse_page` emits.

    Deliberately the same dict and the same `game_key`: a fixture is only useful
    if the ledger join can compare it against what search surfaced, and a second
    key format would make recall read as zero for a league that is working
    perfectly.
    """
    fixtures: list[dict] = []
    event: dict[str, tuple[str, str]] | None = None
    depth = 0

    for line in unfold_ics(text):
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("BEGIN:"):
            component = stripped.split(":", 1)[1].strip().upper()
            if component == "VEVENT":
                event, depth = {}, 0
            elif event is not None:
                depth += 1
            continue
        if upper.startswith("END:"):
            component = stripped.split(":", 1)[1].strip().upper()
            if component == "VEVENT" and event is not None:
                fixture = _event_to_fixture(
                    event, league=league, source=source, source_url=source_url
                )
                if fixture is not None:
                    fixtures.append(fixture)
                event = None
                depth = 0
            elif event is not None and depth:
                depth -= 1
            continue
        if event is None or depth:
            continue  # a property of a nested component, not of this event
        name, sep, value = stripped.partition(":")
        if not sep:
            continue
        key = name.split(";", 1)[0].strip().upper()
        params = name.split(";", 1)[1] if ";" in name else ""
        # Last value wins, matching how a repeated property overrides.
        event[key] = (value.strip(), params)

    return fixtures


def _event_to_fixture(
    event: dict, *, league: str, source: str, source_url: str = ""
) -> dict | None:
    """One VEVENT to one fixture, or None when it cannot be trusted.

    Three refusals, each a case where a fixture would be wrong rather than
    merely missing:

    * **No usable start.** An all-day entry or a `DTSTART` in a format RFC 5545
      does not define. A fixture with a guessed time joins nothing.
    * **No team pair.** `SUMMARY` is the only place a feed states the matchup, and
      it must yield exactly two names. `split_feed_teams` reads the pair around
      any trailing context; a summary with no match marker at all is not a
      pairing and is refused.
    * **Cancelled.** `STATUS:CANCELLED` names a game that will not be played, so
      no stream can ever exist for it, and counting it as ground truth would
      report a miss for every run.
    """
    if event.get("STATUS", ("", ""))[0].strip().upper() == "CANCELLED":
        return None
    start_value, start_params = event.get("DTSTART", ("", ""))
    start = parse_ics_dt(start_value, start_params)
    if start is None:
        return None
    summary = unescape_ics(event.get("SUMMARY", ("", ""))[0]).strip()
    teams = split_feed_teams(summary)
    if not teams:
        return None
    return {
        "league": league,
        "teams": teams,
        "start": start.isoformat(),
        "game_key": build_game_key(league, teams, start.isoformat()),
        "source": source,
        "source_url": source_url,
    }


def parse_source(
    body: str, *, kind: str, league: str, source: str, source_url: str = ""
) -> list[dict]:
    """Dispatch on the declared source format.

    The format is declared in `config/sources.json` rather than sniffed from the
    body, because sniffing is how a block page starts being parsed as a feed: an
    HTML error page is not valid iCalendar, but "does it contain BEGIN:VEVENT"
    is true of a challenge page that quotes the user's query. A declared kind
    cannot be confused by the content of a response.
    """
    if kind == "ics":
        return parse_ics(body, league=league, source=source, source_url=source_url)
    return parse_page(body, league=league, source=source, source_url=source_url)


def parse_page(
    html: str, *, league: str, source: str, source_url: str = ""
) -> list[dict]:
    """JSON-LD first, then microdata, then the embedded payload."""
    fixtures: list[dict] = []
    for node in extract_json_ld(html):
        fixture = event_to_fixture(
            node, league=league, source=source, source_url=source_url
        )
        if fixture is not None:
            fixtures.append(fixture)
    if fixtures:
        return fixtures
    fixtures = parse_microdata(html, league=league, source=source, source_url=source_url)
    if fixtures:
        return fixtures
    embedded: list[dict] = []
    for game in extract_embedded_games(html):
        fixture = game_to_fixture(
            game, league=league, source=source, source_url=source_url
        )
        if fixture is not None:
            embedded.append(fixture)
    return embedded# Response headers worth keeping when a fetch fails. Named explicitly, for the
# same reason `rung_health.py` builds its rows from named fields only: a ledger
# that stores whatever the response happened to contain becomes unloggable the
# first time a provider echoes a request back.
#
# These are the headers that actually answer "why". The Vercel case, measured
# 2026-09-30 against `euroleaguebasketball.net`: `server: Vercel` plus
# `x-vercel-mitigated: challenge` identifies the 429 as an edge bot challenge in
# one line, where the bare status code only said "something is wrong, retry
# later" — and retrying later does not help, because a challenge is not a
# throttle.
DIAGNOSTIC_HEADERS = (
    "server",
    "content-type",
    "content-length",
    "retry-after",
    "via",
    "cf-ray",
    "cf-mitigated",
    "x-vercel-mitigated",
    "x-vercel-id",
    "x-amzn-cf-id",
    "x-amzn-requestid",
    "x-served-by",
    "x-cache",
)

# A page's <title> is a fingerprint, not content: 200 bytes that identify which
# interstitial was served ("Vercel Security Checkpoint" says the block is a
# challenge; "Just a moment..." says Cloudflare; a German page title says the
# block is regional). The body itself never goes anywhere near the ledger.
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def page_title(body: str) -> str:
    """The document title, whitespace-collapsed and length-capped."""
    match = _TITLE_RE.search(body or "")
    if not match:
        return ""
    text = re.sub(r"\s+", " ", match.group(1)).strip()
    return text[:120]


def diagnostic_headers(headers) -> dict:
    """The whitelisted subset, lowercased keys, empty values dropped."""
    out: dict[str, str] = {}
    for name in DIAGNOSTIC_HEADERS:
        try:
            value = headers.get(name)
        except AttributeError:
            return out
        if value:
            out[name] = str(value)[:200]
    return out


class FetchResult(str):
    """The page body, carrying *why* it is empty when it is.

    A `str` subclass on purpose. `fetch` is monkeypatched throughout the suite
    with `lambda url: "<html>"`, and returning a different type would turn every
    one of those into a failure that has nothing to do with the behaviour under
    test. Anything that is a plain `str` still reads as a successful body.

    The distinction matters because the previous version collapsed every failure
    into `""` and the caller then reported "the page fetched but no fixture parsed
    (markup change?)". A rate-limited source and a redesigned site both
    arrive as an empty string, so a 429 was diagnosed as a parser problem and
    sent someone to re-derive a parser that did not need changing. The measured
    case: `euroleaguebasketball.net` answers **429 to every user agent and every
    path** from a datacenter IP, and that is a block, not a markup change.

    Statuses mirror `scripts/link_check.py`, which already draws this line:
    `ok`, `blocked` (401/403/429/451), `not_found` (404/410), `server_error`
    (5xx), `unreachable` (DNS, TLS, timeout).
    """

    __slots__ = ("status", "code", "headers", "elapsed_ms", "body_bytes")

    def __new__(
        cls,
        body: str,
        status: str = "ok",
        code: int | None = None,
        headers: dict | None = None,
        elapsed_ms: int | None = None,
    ):
        obj = super().__new__(cls, body)
        obj.status = status
        obj.code = code
        obj.headers = headers or {}
        obj.elapsed_ms = elapsed_ms
        obj.body_bytes = len(body)
        return obj

    def evidence(self) -> dict:
        """The ledger row for this attempt. Named fields only, never the body."""
        return {
            "status": self.status,
            "code": self.code,
            "headers": self.headers,
            "elapsed_ms": self.elapsed_ms,
            "body_bytes": self.body_bytes,
            "page_title": page_title(str(self)),
        }

    def why(self) -> str:
        """A one-line, human-readable cause, or "" when the fetch succeeded.

        This is what turns `BLOCKED (HTTP 429)` into something actionable. The
        provider's own headers say what kind of block it is, and they routinely
        contradict the obvious guess: a 429 carrying `x-vercel-mitigated:
        challenge` is a bot challenge that a real browser passes and a retry
        never does, which is the opposite of the "rate limited, try again later"
        reading the status code alone suggests.
        """
        if self.status == "ok":
            return ""
        notes: list[str] = []
        server = self.headers.get("server")
        if server:
            notes.append(f"server: {server}")
        challenge = self.headers.get("x-vercel-mitigated") or self.headers.get("cf-mitigated")
        if challenge:
            notes.append(f"mitigation: {challenge}")
        for name in ("retry-after", "cf-ray", "x-vercel-id", "x-amzn-requestid"):
            if self.headers.get(name):
                notes.append(f"{name}: {self.headers[name]}")
        title = page_title(str(self))
        if title:
            notes.append(f'page: "{title}"')
        if self.elapsed_ms is not None:
            notes.append(f"elapsed: {self.elapsed_ms} ms")
        return "; ".join(notes)


# A block is not a break, and the two need different responses: a block is worth
# retrying later or climbing the fetch ladder, a 404 is worth deleting the URL.
BLOCKED_CODES = frozenset({401, 403, 429, 451})
GONE_CODES = frozenset({404, 410})


def _classify(exc: BaseException) -> tuple[str, int | None]:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        if code in BLOCKED_CODES:
            return "blocked", code
        if code in GONE_CODES:
            return "not_found", code
        if code >= 500:
            return "server_error", code
        return "not_found", code
    return "unreachable", None


FETCH_LEDGER_RELATIVE_PATH = "logs/fetch-attempts.jsonl"


def default_run_id() -> str:
    """The same shape the workflows pass: `2026-09-15T08:30Z`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def append_fetch_row(
    path: Path,
    *,
    source: str,
    url: str,
    result: FetchResult,
    run_id: str = "",
    ts: str = "",
) -> dict:
    """Append one fetch attempt to the ledger. Named fields only, never the body.

    The shape follows `scripts/rung_health.py` deliberately — one append-only
    JSONL row per attempt, built from named fields, so the ledger can be kept
    forever. A blocked response is typically a 30 KB interstitial, and the rule
    that keeps this affordable is the same one rung_health states: a page body
    never reaches the ledger. The `<title>` fingerprint is the compromise — 120
    bytes that identify *which* interstitial was served, which is the difference
    between "blocked" and "blocked by a Vercel challenge, which a browser passes".

    The ledger is best-effort: a telemetry write must never turn a run's verdict
    into a different one, so every failure here is swallowed and the row is
    simply lost. Losing the evidence is bad; changing the answer because
    evidence could not be written is worse.
    """
    # A monkeypatched `fetch` may hand back a plain `str`; treat it as a
    # successful body so the ledger row is still well-formed rather than
    # crashing the run. Same reason `FetchResult` subclasses `str`.
    if not isinstance(result, FetchResult):
        result = FetchResult(str(result), "ok")
    row = {
        "ts": ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": run_id,
        "source": source,
        "url": url,
        "host": _host_of(url),
        **result.evidence(),
        "why": result.why(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return row


def read_fetch_ledger(path: Path) -> list[dict]:
    """Every row in the ledger, oldest first. Unreadable lines are skipped."""
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def failing_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row.get("status") not in (None, "ok")]


def report_fetch_log(
    path: Path, *, limit: int = 0, only_failing: bool = True, as_json: bool = False
) -> int:
    """Print what failed and why. This is the "output the failing" half.

    A FAIL line naming a status code is a claim; this is the evidence for it.
    Without it, the only record of a blocked source is one line in one morning's
    log, which is the failure `rung_health.py` was written to end.
    """
    rows = read_fetch_ledger(path)
    if only_failing:
        rows = failing_rows(rows)
    if limit and len(rows) > limit:
        rows = rows[-limit:]
    if as_json:
        print(json.dumps({"rows": rows, "total": len(rows)}, indent=2))
        return 0 if rows else 1
    if not rows:
        print(
            f"OK: fixtures: no fetch attempts recorded in {path}"
            if only_failing
            else f"OK: fixtures: no fetch attempts recorded in {path}"
        )
        return 0
    for row in rows:
        print(
            "FAIL: fixtures: {source} {status}{code} {url}".format(
                source=row.get("source", "?"),
                status=row.get("status", "?"),
                code=f" (HTTP {row['code']})" if row.get("code") else "",
                url=row.get("url", "?"),
            )
        )
        why = row.get("why") or ""
        if why:
            print(f"  why: {why}")
    print(f"FAIL: fixtures: {len(rows)} failing fetch attempt(s) in {path}")
    return 1


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _overall_advice(causes: dict[str, str]) -> str:
    """The closing line must not contradict the per-source lines above it.

    A summary that always says "the sites may have changed their markup" undoes
    the diagnosis it is summarising: when every source was blocked, the advice
    to go and write a parser is exactly wrong. So it reports the dominant cause.
    """
    if causes and all("BLOCKED" in text for text in causes.values()):
        return (
            "every source was blocked rather than unparsable, so no parser work "
            "is warranted: retry later or climb the fetch ladder"
        )
    if causes and any("BLOCKED" in text for text in causes.values()):
        blocked = sorted(n for n, t in causes.items() if "BLOCKED" in t)
        return (
            f"blocked source(s) {blocked} need no parser work; the rest may have "
            "changed their markup — capture those pages and add parser fixtures"
        )
    return (
        "the sites may have changed their markup; capture a page and add a "
        "parser fixture"
    )


def _empty_cause(status: str, code: int | None) -> str:
    """Why a source produced nothing — the cause, not a guess at the cause.

    `blocked` is the case worth naming. A 429 is not a redesigned site, and the
    old single message told the reader it was: the run log for 2026-09-29 says
    "markup change?" about a host that answers 429 to every user agent. Telling
    a reader to add a parser fixture for a block sends them to write a parser
    that will still be correct and still be blocked.
    """
    if status == "blocked":
        return (
            f"the source is BLOCKED (HTTP {code}) — not a markup change: this is "
            "anti-bot or rate limiting, so retry later or climb the fetch ladder "
            "rather than changing a parser"
        )
    if status == "not_found":
        return (
            f"the source is GONE (HTTP {code}) — the URL itself is wrong, so fix "
            "the source rather than the parser"
        )
    if status == "server_error":
        return (
            f"the source returned HTTP {code} — a server fault, so retry rather "
            "than changing a parser"
        )
    if status == "unreachable":
        return "the source could not be reached (DNS, TLS or timeout)"
    return (
        "the fetch returned nothing (network failure, bot block or TLS error)"
    )


def fetch(url: str) -> FetchResult:
    """Plain GET, with the reason and the evidence kept for the caller.

    Returns the body on success and an empty `FetchResult` on any failure, so a
    caller that ignores `.status` and `.headers` behaves exactly as before.

    A failed `HTTPError` carries its own response, so the diagnostic headers and
    the interstitial body are read off it rather than discarded. That is what
    makes the difference between "429, retry later" and "a Vercel bot challenge,
    which a browser passes and a retry does not".
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read().decode(charset, "replace")
            return FetchResult(
                body,
                "ok",
                getattr(resp, "status", 200),
                diagnostic_headers(getattr(resp, "headers", None)),
                int((time.monotonic() - started) * 1000),
            )
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        status, code = _classify(exc)
        headers, body = {}, ""
        response = getattr(exc, "headers", None)
        if response is not None:
            headers = diagnostic_headers(response)
            try:
                raw = exc.read()  # type: ignore[attr-defined]
                charset = response.get_content_charset() or "utf-8"
                body = raw.decode(charset, "replace")
            except Exception:  # noqa: BLE001 - a body we cannot read is not a reason to fail
                body = ""
        return FetchResult(
            body,
            status,
            code,
            headers,
            int((time.monotonic() - started) * 1000),
        )


def in_window(fixture: dict, *, now: datetime, days: int) -> bool:
    start = parse_dt(fixture.get("start"))
    if start is None:
        return False
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day <= start < start_of_day + timedelta(days=days + 1)


def dedupe(fixtures: list[dict]) -> list[dict]:
    """One row per game_key, first source wins."""
    seen: dict[str, dict] = {}
    for fixture in fixtures:
        key = str(fixture.get("game_key") or "")
        if key and key not in seen:
            seen[key] = fixture
    return [seen[key] for key in sorted(seen)]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalise official league fixtures into ledger game_keys.",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help=f"league source (repeatable): {', '.join(sorted(DEFAULT_SOURCES))}",
    )
    parser.add_argument("--input", help="saved HTML page (offline; requires one --source)")
    parser.add_argument("--out", help="write fixtures JSONL here")
    parser.add_argument("--fixtures", help="fixtures JSONL to diff (with --ledger)")
    parser.add_argument("--ledger", help="candidate ledger JSONL to diff against")
    parser.add_argument("--now", help="ISO-8601 override (for tests)")
    parser.add_argument("--days", type=int, default=7, help="window size (default 7)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--input-kind",
        choices=("html", "ics"),
        default=None,
        help=(
            "parse a saved --input page as this format, overriding the source's "
            "declared kind. Exists so a candidate feed can be evaluated offline "
            "before it is pinned into DEFAULT_SOURCES. A source's declared kind "
            "always wins when it fetches for itself."
        ),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--root",
        default=".",
        help="workspace root, for resolving the fetch ledger (default: .)",
    )
    parser.add_argument(
        "--fetch-ledger",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help=(
            "append one row per fetch attempt to the ledger, so a failure can be "
            "diagnosed later. Opt-in, and the path defaults to "
            f"{FETCH_LEDGER_RELATIVE_PATH} under --root. Off by default because a "
            "parse command that silently writes telemetry is a side effect, and "
            "because the suite drives main() with a patched fetch — which would "
            "otherwise append its synthetic bodies to the real ledger."
        ),
    )
    parser.add_argument(
        "--fetch-log",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help=(
            "read the fetch-attempt ledger and print what failed and why, "
            "instead of fetching anything. With no PATH, reads "
            f"{FETCH_LEDGER_RELATIVE_PATH} under --root."
        ),
    )
    parser.add_argument(
        "--all", action="store_true", help="with --fetch-log, include successes"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="with --fetch-log, show the last N rows"
    )
    args = parser.parse_args()

    if args.fetch_log is not None:
        log_path = (
            Path(args.fetch_log)
            if args.fetch_log
            else Path(args.root) / FETCH_LEDGER_RELATIVE_PATH
        )
        sys.exit(
            report_fetch_log(
                log_path,
                limit=args.limit,
                only_failing=not args.all,
                as_json=args.json,
            )
        )

    if args.now:
        now = parse_dt(args.now)
        if now is None:
            print(f"FAIL: fixtures: --now {args.now!r} is not ISO-8601", file=sys.stderr)
            sys.exit(2)
    else:
        now = datetime.now(timezone.utc)

    # Diff mode: no fetching, no parsing — just the fixture/ledger join.
    if args.fixtures or args.ledger:
        if not (args.fixtures and args.ledger):
            print(
                "FAIL: fixtures: --ledger and --fixtures must be given together",
                file=sys.stderr,
            )
            sys.exit(2)
        fixtures = _read_jsonl(Path(args.fixtures))
        ledger = _read_jsonl(Path(args.ledger))
        if not fixtures:
            print(
                f"FAIL: fixtures: no fixtures in {args.fixtures} — nothing to "
                "measure recall against",
                file=sys.stderr,
            )
            sys.exit(1)
        unseen = unseen_keys(ledger, fixtures)
        payload = {
            "fixtures": len(fixtures),
            "surfaced": len(fixtures) - len(unseen),
            "unseen": len(unseen),
            "unseen_keys": unseen,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for key in unseen:
                print(f"UNSEEN {key}")
            print(
                "OK: fixtures: fixtures={fixtures} surfaced={surfaced} "
                "unseen={unseen}".format(**payload)
            )
        return

    sources = args.source or list(DEFAULT_SOURCES)
    unknown = [name for name in sources if name not in DEFAULT_SOURCES]
    if unknown:
        print(f"FAIL: fixtures: unknown source(s) {unknown}", file=sys.stderr)
        sys.exit(2)

    if args.input and len(sources) != 1:
        print(
            "FAIL: fixtures: --input requires exactly one --source",
            file=sys.stderr,
        )
        sys.exit(2)

    pages: list[tuple[str, str, str, str | None, int | None, str]] = []
    # Per-source format overrides. A local map, never a mutation of
    # DEFAULT_SOURCES: that dict is module-level, and editing it in place would
    # leak the override into every later call in the same process.
    kind_by_source: dict[str, str] = {}
    if args.input:
        path = Path(args.input)
        if not path.is_file():
            print(f"FAIL: fixtures: {path}: file not found", file=sys.stderr)
            sys.exit(2)
        config = DEFAULT_SOURCES[sources[0]]
        pages.append((sources[0], path.read_text(encoding="utf-8", errors="replace"),
                      config["url"], "ok", None, ""))
        # An explicit --input-kind overrides the declared kind for this offline
        # read only; the fetch path below always uses the registry's kind.
        kind_by_source[sources[0]] = args.input_kind or config.get("kind", "html")
    else:
        # Opt-in only: see --fetch-ledger. `None` means do not write.
        ledger_path = None
        if args.fetch_ledger is not None:
            ledger_path = (
                Path(args.fetch_ledger)
                if args.fetch_ledger
                else Path(args.root) / FETCH_LEDGER_RELATIVE_PATH
            )
        for name in sources:
            config = DEFAULT_SOURCES[name]
            result = fetch(config["url"])
            if ledger_path is not None:
                append_fetch_row(
                    ledger_path,
                    source=name,
                    url=config["url"],
                    result=result,
                    run_id=default_run_id(),
                )
            if not isinstance(result, FetchResult):
                result = FetchResult(str(result), "ok")
            pages.append((name, result, config["url"],
                          getattr(result, "status", "ok"),
                          getattr(result, "code", None),
                          result.why()))

    fixtures: list[dict] = []
    empty: list[str] = []
    empty_cause: dict[str, str] = {}
    for name, html, url, status, code, why in pages:
        config = DEFAULT_SOURCES[name]
        # Branch on `status`, never on body emptiness. A blocked response is
        # *not* empty any more — `fetch` reads the interstitial body off the
        # HTTPError precisely so the cause can be reported — so an `if not html`
        # test would classify a Vercel challenge page as "the page fetched but no
        # fixture parsed (markup change?)", which is the exact misdiagnosis this
        # module was written to remove. The ledger is what surfaced it: the row
        # said `blocked` while the FAIL line said "markup change?".
        if status and status != "ok":
            empty.append(name)
            empty_cause[name] = _empty_cause(status, code) + (
                f" [{why}]" if why else ""
            )
            continue
        if not html:
            empty.append(name)
            empty_cause[name] = _empty_cause(status, code) + (
                f" [{why}]" if why else ""
            )
            continue
        found = parse_source(
            html,
            kind=kind_by_source.get(name, config.get("kind", "html")),
            league=config["league"],
            source=name,
            source_url=url,
        )
        if not found:
            empty.append(name)
            empty_cause[name] = (
                "the page fetched but no fixture parsed (markup change?) — "
                "capture it and add a parser fixture"
            )
        fixtures.extend(found)

    parsed = len(fixtures)
    fixtures = dedupe(
        [fixture for fixture in fixtures if in_window(fixture, now=now, days=args.days)]
    )

    # Named first, whichever branch goes on to report the run: a source that
    # fetched nothing has to be named even when the window is empty too, or the
    # message below claims "the pages parsed" about a page that never arrived.
    if empty:
        for name in empty:
            print(
                f"FAIL: fixtures: source {name!r} produced no fixtures — "
                f"{empty_cause[name]}",
                file=sys.stderr,
            )

    if not fixtures:
        # Two different failures, told apart rather than merged: "the page said
        # nothing" is a parser problem, "the page said nothing *in this window*"
        # is not — a league whose season has not started has no fixtures to
        # measure against, and reporting that as a markup change sends the
        # reader to split a page that was parsed correctly.
        if parsed:
            print(
                f"FAIL: fixtures: {parsed} fixture(s) parsed from {sources} but "
                f"none inside the {args.days}-day window from "
                f"{now.isoformat()} — the sources that did parse did so "
                "correctly; this is the window, not the markup",
                file=sys.stderr,
            )
        else:
            print(
                f"FAIL: fixtures: no parsable fixtures from {sources} "
                f"(empty or unparsable: {empty or 'none'}) — "
                + _overall_advice(empty_cause),
                file=sys.stderr,
            )
        sys.exit(1)

    if args.out and not args.dry_run:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # Idempotent append. A daily job re-observes the same 7-day window, so a
        # blind append would write every game seven times and grow the file
        # without bound. Skipping a `game_key` already present keeps the file a
        # growing *set* of distinct fixtures, which is what the recall
        # denominator needs. The candidate ledger stays a raw append-only
        # observation log; this file is a fixture list, and a game is a game.
        existing = {
            str(row.get("game_key") or "") for row in _read_jsonl(out)
        }
        fresh = [
            fixture
            for fixture in fixtures
            if str(fixture.get("game_key") or "") not in existing
        ]
        if fresh:
            with out.open("a", encoding="utf-8") as handle:
                for fixture in fresh:
                    handle.write(json.dumps(fixture, ensure_ascii=False) + "\n")
        skipped = len(fixtures) - len(fresh)
        print(
            f"OK: fixtures: wrote {len(fresh)} fixtures to {out}"
            + (f" ({skipped} already present)" if skipped else "")
        )

    # A source that produced nothing fails the run **even when another league's
    # page parsed.** It used to exit 0 with `(empty: 2)` inside an OK line — which
    # is how two of the three recall sources can sit dead (one retired domain,
    # one rate-limiting) while every gate stays green and `fixture_recall` quietly
    # means "BCL only". The docstring has always promised exit 1 for this; the
    # partial case was the one it never delivered.
    if args.json:
        print(json.dumps({"fixtures": fixtures, "empty_sources": empty}, indent=2))
    else:
        for fixture in fixtures:
            print(f"OK: fixtures: {fixture['game_key']}")
        print(
            f"OK: fixtures: {len(fixtures)} fixtures in the "
            f"{args.days}-day window (empty: {len(empty)})"
        )

    if empty:
        # After the payload: `--json` consumers still get what *was* parsed, and
        # the exit code is what says the run was incomplete.
        sys.exit(1)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


if __name__ == "__main__":
    main()
