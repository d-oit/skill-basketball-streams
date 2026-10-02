"""Workflow pins for the pre-fix-red evidence chain.

`scripts/prefix_red.py` walks verdict -> event_id -> run_id ->
`transcripts/<run_id>.json`. Every link in that chain is a place a silent edit
can break it, which is the exact class of defect this repository records as its
worst: the demonstration would report "not demonstrated, no reason" while the
evidence sat on the branch the whole time.

The chain, and what pins it here:

* the `runtime` job computes ONE run id (`Stamp this run's id`) and every
  writer reads it via `env: RUN_ID` — a second `date` call in any writer is how
  the rows and the transcript file drift apart into different runs, unjoinable;
* the transcript is stamped into `transcript-<run_id>.json` and uploaded, and
  the audit files it under the stamped name (the name the ledger records) —
  not under the audit's own timestamp, which no ledger row names;
* `self-improve.yml` pulls `events.jsonl` (the join's other half), fetches
  ONLY the transcripts the cases join to, and runs the demonstration;
* the demonstration tolerates `prefix_red.py` exit 1 with a notice naming why
  (the pre-filing world), and lets exit 2 red — the join broken is a fault,
  the join unpopulated is history.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"
SELF_IMPROVE = REPO_ROOT / ".github" / "workflows" / "self-improve.yml"

STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)
JOB_START = re.compile(r"^  ([a-z][a-z0-9-]*):$", re.M)

STAMP_ID_STEP = "Stamp this run's id"
STAMP_FILE_STEP = "Stamp the transcript with the run id"
FETCH_STEP = "Pull the filed transcripts the new cases came from"
DEMONSTRATE_STEP = "Demonstrate pre-fix-red against the filed transcript(s)"
LEDGER_PULL_STEP = "Pull the audit ledger from the telemetry branch"


def _jobs(workflow: Path) -> dict[str, str]:
    text = workflow.read_text(encoding="utf-8")
    starts = [(m.group(1), m.start()) for m in JOB_START.finditer(text)]
    return {
        name: text[start : (starts[i + 1][1] if i + 1 < len(starts) else len(text))]
        for i, (name, start) in enumerate(starts)
    }


def _steps(job: str) -> list[str]:
    starts = [m.start() for m in STEP_START.finditer(job)]
    return [job[s:e] for s, e in zip(starts, starts[1:] + [len(job)])]


def _named(steps: list[str], name: str) -> str:
    matches = [
        step
        for step in steps
        if step.strip().splitlines()[0].strip() == f"- name: {name}"
    ]
    assert len(matches) == 1, f"expected one step named {name!r}, got {len(matches)}"
    return matches[0]


def _code(step: str) -> str:
    return "\n".join(
        line for line in step.splitlines() if not line.lstrip().startswith("#")
    )


class TestOneRunIdPerRun:
    """The join depends on the event ledger's `run_id` and the transcript's
    file name agreeing. Two `date` calls in one job is how they stop."""

    def _job(self) -> str:
        return _jobs(RUNTIME_DAILY)["runtime"]

    def test_the_run_id_is_computed_once(self):
        step = _named(_steps(self._job()), STAMP_ID_STEP)
        assert "GITHUB_OUTPUT" in step
        assert "date -u" in step

    def test_the_ledger_writers_read_the_stamped_id_not_their_own_clock(self):
        for name in (
            "Record what was written (Phase 3 input)",
            "Record the run's outcome into the candidate ledger",
        ):
            step = _named(_steps(self._job()), name)
            code = _code(step)
            assert '--run-id "$RUN_ID"' in code, name
            assert "date -u" not in code, (
                f"{name}: a second `date` call is how the rows and the filed "
                "transcript drift into different runs, unjoinable"
            )
            assert "steps.runid.outputs.run_id" in step, (
                f"{name}: RUN_ID must come from the stamp step, not from a "
                "recomputed value the two steps could disagree about"
            )

    def test_the_transcript_is_stamped_with_the_same_id(self):
        step = _named(_steps(self._job()), STAMP_FILE_STEP)
        code = _code(step)
        assert 'cp .tmp/transcript.json ".tmp/transcript-${RUN_ID}.json"' in code
        assert "steps.runid.outputs.run_id" in step
        assert "if: always()" in step, (
            "a run whose agent step failed is the run whose transcript is most "
            "worth keeping"
        )

    def test_the_stamp_precedes_the_upload(self):
        steps = _steps(self._job())
        stamp = next(
            i
            for i, s in enumerate(steps)
            if s.lstrip().startswith(f"- name: {STAMP_FILE_STEP}")
        )
        upload = next(
            i
            for i, s in enumerate(steps)
            if "runtime-transcript" in s and "upload-artifact" in s
        )
        assert stamp < upload

    def test_the_upload_carries_the_stamped_copy(self):
        upload = next(
            s
            for s in _steps(self._job())
            if "upload-artifact" in s and "runtime-transcript" in s
        )
        assert ".tmp/transcript-*.json" in upload, (
            "the audit files the stamped copy; an upload of the teed original "
            "alone cannot be filed under a name the ledger records"
        )


class TestTheAuditFilesUnderTheStampedName:
    def test_the_stamped_name_becomes_the_branch_name(self):
        grade = _named(
            _steps(_jobs(RUNTIME_DAILY)["audit"]),
            "Grade the run's transcript and file it",
        )
        code = _code(grade)
        assert 'dest=".tmp/telemetry/transcripts/${base#transcript-}"' in code, (
            "the file name on the branch must be the run id the ledger "
            "records, or the join verdict -> run -> transcript is unmakeable"
        )
        assert "-unlabelled" in code, (
            "an unstamped upload is kept and named unlabelled — not filed "
            "under a timestamp no ledger row records, which is how a "
            "transcript becomes unfindable"
        )


class TestTheSelfImproveWiring:
    def _steps(self) -> list[str]:
        return _steps(_jobs(SELF_IMPROVE)["synthesise"])

    def test_the_ledger_pull_includes_the_events_file(self):
        step = _named(self._steps(), LEDGER_PULL_STEP)
        assert "origin/telemetry:events.jsonl" in step, (
            "events.jsonl is the join's other half; without it a synthesised "
            "case can never be walked back to its run"
        )

    def test_the_fetch_is_bounded_to_the_joined_run_ids(self):
        step = _named(self._steps(), FETCH_STEP)
        code = _code(step)
        assert "--list-needed" in code, (
            "the fetch must be bounded to the runs the cases join to; the "
            "transcripts directory grows one file per day forever"
        )
        assert "prefix_red.py" in code

    def test_the_demonstration_runs_the_script(self):
        step = _named(self._steps(), DEMONSTRATE_STEP)
        code = _code(step)
        assert "scripts/prefix_red.py" in code
        assert "--out .tmp/prefix-red.json" in code

    def test_not_demonstrated_is_a_notice_not_a_fault(self):
        """Exit 1 is the pre-filing world and is tolerated WITH a reason;
        exit 2 (the join broken) still reds."""
        step = _named(self._steps(), DEMONSTRATE_STEP)
        code = _code(step)
        assert '"$code" -eq 2' in code
        assert 'exit "$code"' in code
        assert "::notice::" in code
        assert "|| true" not in code

    def test_the_summary_reports_the_demonstration(self):
        summary = next(s for s in self._steps() if "- name: Job summary" in s)
        assert "steps.prefixred.outputs.demonstrated" in summary
        assert "steps.evidence.outputs.fetched" in summary