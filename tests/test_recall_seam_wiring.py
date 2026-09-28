"""Workflow pins for the Phase 1/2 -> Phase 0 recall seam.

The defect. `metrics.json` reported `recall 0.000 (0/270)` on the telemetry
branch every day, and the metric could only ever be 0:

    captured  = unique game_keys with disposition == "created"

`candidates.jsonl` is written by ONE job — Phase 0 (`run_daily.py`) — and Phase 0
only searches. It labels every row `unverifiable` because a search hit is not a
decision. The decision lives in Phase 1/2 (`upsert_events.py` -> `plan.json`,
`calendar_io.py apply` -> `applied.json`), and nothing ever appended the row
saying what the run decided. Measured on the real branch ledger: 1322 rows, 1322
`unverifiable`, 0 `created`, 0 non-empty `game_key`.

So this is the "documented artifact with no producer" class in its purest form:
the metric was not wrong, it was reading a field only a fixture ever wrote.
`tests/fixtures/candidates_ledger.jsonl` is hand-built with a `created` row, so
CI read 0.5 while the real pipeline could only produce 0.

The fix spans three jobs, and each seam is where it can break silently:

* `runtime` produces the outcome rows from `plan.json` + `applied.json`, and
  holds no `contents: write` (it holds the calendar credential), so they travel
  in the same `run-ledger` artifact as the event ledger;
* `audit` is the job that publishes to the telemetry branch, and it must APPEND
  to `candidates.jsonl` — writing a sibling file would leave recall unmoved;
* the commit step must not be gated on the audit's inputs alone, or a day that
  decided games without writing an event commits its rows and never pushes them.

Parsed with regex rather than PyYAML, for the reason in
`tests/test_run_ledger_wiring.py`.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"

JOB_START = re.compile(r"^  ([a-z][a-z0-9-]*):$", re.M)
STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)

OUTCOME_STEP = "Record the run's outcome into the candidate ledger"
PUBLISH_STEP = "Append this run's outcome to candidates.jsonl"


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
    """The step whose *first line* is `name: <name>`."""
    matches = [s for s in _steps(job) if s.strip().splitlines()[0].strip() == f"- name: {name}"]
    assert len(matches) == 1, f"expected exactly one step named {name!r}"
    return matches[0]


def _without_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


class TestTheOutcomeWriterIsWired:
    """`runtime` decides the games, so `runtime` records the outcome."""

    def test_the_runtime_job_runs_the_candidate_ledger_writer(self):
        step = _named(_jobs()["runtime"], OUTCOME_STEP)
        assert "scripts/candidates.py record" in step
        assert "--plan .tmp/plan.json" in step
        # The apply result is what makes a dry run record nothing.
        assert "--applied .tmp/applied.json" in step

    def test_the_writer_runs_even_when_a_step_failed(self):
        """A partially failed run still decided some games."""
        assert "if: always()" in _named(_jobs()["runtime"], OUTCOME_STEP)

    def test_a_run_that_never_decided_anything_is_a_notice(self):
        step = _named(_jobs()["runtime"], OUTCOME_STEP)
        assert "[ ! -f .tmp/plan.json ]" in step
        assert "exit 0" in step

    def test_the_outcome_rows_are_handed_off_as_an_artifact(self):
        """`runtime` holds no `contents: write`; the artifact is the handoff."""
        step = next(s for s in _steps(_jobs()["runtime"]) if "name: run-ledger" in s)
        assert ".tmp/outcomes.jsonl" in step

    def test_the_outcome_step_precedes_the_artifact_upload(self):
        steps = _steps(_jobs()["runtime"])
        outcome = next(
            i
            for i, s in enumerate(steps)
            if s.strip().splitlines()[0].strip() == f"- name: {OUTCOME_STEP}"
        )
        upload = next(i for i, s in enumerate(steps) if "name: run-ledger" in s)
        assert outcome < upload, "the artifact must be uploaded after it exists"


class TestTheAuditJobPublishesTheOutcome:
    """`audit` is the only job with `contents: write`."""

    def test_the_outcome_is_appended_to_the_existing_ledger(self):
        """Not written beside it: `recall` reads `candidates.jsonl` by name, so a
        sibling file would be a row nothing joins."""
        step = _named(_jobs()["audit"], PUBLISH_STEP)
        code = _without_comments(step)
        assert ">> .tmp/telemetry/candidates.jsonl" in code

    def test_the_merged_ledger_is_recomputed(self):
        """The snapshot is derived; leaving it stale is how 0.000 survived."""
        step = _named(_jobs()["audit"], PUBLISH_STEP)
        assert "scripts/metrics.py" in step

    def test_it_runs_even_when_the_audit_found_nothing(self):
        """A day that decided games but wrote no event still moved recall."""
        assert "if: always()" in _named(_jobs()["audit"], PUBLISH_STEP)

    def test_an_absent_outcome_is_a_notice_rather_than_a_red_step(self):
        step = _named(_jobs()["audit"], PUBLISH_STEP)
        assert "[ ! -f .tmp/telemetry/outcomes.jsonl ]" in step
        assert "exit 0" in step


class TestTheCommitIsNotGatedOnTheAuditAlone:
    """The defect this seam would have inherited.

    The commit step was gated on the audit's `inputs` output. A run that decided
    games but wrote no new event therefore appended to the worktree, committed
    nothing, and exited — the rows were never pushed, and the next day the same
    thing happened. The gate has to be the *commit*'s own emptiness check.
    """

    def test_the_commit_step_is_unconditional(self):
        job = _jobs()["audit"]
        step = next(
            s
            for s in _steps(job)
            if s.strip().splitlines()[0].strip()
            == "- name: Commit telemetry (one commit per run, never per row)"
        )
        assert "if: always()" in step
        assert "inputs.outputs.ready" not in _without_comments(step)

    def test_an_empty_commit_is_still_a_no_op(self):
        step = next(
            s
            for s in _steps(_jobs()["audit"])
            if "git diff --cached --quiet" in s
        )
        assert "exit 0" in step
