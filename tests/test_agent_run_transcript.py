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

# The CLI the workflow pins, from `Install opencode CLI` in `runtime-daily.yml`.
PINNED_CLI = "1.18.33"

# Flags `opencode run` actually accepts, recorded from `opencode run --help` on
# the published 1.18.33 binary on 2026-09-29. Kept as a list rather than
# inferred because the whole class of failure here is using a flag that does not
# exist: `--standalone` (from an unpublished 2.0.16) and `--attach` (removed on
# the strength of that same binary, then restored) were both committed before
# anyone ran `run --help` against the version CI installs.
KNOWN_RUN_FLAGS = frozenset(
    {
        # Recorded verbatim from `opencode run --help` on the published 1.18.33
        # binary on 2026-09-29 — the version the workflow pins, not the one
        # that happens to be on a developer's machine.
        "--agent",
        "--attach",
        "--auto",
        "--command",
        "--continue",
        "--dir",
        "--file",
        "--fork",
        "--format",
        "--help",
        "--interactive",
        "--log-level",
        "--model",
        "--password",
        "--port",
        "--print-logs",
        "--pure",
        "--session",
        "--share",
        "--thinking",
        "--title",
        "--username",
        "--variant",
        "--version",
    }
)


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


def _agent_step() -> str:
    """The `Run the skill` step, matched by name.

    Matching the command text found two steps once the CLI probe was added —
    the probe also invokes `opencode run` — and a test that resolves to the
    wrong step passes for the wrong reason.
    """
    matches = [s for s in _runtime_steps() if "- name: Run the skill\n" in s]
    assert len(matches) == 1, f"expected exactly one `Run the skill` step, got {len(matches)}"
    return matches[0]


