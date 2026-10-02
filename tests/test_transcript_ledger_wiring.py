"""Workflow pins for the transcript → telemetry-branch handoff.

The defect, recorded in `docs/runtime.md` before this change: "the grader has
never graded a real run". The `runtime` job produced a real transcript on every
run — teed to `.tmp/transcript.json`, uploaded as the `runtime-transcript`
artifact — and then it died with the 30-day artifact retention. Nothing graded
it (the per-case grader keys on `tests/fixtures/runtime_transcripts.json`,
which is deliberately absent), nothing filed it, and the consumers of the
telemetry branch (the audit, the self-improvement loop, a human asking "why
did Tuesday find nothing?") had nothing to read.

This is the same shape as `tests/test_run_ledger_wiring.py`, so the same pins:

* the producer is the `runtime` job (it holds the calendar credential and
  deliberately no `contents: write`, so the handoff is an artifact);
* the filer is the `audit` job, which holds the write and no calendar
  credential;
* if the artifact name drifts between the upload and the download, the
  handoff breaks and goes back to doing nothing, silently — so the two sides
  are asserted against each other, not against a literal;
* the grade happens every run (`if: always()`), in dry-run too, because a
  dry-run day is exactly the day whose transcript nobody would otherwise look
  at.

The grade is a report, not a gate, and the test pins *why*: exit 1 from
`extract_candidates.py` is also what an honest quiet day grades as, so it must
never red. The filed transcript is what tells a quiet day from a failed
emission after the fact — which is only possible if the copy is filed BEFORE
the grade decides anything.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"

JOB_START = re.compile(r"^  ([a-z][a-z0-9-]*):$", re.M)
STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)

PRODUCER_ARTIFACT = "runtime-transcript"
GRADE_STEP = "Grade the run's transcript and file it"


def _text() -> str:
    return RUNTIME_DAILY.read_text(encoding="utf-8")


def _jobs() -> dict[str, str]:
    text = _text()
    starts = [(m.group(1), m.start()) for m in JOB_START.finditer(text)]
    return {
        name: text[start : (starts[i + 1][1] if i + 1 < len(starts) else len(text))]
        for i, (name, start) in enumerate(starts)
    }


def _steps(job: str) -> list[str]:
    starts = [m.start() for m in STEP_START.finditer(job)]
    return [job[s:e] for s, e in zip(starts, starts[1:] + [len(job)])]


def _named(job: str, name: str) -> str:
    """The step whose *first line* is `name: <name>`.

    A step that merely mentions the same script in a comment must not satisfy a
    pin about the step that runs it — see `tests/test_run_ledger_wiring.py`.
    """
    matches = [
        step
        for step in _steps(job)
        if step.strip().splitlines()[0].strip() == f"- name: {name}"
    ]
    assert len(matches) == 1, f"expected one step named {name!r}, got {len(matches)}"
    return matches[0]


def _code(step: str) -> str:
    """The step's shell, with explanatory comments removed.

    Prose describing a defect is not the defect (see `tests/test_write_mode.py`)
    — and this file carries a lot of prose, because the `|| true` it removed is
    documented in the very step that no longer runs it.
    """
    return "\n".join(
        line for line in step.splitlines() if not line.lstrip().startswith("#")
    )


class TestTheProducer:
    def test_the_runtime_job_uploads_the_transcript_it_tees(self):
        step = next(
            s for s in _steps(_jobs()["runtime"]) if f"name: {PRODUCER_ARTIFACT}" in s
        )
        assert "actions/upload-artifact" in step
        assert ".tmp/transcript.json" in step, (
            "the artifact must carry the very file `Run the skill` tees, or the "
            "audit grades a copy of something else"
        )
        assert "if: always()" in step, (
            "a run whose agent step failed is the run whose transcript is "
            "most worth reading"
        )

    def test_the_tee_is_still_the_writer(self):
        """Pinned here because the handoff below reads the same file: if `tee`
        is dropped from `Run the skill`, the filer faithfully publishes an
        absent file forever."""
        agent = _named(_jobs()["runtime"], "Run the skill")
        assert "tee .tmp/transcript.json" in agent


class TestTheArtifactHandoff:
    def _upload(self) -> str:
        return next(
            s for s in _steps(_jobs()["runtime"]) if f"name: {PRODUCER_ARTIFACT}" in s
        )

    def _download(self) -> str:
        """The transcript download, not the ledger one: the audit job has two
        `download-artifact` steps, and a pin satisfied by the wrong one is a
        pin that passes while the handoff is broken. Matched on the indented
        `name:` key, because the explanatory comment above this step names the
        artifact too — prose describing the handoff is not the handoff."""
        matches = [
            s
            for s in _steps(_jobs()["audit"])
            if "actions/download-artifact" in s
            and re.search(rf"^\s+name: {PRODUCER_ARTIFACT}\s*$", s, re.M)
        ]
        assert len(matches) == 1, "expected exactly one transcript download"
        return matches[0]

    def test_the_audit_job_downloads_the_transcript(self):
        assert PRODUCER_ARTIFACT in self._download()

    def test_the_artifact_name_matches_on_both_sides(self):
        """A drifted name is the silent breakage: the download fails, the grade
        step reports "no transcript", and the branch goes back to holding none
        while every job reads green.

        Matched on the *indented* `name:` key, not the first occurrence: the
        download step's own display name (`- name: Fetch this run's
        transcript`) is also `name: something`, and a pin satisfied by the step
        title is a pin that passes while the handoff is broken.
        """
        upload = re.search(r"^\s+name: (\S+)", self._upload(), re.M)
        download = re.search(r"^\s+name: (\S+)", self._download(), re.M)
        assert upload and download
        assert upload.group(1) == download.group(1)

    def test_an_absent_artifact_is_a_notice_not_a_fault(self):
        step = self._download()
        assert "continue-on-error: true" in step
        grade = _named(_jobs()["audit"], GRADE_STEP)
        assert "[ ! -f .tmp/incoming/transcript.json ]" in grade
        assert "::notice::" in grade

    def test_the_download_precedes_the_grade_step(self):
        steps = _steps(_jobs()["audit"])
        download = next(
            i for i, s in enumerate(steps) if "actions/download-artifact" in s
        )
        grade = next(i for i, s in enumerate(steps) if f"- name: {GRADE_STEP}" in s)
        assert download < grade


class TestTheGradeAndFiling:
    def _step(self) -> str:
        return _named(_jobs()["audit"], GRADE_STEP)

    def test_it_grades_with_the_same_seam_grader_the_planner_uses(self):
        code = _code(self._step())
        assert "scripts/extract_candidates.py" in code
        assert "--transcript" in code

    def test_it_files_the_copy_before_deciding_anything(self):
        """A transcript the grader rejects is exactly the one a human needs to
        read, so the copy is filed before the grade is computed."""
        code = _code(self._step())
        filed = code.index("cp .tmp/incoming/transcript.json")
        graded = code.index("scripts/extract_candidates.py")
        assert filed < graded

    def test_it_files_append_only_under_transcripts_on_the_branch(self):
        code = _code(self._step())
        assert ".tmp/telemetry/transcripts/" in code
        assert "mkdir -p .tmp/telemetry/transcripts" in code, (
            "the first transcript ever filed must not fail on a missing directory"
        )

    def test_it_runs_even_when_the_audit_has_no_inputs(self):
        """A dry-run day writes no events, so the audit's own inputs are
        absent — and that is precisely the day whose transcript nobody would
        otherwise look at."""
        assert "if: always()" in self._step()

    def test_a_quiet_day_is_reported_not_red(self):
        """Exit 1 is what an honest quiet day grades as. The grade is a report;
        the filed transcript is what tells it apart from a failed emission."""
        code = _code(self._step())
        assert '"$grade" -eq 1' in code
        assert 'exit "$grade"' in code  # the only exit that propagates the grade

    def test_the_tolerated_failure_is_never_silenced(self):
        """The `|| true` anti-pattern this repository documents: a tolerated
        failure must say why. This step echoes the grader's own reason
        (`tail` of its stderr) and names the two cases the grade cannot tell
        apart."""
        code = _code(self._step())
        assert "|| true" not in code
        assert ".tmp/transcript-stderr.txt" in code

    def test_the_filing_precedes_the_commit(self):
        """`git add -A` in `Commit telemetry` is what puts the transcript on
        the branch; filing after it would publish nothing, every run."""
        steps = _steps(_jobs()["audit"])
        grade = next(i for i, s in enumerate(steps) if f"- name: {GRADE_STEP}" in s)
        commit = next(i for i, s in enumerate(steps) if "Commit telemetry" in s)
        assert grade < commit

    def test_the_commit_still_runs_after_a_red_grade(self):
        """An unreadable transcript reds the grade step — and must still be
        published, because it is the evidence of what the run actually said."""
        commit = next(s for s in _steps(_jobs()["audit"]) if "Commit telemetry" in s)
        assert "if: always()" in commit

    def test_the_summary_reports_the_grade(self):
        """"nothing was graded" is a different fact from "graded empty", and
        the summary is where a red-free run says which one happened."""
        summary = next(s for s in _steps(_jobs()["audit"]) if "Job summary" in s)
        assert "TRANSCRIPT_READY" in summary
        assert "TRANSCRIPT_GRADE" in summary


class TestTheRemovedDeadPreflight:
    """The runtime job once "preflighted" its transcript with
    `capture_transcripts.py --runner replay --from .tmp/transcript.json --check
    || true` — a call that can never succeed (`--check` demands one usable
    transcript per eval case; the raw run stream is none of them), so it exited
    non-zero on every run and the `|| true` hid exactly that. The documented
    anti-pattern: a step that tolerates a failure without saying why is a step
    that has never run. Removed rather than repaired — `extract_candidates.py`
    in the same step is the real contract enforcement."""

    def test_the_runtime_job_no_longer_replays_the_raw_transcript(self):
        candidates = _named(_jobs()["runtime"], "Extract the candidates the agent found")
        assert "--runner replay" not in _code(candidates)

    def test_the_removal_is_documented_in_place(self):
        candidates = _named(_jobs()["runtime"], "Extract the candidates the agent found")
        assert "never succeed" in candidates, (
            "a removed defect that is not recorded in place is one that gets "
            "reintroduced with a clear conscience"
        )