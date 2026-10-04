"""Tests for `scripts/check_product_contract.py` — the product contract gate.

The gate exists because two committed documents are only claims if something
reads them: `PRODUCT.md` (which sensors enforce the product) and `tasks.md`
(the readable mirror of the harness task database). A gate that exits 0 on
everything is decoration, so each check here is exercised twice — once against
the real repository (it must pass) and once against a fixture that carries the
defect (it must fail, and the FAIL line must name it).

The fixtures are a `clean/` root plus one defect file per bad case, so the only
difference between a pass and a fail is the defect itself.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_product_contract import (
    EVAL_COUNT_CLAIM,
    _sensor_table,
    _task_rows,
    _toc_entries,
    _totals,
    check,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_product_contract.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "product"
CLEAN = FIXTURES / "clean"
DEFECTS = FIXTURES / "defects"
TASKS_JSON = REPO_ROOT / "plans" / "tasks.json"
TASKS_BOARD = REPO_ROOT / "tasks.md"
PRODUCT_DOC = REPO_ROOT / "PRODUCT.md"
EVALS = REPO_ROOT / "evals" / "evals.json"


def _cli(root: Path) -> subprocess.CompletedProcess:
    """Run the gate the way a sensor does: as a subprocess, from the CLI."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
    )


def _assembled(
    tmp_path: Path,
    defect: str | None = None,
    target: str | None = None,
    removed: str | None = None,
) -> Path:
    """A copy of the clean fixture root with at most one thing changed."""
    root = tmp_path / "root"
    shutil.copytree(CLEAN, root)
    if defect is not None:
        assert target is not None, "a defect needs a target path"
        shutil.copyfile(DEFECTS / defect, root / target)
    if removed is not None:
        (root / removed).unlink()
    return root


# --------------------------------------------------------------------------
# The real repository passes
# --------------------------------------------------------------------------


class TestTheRealRepositoryPasses:
    def test_the_gate_is_green(self):
        assert check(REPO_ROOT) == 0

    def test_the_cli_exits_zero(self):
        result = _cli(REPO_ROOT)
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("OK: check_product_contract:")

    def test_the_stated_eval_count_matches_the_file(self):
        """A hand-typed count is only allowed if the file can veto it."""
        claims = {
            int(value)
            for value in EVAL_COUNT_CLAIM.findall(
                PRODUCT_DOC.read_text(encoding="utf-8")
            )
        }
        assert claims, "PRODUCT.md states no eval-case count to check"
        assert claims == {len(json.loads(EVALS.read_text(encoding="utf-8"))["evals"])}


class TestTheCleanFixturePasses:
    """The failures below are the defect, not the fact of a fixture root."""

    def test_clean_root_is_green(self, tmp_path):
        root = _assembled(tmp_path)
        assert check(root) == 0

    def test_clean_root_cli_exits_zero(self, tmp_path):
        result = _cli(_assembled(tmp_path))
        assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------
# The board is pinned to the committed export
# --------------------------------------------------------------------------


class TestTheBoardMatchesTheCommittedExport:
    """`plans/tasks.json` is the authoritative committed mirror; `tasks.md`
    must not be able to rot away from it."""

    def test_every_id_title_and_status_matches(self):
        exported = {
            int(task["id"]): (task["title"], task["status"])
            for task in json.loads(TASKS_JSON.read_text(encoding="utf-8"))["tasks"]
        }
        assert _task_rows(TASKS_BOARD.read_text(encoding="utf-8")) == exported

    def test_the_totals_match_the_export_summary(self):
        summary = {
            key: int(value)
            for key, value in json.loads(TASKS_JSON.read_text(encoding="utf-8"))[
                "summary"
            ].items()
        }
        assert _totals(TASKS_BOARD.read_text(encoding="utf-8")) == summary

    def test_the_export_summary_matches_its_own_tasks(self):
        data = json.loads(TASKS_JSON.read_text(encoding="utf-8"))
        counted = {"pending": 0, "in_progress": 0, "done": 0, "failed": 0}
        for task in data["tasks"]:
            counted[task["status"]] += 1
        assert {k: int(v) for k, v in data["summary"].items()} == counted

    def test_a_stale_status_is_detected(self):
        """The defect the fixture carries: one row's status moves, the export
        does not. Shown at the parser level so the assertion names the row."""
        board = (DEFECTS / "bad_tasks.md").read_text(encoding="utf-8")
        assert _task_rows(board)[1][1] == "done"
        assert json.loads(TASKS_JSON.read_text(encoding="utf-8"))["tasks"][0][
            "status"
        ] == "pending"


