"""Tests for scripts/llm_model.py — which model the `opencode run` steps use.

The defect these pin. Both workflows pinned the model with

    LLM_MODEL: ${{ vars.LLM_MODEL || 'opencode/big-pickle' }}

`opencode/big-pickle` is an **OpenCode Zen** id — rung 2 of the ladder. The
ladder is credential-driven, and `capture_transcripts.py --check-rungs` passes for
a repository holding only `GEMINI_API_KEY` or only `OPENROUTER_API_KEY`. Those
repositories are correctly configured, and the run still asked the CLI for a Zen
model they had no credential for: no transcript, which reads exactly like a quiet
day.

These tests cover three things: the decision matrix, the `$GITHUB_OUTPUT`
contract, and the workflow wiring that must keep *reading* the resolved value
instead of re-deriving it inline — the mistake `DRY_RUN` already made once.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import capture_transcripts as ct  # noqa: E402

from scripts.llm_model import (  # noqa: E402
    PIN,
    RUNG_PROVIDERS,
    resolve_model,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LLM_MODEL = REPO_ROOT / "scripts" / "llm_model.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RUNTIME_DAILY = WORKFLOWS / "runtime-daily.yml"
SELF_IMPROVE = WORKFLOWS / "self-improve.yml"

# Every credential any rung accepts, so a "nothing is configured" case cannot
# pass just because the ambient environment happens to carry one.
ALL_CREDENTIALS = tuple(
    name for names in ct.RUNG_CREDENTIALS.values() for name in names
)


def _env(*names: str) -> dict[str, str]:
    """A minimal environment holding exactly `names` as dummy credentials."""
    return {name: "dummy" for name in names}


def _run(args: list[str] | None = None, env: dict | None = None):
    return subprocess.run(
        [sys.executable, str(LLM_MODEL), *(args or [])],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


def _clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ALL_CREDENTIALS}
    env.pop(PIN, None)
    env.update(extra)
    return env


def _outputs(stdout: str) -> dict[str, str]:
    """Parse stdout the way the Actions runner parses `$GITHUB_OUTPUT`.

    A line without `=` is a hard failure for the real parser, so this raises
    rather than skipping it — a test that tolerated a stray human line would pass
    while the step failed in CI.
    """
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        assert "=" in line, f"not a GITHUB_OUTPUT line: {line!r}"
        key, _, value = line.partition("=")
        result[key] = value
    return result


# ---------------------------------------------------------------------------
# the decision matrix
# ---------------------------------------------------------------------------


class TestTheLadderDecides:
    def test_the_pin_wins_over_every_credential(self):
        resolution = resolve_model(
            "anthropic/claude-sonnet-4.5",
            _env(*ALL_CREDENTIALS),
        )
        assert resolution.model == "anthropic/claude-sonnet-4.5"
        assert resolution.rung == "pinned"

    def test_a_gemini_key_selects_rung_one(self):
        resolution = resolve_model("", _env("GEMINI_API_KEY"))
        assert resolution.model == f"{RUNG_PROVIDERS['gemini']}/{ct.GEMINI_DEFAULT_MODEL}"
        assert resolution.rung == "gemini"

    def test_the_google_alias_counts_as_the_gemini_rung(self):
        # `capture_transcripts.gemini_api_key()` accepts both names; a resolver
        # that only knew one of them would send a configured runner to rung 3.
        resolution = resolve_model("", _env("GOOGLE_API_KEY"))
        assert resolution.rung == "gemini"

    def test_a_zen_key_selects_rung_two(self):
        resolution = resolve_model("", _env("OPENCODE_ZEN_API_KEY"))
        assert resolution.model == "opencode/big-pickle"
        assert resolution.rung == "opencode"

    def test_an_openrouter_key_alone_selects_the_free_router(self):
        """The case the change exists for.

        No Gemini key, no Zen key: the run must use OpenRouter's free router
        rather than a Zen model id the repository has no credential for.
        """
        resolution = resolve_model("", _env("OPENROUTER_API_KEY"))
        assert resolution.model == "openrouter/openrouter/free"
        assert resolution.rung == "openrouter"

    def test_an_earlier_rung_beats_a_later_one(self):
        # Order is the ladder's, not dict order: a repo with both keys must not
        # pick rung 3 just because it was checked first.
        resolution = resolve_model("", _env("OPENROUTER_API_KEY", "GEMINI_API_KEY"))
        assert resolution.rung == "gemini"
        resolution = resolve_model("", _env("OPENROUTER_API_KEY", "OPENCODE_ZEN_API_KEY"))
        assert resolution.rung == "opencode"

    def test_nothing_configured_still_resolves_and_says_why(self):
        # A selector, not a gate: refusing to run here would break a replay,
        # where the parent environment's keys are stripped by design. The gate
        # is `--check-rungs`, upstream in the same job.
        resolution = resolve_model("", {})
        assert resolution.model == "openrouter/openrouter/free"
        assert "check-rungs" in resolution.reason

    def test_a_blank_credential_is_not_a_credential(self):
        # GitHub renders an unset secret as the empty string, so "" must never
        # read as "configured" — that is the whole difference between choosing a
        # rung and choosing nothing.
        resolution = resolve_model("", {"GEMINI_API_KEY": "   "})
        assert resolution.rung != "gemini"

    def test_the_cli_providers_do_not_claim_a_zen_model(self):
        """`--check-rungs` accepts them, selection does not.

        A runner authenticated only through `opencode auth login` is configured,
        and `RUNG_CREDENTIALS` says so so the *preflight* does not red it. But a
        Zen id is meaningless without a Zen key, and this module deliberately does
        not probe `auth.json` — that case is what `LLM_MODEL` pins. Falling
        through to the free router is honest; emitting `opencode/big-pickle` here
        would be the original bug.
        """
        resolution = resolve_model("", _env("ANTHROPIC_API_KEY"))
        assert resolution.model != "opencode/big-pickle"
        assert resolution.rung == "openrouter"

    def test_the_precedence_covers_exactly_the_ladder(self):
        # If the ladder is ever reordered or a rung added, this module's branches
        # have to be re-derived rather than silently disagreeing with it.
        assert set(RUNG_PROVIDERS) == set(ct.LADDER)

    def test_each_rung_names_its_provider_prefix(self):
        # opencode composes the id as `provider/model`; a bare id would be sent
        # to whatever provider the CLI defaults to.
        for model, rung in (
            (resolve_model("", _env("GEMINI_API_KEY")).model, "gemini"),
            (resolve_model("", _env("OPENCODE_ZEN_API_KEY")).model, "opencode"),
            (resolve_model("", _env("OPENROUTER_API_KEY")).model, "openrouter"),
        ):
            assert model.startswith(f"{RUNG_PROVIDERS[rung]}/")


# ---------------------------------------------------------------------------
# the CLI surface and the $GITHUB_OUTPUT contract
# ---------------------------------------------------------------------------


class TestCli:
    def test_the_payload_is_only_output_lines(self):
        result = _run(env=_clean_env(OPENROUTER_API_KEY="dummy"))
        assert result.returncode == 0, result.stderr
        assert _outputs(result.stdout)["model"] == "openrouter/openrouter/free"

    def test_the_human_line_goes_to_stderr(self):
        """`>> "$GITHUB_OUTPUT"` would fail the step on a stray stdout line."""
        result = _run(env=_clean_env(GEMINI_API_KEY="dummy"))
        assert "OK: llm_model:" in result.stderr
        assert "OK: llm_model:" not in result.stdout

    def test_the_report_names_the_rung_and_the_model(self):
        result = _run(env=_clean_env(OPENROUTER_API_KEY="dummy"))
        assert "rung openrouter" in result.stderr
        assert "openrouter/openrouter/free" in result.stderr

    def test_the_env_var_is_the_pin(self):
        result = _run(env=_clean_env(**{PIN: "opencode/mimo-v2.5-free"}))
        assert _outputs(result.stdout)["model"] == "opencode/mimo-v2.5-free"
        assert _outputs(result.stdout)["rung"] == "pinned"

    def test_an_empty_env_var_is_not_a_pin(self):
        # GitHub passes "" for an unset variable; treating that as a pin would
        # put an empty `--model` on the command line.
        result = _run(env=_clean_env(**{PIN: ""}))
        assert _outputs(result.stdout)["rung"] != "pinned"

    def test_an_explicit_empty_pin_is_not_a_pin(self):
        result = _run(["--pin", ""], env=_clean_env(GEMINI_API_KEY="dummy"))
        assert _outputs(result.stdout)["rung"] == "gemini"

    def test_a_reason_never_carries_a_newline(self):
        # A credential value cannot reach the reason, but the contract has to
        # hold whatever a future edit interpolates.
        result = _run(env=_clean_env(GEMINI_API_KEY="dummy"))
        assert len(result.stdout.splitlines()) == 3

    def test_no_credential_at_all_is_still_a_decision(self):
        result = _run(env={"PATH": os.environ.get("PATH", "")})
        assert result.returncode == 0, result.stderr
        assert _outputs(result.stdout)["model"] == "openrouter/openrouter/free"


# ---------------------------------------------------------------------------
# the workflow wiring
# ---------------------------------------------------------------------------


class TestWorkflowWiring:
    """The steps must read the resolved value, not re-derive it inline."""

    @staticmethod
    def _without_comments(text: str) -> str:
        """Drop comment lines before scanning.

        The replaced expression appears verbatim in the explanatory comment above
        each fix, and in `llm_model.py`'s docstring. Prose *describing* a defect
        is not the defect.
        """
        return "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )

    def test_no_workflow_pins_the_zen_model_as_a_default(self):
        offenders = [
            path.name
            for path in sorted(WORKFLOWS.glob("*.yml"))
            if "'opencode/big-pickle'"
            in self._without_comments(path.read_text(encoding="utf-8"))
        ]
        assert offenders == [], (
            "a Zen model id as the unconditional default is the defect: a repo "
            "holding a Gemini or OpenRouter key has no credential for it"
        )

    @pytest.mark.parametrize("workflow", [RUNTIME_DAILY, SELF_IMPROVE])
    def test_one_step_resolves_the_model_and_writes_github_output(self, workflow):
        # Comments are stripped first: both workflows *describe* the script above
        # the step that runs it, and prose naming a file is not an invocation.
        text = self._without_comments(workflow.read_text(encoding="utf-8"))
        assert "scripts/llm_model.py >> \"$GITHUB_OUTPUT\"" in text
        assert text.count("scripts/llm_model.py") == 1, (
            "one decision, one place — a second invocation could disagree"
        )

    def test_the_agent_step_reads_the_resolved_model(self):
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        assert "LLM_MODEL: ${{ steps.llm.outputs.model }}" in text
        assert "vars.LLM_MODEL ||" not in self._without_comments(text)

    def test_the_self_improve_agent_step_reads_the_resolved_model(self):
        text = SELF_IMPROVE.read_text(encoding="utf-8")
        assert "LLM_MODEL: ${{ steps.llm.outputs.model }}" in text
        assert "vars.LLM_MODEL ||" not in self._without_comments(text)

    def test_the_pin_is_still_passed_through(self):
        # The documented override has to reach the resolver, or precedence 1 is
        # unreachable in CI — a documented knob with no producer.
        for workflow in (RUNTIME_DAILY, SELF_IMPROVE):
            assert "LLM_MODEL: ${{ vars.LLM_MODEL }}" in workflow.read_text(
                encoding="utf-8"
            )

    def test_the_resolver_precedes_its_consumer_in_runtime_daily(self):
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        resolver = text.index("name: Resolve the model")
        consumer = text.index("opencode run --attach")
        assert resolver < consumer, "the agent's LLM_MODEL env would be empty"

    def test_the_resolver_sees_exactly_what_the_gate_sees(self):
        """A narrower credential set would make the two disagree.

        `--check-rungs` passes on any of these; a resolver that saw fewer would
        answer "no credential" for a repository the gate had just accepted.
        """
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        step = text[text.index("name: Resolve the model") :]
        step = step[: step.index("name: Create the permission-restricted")]
        for name in ALL_CREDENTIALS:
            assert f"{name}: ${{{{ secrets.{name} }}}}" in step, name

    def test_the_runtime_step_still_gets_the_credentials_it_needs(self):
        # The resolver only selects; the run step is the one that has to hold the
        # key for the selected rung, and `GEMINI_API_KEY` was already there.
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        step = text[text.index("name: Run the skill") :]
        step = step[: step.index("─ The write path")]
        for name in ct.RUNG_CREDENTIALS["gemini"]:
            assert name in step, name
        assert "OPENROUTER_API_KEY" in step

    def test_the_step_is_not_gated(self):
        # It must exist for the agent step above it, the same reasoning that
        # keeps the write-mode resolver un-gated.
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        step = text[text.index("name: Resolve the model") :]
        step = step[: step.index("run: |")]
        assert "if:" not in step
