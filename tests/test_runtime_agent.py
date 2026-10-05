#!/usr/bin/env python3
"""Tests for the permission-restricted runtime agent and its selection.

Two halves of one control, and each is useless alone:

* `scripts/runtime_agent.py` writes `.opencode/agent/runtime.md`. It replaced
  `opencode agent create`, which is an **LLM call** and failed the whole
  `runtime` job on 2026-10-04 with a provider's balance error. The agent it
  produces is static, so a live model was never the right dependency for a
  security control.
* `Run the skill` must pass `--agent runtime`. Without it the CLI runs the
  built-in `build` agent, which has every tool — the 2026-10-02 run invoked
  `bash` and spent 21 minutes until `timeout` killed it, so `PRODUCT.md`'s
  "denied `edit` and `bash`" was a promise nothing kept.

Parsed with regex rather than PyYAML: the production scripts stay stdlib-only,
and `tests/test_agent_run_transcript.py` sets the same precedent.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import runtime_agent  # noqa: E402


class TestTheAgentDefinition:
    def test_the_file_name_is_the_agent_name(self, tmp_path):
        """`runtime.md` creates the agent `runtime`; `--agent` must agree."""
        written = runtime_agent.write_agent(tmp_path)
        assert written.name == f"{runtime_agent.AGENT_NAME}.md"
        assert runtime_agent.AGENT_NAME == "runtime"

    def test_it_denies_edit_and_bash(self, tmp_path):
        """The security control `PRODUCT.md` documents."""
        body = runtime_agent.write_agent(tmp_path).read_text(encoding="utf-8")
        assert "  edit: deny" in body
        # `bash` is denied per-command rather than disabled — see below.
        assert '    "*": deny' in body

    def test_bash_is_denied_per_command_not_disabled(self, tmp_path):
        """The obvious, stricter form does not work — it 403s the whole call.

        `bash: deny` drops the tool from the request, and the CLI's free
        provider (OpenCode Zen, the only rung `llm_model.py` can select) then
        rejects the run with `FreeTierError: OpenCode's free tier can only be
        used from within OpenCode`. Measured 2026-10-04 on the pinned 1.18.33
        binary: `bash: deny` and `read: deny` both 403, while `edit: deny`,
        `todowrite: deny`, `task: deny`, `lsp: deny`, `glob: deny` and
        `webfetch: deny` are all accepted.

        So the tool must stay present with a catch-all deny and one inert
        allow. "Simplify this to `bash: deny`" is exactly the edit a future
        reader would make, and it breaks the run one step later — hence this
        test.
        """
        body = runtime_agent.write_agent(tmp_path).read_text(encoding="utf-8")
        assert "  bash:\n" in body, "bash needs the granular (object) form"
        assert '    "*": deny\n' in body, "every command must be denied"
        assert f'    "{runtime_agent.INERT_BASH_ALLOW}": allow\n' in body, (
            "at least one allow keeps the bash tool in the request; without it "
            "the provider refuses the whole call"
        )
        # The shell's no-op, so the single permitted command does nothing.
        assert runtime_agent.INERT_BASH_ALLOW == "true"
        # Not the scalar form, and not a second opinion about it.
        assert "  bash: deny" not in body

    def test_read_is_never_denied(self, tmp_path):
        """`read: deny` 403s the call for the same reason `bash: deny` does."""
        body = runtime_agent.write_agent(tmp_path).read_text(encoding="utf-8")
        assert "  read:" not in body and "  read: deny" not in body
        assert "read" not in runtime_agent.DENIED_PERMISSIONS

    def test_it_denies_the_rest_of_the_cli_vocabulary(self, tmp_path):
        """`agent create --permissions` allows a list; everything else is denied.

        `task` matters on its own: a subagent is not bound by this agent's
        permissions, so denying `edit`/`bash` here while allowing `task` would
        leave the door open.
        """
        body = runtime_agent.write_agent(tmp_path).read_text(encoding="utf-8")
        for permission in ("edit", "task", "todowrite", "lsp"):
            assert f"  {permission}: deny" in body, permission

    def test_the_denials_are_scalar_for_the_keys_that_require_it(self, tmp_path):
        """`todowrite` and `lsp` reject the granular (object) form.

        Measured on 1.18.33: `todowrite: {"*": deny}` is a hard config error —
        `Expected PermissionActionConfig | undefined, got {"*":"deny"}
        permission.todowrite` — so only `bash` may use the object form.
        """
        body = runtime_agent.write_agent(tmp_path).read_text(encoding="utf-8")
        for permission in ("todowrite", "lsp", "task", "edit"):
            assert f"  {permission}: deny" in body
            assert f"  {permission}:\n" not in body

    def test_it_does_not_touch_external_directory(self, tmp_path):
        """A blanket deny would block opencode reading its own tool output.

        `external_directory` defaults to the stricter `ask`, and opencode
        resolves its large tool results through a path outside the working
        directory. Denying it would break reads the run depends on.
        """
        body = runtime_agent.write_agent(tmp_path).read_text(encoding="utf-8")
        assert "external_directory" not in body
        assert "doom_loop" not in body

    def test_it_is_primary(self, tmp_path):
        body = runtime_agent.write_agent(tmp_path).read_text(encoding="utf-8")
        assert "mode: primary" in body

    def test_it_is_idempotent(self, tmp_path):
        first = runtime_agent.write_agent(tmp_path).read_text(encoding="utf-8")
        second = runtime_agent.write_agent(tmp_path).read_text(encoding="utf-8")
        assert first == second

    def test_the_cli_writes_github_output_payload_only_on_stdout(self, tmp_path):
        """stdout is redirected into `$GITHUB_OUTPUT`; a stray line fails it."""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "runtime_agent.py"),
             "--path", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"agent={runtime_agent.AGENT_NAME}"
        assert "OK: runtime_agent:" in result.stderr


class TestTheWorkflowWiresItUp:
    def _runtime_job(self) -> str:
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        return text[text.index("\n  runtime:") : text.index("\n  audit:")]

    def _steps(self) -> list[str]:
        """The runtime job's steps, comment-free, split on `- name:`.

        Comments are dropped because this file *quotes* the defect it fixes —
        `opencode agent create` appears in prose explaining why it is gone, and
        prose describing a defect is not the defect (`test_agent_run_transcript`
        learned the same lesson).
        """
        job = self._runtime_job()
        starts = [m.start() for m in re.finditer(r"^      - (?:name|uses|id):", job, re.M)]
        steps = [job[s:e] for s, e in zip(starts, starts[1:] + [len(job)])]
        return [
            "\n".join(
                line for line in step.splitlines() if not line.lstrip().startswith("#")
            )
            for step in steps
        ]

    def _step(self, name: str) -> str:
        matches = [s for s in self._steps() if f"- name: {name}\n" in s]
        assert len(matches) == 1, f"expected one `{name}` step, got {len(matches)}"
        return matches[0]

    def test_it_writes_the_agent_deterministically(self):
        step = self._step("Write the permission-restricted runtime agent")
        assert "python3 scripts/runtime_agent.py" in step
        # The LLM generator must not come back: it is what failed the job.
        assert "opencode agent create" not in step, (
            "`agent create` is an LLM call; the agent is static, so it is "
            "written by `runtime_agent.py`"
        )

    def test_no_step_reaches_for_the_llm_generator(self):
        for step in self._steps():
            assert "opencode agent create" not in step

    def test_it_selects_the_agent_it_wrote(self):
        """Without `--agent`, the CLI runs the built-in `build` agent."""
        step = self._step("Run the skill")
        assert '--agent "$AGENT"' in step, (
            "`opencode run` without `--agent` selects `build`, which has every "
            "tool, so the restriction would not be in effect"
        )

    def test_the_agent_name_comes_from_the_writer(self):
        """Read, never retyped: a literal could drift from the file name."""
        writer = self._step("Write the permission-restricted runtime agent")
        assert "id: agent" in writer
        run = self._step("Run the skill")
        assert "AGENT: ${{ steps.agent.outputs.agent }}" in run

    def test_the_writer_runs_before_the_run(self):
        names = [s.split("- name: ", 1)[-1].splitlines()[0] for s in self._steps()]
        writer = names.index("Write the permission-restricted runtime agent")
        run = names.index("Run the skill")
        assert writer < run, "the agent must exist before `opencode run` selects it"

    def test_the_run_step_keeps_a_known_agent_flag(self):
        """`--agent` is on the published 1.18.33 `run --help`."""
        assert re.search(r"opencode run\b[\s\S]*?--agent ", self._step("Run the skill"))

    def test_the_restriction_is_asserted_against_the_transcript(self):
        """`--agent <wrong-name>` silently falls back, so the flag is not proof.

        The run is checked against its own evidence: a `bash`/`edit` tool event
        means the restriction is not in effect. This is the difference between
        writing a config and verifying the run obeyed it.
        """
        step = self._step("Assert the agent used no forbidden tool")
        assert "if: always()" in step
        assert "for tool in bash edit" in step
        assert 'grep -q "\\"tool\\":\\"$tool\\"" .tmp/transcript.json' in step
        assert "::error::" in step
        assert "exit 1" in step
        # Tolerant only of the *absent* transcript — never a bare `|| true`.
        assert "[ ! -f .tmp/transcript.json ]" in step
        assert "::notice::" in step
        assert "|| true" not in step

    def test_the_assertion_runs_after_the_run(self):
        names = [s.split("- name: ", 1)[-1].splitlines()[0] for s in self._steps()]
        run = names.index("Run the skill")
        assertion = names.index("Assert the agent used no forbidden tool")
        assert run < assertion, "the transcript has to exist before it is asserted on"

    def test_the_agent_is_proved_servable_before_the_run(self):
        """The same provider message has two causes, and this tells them apart.

        `FreeTierError: OpenCode's free tier can only be used from within
        OpenCode` is the repo's recorded signature of a step with no `env:`
        block. It is *also* what the free provider returns when the request's
        tool set omits `bash` or `read` — so a permission edit that looks
        stricter 403s the whole call. Naming that here means a bad permission
        fails with the agent's name on it, not eight minutes into the run.
        """
        step = self._step("Confirm the opencode CLI can serve the restricted agent")
        assert "opencode run" in step
        assert '--agent "$AGENT"' in step
        assert "steps.llm.outputs.model" in step, "prove the pair the run will use"
        assert "set -o pipefail" in step
        assert "secrets.OPENROUTER_API_KEY" in step, "a CLI step needs credentials"

    def test_the_probe_sits_between_the_writer_and_the_run(self):
        names = [s.split("- name: ", 1)[-1].splitlines()[0] for s in self._steps()]
        writer = names.index("Write the permission-restricted runtime agent")
        probe = names.index("Confirm the opencode CLI can serve the restricted agent")
        run = names.index("Run the skill")
        assert writer < probe < run
