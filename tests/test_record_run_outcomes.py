"""Tests for scripts/record_run_outcomes.py — the caller the run log never had.

`source_learning.py` implements `record`, `score` and `candidates`, and
`references/self-learning.md` tells a reader to run `record` at the end of every
run. Nothing in `.github/` did: `grep source_learning .github/workflows/` found a
single `candidates --dry-run`, so `logs/run-log.jsonl` was never created by CI,
`score` exited 1 on the empty file and `candidates` read nothing. That is
`AGENTS.md`'s "a documented artifact with no producer", one level down.

These tests pin the three things that make the caller real:

* the derivation turns a blocked fixture source and a dying rung into run-log
  rows — the signals that *are* "an issue found during the run";
* `check` fails when the committed run log is blind to either of them, and when
  it is absent altogether (the inert-loop state);
* the rows a real `record` writes are what the real consumers
  (`source_learning.read_log` / `score_entries` / `discover_candidates`) read.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from scripts import source_learning
from scripts.record_run_outcomes import (
    check,
    derive_specs,
    entry_for,
    load_ledgers,
    record,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "record_run_outcomes.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
GOOD = FIXTURES / "run_outcomes_good"
SOURCE_LEDGERS = ("candidates.jsonl", "fetch-attempts.jsonl", "rung-attempts.jsonl", "events.jsonl")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestDerivation:
    def test_a_blocked_fixture_source_becomes_a_row(self):
        specs = derive_specs(load_ledgers(GOOD))
        blocked = [spec for spec in specs if spec["outcome"] == "blocked"]
        assert len(blocked) == 1
        assert blocked[0]["source"] == "euroleaguebasketball.net"
        assert blocked[0]["url"] == "https://www.euroleaguebasketball.net/euroleague/"

    def test_a_dying_rung_becomes_a_row(self):
        specs = derive_specs(load_ledgers(GOOD))
        rungs = [spec for spec in specs if spec.get("rung")]
        assert [spec["rung"] for spec in rungs] == ["tinyfish"]
        assert rungs[0]["outcome"] == "error"

    def test_a_gone_source_is_rejected_not_errored(self):
        ledgers = {
            "fetch-attempts": [
                {"ts": "2026-10-04T08:00:00+00:00", "source": "bcl", "url": "https://x.test/a", "host": "x.test", "status": "not_found", "code": 404}
            ]
        }
        specs = derive_specs(ledgers)
        assert [spec["outcome"] for spec in specs] == ["reject"]

    def test_a_server_fault_is_an_error(self):
        ledgers = {
            "fetch-attempts": [
                {"ts": "2026-10-04T08:00:00+00:00", "source": "bcl", "url": "https://x.test/a", "host": "x.test", "status": "server_error", "code": 503}
            ]
        }
        assert [spec["outcome"] for spec in derive_specs(ledgers)] == ["error"]

    def test_an_unverifiable_candidate_is_a_skip_not_a_rejection(self):
        """A search hit nobody verified was not rejected; scoring it so would lie."""
        ledgers = {
            "candidates": [
                {"ts": "2026-10-04T08:00:00+00:00", "url": "https://x.test/live", "backend": "tinyfish", "disposition": "unverifiable"}
            ]
        }
        specs = derive_specs(ledgers)
        assert specs[0]["outcome"] == "skip"
        assert specs[0]["source"] == "x.test"

    def test_a_rejected_candidate_is_a_rejection(self):
        ledgers = {
            "candidates": [
                {"ts": "2026-10-04T08:00:00+00:00", "url": "https://x.test/live", "disposition": "rejected_auditWrong"}
            ]
        }
        assert derive_specs(ledgers)[0]["outcome"] == "reject"

    def test_a_candidate_with_no_url_is_not_a_source_observation(self):
        ledgers = {"candidates": [{"ts": "t", "game_key": "BBL|a|b|c", "disposition": "created"}]}
        assert derive_specs(ledgers) == []

    def test_a_dry_run_event_is_not_a_create_fact(self):
        ledgers = {
            "events": [
                {"ts": "t", "game_key": "BBL|a|b|c", "action": "create", "event_id": "evt-1", "dry_run": True}
            ]
        }
        assert derive_specs(ledgers) == []

    def test_a_written_event_is_a_create_row(self):
        ledgers = {
            "events": [
                {"ts": "t", "run_id": "r", "game_key": "BBL|a|b|c", "action": "create", "event_id": "evt-1", "state": "UNVERIFIED"}
            ]
        }
        specs = derive_specs(ledgers)
        assert specs[0]["outcome"] == "create"
        assert specs[0]["source"] == "BBL"
        assert specs[0]["event_id"] == "evt-1"


class TestEntryShape:
    def test_the_optional_keys_are_only_emitted_when_carrying_a_value(self):
        entry = entry_for({"source": "x.test", "outcome": "skip", "url": "", "reason": ""})
        assert "rung" not in entry and "event_id" not in entry
        assert entry["run_id"] == "unassigned"
        assert entry["tier"] == 0

    def test_ts_is_normalised_the_way_source_learning_normalises_it(self):
        entry = entry_for({"source": "x.test", "outcome": "skip", "now": "2026-10-04T08:30:00Z"})
        assert entry["ts"] == "2026-10-04T08:30:00+00:00"


class TestCheckGate:
    def test_the_good_fixture_passes(self):
        assert check(GOOD, REPO_ROOT) == 0

    def test_a_log_blind_to_the_blocked_source_fails(self):
        assert check(FIXTURES / "run_outcomes_missing_blocked", REPO_ROOT) == 1

    def test_a_log_blind_to_the_dying_rung_fails(self):
        assert check(FIXTURES / "run_outcomes_missing_rung", REPO_ROOT) == 1

    def test_an_absent_log_fails(self):
        """The inert-loop state: the writer exists, the caller never ran."""
        assert check(FIXTURES / "run_outcomes_absent", REPO_ROOT) == 1

    def test_cli_check_exit_codes(self):
        good = _run("check", "--ledgers", str(GOOD), "--root", str(REPO_ROOT))
        assert good.returncode == 0, good.stderr
        assert "blocked source" in good.stdout
        bad = _run(
            "check", "--ledgers", str(FIXTURES / "run_outcomes_missing_rung"),
            "--root", str(REPO_ROOT),
        )
        assert bad.returncode == 1
        assert "FAIL:" in bad.stderr


class TestRecordThroughTheRealWriter:
    def test_record_writes_rows_the_real_consumers_read(self, tmp_path):
        for name in SOURCE_LEDGERS:
            shutil.copy(GOOD / name, tmp_path / name)
        # `record` appends through `source_learning.mode_record`, the real writer.
        assert record(tmp_path, now="2026-10-04T08:30:00Z") == 0
        log_path = tmp_path / "run-log.jsonl"
        assert log_path.is_file()

        # The real readers, on the file the real writer produced.
        entries = source_learning.read_log(log_path)
        assert len(entries) == 5
        assert any(entry["outcome"] == "blocked" for entry in entries)
        assert any(entry.get("rung") == "tinyfish" for entry in entries)
        stats = source_learning.score_entries(entries)
        assert stats["euroleaguebasketball.net"]["attempts"] == 1
        discovered = source_learning.discover_candidates(
            entries, source_learning.approved_domains(source_learning.load_sources(REPO_ROOT))
        )
        assert "newclub-basketball.tv" in {c["domain"] for c in discovered}

    def test_record_then_check_round_trips(self, tmp_path):
        """The committed fixture is only honest if the real writer agrees with it."""
        for name in SOURCE_LEDGERS:
            shutil.copy(GOOD / name, tmp_path / name)
        result = _run("record", "--dest", str(tmp_path), "--run-id", "2026-10-04T08:30Z")
        assert result.returncode == 0, result.stderr
        assert json.loads((tmp_path / "run-log.jsonl").read_text().splitlines()[0])["source"]
        assert check(tmp_path, REPO_ROOT) == 0

    def test_the_committed_fixture_equals_a_fresh_derivation(self):
        """The fixture is generated by `record`; if the shape drifts, this fails."""
        expected = [entry_for(spec) for spec in derive_specs(load_ledgers(GOOD))]
        stored = source_learning.read_log(GOOD / "run-log.jsonl")
        assert stored == expected

    def test_record_with_no_ledgers_is_a_stated_pass(self, tmp_path):
        result = _run("record", "--dest", str(tmp_path), "--run-id", "r")
        assert result.returncode == 0
        assert "nothing to learn" in result.stdout
        assert not (tmp_path / "run-log.jsonl").exists()
