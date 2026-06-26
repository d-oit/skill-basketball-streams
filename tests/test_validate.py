"""Pytest suite for scripts/validate.py.

Strategy: subprocess-only tests. Every test invokes the script under test via
`python3 scripts/validate.py ...` and asserts on stdout / stderr / exit code.
FAIL branches are exercised by writing targeted bad fixtures into a tmp_path
directory and pointing the script at it via `--root`. This matches the
smoke-test self-check inside `validate.py` itself, which already uses
subprocess for FAIL-branch regression coverage.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "validate.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Test fixtures — minimal valid-skill scaffolding written into tmp_path
# ---------------------------------------------------------------------------

_VALID_SKILL_MD = (
    "---\n"
    "name: skill-basketball-streams\n"
    "description: Valid SKILL.md fixture for testing.\n"
    "version: 1.0.0\n"
    "category: workflow\n"
    "---\n\n"
    "# Title\n\n"
    "## Purpose\n\nSome content.\n\n"
    "## Rationalizations\n\n- [ ] none\n\n"
    "## Red Flags\n\n- [ ] none\n\n"
    "## References\n\n- `references/x.md`\n"
)


def _write_minimal_repo(tmp_path: Path, evals: dict | None = None) -> None:
    """Write the smallest valid-skill fixture into tmp_path. Override `evals`
    to inject a deliberately bad evals.json for FAIL-branch coverage."""
    (tmp_path / "evals").mkdir()
    if evals is None:
        evals = {"skill_name": "skill-basketball-streams", "evals": []}
    (tmp_path / "evals" / "evals.json").write_text(
        json.dumps(evals, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "x.md").write_text("# OK\n", encoding="utf-8")
    (tmp_path / "SKILL.md").write_text(_VALID_SKILL_MD, encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "See `references/x.md`.\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# --check evals
# ---------------------------------------------------------------------------


def test_check_evals_passes_on_current_repo():
    r = _run(["--check", "evals", "--root", str(REPO_ROOT)])
    assert r.returncode == 0, r.stderr
    assert "OK: evals:" in r.stdout
    assert "cases conform to standard schema" in r.stdout


def test_check_evals_fails_on_wrong_skill_name(tmp_path):
    _write_minimal_repo(tmp_path, {"skill_name": "WRONG", "evals": []})
    r = _run(["--check", "evals", "--root", str(tmp_path)])
    assert r.returncode == 1
    assert "FAIL: evals:" in r.stderr
    assert "skill_name expected" in r.stderr


def test_check_evals_fails_on_missing_required_key(tmp_path):
    _write_minimal_repo(
        tmp_path,
        {
            "skill_name": "skill-basketball-streams",
            "evals": [
                # missing 'assertions' key
                {"id": 1, "prompt": "x", "expected_output": "y"}
            ],
        },
    )
    r = _run(["--check", "evals", "--root", str(tmp_path)])
    assert r.returncode == 1
    assert "missing keys" in r.stderr


def test_check_evals_fails_on_type_shape_regression(tmp_path):
    """expected_output must be a string — int/list/null must trigger FAIL."""
    _write_minimal_repo(
        tmp_path,
        {
            "skill_name": "skill-basketball-streams",
            "evals": [
                {
                    "id": 1,
                    "prompt": "x",
                    "expected_output": 42,
                    "assertions": ["a"],
                }
            ],
        },
    )
    r = _run(["--check", "evals", "--root", str(tmp_path)])
    assert r.returncode == 1
    assert "expected_output: expected string" in r.stderr


def test_check_evals_fails_on_non_int_id(tmp_path):
    _write_minimal_repo(
        tmp_path,
        {
            "skill_name": "skill-basketball-streams",
            "evals": [
                {
                    "id": "1",  # string instead of int
                    "prompt": "x",
                    "expected_output": "y",
                    "assertions": ["a"],
                }
            ],
        },
    )
    r = _run(["--check", "evals", "--root", str(tmp_path)])
    assert r.returncode == 1
    assert "id: expected int" in r.stderr


def test_check_evals_fails_on_non_list_assertions(tmp_path):
    _write_minimal_repo(
        tmp_path,
        {
            "skill_name": "skill-basketball-streams",
            "evals": [
                {
                    "id": 1,
                    "prompt": "x",
                    "expected_output": "y",
                    "assertions": "not-a-list",
                }
            ],
        },
    )
    r = _run(["--check", "evals", "--root", str(tmp_path)])
    assert r.returncode == 1
    assert "assertions: expected list" in r.stderr


def test_check_evals_fails_on_non_string_assertion_entry(tmp_path):
    """assertions[i] must be a string — number in list must trigger FAIL."""
    _write_minimal_repo(
        tmp_path,
        {
            "skill_name": "skill-basketball-streams",
            "evals": [
                {
                    "id": 1,
                    "prompt": "x",
                    "expected_output": "y",
                    "assertions": [42],  # int inside a list
                }
            ],
        },
    )
    r = _run(["--check", "evals", "--root", str(tmp_path)])
    assert r.returncode == 1
    assert "assertions[0]: expected string" in r.stderr


def test_check_evals_accepts_empty_assertions_list(tmp_path):
    """Empty assertions[] is legitimate wire form (no PASS/FAIL signal)."""
    _write_minimal_repo(
        tmp_path,
        {
            "skill_name": "skill-basketball-streams",
            "evals": [
                {
                    "id": 1,
                    "prompt": "x",
                    "expected_output": "y",
                    "assertions": [],
                }
            ],
        },
    )
    r = _run(["--check", "evals", "--root", str(tmp_path)])
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------
# --check skill
# ---------------------------------------------------------------------------


def test_check_skill_passes_on_current_repo():
    r = _run(["--check", "skill", "--root", str(REPO_ROOT)])
    assert r.returncode == 0, r.stderr
    assert "OK: SKILL.md:" in r.stdout
    assert "mandatory sections present" in r.stdout


def test_check_skill_fails_on_bad_name_regex(tmp_path):
    """Name with uppercase + underscore fails NAME_REGEX."""
    bad = (
        "---\n"
        "name: Bad_Name\n"
        "description: x\n"
        "version: 1.0.0\n"
        "category: workflow\n"
        "---\n\n"
        "# Title\n\n"
        "## Purpose\n\nContent.\n\n"
        "## Rationalizations\n\n- [ ] none\n\n"
        "## Red Flags\n\n- [ ] none\n\n"
    )
    _write_minimal_repo(tmp_path)
    (tmp_path / "SKILL.md").write_text(bad, encoding="utf-8")
    r = _run(["--check", "skill", "--root", str(tmp_path)])
    assert r.returncode == 1
    assert "FAIL: SKILL.md:" in r.stderr
    assert "spec format" in r.stderr


def test_check_skill_fails_on_over_long_description(tmp_path):
    over_long = (
        "---\n"
        "name: skill-basketball-streams\n"
        f"description: {'x' * 1025}\n"  # > 1024 limit
        "version: 1.0.0\n"
        "category: workflow\n"
        "---\n\n"
        "# Title\n\n"
        "## Purpose\n\nContent.\n\n"
        "## Rationalizations\n\n- [ ] none\n\n"
        "## Red Flags\n\n- [ ] none\n\n"
    )
    _write_minimal_repo(tmp_path)
    (tmp_path / "SKILL.md").write_text(over_long, encoding="utf-8")
    r = _run(["--check", "skill", "--root", str(tmp_path)])
    assert r.returncode == 1, r.stderr
    assert "description length 1025 > 1024" in r.stderr


def test_check_skill_fails_on_missing_mandatory_section(tmp_path):
    no_red_flags = (
        "---\n"
        "name: skill-basketball-streams\n"
        "description: x\n"
        "version: 1.0.0\n"
        "category: workflow\n"
        "---\n\n"
        "# Title\n\n"
        "## Purpose\n\nContent.\n\n"
        "## Rationalizations\n\n- [ ] none\n\n"
        # NOTE: no '## Red Flags' section
    )
    _write_minimal_repo(tmp_path)
    (tmp_path / "SKILL.md").write_text(no_red_flags, encoding="utf-8")
    r = _run(["--check", "skill", "--root", str(tmp_path)])
    assert r.returncode == 1
    assert "FAIL: SKILL.md:" in r.stderr
    assert "missing mandatory sections" in r.stderr


def test_check_skill_accepts_compatibility_at_exactly_500_chars(tmp_path):
    """Boundary test: compatibility of exactly 500 chars should pass."""
    compat = (
        "---\n"
        "name: skill-basketball-streams\n"
        "description: x\n"
        "version: 1.0.0\n"
        "category: workflow\n"
        f"compatibility: {'x' * 500}\n"  # == 500 limit
        "---\n\n"
        "# Title\n\n"
        "## Purpose\n\nContent.\n\n"
        "## Rationalizations\n\n- [ ] none\n\n"
        "## Red Flags\n\n- [ ] none\n\n"
    )
    _write_minimal_repo(tmp_path)
    (tmp_path / "SKILL.md").write_text(compat, encoding="utf-8")
    r = _run(["--check", "skill", "--root", str(tmp_path)])
    assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------
# --check references
# ---------------------------------------------------------------------------


def test_check_references_passes_on_current_repo():
    r = _run(["--check", "references", "--root", str(REPO_ROOT)])
    assert r.returncode == 0, r.stderr
    assert "OK: references:" in r.stdout


def test_check_references_fails_on_dangling_md_reference(tmp_path):
    """A `.md` path cited from README.md but not on disk must trigger FAIL."""
    (tmp_path / "SKILL.md").write_text(_VALID_SKILL_MD, encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "See `references/missing.md`.\n", encoding="utf-8"
    )
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "x.md").write_text("# dummy\n", encoding="utf-8")
    r = _run(["--check", "references", "--root", str(tmp_path)])
    assert r.returncode == 1
    assert "FAIL: references:" in r.stderr
    assert "missing.md" in r.stderr


# ---------------------------------------------------------------------------
# --check all + --help + USAGE/EXIT_CODE contract
# ---------------------------------------------------------------------------


def test_check_all_passes_on_current_repo():
    r = _run(["--check", "all", "--root", str(REPO_ROOT)])
    assert r.returncode == 0, r.stderr
    for expected in ("OK: evals:", "OK: SKILL.md:", "OK: references:"):
        assert expected in r.stdout, f"missing {expected!r} in stdout:\n{r.stdout}"


def test_help_lists_all_choices():
    r = _run(["--help"])
    assert r.returncode == 0
    for choice in ("all", "evals", "skill", "references", "smoke-test"):
        assert choice in r.stdout, f"missing {choice!r} in --help:\n{r.stdout}"


def test_root_not_a_directory_exits_2(tmp_path):
    r = _run(["--root", str(tmp_path / "does-not-exist")])
    assert r.returncode == 2
    assert "not a directory" in r.stderr


# ---------------------------------------------------------------------------
# --check smoke-test (the script's built-in self-test)
# ---------------------------------------------------------------------------


def test_smoke_test_passes_on_current_repo():
    r = _run(["--check", "smoke-test"])
    assert r.returncode == 0, r.stderr
    assert "OK: smoke-test:" in r.stdout
    # smoke-test exercises each FAIL branch via subprocess; verify all three
    # got exercised.
    assert "every FAIL branch correctly rejects" in r.stdout


# ---------------------------------------------------------------------------
# Parametrized FAIL-branch coverage — adding a 4th check requires one tuple
# entry below (and a fixture inside `scripts/validate.py:smoke_test`'s
# `fail_fixtures` dict). The pytest side and the production self-test stay
# in lockstep so a new check ships only after its FAIL-branch coverage is
# registered on both sides.
# ---------------------------------------------------------------------------


def _setup_evals_fail(tmp_path: Path) -> None:
    """Eval case with WRONG skill_name — must FAIL check_evals."""
    _write_minimal_repo(tmp_path, {"skill_name": "WRONG", "evals": []})


def _setup_skill_regex_fail(tmp_path: Path) -> None:
    """Skill name failing NAME_REGEX (uppercase + underscore)."""
    _write_minimal_repo(tmp_path)
    bad = (
        "---\n"
        "name: Bad_Name\n"
        "description: x\n"
        "version: 1.0.0\n"
        "category: workflow\n"
        "---\n\n"
        "# Title\n\n"
    )
    (tmp_path / "SKILL.md").write_text(bad, encoding="utf-8")


def _setup_references_missing_fail(tmp_path: Path) -> None:
    """README.md cites a `.md` path that doesn't exist on disk."""
    _write_minimal_repo(tmp_path)
    (tmp_path / "README.md").write_text(
        "See `references/missing.md`.\n", encoding="utf-8"
    )


