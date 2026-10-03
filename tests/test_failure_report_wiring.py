"""Workflow pins for the red-run report.

The defect, found by reading live Actions history: the 2026-09-30 daily run
failed after 24 minutes and left no trace — no issue, no notification, only a
red mark in a tab nobody schedules time to read. The rung loop files its
findings; a red RUN is a bigger finding than a parked rung and was the one
thing not reported.

What is pinned:

* the job runs `if: failure()` — a green run files nothing, and a
  skipped/cancelled job is not red (the script double-guards with its own
  no-failed-job exit);
* the job holds ONLY `issues: write` — no calendar credential, no search
  key, the same authority split as `rung-issues`;
* the step passes `--file`, the whole filing capability, and reads the run's
  own job table so the report names the exact jobs that failed.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"

JOB_START = re.compile(r"^  ([a-z][a-z0-9-]*):$", re.M)


def _job() -> str:
    text = RUNTIME_DAILY.read_text(encoding="utf-8")
    starts = [(m.group(1), m.start()) for m in JOB_START.finditer(text)]
    jobs = {
        name: text[start : (starts[i + 1][1] if i + 1 < len(starts) else len(text))]
        for i, (name, start) in enumerate(starts)
    }
    assert "report-failure" in jobs, "the Report a red run job is gone"
    return jobs["report-failure"]


class TestTheJob:
    def test_it_runs_only_on_failure(self):
        assert re.search(r"^    if: failure\(\)$", _job(), re.M)

    def test_it_holds_only_issue_permission(self):
        job = _job()
        assert "issues: write" in job
        assert "contents: read" in job
        assert "contents: write" not in job, (
            "the report reads the repo; it never needs to write it"
        )

    def test_it_holds_no_calendar_credential(self):
        """The authority to file an issue and the authority to write the
        calendar are deliberately held by different jobs."""
        assert "COMPOSIO" not in _job()

    def test_it_gathers_the_runs_own_job_table(self):
        code = "\n".join(
            line
            for line in _job().splitlines()
            if not line.lstrip().startswith("#")
        )
        assert 'gh run view "$GITHUB_RUN_ID" --json jobs' in code
        assert "--jobs .tmp/jobs.json" in code

    def test_it_files_through_the_deduper_not_a_bare_gh_call(self):
        code = "\n".join(
            line
            for line in _job().splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "scripts/report_run_failure.py" in code
        assert "--file" in code
        assert "gh issue create" not in code, (
            "a bare gh call would bypass the one-issue-per-finding dedupe"
        )

    def test_it_is_the_last_job_in_the_file(self):
        """Not style: a step's slice extends to the next step marker, so a
        job placed after this one lets its `--file` continuation bleed into
        the next job's keys — which reads, in the attach-a-file pin, as
        `--file <next-job>:` attaching a directory that is not a file. The
        report also reads the run's completed job table: last is the only
        correct place."""
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        jobs = [m.group(1) for m in JOB_START.finditer(text)]
        assert jobs[-1] == "report-failure", (
            f"report-failure must stay last; current order ends with {jobs[-3:]}"
        )