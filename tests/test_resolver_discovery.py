"""Issue #99: evaluate do-web-doc-resolver for alternative official sources.

Opt-in, bounded, uncovered-fixtures-only, provenance on every row,
quarantine preserved, safe degradation, no calendar writes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "resolver_discovery.py"

sys.path.insert(0, str(REPO / "scripts"))

from resolver_discovery import (  # noqa: E402
    BACKEND_NAME,
    STRATEGIES,
    build_strategies,
    discover,
    is_enabled,
    uncovered_fixtures,
)

FIXTURES = [
    {"game_key": "BBL|ALBA|BAYERN|2026-11-20", "league": "BBL", "teams": ["ALBA Berlin", "Bayern"]},
    {"game_key": "NBBL|A|B|2026-11-21", "league": "NBBL", "teams": ["Team A", "Team B"]},
]


def _runner_factory(mapping: dict[str, list[dict]], fail_on: set[str] | None = None):
    calls: list[str] = []

    def _runner(query: str, *, timeout_seconds: int = 20) -> list[dict]:
        calls.append(query)
        if fail_on and query in fail_on:
            raise OSError("no route to host")
        return mapping.get(query, [])

    _runner.calls = calls  # type: ignore[attr-defined]
    return _runner


class TestGating:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("RESOLVER_ENABLED", raising=False)
        assert is_enabled() is False

    def test_explicit_flag_or_env_enables(self, monkeypatch):
        monkeypatch.setenv("RESOLVER_ENABLED", "true")
        assert is_enabled() is True
        monkeypatch.delenv("RESOLVER_ENABLED", raising=False)
        assert is_enabled(explicit=True) is True


class TestStrategies:
    def test_six_distinct_families(self):
        planned = build_strategies(FIXTURES[0])
        assert [p["strategy"] for p in planned] == list(STRATEGIES)
        assert len({p["query"] for p in planned}) == len(planned)

    def test_only_uncovered_fixtures_run(self):
        assert [f["game_key"] for f in uncovered_fixtures(FIXTURES, set())] == [f["game_key"] for f in FIXTURES]
        assert uncovered_fixtures(FIXTURES, {f["game_key"] for f in FIXTURES}) == []


class TestDiscover:
    def test_missed_fixture_gains_unique_official_url(self):
        """Deterministic set: baseline covers BBL, misses NBBL; the resolver
        finds an alternative official page for the missed game (or records
        a reasoned no-benefit)."""
        covered = {"BBL|ALBA|BAYERN|2026-11-20"}
        mapping = {
            "Team A Team B NBBL Spielplan live": [
                {"url": "https://www.sportschau.de/nbbl/team-a-team-b-live", "title": "NBBL live"}
            ],
        }
        leads, stats = discover(FIXTURES, covered, runner=_runner_factory(mapping), run_id="r1")
        assert stats["uncovered_fixtures"] == 1
        assert len(leads) == 1
        lead = leads[0]
        assert lead["url"] == "https://www.sportschau.de/nbbl/team-a-team-b-live"
        assert lead["backend"] == BACKEND_NAME
        assert lead["game_key"] == "NBBL|A|B|2026-11-21"
        for field in ("query", "strategy", "run_id", "ts"):
            assert lead[field]

    def test_everything_covered_means_no_run(self):
        leads, stats = discover(FIXTURES, {f["game_key"] for f in FIXTURES}, runner=_runner_factory({}))
        assert leads == [] and stats["queries"] == 0

    def test_budget_and_time_caps(self):
        runner = _runner_factory({})
        _, stats = discover(FIXTURES, set(), runner=runner, max_queries=2)
        assert stats["queries"] == 2 and stats["budget_hit"] is True and len(runner.calls) == 2
        _, stats = discover(FIXTURES, set(), runner=runner, max_queries=100, max_seconds=0)
        assert stats["queries"] == 0 and stats["budget_hit"] is True

    def test_failed_provider_degrades_safely(self):
        planned = build_strategies(FIXTURES[0])
        runner = _runner_factory({}, fail_on={planned[0]["query"]})
        leads, stats = discover([FIXTURES[0]], set(), runner=runner, max_queries=1)
        assert leads == [] and stats["failures"] == 1


class TestQuarantine:
    def test_new_hosts_stay_quarantined(self):
        """A resolver lead is never an approval: sources.json is untouched
        and the seven-check pipeline still decides."""
        sources = REPO / "config" / "sources.json"
        before = sources.read_text(encoding="utf-8")
        mapping = {
            "ALBA Berlin Bayern BBL official live": [
                {"url": "https://newofficial.example/bbl/alba-bayern", "title": "official"}
            ],
        }
        leads, _ = discover([FIXTURES[0]], set(), runner=_runner_factory(mapping))
        assert leads
        assert all("newofficial.example" not in before for _ in leads)
        assert sources.read_text(encoding="utf-8") == before
        # Discovery rows are evidence, not verdicts: nothing here claims
        # free/live — that proof still belongs to the seven checks.
        assert all("disposition" not in lead or lead.get("disposition") != "created" for lead in leads)


class TestCli:
    def test_disabled_is_advisory(self, tmp_path):
        fixtures = tmp_path / "f.jsonl"
        fixtures.write_text("\n".join(json.dumps(f) for f in FIXTURES) + "\n", encoding="utf-8")
        ledger = tmp_path / "c.jsonl"
        ledger.write_text("", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--fixtures", str(fixtures), "--ledger", str(ledger),
             "--out", str(tmp_path / "leads.json")],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "disabled" in result.stderr

    def test_enabled_no_benefit_is_a_recorded_answer(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RESOLVER_ENABLED", "true")
        fixtures = tmp_path / "f.jsonl"
        fixtures.write_text("\n".join(json.dumps(f) for f in FIXTURES) + "\n", encoding="utf-8")
        ledger = tmp_path / "c.jsonl"
        ledger.write_text("", encoding="utf-8")
        out = tmp_path / "leads.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--fixtures", str(fixtures), "--ledger", str(ledger),
             "--out", str(out)],
            capture_output=True, text=True,
        )
        # No resolver backend in this environment: degraded, recorded, exit 1.
        assert result.returncode == 1
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["leads"] == []
        assert payload["stats"]["queries"] > 0
