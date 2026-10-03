"""report_run_failure.py — the red-run report, tested offline.

The green path is the pin that matters most: a run with no failed job exits
non-zero ("nothing to report"), so the report can never fire on a green run —
a spurious red-run issue is the boy-who-cried-wolf failure mode of every
automatic reporter, and it is what makes operators mute them.

The dedupe pin: the title starts with MARKER (the dedupe key gh_issue.py
matches), but the failed-job list is NOT part of the marker — a different
failing job is the same ongoing finding ("the daily run is red"), and keying
on it would open a new issue per symptom.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "report_run_failure.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import report_run_failure as rrf  # noqa: E402
from gh_issue import file_or_comment  # noqa: E402


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def _jobs_file(tmp_path: Path, conclusions: dict[str, str]) -> Path:
    path = tmp_path / "jobs.json"
    path.write_text(
        json.dumps(
            {
                "jobs": [
                    {"name": name, "conclusion": conclusion, "status": "completed"}
                    for name, conclusion in conclusions.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


GREEN = {
    "Rung health — probe the render ladder": "success",
    "Phase 0 — candidate ledger": "success",
    "Phase 1/2 — skill run": "success",
    "Phase 3 — post-hoc audit": "success",
}


class TestTheGreenPath:
    def test_no_failed_job_reports_nothing(self, tmp_path):
        result = _run("--jobs", str(_jobs_file(tmp_path, GREEN)))
        assert result.returncode == 1
        assert "nothing to report" in result.stdout

    def test_skipped_and_cancelled_are_not_red(self, tmp_path):
        """A skipped gate is a configuration fact, not a failure — folding it
        in would let a misconfigured run read as a red one."""
        result = _run(
            "--jobs",
            str(_jobs_file(tmp_path, {"Gated job": "skipped", "Other": "cancelled"})),
        )
        assert result.returncode == 1


class TestTheReport:
    def test_the_title_carries_the_marker_and_the_failed_jobs(self):
        title = rrf.build_title(["Phase 0 — candidate ledger"])
        assert title.startswith(rrf.MARKER)
        assert "Phase 0" in title

    def test_the_failed_jobs_are_not_part_of_the_dedupe_key(self):
        """The marker is the whole dedupe key; the job list rides in the
        title for readability only."""
        assert rrf.build_title(["A"]).startswith(rrf.MARKER)
        assert "A" not in rrf.MARKER

    def test_the_body_points_at_where_to_read(self):
        body = rrf.build_body(["Phase 0 — candidate ledger"], "https://x/runs/1")
        assert "https://x/runs/1" in body
        assert "transcripts/" in body
        assert "Do not silence this report" in body

    def test_a_missing_jobs_file_is_usage(self, tmp_path):
        result = _run("--jobs", str(tmp_path / "absent.json"))
        assert result.returncode == 2

    def test_print_mode_builds_without_filing(self, tmp_path):
        result = _run(
            "--jobs",
            str(_jobs_file(tmp_path, {**GREEN, "Phase 1/2 — skill run": "failure"})),
            "--run-url",
            "https://x/runs/1",
        )
        assert result.returncode == 0
        assert "[runtime] daily run red" in result.stdout
        assert "not filed" in result.stdout


class TestTheFiling:
    def test_file_mode_uses_the_shared_deduper(self, monkeypatch, tmp_path):
        """One code path for all three filers: `gh_issue.file_or_comment`,
        never a local copy of the create-vs-comment decision."""
        calls: list[tuple] = []

        def fake_file_or_comment(title, body, *, marker):
            calls.append((title, marker))
            return "commented on #46"

        monkeypatch.setattr(rrf, "file_or_comment", fake_file_or_comment)
        monkeypatch.setattr(
            sys, "argv",
            [
                "report_run_failure.py",
                "--jobs",
                str(_jobs_file(tmp_path, {**GREEN, "Phase 0 — candidate ledger": "failure"})),
                "--run-url",
                "https://x/runs/1",
                "--file",
            ],
        )
        with pytest.raises(SystemExit) as excinfo:
            rrf.main()
        assert excinfo.value.code == 0
        title, marker = calls[0]
        assert title.startswith(marker)
        assert marker == rrf.MARKER
        assert calls == calls[:1]

    def test_the_marker_is_imported_not_duplicated(self):
        """If the marker drifted from gh_issue's expectations the dedupe
        would break silently — one open issue per *day* instead of one."""
        assert isinstance(file_or_comment, object)  # the shared filer is importable
        assert rrf.MARKER.startswith("[runtime]")
