#!/usr/bin/env python3
"""Tests for the runtime agent's permission restriction.

The restriction is a security control, not a nicety: the calendar-writing agent
must not be able to edit `SKILL.md`, `references/` or the gate scripts mid-run,
and must not be able to run shell commands.

It is applied by `opencode.json` at the repository root. opencode merges a
project config's `permission` block into **every** agent, including the built-in
`build` agent that `opencode run` selects when no `--agent` is given -- so the
restriction holds without a flag. That matters here: the workflow file that would
carry a `--agent` flag cannot be changed by this repository's automation
credential (a GitHub App without the `workflows` permission), so a fix that
depends on editing `.github/workflows/runtime-daily.yml` cannot land.

Verified on the pinned 1.18.33 binary:

    opencode debug agent build

prints `edit`/`task`/`todowrite`/`lsp` denied and `bash` denied per-command.
With no `opencode.json`, the same command prints `*: allow` for every tool.

Parsed with the stdlib `json` module rather than PyYAML: the production scripts
stay stdlib-only, and `tests/test_agent_run_transcript.py` sets the precedent.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "opencode.json"

# Denied outright. The allow-list (`read`, `glob`, `grep`, `webfetch`,
# `websearch`, `skill`) is the CLI's permissive default, so only the denials need
# writing down. `read` must NOT be here: the CLI's free provider rejects a
# request whose tool set omits `read` (HTTP 403, `FreeTierError`).
DENIED_PERMISSIONS = ("edit", "task", "todowrite", "lsp")

# The one `bash` command the agent may run. See `test_bash_is_denied_per_command`.
INERT_BASH_ALLOW = "true"


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


class TestTheProjectConfig:
    def test_the_config_is_valid_json(self):
        _config()

    def test_it_denies_edit_and_bash(self):
        """The security control `PRODUCT.md` documents."""
        permission = _config()["permission"]
        assert permission["edit"] == "deny"
        # `bash` is denied per-command rather than disabled -- see below.
        assert permission["bash"]["*"] == "deny"

    def test_bash_is_denied_per_command_not_disabled(self):
        """The obvious, stricter form does not work -- it 403s the whole call.

        `bash: deny` drops the tool from the request, and the CLI's free provider
        (OpenCode Zen, the only rung the agent can use) then rejects the run with
        `FreeTierError: OpenCode's free tier can only be used from within
        OpenCode`. Measured 2026-10-04 on the pinned 1.18.33 binary: `bash: deny`
        and `read: deny` both 403, while `edit`/`todowrite`/`task`/`lsp`/`glob`/
        `webfetch` denials are all accepted.

        So the tool must stay present with a catch-all deny and one inert allow.
        "Simplify this to `bash: deny`" is exactly the edit a future reader would
        make, and it breaks the run one step later -- hence this test.
        """
        bash = _config()["permission"]["bash"]
        assert isinstance(bash, dict), "bash needs the granular (object) form"
        assert bash["*"] == "deny", "every command must be denied"
        assert bash[INERT_BASH_ALLOW] == "allow", (
            "at least one allow keeps the bash tool in the request; without it "
            "the provider refuses the whole call"
        )
        # The shell's no-op, so the single permitted command does nothing.
        assert INERT_BASH_ALLOW == "true"

    def test_read_is_never_denied(self):
        """`read: deny` 403s the call for the same reason `bash: deny` does."""
        permission = _config()["permission"]
        assert "read" not in permission
        assert "read" not in DENIED_PERMISSIONS

    def test_it_denies_the_rest_of_the_cli_vocabulary(self):
        """`task` matters on its own: a subagent is not bound by this agent's
        permissions, so denying `edit`/`bash` while allowing `task` would leave
        the door open."""
        permission = _config()["permission"]
        for name in DENIED_PERMISSIONS:
            assert permission[name] == "deny", name

    def test_the_denials_are_scalar_for_the_keys_that_require_it(self):
        """`todowrite` and `lsp` reject the granular (object) form.

        Measured on 1.18.33: `todowrite: {"*": deny}` is a hard config error --
        `Expected PermissionActionConfig | undefined, got {"*":"deny"}
        permission.todowrite` -- so only `bash` may use the object form.
        """
        permission = _config()["permission"]
        for name in ("todowrite", "lsp", "task", "edit"):
            assert permission[name] == "deny", name
            assert not isinstance(permission[name], dict), name

    def test_it_does_not_touch_external_directory(self):
        """A blanket deny would block opencode reading its own tool output.

        `external_directory` defaults to the stricter `ask`, and opencode
        resolves its large tool results through a path outside the working
        directory. Denying it would break reads the run depends on.
        """
        permission = _config()["permission"]
        assert "external_directory" not in permission
        assert "doom_loop" not in permission

    def test_the_model_is_a_free_zen_model(self):
        """`opencode agent create` reads this model, and it must not need billing.

        The runtime job's `Create the permission-restricted runtime agent` step
        is an LLM call whose default model is a paid image/video one; on
        2026-10-04 it failed the whole job with "This request requires at least
        $1.00 in balance for image or video output". Pinning a free Zen model
        here is what lets that step succeed on a repository with no paid balance.
        """
        assert _config()["model"] == "opencode/big-pickle"

    def test_the_cli_resolves_the_restriction(self):
        """The config is only real if the CLI merges it into the default agent.

        `opencode run` without `--agent` selects the built-in `build` agent. This
        asserts against the pinned binary itself, so a config key the CLI
        silently ignores -- the failure mode this repo records for a malformed
        `permission` frontmatter key -- reds the suite instead of passing.
        """
        if shutil.which("opencode") is None:
            pytest.skip("opencode CLI is not installed in this environment")
        result = subprocess.run(
            ["opencode", "debug", "agent", "build"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        agent = json.loads(result.stdout)
        # The tool must stay present: a request without it 403s the free tier.
        assert agent["tools"]["bash"] is True
        for name in ("edit", "write", "task", "todowrite"):
            assert agent["tools"][name] is False, name