# --------------------------------------------------------------------------
# Every check can fail — proved against the fixtures
# --------------------------------------------------------------------------


BAD_CASES = [
    pytest.param(
        "bad_tasks.md",
        "tasks.md",
        None,
        "tasks.md: task 1 status 'done' does not match plans/tasks.json 'pending'",
        id="stale-board",
    ),
    pytest.param(
        "bad_product_sensor.md",
        "PRODUCT.md",
        None,
        "PRODUCT.md: names sensor 'source-registry', which do-harness.toml does not declare",
        id="product-names-a-deleted-sensor",
    ),
    pytest.param(
        "bad_product_toc.md",
        "PRODUCT.md",
        None,
        "PRODUCT.md: the contents list promises section 'Retired sensors', which does not exist",
        id="toc-promises-a-missing-section",
    ),
    pytest.param(
        "bad_methods.json",
        "plans/methods.json",
        None,
        "plans/methods.json: method 'add-source-reader' subtask 0 names sensor 'source-registry'",
        id="methods-name-a-deleted-sensor",
    ),
    pytest.param(
        "bad_eval_count.md",
        "PRODUCT.md",
        None,
        "PRODUCT.md: states 3 eval case(s), but evals/evals.json holds 2",
        id="stale-eval-count",
    ),
]


class TestTheGateCanFail:
    @pytest.mark.parametrize("defect,target,removed,needle", BAD_CASES)
    def test_check_reports_the_violation(self, tmp_path, defect, target, removed, needle):
        root = _assembled(tmp_path, defect=defect, target=target, removed=removed)
        assert check(root) == 1

    @pytest.mark.parametrize("defect,target,removed,needle", BAD_CASES)
    def test_cli_exits_non_zero_and_names_it(
        self, tmp_path, defect, target, removed, needle
    ):
        result = _cli(_assembled(tmp_path, defect=defect, target=target, removed=removed))
        assert result.returncode == 1, result.stdout
        assert result.stdout == "", "a failure must not print an OK payload"
        assert f"FAIL: check_product_contract: {needle}" in result.stderr, result.stderr

    def test_a_missing_product_doc_fails(self, tmp_path):
        result = _cli(_assembled(tmp_path, removed="PRODUCT.md"))
        assert result.returncode == 1
        assert "PRODUCT.md is missing" in result.stderr

    def test_a_missing_board_fails(self, tmp_path):
        result = _cli(_assembled(tmp_path, removed="tasks.md"))
        assert result.returncode == 1
        assert "tasks.md is missing" in result.stderr

    def test_an_empty_harness_config_fails(self, tmp_path):
        root = _assembled(tmp_path)
        (root / "do-harness.toml").write_text("language = \"generic\"\n", encoding="utf-8")
        result = _cli(root)
        assert result.returncode == 1
        assert "declares no [[sensors]]" in result.stderr

    def test_a_missing_root_is_a_usage_error(self, tmp_path):
        result = _cli(tmp_path / "does-not-exist")
        assert result.returncode == 2
        assert "not a directory" in result.stderr


# --------------------------------------------------------------------------
# The parsers read declarations, not prose
# --------------------------------------------------------------------------


class TestDeclarationsAreReadWhereTheyAreMade:
    def test_toc_entries_read_the_visible_text(self):
        text = "# T\n\n## Contents\n\n- [What it is](#what-it-is)\n- Plain entry\n\n## What it is\n"
        assert _toc_entries(text) == ["What it is", "Plain entry"]

    def test_sensor_table_strips_backticks_and_skips_the_separator(self):
        text = (
            "## S\n\n"
            "| Sensor | Pins |\n"
            "| --- | --- |\n"
            "| `pytest` | x |\n"
            "| bare-name | y |\n"
            "\nprose after the table\n"
        )
        assert _sensor_table(text) == ["pytest", "bare-name"]

    def test_a_prose_mention_is_not_a_declaration(self):
        """Same rule as `references/approved-sources.md`'s Notes column: a
        retired name may be explained in prose, only a table cell declares."""
        text = (
            "## S\n\n"
            "| Sensor | Pins |\n"
            "| --- | --- |\n"
            "| `pytest` | x |\n"
            "\nA sentence may name `source-registry` while explaining its removal.\n"
        )
        assert _sensor_table(text) == ["pytest"]

    def test_totals_ignore_a_prose_mention(self):
        text = "Edit the Totals: line below.\n\nTotals: pending=1 done=0\n"
        assert _totals(text) == {"pending": 1, "done": 0}
