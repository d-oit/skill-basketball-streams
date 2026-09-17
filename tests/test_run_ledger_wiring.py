"""Workflow pins for the Phase 2 -> Phase 3 ledger handoff.

The defect. `telemetry/events.jsonl` and `telemetry/evidence.json` are Phase 3's
documented inputs, and the audit job reads them —

    if [ -f .tmp/telemetry/events.jsonl ] && [ -f .tmp/telemetry/evidence.json ]

— but nothing wrote them, so the audit printed a notice and did nothing on every
run. Phase 3 had never once run. `scripts/event_ledger.py` is the writer; these
tests pin the wiring that delivers it, because that wiring is where this can
break silently:

* the writer lives in `runtime` (it is the only job that knows what it wrote) and
  `runtime` deliberately holds no `contents: write`, so the handoff is an
  artifact;
* the audit job is the one that publishes it, and it must still run on a day the
  skill run *failed* — `needs: runtime` may order it, never gate it;
* if the artifact name drifts between the upload and the download, the handoff
  breaks and the audit goes back to doing nothing, silently.

Parsed with regex rather than PyYAML: CI installs pytest and nothing else.
"""
from __future__ import annotations

import re
from pathlib import Path

from scripts.event_ledger import EVIDENCE_NAME, LEDGER_NAME

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"

JOB_START = re.compile(r"^  ([a-z][a-z0-9-]*):$", re.M)
STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)


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


def _find(job: str, needle: str) -> str:
    matches = [step for step in _steps(job) if needle in step]
    assert len(matches) == 1, f"expected one step containing {needle!r}"
    return matches[0]


def _named(job: str, name: str) -> str:
    """The step whose *first line* is `name: <name>`.

    Needed wherever a step's trailing slice includes the explanatory comment
    above the next step: a step that merely *mentions* the same script must not
    be able to satisfy a pin about the step that runs it.
    """
    matches = [
        step
        for step in _steps(job)
        if step.strip().splitlines()[0].strip() == f"- name: {name}"
    ]
    assert len(matches) == 1, f"expected one step named {name!r}"
    return matches[0]