@pytest.mark.parametrize(
    "check_key, setup_fn, expected_diagnostic",
    [
        (
            "evals",
            _setup_evals_fail,
            "FAIL: evals:",
        ),
        (
            "skill",
            _setup_skill_regex_fail,
            "FAIL: SKILL.md:",
        ),
        (
            "references",
            _setup_references_missing_fail,
            "FAIL: references:",
        ),
    ],
    ids=["check=evals", "check=skill", "check=references"],
)
def test_each_check_fail_branch_via_parametrize(
    tmp_path, check_key, setup_fn, expected_diagnostic,
):
    """Each registered CHECKS entry MUST be covered by a per-check FAIL
    fixture. Adding a 4th check requires (a) adding a CHECKS dict entry to
    scripts/validate.py + a `fail_fixtures[*]` entry to its smoke_test(),
    and (b) adding one tuple entry to this parametrize list."""
    setup_fn(tmp_path)
    r = _run(["--check", check_key, "--root", str(tmp_path)])
    assert r.returncode == 1, (
        f"--check {check_key} did not FAIL on its targeted fixture:\n"
        f"stdout={r.stdout!r}\nstderr={r.stderr!r}"
    )
    assert expected_diagnostic in r.stderr, (
        f"--check {check_key} expected {expected_diagnostic!r} in stderr, "
        f"got:\n{r.stderr}"
    )
