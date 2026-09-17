"""Tests for scripts/replay_ci.py — the fresh-checkout replay harness.

The tool exists because replaying a workflow against a clean tree is the only
way this repository has ever found its CI-only bugs (`do-harness` segfaulting on
a second invocation, `status` exiting 1 with no recorded beats). Both were
invisible locally.

So the properties worth pinning are the ones that make a replay *trustworthy*:
that it really is a clean copy, that it supplies the environment variables
Actions always provides, and above all that it can never execute a step which
writes to a real calendar or a remote.

Every test here is offline and fast: the tools that cost time are stubbed by
using synthetic workflows in `tmp_path` rather than the repository's own.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from replay_ci import (  # noqa: E402
    DENYLIST,
    clean_environment,
    copy_tree,
    denylist_reason,
    load_steps,
    should_skip,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REPLAY = REPO_ROOT / "scripts" / "replay_ci.py"


def _workflow(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "wf.yml"
    path.write_text(body, encoding="utf-8")
    return path


SIMPLE = """
name: t
on: [push]
jobs:
  one:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Python
        run: echo setup
      - name: Do the thing
        run: echo hello
  two:
    runs-on: ubuntu-latest
    steps:
      - name: Other job step
        run: echo two
"""


class TestLoadSteps:
    def test_collects_run_steps_and_ignores_uses(self, tmp_path):
        steps = load_steps(_workflow(tmp_path, SIMPLE))
        names = [step.name for step in steps]
        assert "Setup Python" in names
        assert "Other job step" in names
        # `uses:` steps are actions, not shell — there is nothing to replay.
        assert all("checkout" not in name.lower() for name in names)

    def test_records_the_job_each_step_belongs_to(self, tmp_path):
        steps = load_steps(_workflow(tmp_path, SIMPLE))
        by_name = {step.name: step.job for step in steps}
        assert by_name["Do the thing"] == "one"
        assert by_name["Other job step"] == "two"

    def test_job_filter_narrows_the_replay(self, tmp_path):
        steps = load_steps(_workflow(tmp_path, SIMPLE), job_filter="two")
        assert [step.name for step in steps] == ["Other job step"]

    def test_workflow_with_no_jobs_is_a_usage_error(self, tmp_path):
        with pytest.raises(SystemExit):
            load_steps(_workflow(tmp_path, "name: t\non: [push]\n"))


class TestDenylist:
    @pytest.mark.parametrize(
        "script",
        [
            "python3 scripts/calendar_io.py apply --plan .tmp/plan.json --live",
            "git push origin telemetry",
            "uses: google-github-actions/auth@v2",
            "opencode run --attach http://127.0.0.1:4096 --model x",
            "opencode serve --port 4096",
            "opencode agent create --path .opencode/agent",
            "npm install -g opencode-ai@latest",
            "gh issue create --title x --body y",
            "gh issue comment 12 --body y",
            "gh pr create --base main --head branch",
            # Multi-line, and the reason the pattern needs DOTALL: the flag is
            # nearly always on a continuation line, which a line-anchored regex
            # steps quietly over.
            "python3 scripts/corpus_flip_issue.py \\\n  --report .tmp/refresh.json \\\n  --file",
            # Same shape, second filer. `gh` never appears in this string, so
            # only denying the *capability* keeps it out of a replay — and this
            # step would otherwise open a real issue on the public repository.
            "python3 scripts/rung_health.py parked \\\n  --dest .tmp/telemetry --days 3 \\\n  --file",
        ],
    )
    def test_dangerous_steps_are_denied(self, script):
        assert denylist_reason(script) is not None, script

    @pytest.mark.parametrize(
        "script",
        [
            # Reading is how a step decides what to do; it cannot open anything.
            "gh issue list --state open --json number,title",
            "gh pr view 3",
            # Reporting a flip is the safe half of the issue filer, and it is
            # what renders the job summary — so it stays replayable.
            "python3 scripts/corpus_flip_issue.py --report .tmp/refresh.json --markdown",
            "python3 scripts/corpus_flip_issue.py --report .tmp/refresh.json",
            "python3 scripts/rung_health.py parked --dest .tmp/telemetry --days 3 --markdown",
            "python3 scripts/rung_health.py parked --dest .tmp/telemetry --days 3",
        ],
    )
    def test_read_only_github_use_is_allowed(self, script):
        assert denylist_reason(script) is None, script

    def test_the_dry_run_apply_in_validate_is_allowed(self):
        # The load-bearing distinction: planning is safe, `--live` is not. If the
        # denylist matched the script name it would block the very step that
        # proves the write path works without credentials.
        assert denylist_reason("python3 scripts/calendar_io.py apply --plan .tmp/plan.json") is None

    def test_a_step_merely_mentioning_live_in_a_message_is_allowed(self):
        assert denylist_reason('echo "DRY_RUN=true — no live writes"') is None

    def test_every_denylist_entry_carries_a_reason(self):
        assert all(reason for _, reason in DENYLIST)

    def test_a_bare_opencode_mention_is_denied_on_purpose(self):
        # Fail closed. `echo opencode status` is harmless, and denying it costs a
        # visibly SKIPPED step with a printed reason — whereas the opposite bias
        # costs a live model invocation nobody can undo. A false skip is loud and
        # cheap; a false execution is neither, which is the direction a denylist
        # guarding a public calendar and a git remote must err in.
        assert denylist_reason("echo opencode status") is not None

    def test_a_denied_bare_mention_is_reported_not_silent(self, tmp_path):
        workflow = _workflow(
            tmp_path,
            "name: t\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            '    steps:\n      - name: Mention\n        run: echo opencode status\n',
        )
        steps = load_steps(workflow)
        assert denylist_reason(steps[0].script) is not None


class TestShouldSkip:
    def test_runner_preparation_is_skipped_by_default(self):
        assert should_skip("Setup Python (3.12)") is not None
        assert should_skip("Install test dependencies") is not None

    def test_a_real_check_is_not_skipped(self):
        assert should_skip("Run validator (all checks)") is None


class TestCleanEnvironment:
    def test_credentials_are_stripped(self, monkeypatch):
        monkeypatch.setenv("EXA_API_KEY", "secret")
        monkeypatch.setenv("GITHUB_TOKEN", "secret")
        monkeypatch.setenv("GCP_SECRET", "secret")
        monkeypatch.setenv("PATH", "/usr/bin")
        env = clean_environment()
        assert "EXA_API_KEY" not in env
        assert "GITHUB_TOKEN" not in env
        assert "GCP_SECRET" not in env
        # The replay still needs a working PATH.
        assert env["PATH"] == "/usr/bin"

    def test_ci_is_flagged_and_extras_are_applied(self):
        env = clean_environment({"GITHUB_STEP_SUMMARY": "/tmp/s.md"})
        assert env["CI"] == "true"
        assert env["GITHUB_STEP_SUMMARY"] == "/tmp/s.md"

    def test_a_live_key_cannot_reach_a_step(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "live-key")
        assert "OPENROUTER_API_KEY" not in clean_environment()


class TestCopyTree:
    def test_machine_local_state_is_excluded(self, tmp_path):
        source = tmp_path / "src"
        (source / ".do-harness").mkdir(parents=True)
        (source / ".do-harness" / "agent_state.db").write_text("x")
        (source / ".git").mkdir()
        (source / ".git" / "HEAD").write_text("ref")
        (source / ".tmp").mkdir()
        (source / "__pycache__").mkdir()
        (source / "scripts").mkdir()
        (source / "scripts" / "keep.py").write_text("x = 1")
        (source / "stale.pyc").write_text("")

        dest = tmp_path / "dest"
        copy_tree(source, dest)

        assert not (dest / ".do-harness").exists(), (
            "inheriting the state database is exactly what hides the bug a replay is looking for"
        )
        assert not (dest / ".git").exists()
        assert not (dest / ".tmp").exists()
        assert not (dest / "__pycache__").exists()
        assert not (dest / "stale.pyc").exists()
        assert (dest / "scripts" / "keep.py").is_file()


class TestCliContracts:
    def _run(self, args, cwd):
        return subprocess.run(
            [sys.executable, str(REPLAY), *args], capture_output=True, text=True, cwd=cwd
        )

    def test_missing_workflow_is_a_usage_error(self, tmp_path):
        result = self._run(["--workflow", "nope.yml"], tmp_path)
        assert result.returncode == 2
        assert "workflow not found" in result.stderr

    def test_json_output_is_parseable(self, tmp_path):
        # The same stdout contract the rest of this repo's tools follow: the
        # payload must be json.load-able without stripping a summary line.
        (tmp_path / "scripts").mkdir()
        workflow = _workflow(
            tmp_path,
            "name: t\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - name: Say hi\n        run: echo hi\n",
        )
        result = self._run(
            ["--workflow", str(workflow), "--root", str(tmp_path), "--json",
             "--work-dir", str(tmp_path / "replay")],
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["failed"] == 0
        assert payload["steps"][0]["status"] == "ok"

    def test_a_failing_step_fails_the_replay(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        workflow = _workflow(
            tmp_path,
            "name: t\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - name: Break\n        run: exit 3\n",
        )
        result = self._run(
            ["--workflow", str(workflow), "--root", str(tmp_path), "--json",
             "--work-dir", str(tmp_path / "replay")],
            tmp_path,
        )
        assert result.returncode == 1
        assert json.loads(result.stdout)["failed"] == 1

    def test_a_denied_step_is_skipped_not_run(self, tmp_path):
        # The single most important property: a dangerous step must not execute
        # even when it is the only step in the workflow.
        (tmp_path / "scripts").mkdir()
        marker = tmp_path / "replay" / "SHOULD_NOT_EXIST"
        workflow = _workflow(
            tmp_path,
            "name: t\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - name: Write live\n        run: |\n"
            f"          touch {marker}\n"
            "          python3 scripts/calendar_io.py apply --plan p.json --live\n",
        )
        result = self._run(
            ["--workflow", str(workflow), "--root", str(tmp_path), "--json",
             "--work-dir", str(tmp_path / "replay")],
            tmp_path,
        )
        payload = json.loads(result.stdout)
        assert payload["skipped"] == 1
        assert payload["steps"][0]["status"] == "SKIPPED"
        assert not marker.exists(), "a denied step must not run at all"

    def test_github_step_summary_is_provided(self, tmp_path):
        # Without this the step dies with `: No such file or directory` and the
        # replay blames the workflow for a defect of the replay itself.
        (tmp_path / "scripts").mkdir()
        workflow = _workflow(
            tmp_path,
            "name: t\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - name: Summarise\n        run: |\n"
            '          echo "hello" >> "$GITHUB_STEP_SUMMARY"\n',
        )
        result = self._run(
            ["--workflow", str(workflow), "--root", str(tmp_path), "--json",
             "--work-dir", str(tmp_path / "replay")],
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_replay_directory_is_a_git_repo_by_default(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        workflow = _workflow(
            tmp_path,
            "name: t\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - name: Git identity\n        run: |\n"
            '          git config user.name "replay"\n'
            '          git config user.email "replay@example.com"\n',
        )
        result = self._run(
            ["--workflow", str(workflow), "--root", str(tmp_path), "--json",
             "--work-dir", str(tmp_path / "replay")],
            tmp_path,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_no_git_flag_reproduces_the_bare_directory(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        workflow = _workflow(
            tmp_path,
            "name: t\non: [push]\njobs:\n  a:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - name: Git identity\n        run: git config user.name x\n",
        )
        result = self._run(
            ["--workflow", str(workflow), "--root", str(tmp_path), "--json",
             "--work-dir", str(tmp_path / "replay"), "--no-git"],
            tmp_path,
        )
        assert result.returncode == 1
