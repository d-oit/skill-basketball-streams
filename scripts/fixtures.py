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
3. **No match means no fixture, never a guess.** A partially parsed event is
   dropped, because a phantom fixture would show up as a "missed" game that does
   not exist and would quietly make recall look worse than it is.
4. **This module is stdlib-only.** Adding a real DOM parser would mean a new
   dependency in a repo that has none outside the test suite, so the honest
   trade is fewer supported sites and a captured fixture per site — see
   `tests/fixtures/README.md` for the input/output rule.
5. **Nothing here touches the network in tests.** `--input` parses a saved page,
   which is the only path CI exercises.

Usage:
    python3 scripts/fixtures.py --input page.html --source bbl --json
    python3 scripts/fixtures.py --source bbl --source bcl --out fixtures.jsonl
    python3 scripts/fixtures.py --source bbl --now 2026-09-14T08:30:00Z --days 7

Exit codes:
    0  PASS — at least one fixture normalised
    1  FAIL — sources returned nothing parseable
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

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
        "url": "https://www.basketball-bundesliga.de/spielplan/",
    },
    "euroleague": {
        "league": "EuroLeague",
        "url": "https://www.euroleaguebasketball.net/euroleague/game-center/",
    },
    "bcl": {
        "league": "Basketball Champions League",
        "url": "https://www.championsleague.basketball/",
    },
}
EVENT_TYPES = {"sportsevent", "event"}


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


def parse_page(
    html: str, *, league: str, source: str, source_url: str = ""
) -> list[dict]:
    """JSON-LD first, microdata only as a fallback for the same page."""
    fixtures: list[dict] = []
    for node in extract_json_ld(html):
        fixture = event_to_fixture(
            node, league=league, source=source, source_url=source_url
        )
        if fixture is not None:
            fixtures.append(fixture)
    if fixtures:
        return fixtures
    return parse_microdata(html, league=league, source=source, source_url=source_url)


def fetch(url: str) -> str:
    """Plain GET. Returns "" on any failure — the caller decides what that means."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return ""


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
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

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

    pages: list[tuple[str, str, str]] = []
    if args.input:
        path = Path(args.input)
        if not path.is_file():
            print(f"FAIL: fixtures: {path}: file not found", file=sys.stderr)
            sys.exit(2)
        config = DEFAULT_SOURCES[sources[0]]
        pages.append((sources[0], path.read_text(encoding="utf-8", errors="replace"), config["url"]))
    else:
        for name in sources:
            config = DEFAULT_SOURCES[name]
            pages.append((name, fetch(config["url"]), config["url"]))

    fixtures: list[dict] = []
    empty: list[str] = []
    for name, html, url in pages:
        config = DEFAULT_SOURCES[name]
        if not html:
            empty.append(name)
            continue
        found = parse_page(
            html, league=config["league"], source=name, source_url=url
        )
        if not found:
            empty.append(name)
        fixtures.extend(found)

    fixtures = dedupe(
        [fixture for fixture in fixtures if in_window(fixture, now=now, days=args.days)]
    )

    if not fixtures:
        print(
            f"FAIL: fixtures: no parsable fixtures from {sources} "
            f"(empty or unparsable: {empty or 'none'}) — the sites may have "
            "changed their markup; capture a page and add a parser fixture",
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

    if args.json:
        print(json.dumps({"fixtures": fixtures, "empty_sources": empty}, indent=2))
    else:
        for fixture in fixtures:
            print(f"OK: fixtures: {fixture['game_key']}")
        print(
            f"OK: fixtures: {len(fixtures)} fixtures in the "
            f"{args.days}-day window (empty: {len(empty)})"
        )


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
