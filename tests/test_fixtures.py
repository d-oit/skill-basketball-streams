"""Tests for scripts/fixtures.py.

Phase 5 exists to measure the failure the search-derived metric structurally
cannot see — a game that *no* backend surfaced. Every test here defends the rule
that makes that measurement trustworthy: **no match means no fixture**. A
half-parsed event would be reported as a missed game that does not exist, which
would make recall look worse than reality and send someone hunting a phantom.

The two page fixtures are synthetic *inputs* (allowed — see
`tests/fixtures/README.md`): one JSON-LD, one microdata-only. No test touches
the network.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.fixtures import (
    DEFAULT_SOURCES,
    dedupe,
    event_to_fixture,
    extract_json_ld,
    in_window,
    parse_dt,
    parse_microdata,
    parse_page,
    split_teams,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "fixtures.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
JSONLD_PAGE = FIXTURES / "fixtures_page_bbl.html"
MICRODATA_PAGE = FIXTURES / "fixtures_page_microdata.html"
NOW = datetime(2026, 9, 14, 8, 30, tzinfo=timezone.utc)

BBL = DEFAULT_SOURCES["bbl"]


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

    def test_unparsable_page_exits_one(self, tmp_path):
        page = tmp_path / "empty.html"
        page.write_text("<html><body>nothing here</body></html>", encoding="utf-8")
        result = _run(["--input", str(page), "--source", "bbl"])
        assert result.returncode == 1
        assert "capture a page and add a parser fixture" in result.stderr

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
        assert _run(["--ledger", str(FIXTURES / "candidates_ledger.jsonl")]).returncode == 2
