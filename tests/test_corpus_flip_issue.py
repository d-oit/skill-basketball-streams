"""Tests for scripts/corpus_flip_issue.py and the weekly corpus-refresh workflow.

Two things are worth pinning here, and they are different in kind.

**The issue-filing path never runs by accident.** Almost every weekly run finds
nothing, and the one thing that must not happen is a script that files an issue
anyway. So the tests below assert the *negative* directly: with no flips, `gh` is
never invoked at all — proven with a fake `gh` on `PATH` that records every call
it receives, which is also how the create-vs-comment dedupe is checked without
touching GitHub.

**A flip is filed once, not once a week.** The issue title is the dedupe key, so
it must not carry a date or a page name; a test pins that a second flip comments
on the open issue rather than opening a second one.

Offline throughout: the workflow is read as YAML, and `gh` is a shell stub.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from corpus_flip_issue import (  # noqa: E402
    TITLE_MARKER,
    _existing_open_issue,
    build_body,
    build_markdown,
    build_title,
)

SCRIPT = REPO_ROOT / "scripts" / "corpus_flip_issue.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "corpus-refresh.yml"

FLIP_LOST = {
    "name": "youtube-channel-live",
    "url": "https://www.youtube.com/@FIBA/live",
    "was": "evidence",
    "now": "no-evidence",
    "expect": "evidence",
    "direction": "lost-evidence",
    "agrees_with_expect": False,
    "note": "channel live tab with a broadcast attached",
}
FLIP_GAINED = {
    "name": "magentasport-home",
    "url": "https://www.magentasport.de/",
    "was": "no-evidence",
    "now": "evidence",
    "expect": "no-evidence",
    "direction": "gained-evidence",
    "agrees_with_expect": False,
    "note": "announce site",
}


def report(tmp_path: Path, flips: list[dict], *, pages: int = 8) -> Path:
    path = tmp_path / "refresh.json"
    path.write_text(
        json.dumps(
            {
                "refreshed_at": "2026-09-15T07:40:06Z",
                "pages": [{"name": f"p{i}"} for i in range(pages)],
                "flips": flips,
            }
        )
    )
    return path


def fake_gh(tmp_path: Path, list_payload: str = "[]") -> tuple[Path, Path]:
    """A `gh` that records its argv instead of talking to GitHub.

    Returns (bindir, logfile). The log is the assertion surface: it is how
    "nothing was filed" is checked as hard as "something was".
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "gh-calls.log"
    log.write_text("")
    gh = bindir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$GH_LOG"\n'
        'case "$1 $2" in\n'
        # Read the payload with a shell builtin, so the stub works even when the
        # test has deliberately narrowed PATH to just this directory.
        '  "issue list") while IFS= read -r line; do printf "%s" "$line"; done < "$GH_LIST" ;;\n'
        "  *) echo ok ;;\n"
        "esac\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    # Trailing newline so the stub's `while read` loop (a builtin, because the
    # test narrows PATH) sees the final line.
    (tmp_path / "gh-list.json").write_text(list_payload + "\n")
    return bindir, log


def run_cli(tmp_path: Path, args: list[str], *, bindir: Path | None = None, log: Path | None = None):
    env = dict(os.environ)
    env["PATH"] = str(bindir) if bindir is not None else str(tmp_path / "empty-bin")
    if log is not None:
        env["GH_LOG"] = str(log)
        env["GH_LIST"] = str(tmp_path / "gh-list.json")
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env,
    )


class TestNothingFlips:
    def test_no_flips_never_reaches_github(self, tmp_path):
        """The expected outcome on almost every run. It must be silent and free:
        a fake `gh` that records its calls is left completely untouched."""
        bindir, log = fake_gh(tmp_path)
        result = run_cli(
            tmp_path, ["--report", str(report(tmp_path, [])), "--file"],
            bindir=bindir, log=log,
        )
        assert result.returncode == 0, result.stderr
        assert log.read_text() == "", "gh was invoked for a run with no flip"
        assert "no verdict flipped" in result.stdout

    def test_a_report_without_the_key_is_not_a_flip(self, tmp_path):
        path = tmp_path / "refresh.json"
        path.write_text(json.dumps({"pages": [{"name": "p"}]}))
        bindir, log = fake_gh(tmp_path)
        result = run_cli(tmp_path, ["--report", str(path), "--file"], bindir=bindir, log=log)
        assert result.returncode == 0
        assert log.read_text() == ""


