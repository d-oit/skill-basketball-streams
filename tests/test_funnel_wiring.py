"""Workflow pins for the funnel report.

The funnel is a derived metric — `scripts/metrics.py` computes it from ledgers
that already exist, and there is deliberately no funnel writer (a second source
of truth for numbers the append-only streams already carry). What CAN break
silently is the *consumer*: a report step that stops running, or drifts into
rewriting the snapshot a step earlier, would make the metric invisible while
every job reads green.

Pinned here:

* the audit job reports the funnel (`--funnel --dry-run`: recomputes and
  prints only, so the step cannot drift from the snapshot the branch holds);
* a missing candidate ledger is a notice, not a red — the funnel has no input,
  which is the state of every fresh install's first run;
* nothing is tolerated — a report that swallows its failure reports nothing
  while looking like a report;
* the report goes to the job summary, where an operator reads it, not only
  into the log.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"

STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)
JOB_START = re.compile(r"^  ([a-z][a-z0-9-]*):$", re.M)

FUNNEL_STEP = "Funnel report"


def _jobs() -> dict[str, str]:
    text = RUNTIME_DAILY.read_text(encoding="utf-8")
    starts = [(m.group(1), m.start()) for m in JOB_START.finditer(text)]
    return {
        name: text[start : (starts[i + 1][1] if i + 1 < len(starts) else len(text))]
        for i, (name, start) in enumerate(starts)
    }


def _steps(job: str) -> list[str]:
    starts = [m.start() for m in STEP_START.finditer(job)]
    return [job[s:e] for s, e in zip(starts, starts[1:] + [len(job)])]


def _step() -> str:
    matches = [
        step
        for step in _steps(_jobs()["audit"])
        if step.strip().splitlines()[0].strip() == f"- name: {FUNNEL_STEP}"
    ]
    assert len(matches) == 1, f"expected one {FUNNEL_STEP!r} step in the audit job"
    return matches[0]


def _code(step: str) -> str:
    return "\n".join(
        line for line in step.splitlines() if not line.lstrip().startswith("#")
    )


class TestTheFunnelReport:
    def test_it_runs_the_metrics_tool_with_the_funnel_flag(self):
        code = _code(_step())
        assert "scripts/metrics.py" in code
        assert "--funnel" in code

    def test_it_recomputes_without_rewriting_the_snapshot(self):
        """The snapshot was rewritten a step earlier and carries the same
        table; a second write here is a chance to drift from the branch."""
        code = _code(_step())
        assert "--dry-run" in code

    def test_a_missing_input_is_a_notice_not_a_fault(self):
        code = _code(_step())
        assert "[ ! -f .tmp/telemetry/candidates.jsonl ]" in code
        assert "::notice::" in code
        assert "exit 0" in code

    def test_nothing_is_tolerated(self):
        step = _step()
        assert "|| true" not in _code(step)
        assert "continue-on-error" not in step

    def test_it_lands_in_the_job_summary(self):
        code = _code(_step())
        assert "GITHUB_STEP_SUMMARY" in code

    def test_it_runs_even_when_the_audit_found_nothing(self):
        assert "if: always()" in _step()

    def test_it_is_the_only_funnel_reporter(self):
        """Two reporters reading the same ledgers are a disagreement waiting
        to happen; there is exactly one."""
        every_step = "\n".join(
            step
            for job in ("telemetry", "runtime", "audit", "rung-health")
            if job in _jobs()
            for step in _steps(_jobs()[job])
        )
        assert every_step.count("--funnel") == 1