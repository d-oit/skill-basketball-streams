"""Pytest suite for scripts/check_workflow_refs.py.

The check exists because `runtime-daily.yml` spent a long time running a step
that could only ever no-op: it read a fixture that was never committed, and
`|| true` hid the exit code. The last test here pins that exact step text, so
the guard cannot be quietly weakened back to tolerating it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.check_workflow_refs import (
    check_file,
    find_guarded,
    find_refs,
    workflow_files,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_workflow_refs.py"

# The step as it shipped before this check existed. Kept verbatim: it is the
# reason the check exists, so it is the one input the check must reject.
PRE_FIX_STEP = """\
name: old
jobs:
  runtime:
    steps:
      - name: Capture transcripts for grading
        if: always()
        run: |
          python3 scripts/capture_transcripts.py --runner replay \\
            --from tests/fixtures/runtime_transcripts.json --check || true
"""


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


class TestFindRefs:
    def test_a_repo_path_is_a_ref(self):
        assert find_refs("python3 scripts/validate.py") == {"scripts/validate.py"}

    def test_a_bare_filename_is_not_a_ref(self):
        assert find_refs("python3 validate.py") == set()

    def test_comments_are_not_claims(self):
        text = "# see scripts/does-not-exist.py\npython3 scripts/validate.py\n"
        assert find_refs(text) == {"scripts/validate.py"}

    def test_a_trailing_comment_is_not_a_claim(self):
        text = "python3 scripts/validate.py  # not scripts/ghost.py\n"
        assert find_refs(text) == {"scripts/validate.py"}

    def test_urls_are_not_repo_paths(self):
        text = "curl -fsSL https://raw.githubusercontent.com/o/r/main/scripts/install.sh"
        assert find_refs(text) == set()

    def test_prose_punctuation_is_stripped(self):
        """`docs/do-harness.md.` is a sentence, not a path."""
        assert find_refs('echo "see docs/do-harness.md." >&2') == set()

    def test_write_targets_are_not_refs(self):
        assert find_refs("python3 scripts/fixtures.py --out tests/fixtures/new.jsonl") == {
            "scripts/fixtures.py"
        }
        assert find_refs("python3 scripts/x.py > tests/fixtures/out.json") == {
            "scripts/x.py"
        }

    def test_globs_and_tmp_paths_are_not_refs(self):
        text = (
            "for f in tests/fixtures/*.json; do echo $f; done\n"
            "python3 scripts/x.py --out .tmp/plan.json\n"
        )
        assert find_refs(text) == {"scripts/x.py"}


class TestFindGuarded:
    def test_both_test_forms_are_recognised(self):
        text = "if [ -f tests/fixtures/a.json ]; then\n[ -e scripts/b.py ]\n"
        assert find_guarded(text) == {"tests/fixtures/a.json", "scripts/b.py"}

    def test_a_plain_reference_is_not_guarded(self):
        assert find_guarded("python3 scripts/validate.py") == set()


class TestCheckFile:
    def test_a_missing_path_is_reported(self, tmp_path):
        workflow = tmp_path / "w.yml"
        workflow.write_text("run: python3 scripts/ghost.py\n", encoding="utf-8")
        problems = check_file(workflow, REPO_ROOT)
        assert len(problems) == 1
        assert "scripts/ghost.py" in problems[0]
        assert problems[0].startswith("w.yml:")

    def test_an_existing_path_is_clean(self, tmp_path):
        workflow = tmp_path / "w.yml"
        workflow.write_text("run: python3 scripts/validate.py\n", encoding="utf-8")
        assert check_file(workflow, REPO_ROOT) == []

    def test_a_guarded_absent_path_is_clean(self, tmp_path):
        workflow = tmp_path / "w.yml"
        workflow.write_text(
            "run: |\n"
            "  if [ -f tests/fixtures/absent-by-design.json ]; then\n"
            "    python3 scripts/runtime_eval.py "
            "--transcripts tests/fixtures/absent-by-design.json\n"
            "  fi\n",
            encoding="utf-8",
        )
        assert check_file(workflow, REPO_ROOT) == []


class TestCli:
    def test_missing_workflow_directory_is_a_usage_error(self, tmp_path):
        result = _run("--workflows", str(tmp_path / "nope"))
        assert result.returncode == 2
        assert "not a directory" in result.stderr

    def test_a_clean_directory_passes(self, tmp_path):
        (tmp_path / "w.yml").write_text(
            "run: python3 scripts/validate.py\n", encoding="utf-8"
        )
        result = _run("--workflows", str(tmp_path))
        assert result.returncode == 0
        assert "1 workflow(s)" in result.stdout

    def test_a_bad_directory_fails_and_names_the_path(self, tmp_path):
        (tmp_path / "w.yml").write_text(
            "run: python3 scripts/ghost.py\n", encoding="utf-8"
        )
        result = _run("--workflows", str(tmp_path))
        assert result.returncode == 1
        assert "scripts/ghost.py" in result.stderr

    def test_the_pre_fix_step_is_rejected(self, tmp_path):
        """The regression this check was written for, kept as an input."""
        (tmp_path / "runtime-daily.yml").write_text(PRE_FIX_STEP, encoding="utf-8")
        result = _run("--workflows", str(tmp_path))
        assert result.returncode == 1
        assert "tests/fixtures/runtime_transcripts.json" in result.stderr

    def test_this_repo_is_clean(self):
        result = _run("--root", ".")
        assert result.returncode == 0, result.stderr

    def test_every_workflow_file_is_discovered(self):
        found = [p.name for p in workflow_files(REPO_ROOT / ".github" / "workflows")]
        assert "validate.yml" in found
        assert "runtime-daily.yml" in found
        assert "self-improve.yml" in found
        assert "verify.yml" in found
