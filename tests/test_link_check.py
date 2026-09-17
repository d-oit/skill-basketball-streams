"""Tests for scripts/link_check.py.

The network path is exercised through monkeypatched HTTPError/URLError objects
so the classification logic (BROKEN vs BLOCKED vs ERROR) is pinned without any
real request; the CLI is covered in --dry-run mode for offline determinism.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from scripts.link_check import (
    BLOCKED,
    BROKEN,
    ERROR,
    INVALID,
    OK,
    UNREACHABLE,
    check_link,
    probe,
    structural_check,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "link_check.py"


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://example.com", code=code, msg="err", hdrs=None, fp=None
    )


class TestStructuralCheck:
    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not-a-url",
            "ftp://example.com/live",
            "http://localhost:8000/live",
            "https://www.youtube.com/channel/UCxyz123",
            "https://www.youtube.com/user/FIBA",
            "https://www.youtube.com/user/TheDBBTV",  # /user/ other than this one
        ],
    )
    def test_rejected_inputs_return_a_status(self, url):
        # /user/TheDBBTV is the one allowed legacy shape, so it is *not* INVALID.
        result = structural_check(url)
        if url.endswith("/user/TheDBBTV"):
            assert result is None
        else:
            assert result is not None
            assert result[0] == INVALID

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/@fiba/live",
            "https://www.magenta.tv/tv/live-basketball-euroleague-88213",
            "https://www.championsleague.basketball/live/tenerife-vs-bonn",
        ],
    )
    def test_usable_urls_return_none(self, url):
        assert structural_check(url) is None


class TestProbeClassification:
    def test_403_is_blocked_not_broken(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **k: (_ for _ in ()).throw(_http_error(403)),
        )
        status, reason, code = probe("https://www.magenta.tv/x")
        assert (status, code) == (BLOCKED, 403)
        assert "anti-bot" in reason

    def test_429_is_blocked(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **k: (_ for _ in ()).throw(_http_error(429)),
        )
        assert probe("https://example.com")[0] == BLOCKED

    @pytest.mark.parametrize("code", [404, 410])
    def test_dead_links_are_broken(self, monkeypatch, code):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **k: (_ for _ in ()).throw(_http_error(code)),
        )
        status, reason, _ = probe("https://example.com")
        assert status == BROKEN
        assert "quarantine" in reason

    def test_5xx_is_retryable_error(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **k: (_ for _ in ()).throw(_http_error(503)),
        )
        status, reason, _ = probe("https://example.com")
        assert status == ERROR
        assert "retry" in reason

    def test_network_failure_is_unreachable(self, monkeypatch):
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("boom")),
        )
        assert probe("https://example.com")[0] == UNREACHABLE


class TestCheckLink:
    def test_invalid_url_short_circuits(self):
        result = check_link({"url": "not-a-url", "source": "cli"})
        assert result["status"] == INVALID
        assert result["http_status"] is None

    def test_dry_run_makes_no_request(self, monkeypatch):
        def explode(*args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("dry-run must not perform network I/O")

        monkeypatch.setattr("urllib.request.urlopen", explode)
        result = check_link(
            {"url": "https://www.youtube.com/@fiba/live"}, dry_run=True
        )
        assert result["status"] == OK
        assert result["mode"] == "dry-run"

    def test_entry_metadata_is_carried_through(self):
        result = check_link(
            {"url": "https://www.youtube.com/@fiba/live", "source": "youtube",
             "event_id": "abc123"},
            dry_run=True,
        )
        assert result["source"] == "youtube"
        assert result["event_id"] == "abc123"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestCli:
    def test_no_arguments_is_usage_error(self):
        assert _run([]).returncode == 2

    def test_dry_run_ok_url_exits_zero(self):
        result = _run(
            ["--url", "https://www.youtube.com/@fiba/live", "--dry-run"]
        )
        assert result.returncode == 0
        assert "OK: link_check:" in result.stdout

    def test_dry_run_invalid_url_exits_one(self):
        result = _run(["--url", "not-a-url", "--dry-run"])
        assert result.returncode == 1
        assert "FAIL: link_check:" in result.stderr

    def test_youtube_channel_url_is_invalid(self):
        result = _run(
            ["--url", "https://www.youtube.com/channel/UCxyz123", "--dry-run"]
        )
        assert result.returncode == 1

    def test_input_file_report_and_summary(self, tmp_path):
        links = tmp_path / "links.json"
        links.write_text(
            json.dumps(
                {
                    "links": [
                        {"url": "https://www.youtube.com/@fiba/live", "source": "youtube"},
                        {"url": "https://www.magenta.tv/tv/live-x", "event_id": "e1"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        out = tmp_path / "report.json"
        result = _run(
            [
                "--input", str(links), "--dry-run",
                "--checked-at", "2026-09-14T08:00:00Z",
                "--out", str(out),
            ]
        )
        assert result.returncode == 0
        assert f"OK: link_check: wrote report to {out}" in result.stdout
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["checked_at"] == "2026-09-14T08:00:00Z"
        assert report["mode"] == "dry-run"
        assert report["summary"][OK] == 2
        assert len(report["results"]) == 2

    def test_missing_input_file_is_usage_error(self):
        result = _run(["--input", "nope.json", "--dry-run"])
        assert result.returncode == 2

    def test_empty_link_list_exits_one(self, tmp_path):
        empty = tmp_path / "empty.json"
        empty.write_text("[]", encoding="utf-8")
        result = _run(["--input", str(empty), "--dry-run"])
        assert result.returncode == 1
        assert "no links checked" in result.stdout

    def test_json_flag_prints_report(self):
        result = _run(
            ["--url", "https://www.youtube.com/@fiba/live", "--dry-run", "--json"]
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["results"][0]["status"] == OK
