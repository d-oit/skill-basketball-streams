"""Tests for scripts/fixtures.py.

Phase 5 exists to measure the failure the search-derived metric structurally
cannot see — a game that *no* backend surfaced. Every test here defends the rule
that makes that measurement trustworthy: **no match means no fixture**. A
half-parsed event would be reported as a missed game that does not exist, which
would make recall look worse than reality and send someone hunting a phantom.

The two BBL page fixtures are synthetic *inputs* (allowed — see
`tests/fixtures/README.md`): one JSON-LD, one microdata-only. The BCL one is a
**recorded** response reduced to three games, because the shape it pins — a
Next.js flight payload — is a provider's output, and a hand-written version of
it would prove the test rather than the page. No test touches the network.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

from scripts.fixtures import (
    DEFAULT_SOURCES,
    dedupe,
    event_to_fixture,
    extract_embedded_games,
    extract_json_ld,
    game_to_fixture,
    in_window,
    parse_dt,
    parse_microdata,
    parse_page,
    split_teams,
)
from scripts.source_learning import approved_domains, load_sources

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "fixtures.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
JSONLD_PAGE = FIXTURES / "fixtures_page_bbl.html"
MICRODATA_PAGE = FIXTURES / "fixtures_page_microdata.html"
BCL_PAGE = FIXTURES / "fixtures_page_bcl_payload.html"
NOW = datetime(2026, 9, 14, 8, 30, tzinfo=timezone.utc)
# The first day of the BCL season inside the recorded page's own list.
BCL_GAME_DAY = "2026-10-06T08:30:00Z"

BBL = DEFAULT_SOURCES["bbl"]
BCL = DEFAULT_SOURCES["bcl"]


def _host(value: str) -> str:
    """A bare, lowercase, `www.`-stripped host from a URL *or* a bare host.

    `config/sources.json` lists social accounts as host+path
    (`x.com/FIBA`), so the same normaliser has to accept both shapes.
    """
    host = value.split("/")[0].strip().lower()
    return host[4:] if host.startswith("www.") else host


class TestFixtureSourcesAreApproved:
    """Every league page live code fetches must sit on an approved host.

    `config/sources.json` is the machine-readable mirror of the approved-source
    list, and `DEFAULT_SOURCES` is what `fixtures.py` actually fetches. Nothing
    connected the two, so when BBL moved to `easycredit-bbl.de` — recorded in
    the registry's own note and re-verified 2026-09-17 — the registry was
    migrated and the fetcher was not. `DEFAULT_SOURCES["bbl"]` kept
    `https://www.basketball-bundesliga.de/spielplan/`, a host that fails TLS SNI
    (`tlsv1 unrecognized name`) while DNS still resolves, so the BBL source
    could never produce a fixture: its `fixture_recall` contribution was
    permanently zero and the daily run had been reporting a TLS error where the
    real problem was a stale URL.

    This is the AGENTS.md trap "a rule that reads a field nothing writes" in its
    other direction: the registry carried the correction and nothing asserted
    the fetcher obeyed it, so the correction reached every document and no code
    path. A test that asserts the *current* URL would pass again the day the
    league moves; asserting the *relation* fails for every future move.
    """

    def test_every_fixture_source_is_on_an_approved_host(self):
        approved = {_host(d) for d in approved_domains(load_sources(REPO_ROOT))}
        offending = {
            name: cfg["url"]
            for name, cfg in DEFAULT_SOURCES.items()
            if _host(urlparse(cfg["url"]).hostname or "") not in approved
        }
        assert not offending, (
            "scripts/fixtures.py fetches a host that config/sources.json does "
            f"not approve: {offending}. A league page must be approved before "
            "it is fetched, or Check 3 is not being honoured by the tooling "
            "that measures it."
        )

    def test_the_registry_approves_the_bbl_host_the_fetcher_uses(self):
        """The specific correction, so a future rename is traceable.

        Kept separately from the relation above on purpose: the relation says
        "these agree", this says "and they agree on *this*", which is the fact
        a reviewer of the 2026-09-30 change wants to see.
        """
        assert _host(urlparse(BBL["url"]).hostname or "") == "easycredit-bbl.de"
        assert "basketball-bundesliga.de" not in BBL["url"]


def _payload_page(payload: object, *, splits: int = 1) -> str:
    """A page carrying `payload` as a Next flight stream, in `splits` chunks.

    Next streams its data, so a real value is routinely cut in half between two
    calls; joining the literals is the only way the tail of one chunk and the
    head of the next become readable JSON again.
    """
    text = json.dumps(payload, ensure_ascii=False)
    size = max(1, (len(text) + splits - 1) // splits)
    chunks = [text[index:index + size] for index in range(0, len(text), size)]
    calls = "\n".join(
        f"<script>self.__next_f.push([1,{json.dumps(chunk)}])</script>"
        for chunk in chunks
    )
    return f"<html><body>{calls}</body></html>"


def _page(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestParseDt:
    def test_z_and_offset(self):
        assert parse_dt("2026-09-16T19:00:00Z").tzinfo is timezone.utc
        assert parse_dt("2026-09-16T19:00:00+02:00").utcoffset().total_seconds() == 7200

    def test_naive_is_assumed_utc(self):
        assert parse_dt("2026-09-16T19:00:00").tzinfo is timezone.utc

    @pytest.mark.parametrize("bad", [None, "", "   ", "20.09.2026 15:00", 5])
    def test_unusable_returns_none(self, bad):
        assert parse_dt(bad) is None


class TestSplitTeams:
    @pytest.mark.parametrize(
        "text",
        [
            "ALBA Berlin vs FC Bayern Muenchen",
            "ALBA Berlin - FC Bayern Muenchen",
            "ALBA Berlin gegen FC Bayern Muenchen",
            "ALBA Berlin vs. FC Bayern Muenchen",
            "ALBA Berlin – FC Bayern Muenchen",
        ],
    )
    def test_recognised_separators(self, text):
        assert len(split_teams(text)) == 2

    @pytest.mark.parametrize(
        "text", ["", "single team", "a - b - c", None, "ALBA Berlin vs"]
    )
    def test_anything_but_exactly_two_is_rejected(self, text):
        assert split_teams(text) == []


class TestExtractJsonLd:
    def test_graph_is_flattened(self):
        nodes = extract_json_ld(_page(JSONLD_PAGE))
        assert any(node.get("@type") == "SportsEvent" for node in nodes)
        assert any(node.get("@type") == "Basketball" for node in nodes)

    def test_bare_list_payload(self):
        html = (
            '<script type="application/ld+json">'
            '[{"@type":"SportsEvent","name":"A vs B"}]'
            "</script>"
        )
        assert len(extract_json_ld(html)) == 1

    def test_invalid_json_is_skipped_not_raised(self):
        html = '<script type="application/ld+json">{not json}</script>'
        assert extract_json_ld(html) == []

    def test_no_script_is_empty(self):
        assert extract_json_ld("<html><body>hi</body></html>") == []


class TestEventToFixture:
    def test_name_based_pair(self):
        fixture = event_to_fixture(
            {
                "@type": "SportsEvent",
                "name": "ALBA Berlin vs FC Bayern Muenchen",
                "startDate": "2026-09-16T19:00:00+02:00",
            },
            league="BBL",
            source="bbl",
        )
        assert fixture["teams"] == ["ALBA Berlin", "FC Bayern Muenchen"]

    def test_competitor_nodes_win_over_name(self):
        fixture = event_to_fixture(
            {
                "@type": "SportsEvent",
                "name": "Spieltag 3",
                "startDate": "2026-09-17T20:30:00+02:00",
                "competitor": [
                    {"@type": "SportsTeam", "name": "Bonn"},
                    {"@type": "SportsTeam", "name": "Ulm"},
                ],
            },
            league="BBL",
            source="bbl",
        )
        assert set(fixture["teams"]) == {"Bonn", "Ulm"}

    def test_missing_start_date_is_rejected(self):
        assert (
            event_to_fixture(
                {"@type": "SportsEvent", "name": "A vs B"}, league="BBL", source="bbl"
            )
            is None
        )

    def test_unidentifiable_teams_are_rejected(self):
        # One competitor and an unsplittable name: cannot be joined to a
        # candidate, so it must not become fixture-shaped data.
        assert (
            event_to_fixture(
                {
                    "@type": "SportsEvent",
                    "name": "Spieltag 3",
                    "startDate": "2026-09-17T20:30:00+02:00",
                    "competitor": [{"name": "Bonn"}],
                },
                league="BBL",
                source="bbl",
            )
            is None
        )

    def test_non_sportsevent_is_rejected(self):
        assert (
            event_to_fixture(
                {
                    "@type": "Basketball",
                    "name": "A vs B",
                    "startDate": "2026-09-17T20:30:00+02:00",
                },
                league="BBL",
                source="bbl",
            )
            is None
        )

    def test_game_key_matches_the_ledger_shape(self):
        """The key must be byte-identical to `upsert_events.game_key` or the join
        silently reports every game as unseen."""
        fixture = event_to_fixture(
            {
                "@type": "SportsEvent",
                "name": "FC Bayern Muenchen vs ALBA Berlin",
                "startDate": "2026-09-16T19:00:00+02:00",
            },
            league="BBL",
            source="bbl",
        )
        assert fixture["game_key"] == "BBL|ALBA Berlin|FC Bayern Muenchen|2026-09-16T17:00Z"

    def test_type_as_list_is_accepted(self):
        fixture = event_to_fixture(
            {
                "@type": ["Event", "SportsEvent"],
                "name": "A vs B",
                "startDate": "2026-09-16T19:00:00+02:00",
            },
            league="BBL",
            source="bbl",
        )
        assert fixture is not None


class TestParseMicrodata:
    def test_datetime_attribute_is_used(self):
        found = parse_microdata(
            _page(MICRODATA_PAGE), league="BBL", source="bbl"
        )
        assert len(found) == 1
        assert found[0]["teams"] == ["MHP Riesen Ludwigsburg", "Wuerzburg Baskets"]

    def test_event_without_a_start_date_is_dropped(self):
        # The fixture page contains exactly such a block.
        html = (
            '<div itemscope itemtype="https://schema.org/SportsEvent">'
            '<span itemprop="name">A - B</span></div>'
        )
        assert parse_microdata(html, league="BBL", source="bbl") == []

    def test_meta_content_attribute_carries_the_date(self):
        html = (
            '<div itemscope itemtype="https://schema.org/SportsEvent">'
            '<meta itemprop="name" content="A - B">'
            '<meta itemprop="startDate" content="2026-09-20T15:00:00+02:00">'
            "</div>"
        )
        found = parse_microdata(html, league="BBL", source="bbl")
        assert len(found) == 1

    def test_unclosed_block_is_still_flushed(self):
        html = (
            '<div itemscope itemtype="https://schema.org/SportsEvent">'
            '<span itemprop="name">A - B</span>'
            '<time itemprop="startDate" datetime="2026-09-20T15:00:00+02:00">x</time>'
        )
        assert len(parse_microdata(html, league="BBL", source="bbl")) == 1


class TestParsePage:
    def test_json_ld_is_preferred_and_microdata_ignored(self):
        found = parse_page(_page(JSONLD_PAGE), league="BBL", source="bbl")
        # Three usable events; the unusable one and the non-event are dropped,
        # and the microdata block is NOT added on top.
        assert len(found) == 3
        assert not any("Ludwigsburg" in team for f in found for team in f["teams"])

    def test_falls_back_to_microdata_when_there_is_no_json_ld(self):
        found = parse_page(
            _page(MICRODATA_PAGE),
            league=BBL["league"],
            source="bbl",
            source_url=BBL["url"],
        )
        assert len(found) == 1
        assert found[0]["source_url"] == BBL["url"]

    def test_a_page_with_neither_shape_yields_nothing(self):
        assert parse_page("<html><body>hi</body></html>", league="BBL", source="bbl") == []


class TestEmbeddedPayload:
    """The third rung: a site that publishes neither JSON-LD nor microdata.

    `championsleague.basketball` is that site. Its game list is a Next.js flight
    payload (`self.__next_f.push`), so before this rung its fixtures could not be
    measured at all and its `fixture_recall` was `n/a` for ever.
    """

    def test_the_payload_is_joined_across_push_calls(self):
        payload = {"data": {"games": [{"gameId": 1}]}}
        assert extract_embedded_games(_payload_page(payload, splits=4)) == [
            {"gameId": 1}
        ]

    def test_a_value_split_between_two_chunks_is_still_read(self):
        # The realistic version of the test above: the boundary falls inside a
        # game object, which is what a streamed payload does on its own.
        payload = {"data": {"games": [{"gameId": 136223, "teamA": {"code": "VILN"}}]}}
        assert extract_embedded_games(_payload_page(payload, splits=7)) == [
            {"gameId": 136223, "teamA": {"code": "VILN"}}
        ]

    def test_a_round_start_date_is_not_a_game(self):
        """The payload is full of `startDate`/`endDate` pairs belonging to
        rounds. Reading one as a fixture would invent a game with no teams."""
        page = _payload_page(
            {
                "data": {
                    "round": {
                        "roundCode": "RS",
                        "startDate": "2026-10-06T00:00:00",
                        "endDate": "2026-12-23T23:59:59.9999999",
                    }
                }
            }
        )
        assert parse_page(page, league=BCL["league"], source="bcl") == []

    def test_a_non_event_json_ld_node_falls_through_to_the_payload(self):
        """A game page's only `ld+json` node is a `BreadcrumbList`. Presenting
        JSON-LD is not the same as presenting a fixture, so the rung above must
        yield nothing and let the payload be read."""
        breadcrumb = (
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"BreadcrumbList",'
            '"itemListElement":[{"@type":"ListItem","position":1}]}</script>'
        )
        page = _payload_page(
            {
                "data": {
                    "games": [
                        {
                            "gameId": 2,
                            "teamA": {"shortName": "Rytas Vilnius"},
                            "teamB": {"shortName": "Sabah BC"},
                            "gameDateTimeUTC": "2026-10-06T16:30:00",
                            "hasTimeGameDateTime": True,
                        }
                    ]
                }
            }
        )
        found = parse_page(breadcrumb + page, league=BCL["league"], source="bcl")
        assert [tuple(f["teams"]) for f in found] == [("Rytas Vilnius", "Sabah BC")]

    def test_the_real_page_yields_its_scheduled_games(self):
        found = parse_page(
            _page(BCL_PAGE), league=BCL["league"], source="bcl", source_url=BCL["url"]
        )
        assert len(found) == 2
        assert {tuple(f["teams"]) for f in found} == {
            ("Rytas Vilnius", "Sabah BC"),
            ("Trabzonspor", "Nanterre 92"),
        }
        keys = {f["game_key"] for f in found}
        assert keys == {
            "Basketball Champions League|Rytas Vilnius|Sabah BC|2026-10-06T16:30Z",
            "Basketball Champions League|Nanterre 92|Trabzonspor|2026-10-07T16:00Z",
        }
        # The name that becomes the key is the site's own display name, which is
        # what the exact-string join against the candidate ledger compares.
        assert all(f["source"] == "bcl" and f["source_url"] == BCL["url"] for f in found)

    def test_the_placeholder_game_is_not_a_fixture(self):
        """46 of the page's 136 rows are season placeholders: no tip-off
        (`hasTimeGameDateTime: false`) and one side still `null`. Counted, each
        would be a game no stream could ever match — a phantom miss."""
        games = extract_embedded_games(_page(BCL_PAGE))
        placeholder = [g for g in games if g.get("hasTimeGameDateTime") is False]
        assert len(placeholder) == 1
        assert placeholder[0]["teamA"] is None
        assert game_to_fixture(
            placeholder[0], league=BCL["league"], source="bcl"
        ) is None
        found = parse_page(_page(BCL_PAGE), league=BCL["league"], source="bcl")
        assert not any("Windrose Giants Antwerp" in f["teams"] for f in found)
        assert not any(f["start"].startswith("2026-11-03") for f in found)

    def test_a_game_with_no_pair_of_teams_is_not_a_fixture(self):
        game = {
            "gameId": 9,
            "teamA": None,
            "teamB": {"shortName": "Sabah BC"},
            "gameDateTimeUTC": "2026-10-06T16:30:00",
            "hasTimeGameDateTime": True,
        }
        assert game_to_fixture(game, league=BCL["league"], source="bcl") is None

    def test_a_game_with_no_tip_off_is_not_a_fixture(self):
        game = {
            "gameId": 9,
            "teamA": {"shortName": "Rytas Vilnius"},
            "teamB": {"shortName": "Sabah BC"},
            "gameDateTimeUTC": "2026-10-06T00:00:00",
            "hasTimeGameDateTime": False,
        }
        assert game_to_fixture(game, league=BCL["league"], source="bcl") is None

    def test_the_name_falls_back_through_short_official_and_code(self):
        game = {
            "gameId": 9,
            "teamA": {"code": "VILN"},
            "teamB": {"officialName": "Sabah BC"},
            "gameDateTimeUTC": "2026-10-06T16:30:00",
        }
        fixture = game_to_fixture(game, league=BCL["league"], source="bcl")
        assert fixture is not None
        assert sorted(fixture["teams"]) == ["Sabah BC", "VILN"]


class TestWindow:
    def test_inside_the_window(self):
        fixture = {"start": "2026-09-16T19:00:00+02:00"}
        assert in_window(fixture, now=NOW, days=7)

    def test_yesterday_is_outside(self):
        assert not in_window({"start": "2026-09-13T19:00:00+02:00"}, now=NOW, days=7)

    def test_beyond_the_window_is_outside(self):
        assert not in_window({"start": "2026-09-30T19:00:00+02:00"}, now=NOW, days=7)

    def test_last_day_of_the_window_is_inside(self):
        assert in_window({"start": "2026-09-21T12:00:00+02:00"}, now=NOW, days=7)

    def test_unparseable_start_is_outside(self):
        assert not in_window({"start": "nonsense"}, now=NOW, days=7)


class TestDedupe:
    def test_one_row_per_game_key_sorted(self):
        fixtures = [
            {"game_key": "B", "source": "a"},
            {"game_key": "A", "source": "b"},
            {"game_key": "B", "source": "c"},
            {"game_key": "", "source": "d"},
        ]
        assert [f["game_key"] for f in dedupe(fixtures)] == ["A", "B"]

    def test_empty_input(self):
        assert dedupe([]) == []


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestCli:
    def test_json_ld_input(self):
        result = _run(
            ["--input", str(JSONLD_PAGE), "--source", "bbl",
             "--now", "2026-09-14T08:30:00Z", "--json"]
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert len(payload["fixtures"]) == 3
        assert payload["empty_sources"] == []

    def test_microdata_input(self):
        result = _run(
            ["--input", str(MICRODATA_PAGE), "--source", "bbl",
             "--now", "2026-09-14T08:30:00Z"]
        )
        assert result.returncode == 0
        assert "fixtures: 1 fixtures" in result.stdout

    def test_days_filter_excludes_out_of_window_games(self):
        # `--days 2` means today plus the next two days inclusive, so of the
        # fixture page's games (16th, 17th, 18th) only the 16th qualifies.
        result = _run(
            ["--input", str(JSONLD_PAGE), "--source", "bbl",
             "--now", "2026-09-14T08:30:00Z", "--days", "2", "--json"]
        )
        assert result.returncode == 0, result.stderr
        fixtures = json.loads(result.stdout)["fixtures"]
        assert len(fixtures) == 1
        assert "2026-09-16" in fixtures[0]["start"]

    def test_out_writes_jsonl_and_dry_run_does_not(self, tmp_path):
        out = tmp_path / "fixtures.jsonl"
        args = ["--input", str(JSONLD_PAGE), "--source", "bbl",
                "--now", "2026-09-14T08:30:00Z", "--out", str(out)]
        assert _run([*args, "--dry-run"]).returncode == 0
        assert not out.exists()
        assert _run(args).returncode == 0
        assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 3

    def test_out_is_idempotent_across_runs(self, tmp_path):
        # The telemetry job re-observes the same window every day. A blind
        # append would write each game once per day forever, so the file would
        # grow without bound and fixture_recall would end up measuring file
        # length instead of coverage.
        out = tmp_path / "fixtures.jsonl"
        args = ["--input", str(JSONLD_PAGE), "--source", "bbl",
                "--now", "2026-09-14T08:30:00Z", "--out", str(out)]
        for _ in range(3):
            assert _run(args).returncode == 0
        assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 3

    def test_out_reports_how_many_were_already_present(self, tmp_path):
        out = tmp_path / "fixtures.jsonl"
        args = ["--input", str(JSONLD_PAGE), "--source", "bbl",
                "--now", "2026-09-14T08:30:00Z", "--out", str(out)]
        assert _run(args).returncode == 0
        second = _run(args)
        assert second.returncode == 0
        assert "already present" in second.stdout

    def test_out_appends_only_new_games(self, tmp_path):
        """A later run must still be able to add a game the first run missed."""
        out = tmp_path / "fixtures.jsonl"
        first = ["--input", str(JSONLD_PAGE), "--source", "bbl",
                 "--now", "2026-09-16T08:30:00Z", "--days", "1", "--out", str(out)]
        assert _run(first).returncode == 0
        before = len(out.read_text(encoding="utf-8").strip().splitlines())
        wider = ["--input", str(JSONLD_PAGE), "--source", "bbl",
                 "--now", "2026-09-14T08:30:00Z", "--days", "7", "--out", str(out)]
        assert _run(wider).returncode == 0
        after = len(out.read_text(encoding="utf-8").strip().splitlines())
        assert after >= before

    def test_unknown_source_is_a_usage_error(self):
        result = _run(["--input", str(JSONLD_PAGE), "--source", "nba"])
        assert result.returncode == 2
        assert "unknown source" in result.stderr

    def test_input_with_multiple_sources_is_a_usage_error(self):
        result = _run(
            ["--input", str(JSONLD_PAGE), "--source", "bbl", "--source", "bcl"]
        )
        assert result.returncode == 2

    def test_missing_input_file_is_a_usage_error(self, tmp_path):
        result = _run(["--input", str(tmp_path / "nope.html"), "--source", "bbl"])
        assert result.returncode == 2

    def test_bad_now_is_a_usage_error(self):
        result = _run(["--input", str(JSONLD_PAGE), "--source", "bbl", "--now", "soon"])
        assert result.returncode == 2

    def test_embedded_payload_input(self):
        result = _run(
            ["--input", str(BCL_PAGE), "--source", "bcl",
             "--now", BCL_GAME_DAY, "--json"]
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert len(payload["fixtures"]) == 2
        assert payload["empty_sources"] == []

    def test_unparsable_page_exits_one(self, tmp_path):
        page = tmp_path / "empty.html"
        page.write_text("<html><body>nothing here</body></html>", encoding="utf-8")
        result = _run(["--input", str(page), "--source", "bbl"])
        assert result.returncode == 1
        assert "capture a page and add a parser fixture" in result.stderr

    def test_parsed_but_out_of_window_is_its_own_failure(self):
        """A league whose season has not started parses perfectly and still has
        nothing in the window. Reporting that as a markup change sends the
        reader to split a page that was parsed correctly."""
        result = _run(
            ["--input", str(BCL_PAGE), "--source", "bcl", "--now", "2026-09-18T08:30:00Z"]
        )
        assert result.returncode == 1
        assert "2 fixture(s) parsed" in result.stderr
        assert "none inside the 7-day window" in result.stderr
        assert "capture a page and add a parser fixture" not in result.stderr

    def test_diff_mode_reports_the_unseen_game(self):
        result = _run(
            ["--ledger", str(FIXTURES / "candidates_ledger.jsonl"),
             "--fixtures", str(FIXTURES / "league_fixtures.jsonl"), "--json"]
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["fixtures"] == 3
        assert payload["surfaced"] == 2
        assert payload["unseen"] == 1
        assert payload["unseen_keys"] == [
            "BBL|EWE Baskets Oldenburg|Hamburg Towers|2026-09-18T17:00Z"
        ]

    def test_diff_mode_with_no_fixtures_exits_one(self, tmp_path):
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        result = _run(
            ["--ledger", str(FIXTURES / "candidates_ledger.jsonl"),
             "--fixtures", str(empty)]
        )
        assert result.returncode == 1
        assert "nothing to measure" in result.stderr

    def test_diff_mode_requires_both_files(self):
        assert _run(["--fixtures", str(FIXTURES / "league_fixtures.jsonl")]).returncode == 2


class TestPartialSourceFailure:
    """A source that yields nothing fails the run even when another parsed.

    The old contract was exit 0 with `(empty: 2)` inside an OK line, which is
    how two of the three recall sources can be dead at once — one retired
    domain, one rate-limiting — while every gate stays green and
    `fixture_recall` silently means "the one league that still parses".

    These drive `main()` with `fetch` patched, so they exercise the real
    multi-source path without touching the network.
    """

    @staticmethod
    def _argv(*args):
        return ["fixtures.py", *args]

    def test_a_dead_fetch_fails_even_when_the_other_source_parsed(
        self, tmp_path, monkeypatch, capsys
    ):
        import scripts.fixtures as mod

        page = _page(JSONLD_PAGE)
        monkeypatch.setattr(
            mod, "fetch", lambda url: "" if "euroleague" in url else page
        )
        out = tmp_path / "fixtures.jsonl"
        monkeypatch.setattr(
            sys,
            "argv",
            self._argv(
                "--source", "bbl", "--source", "euroleague",
                "--now", "2026-09-14T08:30:00Z", "--out", str(out),
            ),
        )
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "FAIL: fixtures: source 'euroleague'" in captured.err
        assert "fetch returned nothing" in captured.err
        # Partial data is still written: the failure is loud, not destructive.
        assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 3
        assert "OK: fixtures: 3 fixtures" in captured.out

    def test_a_source_that_parses_nothing_blames_the_parser_not_the_network(
        self, monkeypatch, capsys
    ):
        import scripts.fixtures as mod

        page = _page(JSONLD_PAGE)
        monkeypatch.setattr(
            mod,
            "fetch",
            lambda url: "<html><body>redesigned</body></html>"
            if "euroleaguebasketball" in url
            else page,
        )
        monkeypatch.setattr(
            sys,
            "argv",
            self._argv(
                "--source", "bbl", "--source", "euroleague",
                "--now", "2026-09-14T08:30:00Z",
            ),
        )
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "no fixture parsed" in captured.err
        assert "add a parser fixture" in captured.err

    def test_every_source_parsing_keeps_the_run_green(self, monkeypatch, capsys):
        """The regression guard for the guard: a healthy multi-source run must
        not be reddened by the very check that catches a dead one."""
        import scripts.fixtures as mod

        page = _page(JSONLD_PAGE)
        monkeypatch.setattr(mod, "fetch", lambda url: page)
        monkeypatch.setattr(
            sys,
            "argv",
            self._argv(
                "--source", "bbl", "--source", "euroleague",
                "--now", "2026-09-14T08:30:00Z",
            ),
        )
        mod.main()
        captured = capsys.readouterr()
        assert "FAIL" not in captured.err
        assert "OK: fixtures:" in captured.out
        assert _run(["--ledger", str(FIXTURES / "candidates_ledger.jsonl")]).returncode == 2