class TestFiling:
    def test_a_flip_opens_one_issue(self, tmp_path):
        bindir, log = fake_gh(tmp_path, list_payload="[]")
        result = run_cli(
            tmp_path, ["--report", str(report(tmp_path, [FLIP_LOST])), "--file"],
            bindir=bindir, log=log,
        )
        assert result.returncode == 0, result.stderr
        calls = log.read_text().splitlines()
        assert any(call.startswith("issue list") for call in calls)
        assert any(call.startswith("issue create") for call in calls), calls
        assert not any(call.startswith("issue comment") for call in calls), calls
        assert "lost-evidence" in result.stdout

    def test_a_persistent_flip_comments_instead_of_piling_up(self, tmp_path):
        """One stuck page must not become one issue per week. The title is the
        dedupe key, so this is really a test about the title being stable."""
        existing = json.dumps(
            [{"number": 12, "title": f"{TITLE_MARKER} — 1 page"}]
        )
        bindir, log = fake_gh(tmp_path, list_payload=existing)
        result = run_cli(
            tmp_path, ["--report", str(report(tmp_path, [FLIP_LOST])), "--file"],
            bindir=bindir, log=log,
        )
        assert result.returncode == 0, result.stderr
        calls = log.read_text().splitlines()
        assert any(call.startswith("issue comment 12") for call in calls), calls
        assert not any(call.startswith("issue create") for call in calls), calls

    def test_a_flip_that_cannot_be_filed_is_loud(self, tmp_path):
        """No `gh` must not read as "nothing to report": a finding that was
        dropped silently is the failure this whole workflow exists to avoid."""
        result = run_cli(
            tmp_path, ["--report", str(report(tmp_path, [FLIP_LOST])), "--file"],
            bindir=tmp_path / "no-such-bin",
        )
        assert result.returncode == 1
        assert "FAIL: corpus_flip_issue" in result.stderr

    def test_without_file_it_only_reports(self, tmp_path):
        bindir, log = fake_gh(tmp_path)
        result = run_cli(
            tmp_path, ["--report", str(report(tmp_path, [FLIP_LOST]))],
            bindir=bindir, log=log,
        )
        assert result.returncode == 0
        assert log.read_text() == "", "a report must not touch GitHub"
        assert "FLIP: corpus_flip_issue" in result.stdout
        assert "pass --file" in result.stdout

    def test_file_and_dry_run_are_exclusive(self, tmp_path):
        result = run_cli(
            tmp_path, ["--report", str(report(tmp_path, [])), "--file", "--dry-run"],
        )
        assert result.returncode == 2

    def test_an_unreadable_report_is_a_usage_error(self, tmp_path):
        result = run_cli(tmp_path, ["--report", str(tmp_path / "nope.json")])
        assert result.returncode == 2
        assert "FAIL: corpus_flip_issue" in result.stderr


class TestDedupeKey:
    def test_the_marker_finds_the_open_issue(self):
        payload = json.dumps(
            [
                {"number": 3, "title": "unrelated"},
                {"number": 12, "title": TITLE_MARKER},
                {"number": 20, "title": f"{TITLE_MARKER} — older wording"},
            ]
        )
        assert _existing_open_issue(payload) == 12

    def test_no_open_issue_is_none(self):
        assert _existing_open_issue("[]") is None
        assert _existing_open_issue(json.dumps([{"number": 1, "title": "x"}])) is None

    def test_unparseable_gh_output_does_not_create_a_duplicate_guess(self):
        assert _existing_open_issue("not json") is None
        assert _existing_open_issue("") is None

    def test_the_title_carries_no_date_or_page_name(self):
        """A date would make every week a new issue, which is the noise the
        workflow is built to avoid."""
        title = build_title()
        assert title == TITLE_MARKER
        assert "2026" not in title and ":" not in title


