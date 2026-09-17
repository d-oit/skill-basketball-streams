"""Tests for scripts/gh_issue.py — the shared issue filer.

Two scheduled workflows now report a finding as an issue: the weekly
`corpus-refresh` run (a recorded page's verdict flipped) and the daily
`runtime-daily` run (a render rung parked for N consecutive days). The filer was
extracted from `corpus_flip_issue.py` rather than copied, so these tests cover
the behaviours both depend on:

* the title marker is the dedupe key, and it is a **required** argument — two
  callers must not be able to answer "is there already an issue?" with each
  other's key, which would silently merge two unrelated findings;
* an unparseable `gh` list returns `None` rather than guessing, so a `gh` format
  change cannot open a duplicate issue every week;
* the `gh` runner is injectable, so create-vs-comment is tested without GitHub.

Offline: `gh` is never invoked — the runner is a stub.
"""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from gh_issue import (  # noqa: E402
    existing_open_issue,
    file_or_comment,
    run_url,
)

MARKER = "[x] something"


class FakeGh:
    """A `gh` that records its calls and answers `issue list` from a script."""

    def __init__(self, *, list_output: str = "[]", fail: bool = False):
        self.list_output = list_output
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(args)
        if self.fail:
            raise subprocess.CalledProcessError(
                1, args, output="", stderr="gh: Not Found (HTTP 404)"
            )
        stdout = self.list_output if args[:2] == ["issue", "list"] else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


class TestExistingOpenIssue:
    def test_the_marker_finds_the_open_issue(self):
        payload = json.dumps(
            [{"number": 3, "title": "unrelated"}, {"number": 12, "title": MARKER}]
        )
        assert existing_open_issue(payload, MARKER) == 12

    def test_a_different_marker_is_not_a_match(self):
        # The bug this prevents: two features deduping against one key would
        # merge a rung report into the corpus issue and vice versa.
        payload = json.dumps([{"number": 12, "title": "[corpus] page verdict flip"}])
        assert existing_open_issue(payload, "[rungs] render rung parked") is None

    @pytest.mark.parametrize("payload", ["", "not json", "[]", None])
    def test_an_unusable_list_is_none_not_a_guess(self, payload):
        assert existing_open_issue(payload, MARKER) is None

    def test_the_marker_must_be_a_prefix_of_the_title(self):
        payload = json.dumps([{"number": 4, "title": f"prefix {MARKER}"}])
        assert existing_open_issue(payload, MARKER) is None

    def test_a_title_with_the_marker_is_returned_as_an_int(self):
        payload = json.dumps([{"number": "7", "title": MARKER}])
        assert existing_open_issue(payload, MARKER) == 7


class TestFileOrComment:
    def test_it_creates_when_nothing_is_open(self):
        gh = FakeGh()
        assert file_or_comment("t", "b", marker=MARKER, runner=gh) == "created"
        assert gh.calls[0][:2] == ["issue", "list"]
        assert gh.calls[1][:2] == ["issue", "create"]
        assert gh.calls[1][gh.calls[1].index("--title") + 1] == "t"

    def test_it_comments_on_the_open_one_instead_of_piling_up(self):
        gh = FakeGh(list_output=json.dumps([{"number": 12, "title": MARKER}]))
        assert file_or_comment("t", "b", marker=MARKER, runner=gh) == "commented on #12"
        assert gh.calls[1][:2] == ["issue", "comment"]
        assert "create" not in " ".join(gh.calls[1])

    def test_it_only_ever_lists_read_only(self):
        gh = FakeGh()
        file_or_comment("t", "b", marker=MARKER, runner=gh)
        listed = gh.calls[0]
        assert listed == [
            "issue", "list", "--state", "open", "--limit", "100",
            "--json", "number,title",
        ]

    def test_delete_and_close_are_never_used(self):
        for marker, output in ((MARKER, "[]"), (MARKER, json.dumps([{"number": 1, "title": MARKER}]))):
            gh = FakeGh(list_output=output)
            file_or_comment("t", "b", marker=marker, runner=gh)
            assert not any(
                word in call for call in gh.calls for word in ("delete", "close")
            )

    def test_a_failing_gh_raises_so_the_caller_can_be_loud(self):
        with pytest.raises(subprocess.CalledProcessError):
            file_or_comment("t", "b", marker=MARKER, runner=FakeGh(fail=True))

    def test_the_marker_is_required_and_keyword_only(self):
        # Positional would let `file_or_comment(title, body, marker)` be called
        # with the arguments swapped, and defaulting it would let a caller
        # inherit someone else's dedupe key — which silently merges two findings
        # into one issue.
        signature = inspect.signature(file_or_comment)
        parameter = signature.parameters["marker"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty

    def test_the_two_callers_use_different_markers(self):
        from corpus_flip_issue import TITLE_MARKER as corpus
        from rung_health import TITLE_MARKER as rungs

        assert corpus != rungs
        assert existing_open_issue(
            json.dumps([{"number": 1, "title": corpus}]), corpus
        ) == 1
        assert existing_open_issue(
            json.dumps([{"number": 1, "title": corpus}]), rungs
        ) is None


class TestRunUrl:
    def test_it_builds_a_link_from_the_actions_environment(self, monkeypatch):
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
        monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
        monkeypatch.setenv("GITHUB_RUN_ID", "42")
        assert run_url() == "https://github.com/o/r/actions/runs/42"

    def test_it_says_so_outside_actions(self, monkeypatch):
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
        assert run_url() == "(local run)"