class TestTheAgentStepsReportTheirExitCode:
    """A `| tee` without `pipefail` reports tee's status, not the CLI's."""

    @pytest.mark.parametrize("workflow", [RUNTIME_DAILY, SELF_IMPROVE])
    def test_a_piped_agent_run_arms_pipefail(self, workflow):
        agent_steps = [
            s
            for s in _all_steps(workflow)
            if "opencode run" in s and "Confirm the opencode CLI" not in s
        ]
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
        code = _script(_agent_step())
        assert "tee .tmp/transcript.json" in code

    def test_the_self_improve_step_still_writes_the_transcript(self):
        code = _script(_find(_all_steps(SELF_IMPROVE), "opencode run --model"))
        assert "tee .tmp/agent-edit.json" in code

    def test_the_cli_is_installed_pinned_to_a_version_that_exists(self):
        """`@latest` is a moving target; an *unpublished* pin is worse.

        An earlier revision pinned `2.0.16` from `https://opencode.ai/install`,
        on the strength of a binary found on a developer's machine. There is no
        `v2.0.16` release — `opencode-ai@latest` is 1.18.33 and the upstream
        release feed has no such tag — so the installer fetched nothing, the
        extract failed, and the version check then passed against whatever
        stale binary was already on PATH.

        That is a guard that cannot fail, in the step written to catch exactly
        that. Pinned to a real version, from the channel that actually serves it.
        """
        for workflow in (RUNTIME_DAILY, SELF_IMPROVE):
            scripts = "\n".join(_script(s) for s in _all_steps(workflow))
            assert "opencode.ai/install" not in scripts, workflow.name
            assert "opencode-ai@" in scripts, workflow.name
            assert "OPENCODE_VERSION" in scripts, workflow.name

    def test_the_pinned_version_is_one_that_is_published(self):
        """The pin and the registry must agree, checked in the tree.

        Offline, so it cannot query npm: it asserts the pin is the version
        `opencode-ai@latest` resolved to on 2026-09-28, and the install step
        checks the installed binary against the same pin. A pin that drifts from
        the registry is caught by the install step, which runs against the real
        one.
        """
        scripts = "\n".join(
            _script(step) for step in _all_steps(RUNTIME_DAILY) if "opencode" in step
        )
        pinned = set(re.findall(r"OPENCODE_VERSION[=:]\s*([\d.]+)", scripts))
        assert pinned, "the CLI version must be pinned, not `@latest`"
        for version in pinned:
            assert version == "1.18.33", (
                f"pinned {version}, but opencode-ai@latest resolved to 1.18.33 "
                "(2026-09-28) and no other release is published"
            )

    def test_no_workflow_attaches_a_directory_to_the_agent(self):
        """v2.0.16 refuses a directory, and says so instead of running."""
        for workflow in (RUNTIME_DAILY, SELF_IMPROVE):
            for step in _all_steps(workflow):
                code = _script(step)
                for path in re.findall(r"--file\s+(\S+)", code):
                    name = path.split("/")[-1]
                    assert (REPO_ROOT / path).is_file() or path.startswith("."), (
                        f"{workflow.name}: --file {path} is not a file; the CLI "
                        "cannot attach a directory"
                    )
                    del name


    def test_the_prompt_precedes_the_file_array(self):
        """`--file` is an **array** in opencode 1.18.33, so order is load-bearing.

        The flags are declared `-f, --file ... [array]`, which means every token
        after one is consumed as a filename. With the prompt last, the CLI read
        the message as a path and answered `Error: File not found: Reply with
        exactly: PONG`. Measured on the published binary: prompt first answers
        `PONG`, prompt last does not.

        This is the opposite failure to the one it replaced, and both are silent
        in a way a reader would not expect: the removed flag failed loudly, and
        this one fails with a message about a *file*.
        """
        for workflow in (RUNTIME_DAILY, SELF_IMPROVE):
            for step in _all_steps(workflow):
                code = _script(step)
                if "--file" not in code or "opencode run" not in code:
                    continue
                first_file = code.index("--file")
                prompt = re.search(
                    r'"(Execute Steps|The appended audit verdicts)', code
                )
                assert prompt, workflow.name
                assert prompt.start() < first_file, (
                    f"{workflow.name}: the prompt must precede --file, or the "
                    "array swallows it and the CLI reports the message as a "
                    "missing file"
                )

    def test_the_agent_step_owns_its_own_server(self):
        """No backgrounded service in a step that has to return.

        The long-lived `opencode serve` hung on four dispatches — 45, 34, 18 and
        7 minutes, each time with the two steps above it green, which is the
        signature of a step that never returned rather than one still working.
        Two shell-level patches were tried and neither held: measured eight
        trials each on identical code, the same body blocked 2/8 in one run and
        0/8 in the next. So the environment was the variable and the *shape* was
        wrong.

        `--port` matches the shape: the CLI starts and stops its own server
        inside the step, so the step cannot outlive its job.
        """
        code = _script(_agent_step())
        assert "opencode run --port" in code, (
            "the agent must start its own server; nothing else in the job uses it"
        )
        assert "--attach" not in code, "there is no long-lived server to attach to"
        assert "opencode serve" not in code, (
            "a backgrounded service in a step that must return is what hung"
        )

    def test_the_agent_step_uses_only_flags_this_cli_has(self):
        """An unknown flag makes the CLI print its usage and exit.

        `--standalone` was committed here on the strength of a 2.0.16 binary
        that is not published. The first dispatch with it produced an **empty
        transcript** and a red step in 30 seconds. 1.18.33's `run --help` lists
        `--attach` and `--port`; it has no `--standalone`.

        The flag list is pinned rather than the intent, because intent is what
        went wrong twice: this is the third flag-shape change in this file, and
        each was checked against a binary CI does not have.
        """
        code = _script(_agent_step())
        for flag in re.findall(r"opencode run ((?:--[\w-]+ )*--[\w-]+)", code):
            for single in flag.split():
                assert single in KNOWN_RUN_FLAGS, (
                    f"{single} is not a flag opencode {PINNED_CLI} accepts; check "
                    "`opencode run --help` against the pinned CLI before using it"
                )

    def test_the_agent_step_is_bounded(self):
        """An unbounded step produces a cancellation that names nothing.

        45 minutes of job timeout says only "Phase 1/2" — not which step, and
        not that it was waiting. A timeout is a red with a name on it. The run
        needs ~8 minutes, so the bound is generous and still an answer.
        """
        code = _script(_agent_step())
        assert "timeout --signal=TERM" in code, "the skill run must be bounded"
        assert "opencode run" in code


class TestTheTranscriptDirectoryExists:
    def test_the_runtime_step_creates_dot_tmp_before_writing(self):
        code = _script(_agent_step())
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
        agent = steps.index(_agent_step())
        earlier = [
            step
            for step in steps[:agent]
            if ".tmp/" in _script(step) or "mkdir -p .tmp" in _script(step)
        ]
        assert earlier == [], (
            "an earlier step now creates or writes `.tmp`; re-check whether the "
            "`mkdir -p .tmp` in `Run the skill` is still the one that matters"
        )
