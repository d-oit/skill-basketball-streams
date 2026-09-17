"""Tests for the workflow steps that run the agent: their transcript and exit code.

Two defects, both of the same shape — a step that reports success while producing
nothing, which is the one failure mode that cannot be seen from the outside.

**The pipe.** Both `opencode run` steps persisted the transcript with
`... | tee FILE`. A pipeline's exit status is the *last* command's, so the CLI could
fail and the step would still be green — and every step below it then worked from a
transcript that was never written. The repo had already learned this once:
`runtime-daily.yml`'s Phase 0 ladder carries the comment "Do not pipe this through
`tee`: the pipe would mask the exit status", and `tests/test_run_daily.py` pins it.
The ladder avoids the pipe; here `tee` IS the writer, so the pipe is armed with
`pipefail` instead.

**The directory.** `Run the skill` was the earliest step in its job to write into
`.tmp`, and nothing before it created the directory — so `tee .tmp/transcript.json`
could not open its own output file, and the artifact upload's
`if-no-files-found: ignore` hid both the failure and the missing transcript.

Parsed with regex rather than PyYAML: the production scripts stay stdlib-only.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RUNTIME_DAILY = WORKFLOWS / "runtime-daily.yml"
SELF_IMPROVE = WORKFLOWS / "self-improve.yml"

STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)
# A single `|` that is not part of `||`, and not the `|` opening a block scalar.
PIPE = re.compile(r"(?<![\|])\|(?!\s*$)")


def _all_steps(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    starts = [m.start() for m in STEP_START.finditer(text)]
    return [text[s:e] for s, e in zip(starts, starts[1:] + [len(text)])]


def _runtime_steps() -> list[str]:
    text = RUNTIME_DAILY.read_text(encoding="utf-8")
    job = text[text.index("\n  runtime:") : text.index("\n  audit:")]
    starts = [m.start() for m in STEP_START.finditer(job)]
    return [job[s:e] for s, e in zip(starts, starts[1:] + [len(job)])]


def _without_comments(text: str) -> str:
    """Drop comment lines before scanning.

    The defect is quoted verbatim in the comments that explain the fix, and prose
    *describing* a defect is not the defect.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _find(steps: list[str], needle: str) -> str:
    matches = [s for s in steps if needle in s]
    assert len(matches) == 1, f"expected exactly one step containing {needle!r}"
    return matches[0]


def _script(step: str) -> str:
    return _without_comments(step.partition("run: |")[2])


class TestTheAgentStepsReportTheirExitCode:
    """A `| tee` without `pipefail` reports tee's status, not the CLI's."""

    @pytest.mark.parametrize("workflow", [RUNTIME_DAILY, SELF_IMPROVE])
    def test_a_piped_agent_run_arms_pipefail(self, workflow):
        agent_steps = [s for s in _all_steps(workflow) if "opencode run" in s]
        assert agent_steps, f"{workflow.name} has no `opencode run` step"
        for step in agent_steps:
            code = _script(step)
            assert PIPE.search(code), (
                "this test covers the piped case; if the pipe is gone, delete the "
                "step from this test rather than the assertion"
            )
            assert "set -o pipefail" in code, (
                f"a pipe reports tee's status, so a failed run reads as a pass:\n{code}"
            )

    def test_the_runtime_step_still_writes_the_transcript(self):
        # `pipefail` must not be bought by dropping the file the later steps and
        # the artifact consume.
        code = _script(_find(_runtime_steps(), "opencode run --attach"))
        assert "tee .tmp/transcript.json" in code

    def test_the_self_improve_step_still_writes_the_transcript(self):
        code = _script(_find(_all_steps(SELF_IMPROVE), "opencode run --model"))
        assert "tee .tmp/agent-edit.json" in code


class TestTheTranscriptDirectoryExists:
    def test_the_runtime_step_creates_dot_tmp_before_writing(self):
        code = _script(_find(_runtime_steps(), "opencode run --attach"))
        assert "mkdir -p .tmp" in code, (
            "nothing earlier in this job creates `.tmp`, so `tee` could not open "
            "its own output file and `if-no-files-found: ignore` hid it"
        )
        assert code.index("mkdir -p .tmp") < code.index("opencode run"), (
            "the directory has to exist before the command that writes into it"
        )

    def test_no_earlier_step_touches_dot_tmp(self):
        """The `mkdir` is load-bearing only because it is the first one.

        If a future edit adds an earlier `.tmp` writer, this test says so — rather
        than letting a removed `mkdir` look harmless because some other step
        happens to create the directory.
        """
        steps = _runtime_steps()
        agent = steps.index(_find(steps, "opencode run --attach"))
        earlier = [
            step
            for step in steps[:agent]
            if ".tmp/" in _script(step) or "mkdir -p .tmp" in _script(step)
        ]
        assert earlier == [], (
            "an earlier step now creates or writes `.tmp`; re-check whether the "
            "`mkdir -p .tmp` in `Run the skill` is still the one that matters"
        )