def _without_comments(text: str) -> str:
    """Prose describing a defect is not the defect (see tests/test_write_mode.py)."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


LEDGER_STEP = "Record what was written (Phase 3 input)"
PLAN_STEP = "Plan the calendar writes (no API calls)"
VERDICTS_STEP = "Read the audit verdict ledger"


class TestTheWriterIsWired:
    def test_the_runtime_job_runs_the_ledger_writer(self):
        step = _named(_jobs()["runtime"], LEDGER_STEP)
        assert "scripts/event_ledger.py record" in step
        assert "--plan .tmp/plan.json" in step
        assert "--applied .tmp/applied.json" in step
        assert "--dest .tmp" in step

    def test_the_ledger_writer_runs_even_when_a_step_failed(self):
        """A partially failed apply still writes some events — record them."""
        assert "if: always()" in _named(_jobs()["runtime"], LEDGER_STEP)

    def test_a_run_that_never_reached_the_write_path_is_a_notice(self):
        """Absent input is not a fault; an unkeyable write still reds the step."""
        step = _named(_jobs()["runtime"], LEDGER_STEP)
        assert "[ ! -f .tmp/applied.json ]" in step
        assert "exit 0" in step

    def test_the_ledger_step_precedes_the_artifact_upload(self):
        steps = _steps(_jobs()["runtime"])
        ledger = next(
            i
            for i, s in enumerate(steps)
            if s.strip().splitlines()[0].strip() == f"- name: {LEDGER_STEP}"
        )
        upload = next(i for i, s in enumerate(steps) if "name: run-ledger" in s)
        assert ledger < upload, "the artifact must be uploaded after it exists"


class TestTheApplyStepProducesTheResult:
    """The ledger reads the applied result, so `apply` must write one."""

    def _step(self) -> str:
        return _named(_jobs()["runtime"], "Apply the plan")

    def test_every_apply_call_writes_its_result(self):
        """Not just the live one: a dry run must be visibly a dry run."""
        step = self._step()
        assert step.count("calendar_io.py apply") == step.count(
            "--out .tmp/applied.json"
        ), (
            "every apply invocation needs --out; an unrecorded one leaves the "
            "audit with no ids to resolve a verdict against"
        )

    def test_the_live_branch_is_the_one_that_gets_it_before_exiting(self):
        code = _without_comments(self._step())
        live = code.index("--live")
        assert "--out .tmp/applied.json" in code[live:]

    def test_the_dry_run_branch_also_writes_it(self):
        """Otherwise a dry run leaves no applied result, and the ledger's guard
        reads that as "the run never reached the write path"."""
        code = _without_comments(self._step())
        dry = code.split("if [")[1].split("else")[0]
        otherwise = code.split("else")[1]
        assert "--out .tmp/applied.json" in dry
        assert "--out .tmp/applied.json" in otherwise


class TestTheArtifactHandoff:
    """The runtime job cannot push to the telemetry branch (and must not)."""

    def test_the_runtime_job_uploads_both_files(self):
        step = _find(_jobs()["runtime"], "name: run-ledger")
        assert "actions/upload-artifact" in step
        assert LEDGER_NAME in step
        assert EVIDENCE_NAME in step

    def test_the_audit_job_downloads_the_same_artifact(self):
        step = _find(_jobs()["audit"], "actions/download-artifact")
        assert "name: run-ledger" in _find(_jobs()["runtime"], "name: run-ledger")

    def test_the_artifact_name_matches_on_both_sides(self):
        upload = re.search(
            r"name: (run-ledger)", _find(_jobs()["runtime"], "name: run-ledger")
        )
        download = re.search(
            r"name: (run-ledger)", _find(_jobs()["audit"], "actions/download-artifact")
        )
        assert upload and download
        assert upload.group(1) == download.group(1)

    def test_it_lands_where_the_audit_looks_for_it(self):
        step = _find(_jobs()["audit"], "actions/download-artifact")
        assert "path: .tmp/telemetry" in step, (
            "the audit checks .tmp/telemetry, and the commit there is what puts "
            "these files on the telemetry branch"
        )

    def test_an_absent_artifact_is_tolerated(self):
        """A skipped or failed run produces none, and the branch may still have
        events worth auditing."""
        step = _find(_jobs()["audit"], "actions/download-artifact")
        assert "continue-on-error: true" in step

    def test_the_download_precedes_the_inputs_check(self):
        steps = _steps(_jobs()["audit"])
        download = next(
            i for i, s in enumerate(steps) if "actions/download-artifact" in s
        )
        check = next(i for i, s in enumerate(steps) if "Check for audit inputs" in s)
        assert download < check

    def test_the_audit_still_reads_the_files_the_ledger_writes(self):
        """Ties the workflow's existence test to the writer's own names."""
        step = _find(_jobs()["audit"], "Check for audit inputs")
        assert f".tmp/telemetry/{LEDGER_NAME}" in step
        assert f".tmp/telemetry/{EVIDENCE_NAME}" in step


class TestTheVerdictLoop:
    """Phase 3's verdicts, read back by Phase 2 — the other direction of the seam.

    §17's hard requirement is that a `WRONG` event is never shown as `VERIFIED`.
    `plan_upsert` reads a stored `WRONG` off the calendar, and the calendar
    recovers it from a `[WRONG] ` title prefix that `audit_events` *computes* and
    puts in `audit.jsonl` — and that nothing applies. So the rule was unreachable
    on the real path until the planner was given the record itself.
    """

    def test_the_runtime_job_reads_the_audit_ledger(self):
        step = _named(_jobs()["runtime"], VERDICTS_STEP)
        assert "git show origin/telemetry:audit.jsonl" in step

    def test_it_reads_without_writing_the_branch(self):
        """A read-only `git show` needs no `contents: write`, and this job must
        not have it: it holds the calendar credential."""
        step = _named(_jobs()["runtime"], VERDICTS_STEP)
        assert "git push" not in step
        assert "worktree" not in step

    def test_the_plan_step_hands_it_to_the_planner(self):
        step = _without_comments(_named(_jobs()["runtime"], PLAN_STEP))
        assert "--verdicts .tmp/audit.jsonl" in step
        assert "scripts/upsert_events.py" in step

    def test_the_flag_is_only_added_when_the_ledger_exists(self):
        """`--verdicts` on a missing file is a usage error, not an empty ledger."""
        read = _named(_jobs()["runtime"], VERDICTS_STEP)
        assert "ready=true" in read and "ready=false" in read
        plan = _without_comments(_named(_jobs()["runtime"], PLAN_STEP))
        assert "VERDICTS_READY" in plan
        assert '"$VERDICTS_READY" = "true"' in plan

    def test_the_plan_step_does_not_interpolate_into_its_shell(self):
        """`${{ }}` reaches the shell unexpanded in a replay, where it is a bash
        "bad substitution" — a step that dies for a reason unrelated to it."""
        plan = _named(_jobs()["runtime"], PLAN_STEP)
        code = "\n".join(
            line for line in plan.splitlines() if not line.lstrip().startswith("#")
        )
        assert "${{ github" not in code

    def test_reading_the_ledger_precedes_planning(self):
        steps = _steps(_jobs()["runtime"])
        read = next(
            i for i, s in enumerate(steps)
            if s.strip().splitlines()[0].strip() == f"- name: {VERDICTS_STEP}"
        )
        plan = next(
            i for i, s in enumerate(steps)
            if s.strip().splitlines()[0].strip() == f"- name: {PLAN_STEP}"
        )
        assert read < plan


class TestTheAuditIsOrderedNotGated:
    def test_the_audit_is_ordered_after_the_runtime_job(self):
        assert "needs: [telemetry, runtime]" in _jobs()["audit"]

    def test_but_it_still_runs_when_the_run_failed(self):
        """A day the skill run failed is when a false positive is most likely."""
        job = _jobs()["audit"]
        assert re.search(r"if: always\(\)", job), (
            "`needs:` orders the job; without `always()` a failed or skipped "
            "runtime would take the audit down with it"
        )

    def test_the_runtime_job_gains_no_write_permission(self):
        """It holds the calendar credential; the two authorities stay apart.

        True of the *code*: the job comment discusses `contents: write` while
        explaining the handoff, and prose about a permission is not one.
        """
        assert "contents: write" not in _without_comments(_jobs()["runtime"])


class TestNothingWasToleratedIntoASilentNoOp:
    def test_the_ledger_step_is_not_swallowed(self):
        """`|| true` here would restore exactly the silent audit this fixes."""
        step = _without_comments(_named(_jobs()["runtime"], LEDGER_STEP))
        assert "|| true" not in step

    def test_the_summary_step_cannot_satisfy_the_writer_pins(self):
        """Two steps mention the same script; a name lookup tells them apart."""
        writer = _named(_jobs()["runtime"], LEDGER_STEP)
        summary = _named(_jobs()["runtime"], "Ledger summary")
        assert writer != summary
        # The summary is the read-only view: it must not be the one recording.
        assert "--markdown" in summary
        assert "--markdown" not in writer
