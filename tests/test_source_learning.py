"""Tests for scripts/source_learning.py.

Covers the aggregation/discovery functions directly and the three CLI modes via
subprocess against a tmp_path workspace, so nothing touches the real logs/.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.source_learning import (
    approved_domains,
    discover_candidates,
    load_sources,
    read_log,
    score_entries,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "source_learning.py"


def _entry(source, outcome, url="", tier=0):
    return {"ts": "2026-09-14T09:00:00+00:00", "source": source, "tier": tier,
            "url": url, "outcome": outcome, "reason": ""}


class TestScoreEntries:
    def test_hit_rate_and_ordering(self):
        stats = score_entries(
            [
                _entry("championsleague.basketball", "create"),
                _entry("championsleague.basketball", "create"),
                _entry("championsleague.basketball", "reject"),
                _entry("magenta.tv", "reject"),
            ]
        )
        assert stats["championsleague.basketball"]["attempts"] == 3
        assert stats["championsleague.basketball"]["creates"] == 2
        assert stats["championsleague.basketball"]["hit_rate"] == 0.667
        assert stats["magenta.tv"]["hit_rate"] == 0.0
        # sorted by hit_rate descending
        assert list(stats) == ["championsleague.basketball", "magenta.tv"]

    def test_blocked_and_skip_buckets(self):
        stats = score_entries(
            [_entry("magenta.tv", "blocked"), _entry("magenta.tv", "skip")]
        )
        assert stats["magenta.tv"]["blocked"] == 1
        assert stats["magenta.tv"]["skips"] == 1
        assert stats["magenta.tv"]["creates"] == 0

    def test_unknown_source_label(self):
        stats = score_entries([{"outcome": "create"}])
        assert "unknown" in stats

    def test_empty_log(self):
        assert score_entries([]) == {}


class TestApprovedDomains:
    def test_real_registry_includes_bcl(self):
        domains = approved_domains(load_sources(REPO_ROOT))
        assert "championsleague.basketball" in domains
        assert "magenta.tv" in domains
        assert "magentasport.de" in domains


class TestDiscoverCandidates:
    def test_approved_and_plumbing_domains_are_excluded(self):
        entries = [
            _entry("", "skip", "https://www.championsleague.basketball/live/a"),
            _entry("", "skip", "https://www.google.com/search?q=x"),
            _entry("", "skip", "https://x.com/BasketballCL"),
            _entry("", "skip", "https://new-basketball.tv/live/1"),
            _entry("", "skip", "https://new-basketball.tv/live/2"),
        ]
        approved = approved_domains(load_sources(REPO_ROOT))
        found = discover_candidates(entries, approved)
        domains = [item["domain"] for item in found]
        assert domains == ["new-basketball.tv"]
        assert found[0]["hits"] == 2
        assert found[0]["status"] == "quarantined"
        assert len(found[0]["examples"]) == 2

    def test_subdomain_of_approved_is_excluded(self):
        approved = {"championsleague.basketball"}
        found = discover_candidates(
            [_entry("", "skip", "https://live.championsleague.basketball/x")],
            approved,
        )
        assert found == []

    def test_examples_capped_at_three(self):
        entries = [
            _entry("", "skip", f"https://new-basketball.tv/live/{i}")
            for i in range(6)
        ]
        found = discover_candidates(entries, set())
        assert found[0]["hits"] == 6
        assert len(found[0]["examples"]) == 3


class TestReadLog:
    def test_skips_blank_and_malformed_lines(self, tmp_path):
        log = tmp_path / "run-log.jsonl"
        log.write_text(
            json.dumps(_entry("a", "create")) + "\n\n{not json\n",
            encoding="utf-8",
        )
        assert len(read_log(log)) == 1

    def test_missing_file_returns_empty(self, tmp_path):
        assert read_log(tmp_path / "nope.jsonl") == []


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestCliRecord:
    def test_dry_run_does_not_write(self, tmp_path):
        log = tmp_path / "logs" / "run-log.jsonl"
        result = _run(
            [
                "record", "--source", "magenta.tv", "--url",
                "https://www.magenta.tv/tv/live-x", "--outcome", "reject",
                "--reason", "no announcement", "--log", str(log),
                "--now", "2026-09-14T09:00:00Z", "--dry-run",
            ]
        )
        assert result.returncode == 0
        assert "dry-run" in result.stdout
        assert not log.exists()

    def test_append_writes_one_jsonl_row(self, tmp_path):
        log = tmp_path / "logs" / "run-log.jsonl"
        for _ in range(2):
            result = _run(
                [
                    "record", "--source", "championsleague.basketball",
                    "--outcome", "create", "--url",
                    "https://www.championsleague.basketball/live/a",
                    "--tier", "1", "--now", "2026-09-14T09:00:00Z",
                    "--log", str(log),
                ]
            )
            assert result.returncode == 0
        rows = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(rows) == 2
        payload = json.loads(rows[0])
        assert payload["source"] == "championsleague.basketball"
        assert payload["tier"] == 1
        assert payload["ts"] == "2026-09-14T09:00:00+00:00"

    def test_bad_outcome_is_usage_error(self, tmp_path):
        result = _run(
            ["record", "--source", "x", "--outcome", "nope",
             "--log", str(tmp_path / "l.jsonl")]
        )
        assert result.returncode == 2

    def test_bad_now_is_usage_error(self, tmp_path):
        result = _run(
            ["record", "--source", "x", "--outcome", "create",
             "--now", "yesterday", "--log", str(tmp_path / "l.jsonl")]
        )
        assert result.returncode == 2

    def test_dest_writes_run_log_into_the_directory(self, tmp_path):
        dest = tmp_path / "telemetry"
        result = _run(
            ["record", "--source", "exa-mcp", "--outcome", "reject",
             "--dest", str(dest), "--now", "2026-09-14T08:30:00Z",
             "--game-key", "BBL|A|B|t", "--backend", "exa-mcp",
             "--rung", "render-0", "--llm", "gemini", "--state", "UNVERIFIED",
             "--color-id", "5", "--event-id", "abc123"]
        )
        assert result.returncode == 0
        log = dest / "run-log.jsonl"
        assert log.is_file()
        payload = json.loads(log.read_text(encoding="utf-8").strip())
        assert payload["game_key"] == "BBL|A|B|t"
        assert payload["color_id"] == "5"
        assert payload["event_id"] == "abc123"
        assert payload["state"] == "UNVERIFIED"

    def test_optional_keys_absent_when_not_supplied(self, tmp_path):
        log = tmp_path / "run-log.jsonl"
        _run(
            ["record", "--source", "x", "--outcome", "create",
             "--log", str(log), "--now", "2026-09-14T08:30:00Z"]
        )
        payload = json.loads(log.read_text(encoding="utf-8").strip())
        assert "game_key" not in payload
        assert "color_id" not in payload

    def test_score_reads_from_dest(self, tmp_path):
        dest = tmp_path / "telemetry"
        _run(
            ["record", "--source", "exa-mcp", "--outcome", "create",
             "--dest", str(dest), "--now", "2026-09-14T08:30:00Z"]
        )
        result = _run(["score", "--dest", str(dest)])
        assert result.returncode == 0
        assert "OK: source_learning: exa-mcp" in result.stdout

    def test_candidates_reads_from_dest(self, tmp_path):
        dest = tmp_path / "telemetry"
        _run(
            ["record", "--source", "x", "--outcome", "skip",
             "--url", "https://new-basketball.tv/live/1",
             "--dest", str(dest), "--now", "2026-09-14T08:30:00Z"]
        )
        result = _run(
            ["candidates", "--root", str(REPO_ROOT), "--dest", str(dest),
             "--out", str(tmp_path / "c.json")]
        )
        assert result.returncode == 0
        assert "new-basketball.tv" in result.stdout


class TestCliScore:
    def test_missing_log_exits_one(self, tmp_path):
        result = _run(["score", "--log", str(tmp_path / "nope.jsonl")])
        assert result.returncode == 1
        assert "no run log entries" in result.stderr

    def test_scores_and_json_output(self, tmp_path):
        log = tmp_path / "run-log.jsonl"
        log.write_text(
            json.dumps(_entry("championsleague.basketball", "create")) + "\n"
            + json.dumps(_entry("magenta.tv", "reject")) + "\n",
            encoding="utf-8",
        )
        text = _run(["score", "--log", str(log)])
        assert text.returncode == 0
        assert "OK: source_learning: championsleague.basketball" in text.stdout

        as_json = _run(["score", "--log", str(log), "--json"])
        assert as_json.returncode == 0
        payload = json.loads(as_json.stdout)
        assert payload["magenta.tv"]["hit_rate"] == 0.0


class TestCliCandidates:
    def test_no_candidates_exits_zero(self, tmp_path):
        log = tmp_path / "run-log.jsonl"
        log.write_text(
            json.dumps(_entry("x", "skip", "https://www.google.com/search")) + "\n",
            encoding="utf-8",
        )
        result = _run(
            ["candidates", "--root", str(REPO_ROOT), "--log", str(log),
             "--dry-run"]
        )
        assert result.returncode == 0
        assert "no new source candidates" in result.stdout

    def test_quarantine_write_and_dry_run(self, tmp_path):
        log = tmp_path / "run-log.jsonl"
        log.write_text(
            json.dumps(_entry("x", "skip", "https://new-basketball.tv/live/1")) + "\n",
            encoding="utf-8",
        )
        dry = _run(
            ["candidates", "--root", str(REPO_ROOT), "--log", str(log),
             "--out", str(tmp_path / "c.json"), "--dry-run"]
        )
        assert dry.returncode == 0
        assert "quarantined" in dry.stdout
        assert not (tmp_path / "c.json").exists()

        written = _run(
            ["candidates", "--root", str(REPO_ROOT), "--log", str(log),
             "--out", str(tmp_path / "c.json")]
        )
        assert written.returncode == 0
        payload = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
        assert payload["candidates"][0]["domain"] == "new-basketball.tv"
        assert payload["candidates"][0]["status"] == "quarantined"
