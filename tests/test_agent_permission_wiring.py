"""Workflow pins for the runtime agent's permission restriction.

**The live finding these tests prevent from coming back.** Verified against the
2026-10-01 production run (job 110449836420, main HEAD) and the pinned CLI
binary itself (2026-10-02):

* the workflow created a "permission-restricted runtime agent" and then ran
  `opencode run` WITHOUT `--agent` — so the run was served by the default
  agent, whose permission set is `*: allow`. The restricted agent was dead
  config: a security control written but never consumed;
* the production transcript shows the consequence — the agent ran
  `env | grep -i composio`, listed for secret files, and called
  `scripts/calendar_io.py list` directly, the exact behaviour the workflow's
  comment claimed was impossible;
* `opencode run --agent <wrong-name>` does NOT error — it silently falls back
  to the default agent, so passing the flag is not enough: the name must be
  verified discoverable BEFORE the run, and the claim must be asserted
  against the transcript AFTER it (a malformed permission frontmatter key is
  ignored without complaint — verified by hand on 1.18.33).

Pinned: the create step exports the generated name, verifies it is
discoverable, the run selects it via `--agent`, and the forbidden-tool
assertion reds on `bash`/`edit` in the transcript.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"

STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)
JOB_START = re.compile(r"^  ([a-z][a-z0-9-]*):$", re.M)

CREATE_STEP = "Create the permission-restricted runtime agent"
RUN_STEP = "Run the skill"
ASSERT_STEP = "Assert the agent used no forbidden tool"


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


class TestTheNameIsCapturedAndVerified:
    def test_the_create_step_exports_the_generated_name(self):
        step = _named(CREATE_STEP)
        assert "id: agent" in step
        assert "name=$agent_name" in _code(step)

    def test_the_name_comes_from_the_generated_file_not_a_hardcode(self):
        """The name is LLM-chosen; a hardcoded one would work until the day it
        did not, and `--agent <wrong-name>` silently falls back."""
        code = _code(_named(CREATE_STEP))
        assert 'basename "$agent_file" .md' in code
        assert ".opencode/agent/agents/*.md" in code

    def test_no_generated_file_is_a_red_not_a_fallback(self):
        code = _code(_named(CREATE_STEP))
        assert '[ -z "$agent_file" ]' in code
        assert "::error::" in code
        assert "exit 1" in code

    def test_discoverability_is_verified_before_the_run(self):
        """`--agent <wrong-name>` does not error — it falls back. Discovering
        the agent in the create step is what makes the fallback impossible."""
        code = _code(_named(CREATE_STEP))
        assert "opencode agent list" in code
        assert 'grep -q "$agent_name"' in code
        assert "silently fall back" in code


class TestTheRunSelectsTheAgent:
    def test_the_run_passes_agent(self):
        run = _named(RUN_STEP)
        assert '--agent "$AGENT_NAME"' in _code(run)

    def test_the_name_is_read_from_the_create_step(self):
        run = _named(RUN_STEP)
        assert "steps.agent.outputs.name" in run

    def test_a_bare_run_cannot_pass_this_pin(self):
        """`opencode run` without `--agent` is the exact defect; the flag must
        be on the command, not merely present somewhere in the step."""
        code = _code(_named(RUN_STEP))
        assert re.search(r"opencode run[^\n]*\\\n(\s*--agent[^\n]*\\\n)*", code)
        assert "--agent" in code


class TestTheClaimIsAssertedAgainstEvidence:
    def _step(self) -> str:
        return _named(ASSERT_STEP)

    def test_it_reds_on_bash_or_edit_in_the_transcript(self):
        code = _code(self._step())
        assert "for tool in bash edit" in code
        assert 'grep -q "\\"tool\\":\\"$tool\\"" .tmp/transcript.json' in code
        assert "::error::" in code
        assert "exit 1" in code

    def test_it_runs_always_but_tolerates_only_the_absent_transcript(self):
        step = self._step()
        assert "if: always()" in step
        code = _code(step)
        assert "[ ! -f .tmp/transcript.json ]" in code
        assert "::notice::" in code
        assert "|| true" not in code

    def test_it_precedes_the_filing_so_the_evidence_is_kept(self):
        """A red assertion must not stop the transcript from being graded and
        filed — the transcript IS the evidence of the violation."""
        steps = _steps()
        assert_step = next(
            i for i, s in enumerate(steps) if f"- name: {ASSERT_STEP}" in s
        )
        stamp = next(
            i for i, s in enumerate(steps) if "Stamp the transcript with the run id" in s
        )
        assert assert_step < stamp