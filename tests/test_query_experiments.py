"""Issue #98: missed-fixture suggestions become measured daily experiments.

Persisted, capped, deduped, query-identity-preserving, scored per query,
promoted only on evidence — and promotion never writes sources or calendar
state by itself.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "query_experiments.py"

sys.path.insert(0, str(REPO / "scripts"))

from query_experiments import (  # noqa: E402
    classify_outcome,
    persist_suggestions,
    propose_promotions,
    score_queries,
    select_canary,
    stable_query_id,
)

RUN_ID = "2026-11-17T08:30Z"
FIXTURES = {
    "BBL|ALBA|BAYERN|2026-11-20": {"game_key": "BBL|ALBA|BAYERN|2026-11-20", "league": "BBL", "teams": ["ALBA Berlin", "Bayern"]},
    "NBBL|A|B|2026-11-21": {"game_key": "NBBL|A|B|2026-11-21", "league": "NBBL", "teams": ["Team A", "Team B"]},
}


def _suggestions() -> list[dict]:
    return [
        {"game_key": "BBL|ALBA|BAYERN|2026-11-20", "queries": ["ALBA Berlin Bayern basketball livestream kostenlos"]},
        {"game_key": "NBBL|A|B|2026-11-21", "queries": ["NBBL Team A Team B live stream"]},
    ]


class TestPersist:
    def test_stable_identity_and_dedup(self):
        once = persist_suggestions(_suggestions(), FIXTURES, run_id=RUN_ID)
        twice = persist_suggestions(_suggestions() + _suggestions(), FIXTURES, run_id="other-run")
        assert [r["query_id"] for r in once] == [r["query_id"] for r in twice]
        assert all(r["status"] == "proposed" for r in once)
        assert all(r["query_family"] and r["language"] and r["ts"] for r in once)
        assert once[0]["league"] == "BBL"

    def test_id_is_stable_across_runs(self):
        assert stable_query_id("ALBA Berlin Bayern live", "BBL|X|Y|t", "live") == stable_query_id(
            "  alba berlin  bayern LIVE ", "BBL|X|Y|t", "live"
        )

    def test_suggestions_without_keys_are_noise(self):
        assert persist_suggestions([{"queries": ["foo live"]}], FIXTURES, run_id=RUN_ID) == []


class TestSelect:
    def _experiments(self) -> list[dict]:
        return persist_suggestions(_suggestions(), FIXTURES, run_id=RUN_ID)

    def test_cap_and_dedup_against_baseline(self):
        rows = self._experiments()
        base = [rows[0]["query"]]
        selected = select_canary(rows, base, [], cap=6)
        assert {r["query_id"] for r in selected} == {rows[1]["query_id"]}
        assert select_canary(rows, [], [], cap=1)[0]["game_key"] == "BBL|ALBA|BAYERN|2026-11-20"

    def test_only_eligible_statuses_run(self):
        rows = self._experiments()
        rows[0]["status"] = "retained"
        selected = select_canary(rows, [], [], cap=6)
        assert [r["query_id"] for r in selected] == [rows[1]["query_id"]]

    def test_deterministic_order(self):
        rows = self._experiments()
        assert [r["query_id"] for r in select_canary(list(reversed(rows)), [], [], cap=6)] == [
            r["query_id"] for r in select_canary(rows, [], [], cap=6)
        ]


class TestClassify:
    def test_blocked_is_never_empty(self):
        assert classify_outcome({"query_id": "q_1", "transport": "blocked", "result_count": 0}) == "blocked"
        assert classify_outcome({"query_id": "q_1", "transport": "rate-limited", "result_count": 0}) == "blocked"
        assert classify_outcome({"query_id": "q_1", "outcome": "error", "result_count": 5}) == "blocked"

    def test_empty_rejected_duplicate_verified(self):
        assert classify_outcome({"query_id": "q_1", "result_count": 0}) == "empty"
        assert classify_outcome({"query_id": "q_1", "disposition": "rejected_check3", "result_count": 2}) == "rejected"
        assert classify_outcome({"query_id": "q_1", "disposition": "skipped_duplicate", "result_count": 2}) == "duplicate"
        assert classify_outcome({"query_id": "q_1", "disposition": "created", "result_count": 2}) == "verified"
        assert classify_outcome({"query_id": "q_1", "verified": True, "result_count": 1}) == "verified"


class TestScore:
    def test_per_query_aggregates(self):
        qid = stable_query_id("ALBA live", "BBL|X|Y|t", "live")
        outcomes = [
            {"query_id": qid, "query": "ALBA live", "game_key": "BBL|X|Y|t", "disposition": "created", "result_count": 2, "latency_ms": 100},
            {"query_id": qid, "query": "ALBA live", "game_key": "BBL|X|Y|t", "disposition": "skipped_duplicate", "result_count": 1},
            {"query_id": qid, "query": "ALBA live", "transport": "blocked", "result_count": 0},
            {"query_id": "q_other", "query": "other", "result_count": 0},
        ]
        report = score_queries(outcomes)
        stats = report[qid]
        assert (stats["attempts"], stats["unique_games"]) == (3, 1)
        assert stats["verified"] == 1 and stats["blocked"] == 1
        assert stats["avg_latency_ms"] == pytest.approx(100.0)


class TestRegression:
    def test_canary_finds_the_game_the_baseline_missed(self):
        """The #98 loop end to end on fixtures: baseline covers nothing, the
        canary covers the missed game, scoring credits the query."""
        baseline_ledger_keys = {"BBL|OTHER|GAME|2026-11-20"}
        missed = [f for f in FIXTURES.values() if f["game_key"] not in baseline_ledger_keys]
        assert len(missed) == 2
        experiments = persist_suggestions(_suggestions(), FIXTURES, run_id=RUN_ID)
        canary = select_canary(experiments, ["unrelated query live"], [], cap=6)
        assert len(canary) == 2
        # The canary run finds one of the missed games; the outcome carries
        # the query identity all the way to scoring.
        outcomes = [
            {"query_id": canary[0]["query_id"], "query": canary[0]["query"], "game_key": canary[0]["game_key"],
             "backend": "tinyfish", "provider": "tinyfish", "source_domain": "sportschau.de",
             "result_count": 2, "disposition": "created", "verified": True, "latency_ms": 420},
        ]
        report = score_queries(outcomes)
        assert report[canary[0]["query_id"]]["verified"] == 1
        assert report[canary[0]["query_id"]]["unique_games"] == 1


class TestPromotion:
    def test_promotion_is_advisory_and_gated(self, tmp_path):
        sources = REPO / "config" / "sources.json"
        before = sources.read_text(encoding="utf-8")
        qid = stable_query_id("ALBA live", "BBL|X|Y|t", "live")
        report = {
            qid: {"query_id": qid, "query": "ALBA live", "attempts": 4, "unique_games": 2,
                  "verified": 3, "verified_precision": 0.75, "blocked": 0},
            "q_weak": {"query_id": "q_weak", "query": "weak", "attempts": 1, "unique_games": 1,
                       "verified": 1, "verified_precision": 1.0, "blocked": 0},
        }
        proposals = propose_promotions(report, baseline_recall=0.6, baseline_precision=0.5, min_attempts=3)
        assert [p["query_id"] for p in proposals] == [qid]
        assert "no automatic change" in proposals[0]["action"]
        # Nothing in the promotion path writes sources or calendar state.
        assert sources.read_text(encoding="utf-8") == before
        assert not (tmp_path / "events.jsonl").exists()

    def test_cli_round_trip(self, tmp_path):
        sugg = tmp_path / "sugg.json"
        sugg.write_text(json.dumps({"suggestions": _suggestions()}), encoding="utf-8")
        fixtures = tmp_path / "fixtures.jsonl"
        fixtures.write_text("\n".join(json.dumps(FIXTURES[k]) for k in FIXTURES) + "\n", encoding="utf-8")
        experiments = tmp_path / "experiments.jsonl"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "persist", "--suggestions", str(sugg),
             "--fixtures", str(fixtures), "--run-id", RUN_ID, "--out", str(experiments)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        canary = tmp_path / "canary.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "select", "--experiments", str(experiments),
             "--cap", "1", "--out", str(canary)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert len(json.loads(canary.read_text(encoding="utf-8"))["canary"]) == 1
