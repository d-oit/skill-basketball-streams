"""Failure-path integration tests for the two 2026-10-21 runtime-daily outages.

1. GH001 push rejection: run-log.jsonl hit 101 MB and the telemetry push was
   rejected. Fixed by `record_run_outcomes.compact` (40 MB bound). This test
   pins the half the first fix missed: dropped rows must be *archived outside
   the committed worktree*, not deleted — otherwise the bound trades a red
   push for silent history loss.
2. Dead preview model: longcat-2.5-preview-free failed its PONG probe 3/3 and
   Phase 1/2 redded with no transcript, which then cascaded into a secondary
   `FileNotFoundError` for `.tmp/source-leads.json`. Fixed by a probe fallback
   to a working free id plus `mkdir -p .tmp` before the empty-leads write.

These tests assert the wired outcome, not the existence of a helper: an
unwired helper is the reader-is-not-a-producer shape AGENTS.md warns about.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts import source_learning
from scripts.record_run_outcomes import compact_logs

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"


def _row(source: str, size: int = 64) -> bytes:
    return (json.dumps({
        "ts": "2026-10-01T00:00:00+00:00", "run_id": "old",
        "source": source, "tier": 0, "url": f"https://{source}/x",
        "outcome": "skip", "reason": "x" * size,
    }) + "\n").encode()


class TestTrimmedHistoryIsArchivedNotDeleted:
    def test_dropped_rows_land_outside_the_committed_worktree(self, tmp_path):
        dest = tmp_path / "work"
        dest.mkdir()
        log_path = dest / "run-log.jsonl"
        with log_path.open("wb") as handle:
            for _ in range(2000):
                handle.write(_row("old.example"))
        assert compact_logs(dest, max_bytes=50 * 1024) == 0
        assert log_path.stat().st_size <= 50 * 1024

        archive = tmp_path / "telemetry-archive" / "run-log.jsonl"
        assert archive.is_file(), "dropped rows must be archived, not deleted"
        archived = source_learning.read_log(archive)
        assert len(archived) > 1000
        assert all(entry["source"] == "old.example" for entry in archived)

        # The kept window stays parseable by the real readers.
        kept = source_learning.read_log(log_path)
        assert kept and all(entry["source"] for entry in kept)

    def test_small_ledgers_leave_no_archive_behind(self, tmp_path):
        dest = tmp_path / "work"
        dest.mkdir()
        (dest / "run-log.jsonl").write_bytes(_row("old.example"))
        assert compact_logs(dest, max_bytes=50 * 1024) == 0
        assert not (tmp_path / "telemetry-archive").exists()


class TestModelFallbackIsWired:
    def test_resolver_emits_a_fallback_id(self):
        result = subprocess.run(
            [sys.executable, "scripts/llm_model.py"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, result.stderr
        outputs = dict(
            line.split("=", 1)
            for line in result.stdout.splitlines() if "=" in line
        )
        assert outputs["fallback_model"] == "opencode/big-pickle"
        assert outputs["model"] != outputs["fallback_model"]

    def test_probe_exports_the_winner_for_the_skill(self):
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        assert "id: probe" in text
        assert "effective-model=" in text
        assert "LLM_MODEL: ${{ steps.probe.outputs.effective-model }}" in text

    def test_missing_transcript_still_leaves_an_empty_leads_file(self):
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        step = text[text.index("- name: Extract source leads from runtime research"):]
        step = step[: step.index("── The write path")]
        # The 2026-10-21 cascade: this branch wrote .tmp/source-leads.json
        # without creating .tmp, so a dead model produced a second failure.
        assert "mkdir -p .tmp" in step
        assert "{'leads': []}" in step

    def test_every_telemetry_commit_uploads_its_archive(self):
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        assert text.count("telemetry-archive-") >= 3
        assert "if-no-files-found: ignore" in text
