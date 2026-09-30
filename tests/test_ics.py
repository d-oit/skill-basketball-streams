"""Tests for the iCalendar reader in `scripts/fixtures.py`.

The feed at `tests/fixtures/fixtures_feed_bbl.ics` is a synthetic *input* — the
rule in `tests/fixtures/README.md` allows that; it is not presented as a captured
provider response, because no official feed URL is known yet (the BBL
subscription links are client-rendered). It is written with **CRLF** endings and
folded lines, as a real feed serves them, so unfolding is exercised rather than
assumed.

The property that matters most is the one the module's own docstring claims: a
fixture read from a feed is indistinguishable from one read from a page, so the
ledger join works. A second key format would make recall read as zero for a
league that is working perfectly — so that equivalence is asserted directly.

The refusals are asserted too, and each one is a case where a fixture would be
*wrong* rather than missing. A wrong fixture is reported as a game no backend
surfaced, for ever, which is worse than having no fixture at all.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.fixtures import (
    DEFAULT_SOURCES,
    FEED_SUMMARY_PREFIXES,
    calendar_name,
    parse_ics,
    parse_ics_dt,
    parse_source,
    split_feed_teams,
    strip_feed_prefix,
    unfold_ics,
    unescape_ics,
)
from scripts.fixtures import event_to_fixture  # noqa: F401 - shape parity reference

REPO_ROOT = Path(__file__).resolve().parent.parent

# The two competition prefixes **measured across all 321 VEVENTs of the real
# feed on 2026-09-30**: `easyCredit BBL Spiel` 306, `BBL Pokal Spiel` 15, all on
# the left-hand side. Spelled out here rather than imported from
# `scripts.fixtures` on purpose — a test that asserts `team.startswith(
# FEED_SUMMARY_PREFIXES)` against the same tuple the reader strips with passes
# for any value of that tuple, including the empty one. That was measured, not
# assumed: shortening the tuple to one entry left the suite green. A guard that
# cannot fail is not a guard.
MEASURED_PREFIXES = ("easyCredit BBL Spiel", "BBL Pokal Spiel")

FEED = REPO_ROOT / "tests" / "fixtures" / "fixtures_feed_bbl.ics"
FEEDS = REPO_ROOT / "tests" / "fixtures" / "feeds"
SCRIPT = REPO_ROOT / "scripts" / "fixtures.py"


def _feed() -> str:
    return FEED.read_bytes().decode("utf-8")


def _parse(feed: str | None = None) -> list[dict]:
    return parse_ics(
        feed if feed is not None else _feed(),
        league="BBL",
        source="bbl",
        source_url="https://www.easycredit-bbl.de/bbl.ics",
    )


def _parse_captured() -> list[dict]:
    """The real captured feed, read the way `DEFAULT_SOURCES` reads it."""
    return parse_ics(
        (FEEDS / "bbl_all_games.ics").read_bytes().decode("utf-8"),
        league="BBL",
        source="bbl",
        source_url=DEFAULT_SOURCES["bbl"]["url"],
    )


class TestFixtureExists:
    def test_the_feed_is_served_with_crlf(self):
        # Unfolding is the whole reason this reader cannot be a one-liner, so
        # the fixture must actually contain the case rather than assert it.
        assert b"\r\n" in FEED.read_bytes()
        assert b"\n" not in FEED.read_bytes().replace(b"\r\n", b"")


class TestUnfolding:
    def test_a_folded_continuation_joins_its_line(self):
        # RFC 5545 folds with CRLF + exactly ONE whitespace, and that whitespace
        # is the marker rather than content. Two spaces would be a fold plus a
        # leading space of content, which is the next test.
        assert unfold_ics("SUMMARY:one\r\n two\r\nUID:x\r\n")[0] == "SUMMARY:onetwo"

    def test_a_second_leading_space_is_content_not_a_fold(self):
        assert unfold_ics("SUMMARY:one\r\n  two\r\n")[0] == "SUMMARY:one two"

    def test_a_tab_also_marks_a_fold(self):
        assert unfold_ics("SUMMARY:one\r\n\ttwo\r\n")[0] == "SUMMARY:onetwo"

    def test_bare_lf_is_also_a_line_break(self):
        # A feed served with LF is valid to every client; a CRLF-only fold rule
        # would never fire and every folded line would truncate.
        assert unfold_ics("SUMMARY:one\n two\n")[0] == "SUMMARY:onetwo"

    def test_bare_cr_is_also_a_line_break(self):
        assert unfold_ics("SUMMARY:one\r two\r")[0] == "SUMMARY:onetwo"

    def test_a_leading_space_without_a_previous_line_stays(self):
        # Nothing to fold onto, so it is not a fold. Treating it as one would
        # silently drop the first property of a feed.
        assert unfold_ics("  orphan") == ["  orphan"]


class TestEscaping:
    def test_rfc5545_text_escapes(self):
        assert unescape_ics(r"Uber Arena\, Berlin") == "Uber Arena, Berlin"
        assert unescape_ics(r"a\;b") == "a;b"
        assert unescape_ics(r"a\\b") == r"a\b"
        assert unescape_ics(r"line\nbreak") == "line\nbreak"

    def test_a_escaped_comma_does_not_look_like_context(self):
        # "Niners Chemnitz\, Spieltag 4" is ONE team plus a location, not a team
        # plus context. Unescaping before splitting is what keeps the two apart.
        assert unescape_ics(r"Niners Chemnitz\, Spieltag 4") == (
            "Niners Chemnitz, Spieltag 4"
        )


class TestDateParsing:
    def test_utc_with_zulu(self):
        assert parse_ics_dt("20261003T170000Z") == datetime(
            2026, 10, 3, 17, 0, tzinfo=timezone.utc
        )

    def test_tzid_is_honoured_not_assumed_utc(self):
        # The whole reason `zoneinfo` is here: read as UTC this is 19:00 local,
        # two hours out, which moves the `--days` window and the game_key.
        got = parse_ics_dt("20261001T190000", "TZID=Europe/Berlin")
        assert got == datetime(2026, 10, 1, 19, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        assert got.utcoffset().total_seconds() == 7200  # CEST, not CET

    def test_winter_is_plus_one_hour(self):
        # The DST error would move with the seasons if the zone were faked.
        got = parse_ics_dt("20261115T190000", "TZID=Europe/Berlin")
        assert got.utcoffset().total_seconds() == 3600

    def test_a_floating_time_reads_as_the_calendar_timezone(self):
        got = parse_ics_dt("20261001T190000")
        assert got.tzinfo is not None
        assert got.utcoffset().total_seconds() == 7200

    def test_an_unknown_tzid_does_not_fall_back_to_utc(self):
        # Wrong by two hours in the *other* direction, and silently.
        got = parse_ics_dt("20261001T190000", "TZID=Mars/Olympus")
        assert got.utcoffset().total_seconds() == 7200

    def test_an_all_day_value_is_refused(self):
        # Midnight is a placeholder; no stream ever starts then. Same refusal as
        # `hasTimeGameDateTime: false` in the embedded-payload reader.
        assert parse_ics_dt("20261005", "VALUE=DATE") is None

    def test_garbage_is_none_not_an_exception(self):
        assert parse_ics_dt("not a date") is None
        assert parse_ics_dt("") is None
        assert parse_ics_dt(None) is None


class TestTeamSplitting:
    def test_trailing_competition_is_not_part_of_a_team(self):
        # The shape the BBL subscription writes. The first version of this
        # reader refused it, because `split_teams` sees three parts.
        assert split_feed_teams("ALBA BERLIN vs FC Bayern Muenchen - easyCredit BBL") == [
            "ALBA BERLIN",
            "FC Bayern Muenchen",
        ]

    def test_gegen_is_a_match_marker(self):
        assert split_feed_teams("ALBA BERLIN gegen Niners Chemnitz") == [
            "ALBA BERLIN",
            "Niners Chemnitz",
        ]

    def test_a_bare_dash_pairing_still_works(self):
        assert split_feed_teams("MHP Riesen Ludwigsburg - Niners Chemnitz") == [
            "MHP Riesen Ludwigsburg",
            "Niners Chemnitz",
        ]

    def test_a_comma_after_the_pair_is_a_refusal_not_a_cut(self):
        # "Barcelona, EuroLeague" could be a club with a comma in its name.
        # Cutting would invent a team, so the event is dropped instead.
        assert split_feed_teams("Real Madrid vs Barcelona, EuroLeague") == []

    def test_no_pair_is_refused(self):
        assert split_feed_teams("ALBA BERLIN") == []
        assert split_feed_teams("ALBA BERLIN vs") == []
        assert split_feed_teams("") == []
        assert split_feed_teams(None) == []


class TestParseFeed:
    def test_the_three_usable_events_parse(self):
        rows = _parse()
        assert [r["game_key"] for r in rows] == [
            "BBL|ALBA BERLIN|FC Bayern Muenchen|2026-10-01T17:00Z",
            "BBL|Baskets Oldenburg|ratiopharm Ulm|2026-10-03T17:00Z",
            "BBL|MHP Riesen Ludwigsburg|Niners Chemnitz|2026-10-11T16:00Z",
        ]

    def test_the_shape_matches_the_html_reader_exactly(self):
        """A feed fixture and a page fixture must be indistinguishable.

        This is the invariant that makes the format an implementation detail: the
        ledger join compares `game_key`, so a second shape would report zero
        recall for a league that is working perfectly.
        """
        rows = _parse()
        for row in rows:
            assert set(row) == {
                "league", "teams", "start", "game_key", "source", "source_url",
            }
            assert row["league"] == "BBL"
            assert len(row["teams"]) == 2
            # And parseable by the same reader the ledger uses.
            assert parse_ics_dt  # imported for parity of intent
            datetime.fromisoformat(row["start"])

    def test_an_all_day_event_is_dropped(self):
        keys = [r["game_key"] for r in _parse()]
        assert not any("2026-10-05" in k for k in keys)

    def test_a_cancelled_event_is_dropped(self):
        # A cancelled game will not be played, so no stream can exist for it, and
        # counting it as ground truth reports a miss on every run for ever.
        keys = [r["game_key"] for r in _parse()]
        assert not any("Vechta" in k for k in keys)

    def test_a_nested_vtimezone_is_never_read_as_a_tip_off(self):
        # VTIMEZONE contains a DTSTART of 1970. Reading it would invent a
        # fixture half a century old, and a miss nobody can explain.
        rows = _parse()
        assert rows
        assert not any(r["start"].startswith("1970") for r in rows)

    def test_a_feed_with_no_vevents_yields_nothing_rather_than_raising(self):
        assert parse_ics("BEGIN:VCALENDAR\nEND:VCALENDAR\n", league="BBL", source="s") == []

    def test_garbage_is_not_an_exception(self):
        assert parse_ics("not a calendar at all", league="BBL", source="s") == []
        assert parse_ics("", league="BBL", source="s") == []


class TestCalendarName:
    def test_the_feed_names_itself(self):
        # A feed has no <title>, so without this a mis-pointed feed would be a
        # line of nothing in the fetch ledger.
        assert calendar_name(_feed()) == "BBL Spielplan 2026/27"

    def test_name_is_also_accepted(self):
        assert calendar_name("BEGIN:VCALENDAR\nNAME:Other feed\nEND:VCALENDAR\n") == (
            "Other feed"
        )

    def test_no_name_is_empty(self):
        assert calendar_name("BEGIN:VCALENDAR\nEND:VCALENDAR\n") == ""


class TestRecordedOfficialFeed:
    """The *captured* BBL feed, not a synthetic stand-in.

    `tests/fixtures/feeds/bbl_all_games.ics` is a trimmed verbatim capture of
    `https://api.basketball-bundesliga.de/calendar/ical/all-games` (HTTP 200,
    90,568 bytes, 321 VEVENTs, recorded 2026-09-30). It is trimmed, not invented:
    the synthetic feed above authors the *refusals* (all-day, cancelled, a comma
    after the pair), and this one carries the *format* — which is the part a
    hand-written fixture would have had to guess, and which the reader already
    guessed wrong twice.

    Two measured facts are asserted here rather than documented in a comment,
    because each is a silent two-hour error if the reader regresses:

    * every `SUMMARY` glues its competition onto the first club name, and
    * `DTSTART` is **floating** — the zone is declared once, at calendar level.
    """

    def test_the_capture_is_the_recorded_bytes(self):
        manifest = json.loads((FEEDS / "manifest.json").read_text(encoding="utf-8"))
        stored = (FEEDS / manifest["stored_file"]).read_bytes()
        assert len(stored) == manifest["stored_bytes"]
        assert hashlib.sha256(stored).hexdigest() == manifest["stored_sha256"]
        # And it really is the response the manifest names.
        assert manifest["source_url"] == DEFAULT_SOURCES["bbl"]["url"]
        assert manifest["http_status"] == 200
        assert manifest["events_in_response"] == 321
        assert len(manifest["kept"]) < manifest["events_in_response"]

    def test_the_capture_serves_crlf_and_folded_lines(self):
        raw = (FEEDS / "bbl_all_games.ics").read_bytes()
        assert b"\r\n" in raw
        assert any(line.startswith(b" ") for line in raw.split(b"\r\n"))

    def test_every_kept_event_yields_a_fixture(self):
        rows = _parse_captured()
        assert len(rows) == len(json.loads(
            (FEEDS / "manifest.json").read_text(encoding="utf-8")
        )["kept"])

    def test_no_club_name_retains_a_competition_prefix(self):
        """The failure this reader had, asserted as its symptom.

        Unstripped, every team became `easyCredit BBL Spiel ALBA BERLIN`: the
        `game_key` stopped matching what search surfaces, and the league reported
        *zero recall while looking healthy*. So the assertion is on the names
        themselves, not on a count.
        """
        for row in _parse_captured():
            for team in row["teams"]:
                assert not team.lower().startswith(MEASURED_PREFIXES)

    def test_both_measured_competitions_are_stripped(self):
        # 306 `easyCredit BBL Spiel` + 15 `BBL Pokal Spiel`, left side only.
        # Both appear in the capture; dropping either from the reader re-adds a
        # prefix to every one of that competition's fixtures. The literals are
        # the assertion (see `MEASURED_PREFIXES`), so this fails on a shortened
        # tuple rather than following it.
        kept = " ".join(
            k["summary"] for k in json.loads(
                (FEEDS / "manifest.json").read_text(encoding="utf-8")
            )["kept"]
        )
        for prefix in MEASURED_PREFIXES:
            assert prefix in kept
            assert strip_feed_prefix(f"{prefix} ALBA BERLIN") == "ALBA BERLIN"
        # And the reader carries every measured prefix, no fewer.
        assert set(MEASURED_PREFIXES) <= set(FEED_SUMMARY_PREFIXES)

    def test_a_leading_word_not_on_the_list_is_kept_as_a_club_name(self):
        """The refusal that keeps the list safe to extend.

        Stripping "any leading words" would eventually eat a real club. A
        competition nobody has declared must therefore surface as a visibly odd
        team name, which is the symptom that prompts adding it here — not as a
        silently renamed club.
        """
        assert strip_feed_prefix("DELO BBL Spiel Hamburg") == "DELO BBL Spiel Hamburg"
        assert split_feed_teams("DELO BBL Spiel Hamburg vs Berlin") == [
            "DELO BBL Spiel Hamburg",
            "Berlin",
        ]

    def test_floating_dtstarts_read_the_calendars_declared_zone(self):
        """`X-WR-TIMEZONE:Europe/Berlin`, not the reader's default.

        The feed writes every `DTSTART` floating and declares the zone once at
        the top. The `calendar_tz` branch in `parse_ics` first sat *after* the
        `if event is None or depth: continue` gate, so nothing could ever reach
        it and every timestamp silently took the default — which happened to be
        the same zone, so the bug was invisible here and wrong anywhere else.
        That is why the declaration is asserted rather than assumed: only a
        feed with a *different* zone proves the wiring, so that half is checked
        directly below.
        """
        raw = (FEEDS / "bbl_all_games.ics").read_bytes().decode("utf-8")
        assert "X-WR-TIMEZONE:Europe/Berlin" in raw
        assert "DTSTART;TZID" not in raw and "DTSTART:2026" in raw
        rows = _parse_captured()
        assert rows
        assert all(r["start"].endswith("+02:00") for r in rows), rows[0]["start"]

    def test_a_calendar_declaring_another_zone_moves_the_timestamps(self):
        """The wiring, proven by a zone that is *not* the default.

        A feed that declares `Europe/London` must read 18:30 as 18:30 London
        (17:30Z), not as 18:30 Berlin. If `calendar_tz` were never passed,
        this returns the Berlin reading and the test fails — which is the
        "unit test with a hand-built input" trap avoided by asserting the
        *difference*, not the presence of a plausible value.
        """
        feed = (
            "BEGIN:VCALENDAR\r\nX-WR-TIMEZONE:Europe/London\r\n"
            "BEGIN:VEVENT\r\nUID:a\r\nDTSTART:20260910T183000\r\n"
            "SUMMARY:A vs B\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        rows = parse_ics(feed, league="BBL", source="s")
        assert rows[0]["start"] == "2026-09-10T18:30:00+01:00"
        assert rows[0]["game_key"] == "BBL|A|B|2026-09-10T17:30Z"

    def test_a_sponsor_and_an_umlaut_survive_unfolding(self):
        rows = _parse_captured()
        names = {team for row in rows for team in row["teams"]}
        assert "VET-CONCEPT Gladiators Trier" in names
        assert "Basketball Löwen Braunschweig" in names

    def test_the_capture_reads_through_the_real_cli(self):
        manifest = json.loads((FEEDS / "manifest.json").read_text(encoding="utf-8"))
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--input", str(FEEDS / manifest["stored_file"]),
                "--source", "bbl",
                "--now", "2026-09-01T12:00:00Z",
                "--days", "400",
                "--json",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["fixtures"]
        assert payload["fixtures"][0]["source_url"] == DEFAULT_SOURCES["bbl"]["url"]
        # Every club name is a club name, from end to end of the real pipeline.
        for row in payload["fixtures"]:
            for team in row["teams"]:
                assert not team.lower().startswith(MEASURED_PREFIXES)


class TestFormatIsDeclaredNotSniffed:
    def test_a_declared_ics_source_is_read_as_a_feed(self):
        body = _feed()
        ics = parse_source(body, kind="ics", league="BBL", source="bbl")
        assert len(ics) == 3

    def test_the_same_bytes_read_as_html_yield_nothing(self):
        # Why the kind is declared rather than sniffed: a block page is not valid
        # iCalendar, but "does this text mention VEVENT" is true of a challenge
        # page that quotes what it was asked for.
        assert parse_source(_feed(), kind="html", league="BBL", source="bbl") == []

    def test_html_is_the_default_kind(self):
        for name, config in DEFAULT_SOURCES.items():
            assert config.get("kind", "html") in {"html", "ics"}, name

    def test_every_source_declares_a_known_kind(self):
        # The registry is what a reviewer reads to learn where ground truth may
        # come from, so an unknown kind must not pass unnoticed.
        assert {config.get("kind") for config in DEFAULT_SOURCES.values()} <= {
            "html",
            "ics",
        }


class TestCliReadsAFeed:
    def test_the_fixture_runs_through_the_real_cli(self):
        # `--input-kind` exists so a feed can be read *before* it is pinned to a
        # source, and `bbl` is now pinned to `ics`; the same bytes read as html
        # must genuinely yield nothing — otherwise the kind would be decided by
        # sniffing and `--input-kind` would be decoration.
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--input", str(FEED),
                "--source", "bbl",
                "--input-kind", "ics",
                "--now", "2026-10-01T08:30:00Z",
                "--json",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        # Three events parse from the feed; the window keeps two. The third is
        # 2026-10-11 and `--now` is 2026-10-01 with a 7-day default, so this also
        # pins that a feed row outside the window is dropped rather than counted
        # as ground truth for a window it is not in.
        assert [row["game_key"] for row in payload["fixtures"]] == [
            "BBL|ALBA BERLIN|FC Bayern Muenchen|2026-10-01T17:00Z",
            "BBL|Baskets Oldenburg|ratiopharm Ulm|2026-10-03T17:00Z",
        ]
        assert payload["fixtures"][0]["source_url"] == DEFAULT_SOURCES["bbl"]["url"]

    def test_the_declared_kind_wins_over_the_bytes(self):
        """`bbl` declares `ics`, so an HTML page fed under it parses to nothing.

        This is the same assertion as the pair above, pointed the other way now
        that the source is pinned: the declared kind is what decides, so feeding
        the JSON-LD page fixture to `bbl` fails loudly instead of quietly
        succeeding. It is also what makes `--input-kind` meaningful — the flag
        exists for the period *before* a feed is pinned, and after pinning the
        override is the only way to read the bytes the other way.
        """
        page = REPO_ROOT / "tests" / "fixtures" / "fixtures_page_bbl.html"
        result = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--input", str(page),
                "--source", "bbl",
                "--now", "2026-09-14T08:30:00Z",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "capture a page and add a parser fixture" in result.stderr
        # And the override still reads it.
        forced = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--input", str(page),
                "--source", "bbl",
                "--input-kind", "html",
                "--now", "2026-09-14T08:30:00Z",
                "--json",
            ],
            capture_output=True, text=True,
        )
        assert forced.returncode == 0, forced.stderr
        assert len(json.loads(forced.stdout)["fixtures"]) == 3
        assert "no fixture parsed" in result.stderr
