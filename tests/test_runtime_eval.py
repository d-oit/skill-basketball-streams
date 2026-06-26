"""Pytest suite for scripts/runtime_eval.py.

Strategy: subprocess-only. Runtime-eval cases are validated by writing stub
JSONs that match the per-case `expected_output` and asserting:
  * structural pass emits one OK line per case
  * runtime pass with synthesized stubs PASS-es
  * .strip() normalization makes trailing whitespace in stubs a no-op
  * non-string stub values, missing case stubs, and wrong stub values FAIL
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "runtime_eval.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def _synthesize_stubs(source_evals: Path) -> dict[str, str]:
    """Build `{id_str: expected_output}` from a given evals.json."""
    d = json.loads(source_evals.read_text(encoding="utf-8"))
    return {str(c["id"]): c["expected_output"] for c in d["evals"]}


def _copy_evals_into(tmp_repo: Path) -> None:
    """Copy the current repo's evals/evals.json into a tmp fixture repo."""
    (tmp_repo / "evals").mkdir()
    (tmp_repo / "evals" / "evals.json").write_text(
        (REPO_ROOT / "evals" / "evals.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# --help + structural pass
# ---------------------------------------------------------------------------


def test_help_lists_root_and_stubs():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "--root" in r.stdout
    assert "--stubs" in r.stdout


def test_structural_pass_passes_on_current_repo():
    r = _run(["--root", str(REPO_ROOT)])
    assert r.returncode == 0, r.stderr
    # One "OK: structural: Case N:" line per case.
    assert r.stdout.count("OK: structural:") == len(
        json.loads((REPO_ROOT / "evals" / "evals.json").read_text())["evals"]
    )


def test_structural_pass_spot_checks_lowest_and_highest_case_ids():
    r = _run(["--root", str(REPO_ROOT)])
    assert r.returncode == 0
    assert "Case 1:" in r.stdout
    # Highest id in current data — resolved dynamically to remain robust
    # against future case-count changes.
    cases = json.loads(
        (REPO_ROOT / "evals" / "evals.json").read_text()
    )["evals"]
    highest = max(c["id"] for c in cases)
    assert f"Case {highest}:" in r.stdout


# ---------------------------------------------------------------------------
# runtime pass — happy path + edge cases
# ---------------------------------------------------------------------------


def test_runtime_pass_with_synthesized_stubs_passes(tmp_path):
    tmp_repo = tmp_path / "repo"
    tmp_repo.mkdir()
    _copy_evals_into(tmp_repo)
    stubs = _synthesize_stubs(REPO_ROOT / "evals" / "evals.json")
    stubs_path = tmp_path / "stubs.json"
    stubs_path.write_text(json.dumps(stubs, indent=2), encoding="utf-8")
    r = _run(["--root", str(tmp_repo), "--stubs", str(stubs_path)])
    assert r.returncode == 0, r.stderr
    cases = json.loads(
        (REPO_ROOT / "evals" / "evals.json").read_text()
    )["evals"]
    assert r.stdout.count("OK: runtime:") == len(cases)


def test_runtime_pass_with_trailing_whitespace_still_passes(tmp_path):
    """Trailing newlines / padding in stubs must be tolerated via .strip()."""
    tmp_repo = tmp_path / "repo"
    tmp_repo.mkdir()
    _copy_evals_into(tmp_repo)
    d = json.loads((REPO_ROOT / "evals" / "evals.json").read_text())
    stubs = {
        str(c["id"]): c["expected_output"] + "\n\n  \n" for c in d["evals"]
    }
    stubs_path = tmp_path / "stubs.json"
    stubs_path.write_text(json.dumps(stubs, indent=2), encoding="utf-8")
    r = _run(["--root", str(tmp_repo), "--stubs", str(stubs_path)])
    assert r.returncode == 0, r.stderr


def test_runtime_pass_fails_with_wrong_stub_for_one_case(tmp_path):
    tmp_repo = tmp_path / "repo"
    tmp_repo.mkdir()
    _copy_evals_into(tmp_repo)
    stubs = _synthesize_stubs(REPO_ROOT / "evals" / "evals.json")
    case_id = "3"
    stubs[case_id] = "WRONG-VALUE"
    stubs_path = tmp_path / "stubs.json"
    stubs_path.write_text(json.dumps(stubs, indent=2), encoding="utf-8")
    r = _run(["--root", str(tmp_repo), "--stubs", str(stubs_path)])
    assert r.returncode == 1
    assert "stub != expected_output" in r.stderr
    assert f"case #{case_id}" in r.stderr


def test_runtime_pass_fails_with_non_string_stub_value(tmp_path):
    """isinstance guard — a numeric stub value must trigger the type FAIL."""
    tmp_repo = tmp_path / "repo"
    tmp_repo.mkdir()
    _copy_evals_into(tmp_repo)
    stubs = _synthesize_stubs(REPO_ROOT / "evals" / "evals.json")
    stubs["3"] = 42  # int, not str
    stubs_path = tmp_path / "stubs.json"
    stubs_path.write_text(json.dumps(stubs, indent=2), encoding="utf-8")
    r = _run(["--root", str(tmp_repo), "--stubs", str(stubs_path)])
    assert r.returncode == 1
    assert "non-string value" in r.stderr


def test_runtime_pass_fails_when_stub_missing_for_a_case(tmp_path):
    tmp_repo = tmp_path / "repo"
    tmp_repo.mkdir()
    _copy_evals_into(tmp_repo)
    stubs = _synthesize_stubs(REPO_ROOT / "evals" / "evals.json")
    case_id = "5"
    del stubs[case_id]
    stubs_path = tmp_path / "stubs.json"
    stubs_path.write_text(json.dumps(stubs, indent=2), encoding="utf-8")
    r = _run(["--root", str(tmp_repo), "--stubs", str(stubs_path)])
    assert r.returncode == 1
    assert "stub missing" in r.stderr
    assert f"case #{case_id}" in r.stderr


def test_runtime_pass_fails_when_stubs_json_non_coercible_key(tmp_path):
    """Stub key like 'abc' (cannot int()) must trigger FAIL."""
    tmp_repo = tmp_path / "repo"
    tmp_repo.mkdir()
    _copy_evals_into(tmp_repo)
    stubs_path = tmp_path / "stubs.json"
    stubs_path.write_text(
        json.dumps({"abc": "value"}, indent=2), encoding="utf-8"
    )
    r = _run(["--root", str(tmp_repo), "--stubs", str(stubs_path)])
    assert r.returncode == 1
    assert "not coercible to int" in r.stderr


def test_runtime_pass_fails_when_stubs_file_missing(tmp_path):
    r = _run(
        ["--root", str(REPO_ROOT), "--stubs", str(tmp_path / "nope.json")]
    )
    assert r.returncode == 1
    assert "file missing" in r.stderr


def test_runtime_pass_fails_when_stubs_json_invalid(tmp_path):
    stubs_path = tmp_path / "bad.json"
    stubs_path.write_text("{not valid json", encoding="utf-8")
    r = _run(["--root", str(REPO_ROOT), "--stubs", str(stubs_path)])
    assert r.returncode == 1
    assert "invalid JSON" in r.stderr


def test_root_not_a_directory_exits_2(tmp_path):
    r = _run(["--root", str(tmp_path / "does-not-exist")])
    assert r.returncode == 2
    assert "not a directory" in r.stderr
