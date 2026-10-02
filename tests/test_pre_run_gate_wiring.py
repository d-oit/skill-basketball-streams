"""Workflow pins for the pre-run product gate.

The safety property: a red product gate means the contract is broken, and a
broken contract must not run the agent that reads and writes a subscriber's
calendar. Before this gate existed, a bad merge to `main` would sail through
the daily cron — the workflow only runs the skill, and nothing in the job ever
asked whether the skill's own graders were green.

What is pinned, and why each pin exists:

* all four product graders run (validate, runtime_eval, synthesise --verify,
  pytest) — one missing is a narrowed gate that reads as a full one;
* the gate precedes `Run the skill` AND `Install opencode CLI`, so a red gate
  costs nothing and can never be outrun by an earlier failure;
* the gate is unconditional (no `if:` on the write mode) — a dry run spends
  the free-tier quota and files a transcript produced by a broken contract
  too, so it is gated as well;
* nothing in the gate tolerates a failure — `|| true` or
  `continue-on-error` here would be the exact anti-pattern this repository
  documents: a gate that cannot fail is a vacuous pass;
* the gate stays on the product graders, NOT `do-harness eval` — the boundary
  `tests/test_harness_boundary.py` pins for the whole repo. `eval` resolves
  skills only under `.agents/skills` and never grades the root skill, so a
  gate built on it would grade the tooling while the product ran ungated.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"

STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)
JOB_START = re.compile(r"^  ([a-z][a-z0-9-]*):$", re.M)

GATE_STEP = "Gate — the repo's own graders, before any calendar write"
DEPS_STEP = "Install test dependencies (for the pre-run gate)"

THE_GRADERS = (
    "scripts/validate.py",
    "scripts/runtime_eval.py",
    "scripts/synthesise_eval_case.py",
    "pytest tests/",
)


def _jobs() -> dict[str, str]:
    text = RUNTIME_DAILY.read_text(encoding="utf-8")
    starts = [(m.group(1), m.start()) for m in JOB_START.finditer(text)]
    return {
        name: text[start : (starts[i + 1][1] if i + 1 < len(starts) else len(text))]
        for i, (name, start) in enumerate(starts)
    }


def _steps() -> list[str]:
    job = _jobs()["runtime"]
    starts = [m.start() for m in STEP_START.finditer(job)]
    return [job[s:e] for s, e in zip(starts, starts[1:] + [len(job)])]


def _named(name: str) -> str:
    matches = [
        step
        for step in _steps()
        if step.strip().splitlines()[0].strip() == f"- name: {name}"
    ]
    assert len(matches) == 1, f"expected one step named {name!r}, got {len(matches)}"
    return matches[0]


def _code(step: str) -> str:
    return "\n".join(
        line for line in step.splitlines() if not line.lstrip().startswith("#")
    )


class TestTheGateRuns:
    def test_all_four_product_graders_run(self):
        code = _code(_named(GATE_STEP))
        for grader in THE_GRADERS:
            assert grader in code, (
                f"{grader} missing from the gate: a gate missing one grader is "
                "a narrowed gate that reads as a full one"
            )

    def test_the_test_dependencies_are_installed_first(self):
        steps = _steps()
        deps = next(i for i, s in enumerate(steps) if f"- name: {DEPS_STEP}" in s)
        gate = next(i for i, s in enumerate(steps) if f"- name: {GATE_STEP}" in s)
        assert deps < gate


class TestTheGateStopsTheRun:
    def test_the_gate_precedes_the_agent_and_the_cli_install(self):
        steps = _steps()
        gate = next(i for i, s in enumerate(steps) if f"- name: {GATE_STEP}" in s)
        for later_name in ("Install opencode CLI (pinned)", "Run the skill"):
            later = next(
                i
                for i, s in enumerate(steps)
                if s.strip().splitlines()[0].strip() == f"- name: {later_name}"
            )
            assert gate < later, (
                f"the gate must run before {later_name!r} — placed after it, a "
                "red gate cannot stop the run"
            )

    def test_no_failure_is_tolerated(self):
        step = _named(GATE_STEP)
        assert "|| true" not in _code(step)
        assert "continue-on-error" not in step, (
            "a gate that cannot fail is the vacuous pass this repository "
            "keeps warning about"
        )

    def test_the_gate_is_unconditional(self):
        step = _named(GATE_STEP)
        head = step.splitlines()[0]
        assert not re.search(r"^\s*if:", step, re.M), (
            "a gate conditioned on the write mode lets a dry run of a broken "
            "contract spend quota and file a misleading transcript"
        )
        assert head.strip() == f"- name: {GATE_STEP}"


class TestTheBoundaryHolds:
    def test_the_gate_is_not_do_harness_eval(self):
        code = _code(_named(GATE_STEP))
        assert "do-harness" not in code, (
            "`do-harness eval` grades only .agents/skills and never the root "
            "skill — a gate built on it grades the tooling while the product "
            "runs ungated (see tests/test_harness_boundary.py)"
        )