class TestBodyAndSummary:
    def test_the_body_explains_both_directions_and_both_fixes(self):
        body = build_body([FLIP_LOST, FLIP_GAINED], refreshed_at="2026-09-15T07:40:06Z")
        assert FLIP_LOST["url"] in body and FLIP_GAINED["url"] in body
        assert "lost-evidence" in body and "gained-evidence" in body
        # The two causes are not distinguishable from the report alone, so the
        # issue has to hand the reader both routes rather than a verdict.
        assert "The source changed" in body and "The gate broke" in body
        assert "record_pages.py --refresh" in body
        assert "Never hand-edit a fixture" in body
        assert "2026-09-15T07:40:06Z" in body

    def test_the_body_flags_a_flip_that_moved_away_from_the_expectation(self):
        """`no` must stay a statement about disagreement, not a verdict on which
        side is wrong: that is exactly what the issue asks a human to decide."""
        body = build_body([FLIP_LOST])
        assert "| **no** |" in body
        assert "which one moved is a question for a human" in body
        assert build_body([{**FLIP_LOST, "agrees_with_expect": True}]).count("| **no** |") == 0

    def test_dry_run_prints_exactly_what_would_be_sent(self, tmp_path):
        result = run_cli(
            tmp_path, ["--report", str(report(tmp_path, [FLIP_LOST])), "--dry-run"],
        )
        assert result.returncode == 0
        assert result.stdout.startswith(TITLE_MARKER)
        assert "youtube-channel-live" in result.stdout

    def test_the_markdown_summary_is_the_only_renderer(self, tmp_path):
        """The job summary and the issue are built from the same payload by the
        same module: two renderers of one artifact drift, and the misleading one
        is the one people read."""
        text = run_cli(
            tmp_path, ["--report", str(report(tmp_path, [FLIP_LOST]), ), "--markdown"],
        ).stdout
        assert "| Pages re-recorded | 8 |" in text
        assert "| Verdict flips | 1 |" in text
        assert "`youtube-channel-live` | evidence | **no-evidence** | lost-evidence |" in text

    def test_markdown_without_flips_states_the_policy(self, tmp_path):
        text = run_cli(
            tmp_path, ["--report", str(report(tmp_path, [])), "--markdown"],
        ).stdout
        assert "| Verdict flips | 0 |" in text
        assert "byte diff is not reported by design" in text

    def test_build_markdown_is_pure(self):
        assert "| Verdict flips | 0 |" in build_markdown([], pages=3)

    def test_an_unreachable_page_is_not_a_clean_bill_of_health(self):
        """A partial comparison must say it is partial.

        `record_pages --refresh` now compares the pages it CAN reach and reports
        the ones it cannot, so a summary that read "8 pages re-recorded, no
        verdict changed" would turn an unasked question into a passing result —
        on the `expect=evidence` page whose loss is the whole point of the
        corpus. An unreached page is not a page whose verdict held.
        """
        unusable = [
            {"name": "youtube-watch-live", "url": "u", "reason": "HTTP 429"},
        ]
        text = build_markdown([], pages=7, unusable=unusable)

        assert "| Pages **not reachable** | 1 |" in text
        assert "`youtube-watch-live`" in text and "HTTP 429" in text
        assert "partial" in text
        # Not the unqualified sentence, which would be a claim about pages it
        # never reached.
        assert "No verdict changed. A byte diff" not in text
        assert "No verdict changed among the pages compared" in text

    def test_no_unreachable_page_leaves_the_summary_as_it_was(self):
        text = build_markdown([], pages=8)
        assert "not reachable" not in text
        assert "No verdict changed. A byte diff" in text

    def test_a_partial_summary_still_reports_the_flips_it_did_find(self):
        unusable = [{"name": "youtube-watch-live", "url": "u", "reason": "HTTP 429"}]
        text = build_markdown([FLIP_LOST], pages=7, unusable=unusable)
        assert "| Verdict flips | 1 |" in text
        assert "`youtube-channel-live`" in text


class TestWorkflowIsSafeAndQuiet:
    """Structural pins. The YAML is the safety model, so it is read as data."""

    def _document(self):
        yaml = pytest.importorskip("yaml")
        return yaml.safe_load(WORKFLOW.read_text())

    def _steps(self):
        return self._document()["jobs"]["refresh"]["steps"]

    def test_it_holds_no_calendar_credential(self):
        document = self._document()
        assert document["permissions"] == {"contents": "read", "issues": "write"}
        assert "id-token" not in json.dumps(document["permissions"])
        for step in self._steps():
            script = step.get("run", "")
            assert "calendar_io" not in script, step.get("name")
            assert "--live" not in script, step.get("name")

    def test_it_is_weekly_and_also_runnable_by_hand(self):
        document = self._document()
        triggers = document.get("on") or document.get(True)
        assert triggers["schedule"] == [{"cron": "0 6 * * 1"}]
        assert "workflow_dispatch" in triggers

    def test_the_offline_gate_runs_before_the_network_refresh(self):
        names = [step.get("name", "") for step in self._steps()]
        gate = next(i for i, n in enumerate(names) if "corpus still classifies" in n)
        refresh = next(i for i, n in enumerate(names) if "Re-record" in n)
        assert gate < refresh, names

    def test_the_refresh_compares_against_the_committed_corpus(self):
        refresh = next(s for s in self._steps() if "Re-record" in s.get("name", ""))
        assert "--baseline tests/fixtures/pages" in refresh["run"]

    def test_the_filing_step_is_the_only_one_that_passes_file(self):
        filings = [
            step.get("name")
            for step in self._steps()
            if "--file" in step.get("run", "")
        ]
        assert len(filings) == 1, filings
        assert len([s for s in self._steps() if "--file" in s.get("run", "")]) == 1

    def test_filing_cannot_run_without_a_completed_refresh(self):
        step = next(s for s in self._steps() if "--file" in s.get("run", ""))
        assert step["if"] == "steps.refresh.outputs.refreshed == 'true'"
        assert step["env"]["GH_TOKEN"]  # required by gh

    def test_the_summary_reuses_the_renderer(self):
        step = next(
            s for s in self._steps()
            if "summary" in s.get("name", "").lower() and "run" in s
        )
        assert "--markdown" in step["run"]
        # No hand-rolled second table anywhere in the workflow.
        assert 'echo "|' not in step["run"]

    def test_a_failed_refresh_files_nothing_and_says_so(self):
        refresh = next(s for s in self._steps() if "Re-record" in s.get("name", ""))
        assert "refreshed=false" in refresh["run"]
        assert "no issue is filed" in refresh["run"]
