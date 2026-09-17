"""Tests for scripts/check-commitlint.sh.

This script is the `commit-msg` hook's whole body, and `do-harness hook install`
makes that hook **fail-closed**: if the script is missing or errors out, the
commit is blocked. Two properties therefore matter more than the linting itself:

1. **It never blocks a commit for its own reasons.** A missing message file exits
   `0` with a warning, because a hook that fails closed on its own bug looks
   exactly like a broken repository.
2. **Git-generated subjects pass.** `Merge`, `Revert`, `fixup!`, `squash!` and
   `amend!` are authored by git; an author cannot change their shape. The first
   version of this script filtered them out and then failed on the empty
   remainder, which would have made `git merge` impossible in this repo.

`bash` is the only dependency, so the tests shell out to it directly.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-commitlint.sh"

GOOD = [
    "feat: add the runtime write path",
    "fix(evals): correct the needle for case 33",
    "feat(skill)!: drop the fifth state",
    "chore: bump pinned do-harness to v0.1.0",
    "docs: explain the four states",
    "test(scripts): cover the planner",
    "refactor: split the render ladder",
    "perf: skip the browser rung",
    "build: pin Python 3.12",
    "ci: add the offline contract steps",
    "style: wrap the docstring",
    "revert: drop the fifth state",
]

HOUSEKEEPING = [
    "Merge branch 'main' into feat/x",
    "Revert \"feat: add thing\"",
    "fixup! feat: add thing",
    "squash! fix: correct the needle",
    "amend! feat: add the runtime",
]

BAD = [
    "add the runtime write path",
    "wip: something",
    "feat add the runtime",
    "feat: ",
    "feat:x",
    "FEAT: shouting",
    "feat(Skill): uppercase scope",
    "feat: x",
]


def _run(text: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        input=text,
        capture_output=True,
        text=True,
    )


class TestScript:
    def test_the_script_exists_and_is_executable(self):
        # The hook is fail-closed: a missing script blocks every commit.
        assert SCRIPT.is_file()
        assert SCRIPT.stat().st_mode & 0o111


class TestAcceptedMessages:
    @pytest.mark.parametrize("message", GOOD)
    def test_conventional_subjects_pass(self, message):
        result = _run(message)
        assert result.returncode == 0, result.stderr
        assert "OK: check-commitlint" in result.stdout

    @pytest.mark.parametrize("message", HOUSEKEEPING)
    def test_git_generated_subjects_pass(self, message):
        result = _run(message)
        assert result.returncode == 0, result.stderr
        assert "git-generated" in result.stdout

    def test_a_comment_block_before_the_subject(self):
        result = _run("# a comment\n\nfeat: real subject\n")
        assert result.returncode == 0

    def test_carriage_returns_are_stripped(self):
        result = _run("chore: strip carriage returns\r\n\r\nbody text\r\n")
        assert result.returncode == 0

    def test_body_lines_are_ignored(self):
        result = _run("feat: real subject\n\nthis body is not linted at all !!!\n")
        assert result.returncode == 0

    def test_git_comment_blocks_are_ignored(self):
        result = _run(
            "# Please enter the commit message for your changes. Lines starting\n"
            "# with '#' will be ignored, and an empty message aborts the commit.\n"
            "feat: real subject\n"
            "# On branch main\n"
        )
        assert result.returncode == 0

    def test_exactly_72_characters_passes(self):
        subject = "feat: " + "x" * (72 - 6)
        assert len(subject) == 72
        assert _run(subject).returncode == 0


class TestRejectedMessages:
    @pytest.mark.parametrize("message", BAD)
    def test_non_conventional_subjects_fail(self, message):
        result = _run(message)
        assert result.returncode == 1, f"{message!r} unexpectedly passed"
        assert "FAIL: check-commitlint" in result.stderr

    def test_unknown_type_names_the_allowed_set(self):
        result = _run("wip: something")
        assert "unknown type 'wip'" in result.stderr
        assert "feat" in result.stderr

    def test_one_character_over_the_limit_fails(self):
        subject = "feat: " + "x" * (72 - 5)
        assert len(subject) == 72 + 1
        result = _run(subject)
        assert result.returncode == 1
        assert "max is 72" in result.stderr

    @pytest.mark.parametrize("text", ["", "\n\n", "# only a comment\n"])
    def test_no_subject_at_all_fails(self, text):
        assert _run(text).returncode == 1


class TestNeverBlocksItself:
    def test_a_missing_message_file_warns_and_allows(self, tmp_path):
        """The hook must not fail closed on its own bug."""
        result = _run("", "--message", str(tmp_path / "nope.txt"))
        assert result.returncode == 0
        assert "WARN" in result.stderr

    def test_a_readable_message_file_is_used(self, tmp_path):
        path = tmp_path / "COMMIT_EDITMSG"
        path.write_text("fix(ci): read the message from a file\n", encoding="utf-8")
        result = _run("", "--message", str(path))
        assert result.returncode == 0
        assert "read the message from a file" in result.stdout

    def test_a_bad_message_file_still_fails(self, tmp_path):
        path = tmp_path / "COMMIT_EDITMSG"
        path.write_text("nope\n", encoding="utf-8")
        assert _run("", "--message", str(path)).returncode == 1

    def test_equals_form_of_the_flag(self, tmp_path):
        path = tmp_path / "msg.txt"
        path.write_text("docs: use the equals form\n", encoding="utf-8")
        assert _run("", f"--message={path}").returncode == 0


class TestUsage:
    def test_help_exits_zero(self):
        result = _run("", "--help")
        assert result.returncode == 0
        assert "Usage:" in result.stdout

    def test_unknown_argument_is_a_usage_error(self):
        result = _run("feat: x", "--nope")
        assert result.returncode == 2

    def test_no_stdin_and_no_flag_is_a_usage_error(self, tmp_path):
        # A closed stdin (not a tty) with no content is an empty message, which
        # is a lint failure rather than a usage error.
        result = subprocess.run(
            ["bash", str(SCRIPT)], input="", capture_output=True, text=True
        )
        assert result.returncode == 1
