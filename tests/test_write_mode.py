"""Tests for scripts/write_mode.py — the calendar write switch.

The defect these pin. `runtime-daily.yml` decided dry-run with

    DRY_RUN="${{ inputs.dry_run || 'true' }}"

in both the agent env and the apply step. `inputs` does not exist on a
`schedule` event, so that expression is *always* `'true'` on the cron: a repo
with `ENABLE_CALENDAR_WRITES=true` planned every event, wrote nothing, and ran
green until somebody read the log. `docs/runtime.md` says the variable is the
switch, so the documented control was dead on the one unattended path.

The fix moved the decision into Python, which is why it is now testable at all.
These tests cover three things: the decision matrix, the `$GITHUB_OUTPUT`
contract, and the workflow wiring that must keep reading the resolved value
instead of re-deriving it.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.write_mode import resolve_dry_run

REPO_ROOT = Path(__file__).resolve().parent.parent
WRITE_MODE = REPO_ROOT / "scripts" / "write_mode.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RUNTIME_DAILY = WORKFLOWS / "runtime-daily.yml"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(WRITE_MODE), *args], capture_output=True, text=True
    )


def _outputs(stdout: str) -> dict[str, str]:
    """Parse stdout the way the Actions runner parses `$GITHUB_OUTPUT`.

    A line without `=` is a hard failure for the real parser, so this raises
    rather than skipping it — a test that tolerated a stray human line would
    pass while the step failed in CI.
    """
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        assert "=" in line, f"not a GITHUB_OUTPUT line: {line!r}"
        key, _, value = line.partition("=")
        result[key] = value
    return result


class TestDecisionMatrix:
    """The variable is the master switch; the dispatch input can only add dry-run."""

    def test_the_variable_is_the_switch_for_a_scheduled_run(self):
        # The regression: this used to be dry-run forever, because the inline
        # expression consulted an `inputs` object that a cron does not have.
        dry_run, reason = resolve_dry_run("schedule", "", "true")
        assert dry_run is False
        assert "schedule" in reason

    @pytest.mark.parametrize("value", ["", "false", "1", "yes", "TRUE!", "tru"])
    def test_anything_but_true_keeps_writes_off(self, value):
        dry_run, reason = resolve_dry_run("schedule", "", value)
        assert dry_run is True
        assert "ENABLE_CALENDAR_WRITES" in reason

    def test_lowercase_true_is_case_insensitive(self):
        assert resolve_dry_run("repository_dispatch", "", "TRUE")[0] is False

    def test_a_dispatch_defaults_to_dry_run(self):
        # The input's own default is true, so a person poking the button gets a
        # plan, not a write.
        dry_run, reason = resolve_dry_run("workflow_dispatch", "true", "true")
        assert dry_run is True
        assert "dry_run input is true" in reason

    def test_a_dispatch_can_ask_for_a_write_when_the_switch_is_on(self):
        dry_run, _ = resolve_dry_run("workflow_dispatch", "false", "true")
        assert dry_run is False

    def test_a_dispatch_cannot_write_when_the_variable_is_off(self):
        # The whole point of fail-closed: the input can never widen what the
        # repository switch allows.
        dry_run, reason = resolve_dry_run("workflow_dispatch", "false", "")
        assert dry_run is True
        assert "ENABLE_CALENDAR_WRITES" in reason

    @pytest.mark.parametrize("value", ["maybe", "0", "no", "yes!", "tru"])
    def test_an_unrecognised_input_fails_closed(self, value):
        dry_run, reason = resolve_dry_run("workflow_dispatch", value, "true")
        assert dry_run is True
        assert "failing closed" in reason

    def test_a_whitespace_input_is_treated_as_absent(self):
        # `--input "${{ inputs.dry_run }}"` cannot produce whitespace, so this
        # only happens through mis-wiring; like the absent case it follows the
        # variable rather than inventing a third outcome.
        dry_run, reason = resolve_dry_run("workflow_dispatch", "   ", "true")
        assert dry_run is False
        assert "variable decides" in reason

    def test_a_missing_event_name_fails_closed(self):
        dry_run, reason = resolve_dry_run("", "", "true")
        assert dry_run is True
        assert "no event name" in reason

    def test_a_dispatch_with_no_input_follows_the_variable(self):
        # Defensive branch: a dispatch always carries the input, so an empty one
        # means the caller wired it up wrong.
        dry_run, reason = resolve_dry_run("workflow_dispatch", "", "true")
        assert dry_run is False
        assert "variable decides" in reason


class TestGithubOutputContract:
    """stdout is the payload the runner parses; the human line goes to stderr."""

    def test_stdout_is_only_key_value_lines(self):
        result = _run(["--event", "schedule", "--input", "", "--enabled", "true"])
        assert result.returncode == 0
        outputs = _outputs(result.stdout)
        assert outputs["dry_run"] == "false"
        assert outputs["reason"]

    def test_a_stray_human_line_on_stdout_would_fail_the_step(self):
        # Guards the contract itself: if this ever stops holding, the parser
        # above is the thing that should fail, not the workflow.
        result = _run(["--event", "schedule", "--enabled", "true"])
        assert "OK:" not in result.stdout
        assert "OK: write_mode" in result.stderr

    def test_the_sanitised_reason_cannot_break_the_output_line(self):
        hostile = 'false\nsecond=injected|cell$(id)`x`"y'
        result = _run(["--event", "schedule", "--enabled", hostile])
        outputs = _outputs(result.stdout)
        assert set(outputs) == {"dry_run", "reason"}
        assert "\n" not in outputs["reason"]
        for char in '|"$`<>':
            assert char not in outputs["reason"]
        # stderr keeps the raw value: a human may need to see exactly what was set.
        assert "second=injected" in result.stderr

    def test_a_truthy_reason_reports_the_event(self):
        outputs = _outputs(
            _run(["--event", "schedule", "--enabled", "true"]).stdout
        )
        assert "live" in outputs["reason"]
        assert "schedule" in outputs["reason"]

    def test_omitting_every_argument_is_a_usage_error(self):
        # A step that was never wired up: nothing to decide from.
        result = _run([])
        assert result.returncode == 2
        assert "all omitted" in result.stderr

    def test_being_passed_empty_values_is_not_a_usage_error(self):
        """The replay case: the step's `env:` block is not supplied.

        `replay_ci.py` executes the `run:` block with only the parent
        environment, so `--event "$EVENT"` arrives as `--event ""`. Refusing to
        run would make a correct workflow unreplayable for a reason unrelated to
        it; failing closed to dry-run keeps the step inspectable and safe.
        """
        result = _run(["--event", "", "--input", "", "--enabled", ""])
        assert result.returncode == 0
        assert _outputs(result.stdout)["dry_run"] == "true"
        assert "failing closed to dry-run" in result.stderr

    def test_partial_arguments_still_decide(self):
        result = _run(["--event", "schedule", "--enabled", "true"])
        assert _outputs(result.stdout)["dry_run"] == "false"


class TestRuntimeDailyWiring:
    """The workflow must read one resolved mode, never re-derive it inline.

    Parsed with regex rather than PyYAML: the production scripts stay stdlib-only.
    """

    STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)

    @classmethod
    def _runtime_steps(cls) -> list[str]:
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        start = text.index("\n  runtime:")
        end = text.index("\n  audit:")
        job = text[start:end]
        starts = [m.start() for m in cls.STEP_START.finditer(job)]
        return [job[s:e] for s, e in zip(starts, starts[1:] + [len(job)])]

    @classmethod
    def _find(cls, needle: str) -> str:
        matches = [s for s in cls._runtime_steps() if needle in s]
        assert len(matches) == 1, f"expected exactly one step containing {needle!r}"
        return matches[0]

    @staticmethod
    def _without_comments(text: str) -> str:
        """Drop comment lines before scanning.

        The buggy expression appears verbatim in the explanatory comment above
        the fix, and in `write_mode.py`'s docstring. Prose *describing* a defect
        is not the defect — this repo has already reddened a check once by
        reading a path out of a comment.
        """
        return "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

    def test_the_broken_expression_is_gone(self):
        code = self._without_comments(RUNTIME_DAILY.read_text(encoding="utf-8"))
        assert "inputs.dry_run ||" not in code, (
            "on a schedule event `inputs` is empty, so this is always 'true' "
            "and the cron can never write"
        )

    def test_no_workflow_re_derives_the_mode_inline(self):
        # The same expression anywhere else would reintroduce the dead switch.
        offenders = [
            path.name
            for path in sorted(WORKFLOWS.glob("*.yml"))
            if "inputs.dry_run ||"
            in self._without_comments(path.read_text(encoding="utf-8"))
        ]
        assert offenders == []

    def test_one_step_resolves_the_mode_and_it_runs_always(self):
        step = self._find("Resolve the write mode")
        assert "--event" in step and "--input" in step and "--enabled" in step
        assert '>> "$GITHUB_OUTPUT"' in step
        assert "if:" not in step, (
            "the mode must exist for the un-gated agent step, so it cannot be "
            "conditional on ENABLE_CALENDAR_WRITES"
        )

    def test_the_resolver_reads_its_inputs_from_env(self):
        """A `${{ }}` inside a `run:` block reaches bash unexpanded and dies.

        That is a bash "bad substitution", so the step fails for a reason that
        has nothing to do with the workflow — and the replay, which is the tool
        that found every CI-only defect here, cannot run it at all. Passing the
        values through `env` is also GitHub's script-injection guidance.
        """
        step = self._find("Resolve the write mode")
        env_block, _, run_block = step.partition("run: |")
        assert "EVENT: ${{ github.event_name }}" in env_block
        assert "WRITE_ENABLED: ${{ vars.ENABLE_CALENDAR_WRITES }}" in env_block
        assert "${{ " not in run_block, run_block
        assert '"$EVENT"' in run_block

    def test_the_job_summary_also_reads_from_env(self):
        step = self._find("## Skill run")
        _, _, run_block = step.partition("run: |")
        assert "${{ " not in run_block, run_block

    def test_the_resolver_precedes_its_consumers(self):
        steps = self._runtime_steps()
        resolver = next(i for i, s in enumerate(steps) if "Resolve the write mode" in s)
        agent = next(i for i, s in enumerate(steps) if "opencode run" in s)
        apply_ = next(i for i, s in enumerate(steps) if "calendar_io.py apply" in s)
        assert resolver < agent, "the agent's DRY_RUN env would be empty"
        assert resolver < apply_

    def test_the_agent_env_reads_the_resolved_mode(self):
        step = self._find("opencode run")
        assert "DRY_RUN: ${{ steps.mode.outputs.dry_run }}" in step

    def test_the_apply_step_reads_the_resolved_mode(self):
        step = self._find("calendar_io.py apply")
        assert "MODE: ${{ steps.mode.outputs.dry_run }}" in step
        # --live must still be unreachable in dry-run.
        assert '--plan .tmp/plan.json --live' in step

    def test_the_apply_step_writes_only_on_an_explicit_false(self):
        # `= false` rather than `!= true`: an empty, unset, or unexpanded value
        # (the last is what a local replay produces) must stay on the dry side.
        step = self._find("calendar_io.py apply")
        assert 'if [ "$MODE" = "false" ]; then' in step
        assert '!= "true"' not in step

    def test_the_apply_step_still_plans_before_writing(self):
        step = self._find("calendar_io.py apply")
        # Anchored on the flags, not on a trailing newline: `--out` is written on
        # a continuation line now, and a pin that depended on which argument
        # ended a line would be a pin on formatting rather than on the order.
        plan_only = step.index("apply --plan .tmp/plan.json")
        live = step.index("--plan .tmp/plan.json --live")
        assert plan_only < live, "the log must contain the intended change first"

    def test_the_apply_step_has_a_dry_run_branch(self):
        step = self._find("calendar_io.py apply")
        code = self._without_comments("\n".join(step.splitlines()[1:]))
        assert "else" in code, "a non-live mode must still print the plan"
        assert "::notice::dry-run" in code

    def test_the_job_summary_reports_the_mode(self):
        step = self._find("## Skill run")
        assert "MODE: ${{ steps.mode.outputs.dry_run }}" in step
        assert "REASON: ${{ steps.mode.outputs.reason }}" in step
        assert "GITHUB_STEP_SUMMARY" in step

    def test_the_job_summary_runs_even_when_the_run_fails(self):
        assert "if: always()" in self._find("## Skill run")
