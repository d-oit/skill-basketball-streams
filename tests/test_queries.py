"""test_queries.py — fixture-driven query expansion and missed-game suggestions.

`scripts/queries.py` is the recall loop search cannot close itself: generic
queries ask the index what it ranks, fixtures name games that exist. These
tests pin the merge (base first, deduped, capped) and the suggest join
(fixtures no ledger row covers), all offline.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from queries import expand_queries, suggest_for_unseen

ROOT = Path(__file__).resolve().parent.parent


def _fixture(teams, league="BBL", key="K"):
    return {"league": league, "teams": list(teams), "game_key": key}


class TestExpandQueries:
    def test_base_queries_come_first_in_order(self):
        out = expand_queries(
            ["generic stream"], [_fixture(["A", "B"])], cap=10
        )
        assert out[0] == "generic stream"
        assert len(out) == 3

    def test_fixture_query_names_the_pairing(self):
        out = expand_queries([], [_fixture(["ALBA Berlin", "Bayern"], "EuroLeague")])
        assert out[0] == "ALBA Berlin Bayern basketball livestream kostenlos"
        assert out[1] == "EuroLeague ALBA Berlin Bayern live stream"

    def test_dedupe_is_case_insensitive(self):
        out = expand_queries(
            ["ALBA Berlin Bayern basketball LIVESTREAM kostenlos"],
            [_fixture(["ALBA Berlin", "Bayern"])],
            cap=10,
        )
        # The base spelling wins; the fixture twin is dropped.
        assert out[0] == "ALBA Berlin Bayern basketball LIVESTREAM kostenlos"
        assert sum(1 for q in out if q.casefold() == out[0].casefold()) == 1

    def test_cap_counts_fixture_queries_only(self):
        fixtures = [_fixture([f"H{i}", f"A{i}"], key=f"K{i}") for i in range(10)]
        out = expand_queries(["base"], fixtures, cap=3)
        assert out[0] == "base"
        assert len(out) == 4

    def test_malformed_fixtures_are_skipped(self):
        out = expand_queries(
            [],
            [
                {"league": "BBL", "teams": ["Only one"], "game_key": "K1"},
                {"league": "BBL", "teams": "not-a-list", "game_key": "K2"},
                {"league": "BBL", "game_key": "K3"},
                "not-a-dict",
                _fixture(["X", "Y"], key="K4"),
            ],
            cap=10,
        )
        assert len(out) == 2
        assert "X Y basketball livestream kostenlos" in out

    def test_zero_cap_means_base_only(self):
        out = expand_queries(["base"], [_fixture(["A", "B"])], cap=0)
        assert out == ["base"]


class TestSuggestForUnseen:
    def test_covered_fixtures_produce_nothing(self):
        fixtures = [_fixture(["A", "B"], key="K1")]
        assert suggest_for_unseen(fixtures, {"K1"}) == []

    def test_uncovered_fixture_proposes_a_query(self):
        suggestions = suggest_for_unseen([_fixture(["A", "B"], key="K1")], set())
        assert len(suggestions) == 1
        assert suggestions[0]["game_key"] == "K1"
        assert suggestions[0]["queries"] == [
            "A B basketball livestream kostenlos"
        ]

    def test_keyless_fixtures_are_skipped(self):
        assert suggest_for_unseen([{"league": "BBL", "teams": ["A", "B"]}], set()) == []

    def test_cap_bounds_suggestions(self):
        fixtures = [_fixture(["A", f"B{i}"], key=f"K{i}") for i in range(10)]
        assert len(suggest_for_unseen(fixtures, set(), cap=3)) == 3


class TestQueriesCli:
    def test_expand_cli_lists_queries_on_stdout(self, tmp_path):
        fixtures = tmp_path / "fixtures.jsonl"
        fixtures.write_text(
            json.dumps(_fixture(["A", "B"], key="K1")) + "\n", encoding="utf-8"
        )
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "queries.py"),
             "--fixtures", str(fixtures)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert result.returncode == 0, result.stderr
        assert "A B basketball livestream kostenlos" in result.stdout
        # Payload on stdout must stay parseable: JSON mode is exact.
        as_json = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "queries.py"),
             "--fixtures", str(fixtures), "--json"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert json.loads(as_json.stdout)["queries"]

    def test_suggest_cli_needs_a_ledger(self, tmp_path):
        fixtures = tmp_path / "fixtures.jsonl"
        fixtures.write_text(
            json.dumps(_fixture(["A", "B"], key="K1")) + "\n", encoding="utf-8"
        )
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "queries.py"),
             "--suggest", "--fixtures", str(fixtures)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        assert result.returncode == 2
        assert "--ledger" in result.stderr
