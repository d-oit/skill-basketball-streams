"""Tests for scripts/youtube_live.py.

Pure-function coverage for the live-only / future-only gate (the part that must
never be delegated to an LLM), plus subprocess coverage for the CLI contract.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.youtube_live import (
    DEFAULT_ALLOWED_HANDLES,
    handle_of,
    is_allowed_handle,
    load_allowed_handles,
    DECISION_LIVE,
    DECISION_REJECT,
    DECISION_SCHEDULED,
    LIVE_SEARCH_SP,
    build_api_live_search_url,
    build_live_search_url,
    classify_stream,
    filter_candidates,
    is_youtube_live_url,
    parse_iso,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "youtube_live.py"
NOW = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)


class TestApprovedHandles:
    """A URL-shape check is not a channel check.

    `@FIBAWorld/live` is shaped exactly like `@fiba/live` and does not exist on
    YouTube. Eval case 2 and `references/lessons-learned.md` had documented the
    rule for months while no gate enforced it, which is why the allow-list is
    tested here rather than trusted to prose.
    """

    def test_the_module_default_matches_config_sources(self):
        """The fallback constant and the config file must not drift.

        `classify_stream` runs without touching the filesystem, so the list is
        duplicated in code on purpose. This test is what makes that safe.
        """
        from_config = load_allowed_handles(REPO_ROOT)
        assert from_config, "config/sources.json declares no YouTube handles"
        assert set(from_config) == set(DEFAULT_ALLOWED_HANDLES)

    def test_missing_config_falls_back_to_the_default(self, tmp_path):
        assert load_allowed_handles(tmp_path) == DEFAULT_ALLOWED_HANDLES

    def test_config_without_handles_falls_back(self, tmp_path):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "sources.json").write_text(
            '{"sources": []}', encoding="utf-8"
        )
        assert load_allowed_handles(tmp_path) == DEFAULT_ALLOWED_HANDLES

    def test_handle_extraction(self):
        assert handle_of("https://www.youtube.com/@EuroLeague/live") == "euroleague"
        assert handle_of("https://youtube.com/@fiba") == "fiba"
        # These carry no handle and must not be rejected by this check.
        assert handle_of("https://www.youtube.com/live/abc123") == ""
        assert handle_of("https://www.youtube.com/watch?v=abc123") == ""

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/@fiba/live",
            "https://www.youtube.com/@EuroLeague/live",
            "https://www.youtube.com/@bbl_basketball/live",
            "https://www.youtube.com/@BasketballCL/live",
            "https://www.youtube.com/live/abc123",
            "https://www.youtube.com/watch?v=abc123",
        ],
    )
    def test_approved_and_handleless_urls_pass(self, url):
        assert is_allowed_handle(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/@FIBAWorld/live",
            "https://www.youtube.com/@SomeFanChannel/live",
            "https://www.youtube.com/@NBA/live",
        ],
    )
    def test_unapproved_channels_fail(self, url):
        assert not is_allowed_handle(url)

    def test_case_is_insensitive(self):
        assert is_allowed_handle("https://www.youtube.com/@EUROLEAGUE/live")

    def test_an_explicit_allow_list_overrides_the_default(self):
        assert is_allowed_handle(
            "https://www.youtube.com/@FIBAWorld/live", allowed=("fibaworld",)
        )


class TestChannelGate:
    def _candidate(self, url: str) -> dict:
        return {
            "url": url,
            "live_broadcast_content": "upcoming",
            "is_live_content": True,
            "scheduled_start": "2026-09-16T20:00:00+02:00",
        }

    def test_fibaworld_is_rejected_even_with_a_perfect_payload(self):
        decision, reason = classify_stream(
            self._candidate("https://www.youtube.com/@FIBAWorld/live"), NOW
        )
        assert decision == DECISION_REJECT
        assert "allow-list" in reason
        assert "@fibaworld" in reason

    def test_the_reason_names_the_approved_handles(self):
        _, reason = classify_stream(
            self._candidate("https://www.youtube.com/@FIBAWorld/live"), NOW
        )
        assert "@fiba" in reason and "@EuroLeague".lower() in reason.lower()

    def test_an_approved_channel_still_promotes(self):
        decision, _ = classify_stream(
            self._candidate("https://www.youtube.com/@EuroLeague/live"), NOW
        )
        assert decision == DECISION_SCHEDULED

    def test_the_channel_check_runs_before_the_payload_gates(self):
        # A VOD from an unapproved channel reports the channel, because the
        # channel is the static fact and the one a fix must act on.
        decision, reason = classify_stream(
            {
                "url": "https://www.youtube.com/@FIBAWorld/live",
                "live_broadcast_content": "none",
            },
            NOW,
        )
        assert decision == DECISION_REJECT
        assert "allow-list" in reason


class TestParseIso:
    def test_zulu_suffix_is_aware_utc(self):
        parsed = parse_iso("2026-09-14T09:00:00Z")
        assert parsed == NOW and parsed.tzinfo is not None

    def test_naive_input_is_assumed_utc(self):
        parsed = parse_iso("2026-09-14T09:00:00")
        assert parsed == NOW

    @pytest.mark.parametrize("value", [None, "", "   ", "not-a-date", 42, {}])
    def test_unparseable_returns_none(self, value):
        assert parse_iso(value) is None


class TestIsYoutubeLiveUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/@EuroLeague/live",
            "https://youtube.com/@fiba/live",
            "https://www.youtube.com/live/xyz789",
            "https://www.youtube.com/watch?v=abc123",
            "https://www.youtube.com/watch?list=PL1&v=abc123",
        ],
    )
    def test_accepted_shapes(self, url):
        assert is_youtube_live_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "https://www.youtube.com/channel/UCxyz123",
            "https://www.youtube.com/user/FIBA",
            "https://www.youtube.com/c/SomeChannel",
            "https://www.youtube.com/playlist?list=PL1",
            "https://www.youtube.com/results?search_query=x",
            "https://example.com/live",
        ],
    )
    def test_rejected_shapes(self, url):
        assert is_youtube_live_url(url) is False

    def test_theDBBTV_legacy_url_still_rejected_by_this_gate(self):
        # /user/TheDBBTV is allowed by the source allow-list, but it is a channel
        # page and never a direct live URL, so the live gate rejects it.
        assert is_youtube_live_url("https://www.youtube.com/user/TheDBBTV") is False


class TestClassifyStream:
    def test_upcoming_with_future_start_is_scheduled(self):
        decision, reason = classify_stream(
            {
                "url": "https://www.youtube.com/@EuroLeague/live",
                "live_broadcast_content": "upcoming",
                "scheduled_start": "2026-09-16T20:30:00+02:00",
            },
            now=NOW,
        )
        assert decision == DECISION_SCHEDULED
        assert "greater than now" in reason

    def test_live_now_is_promoted(self):
        decision, _ = classify_stream(
            {
                "url": "https://www.youtube.com/@fiba/live",
                "is_live_now": True,
                "actual_start": "2026-09-14T08:55:00Z",
            },
            now=NOW,
        )
        assert decision == DECISION_LIVE

    def test_regular_upload_is_rejected(self):
        decision, reason = classify_stream(
            {
                "url": "https://www.youtube.com/watch?v=abc123",
                "live_broadcast_content": "none",
                "duration_seconds": 6420,
            },
            now=NOW,
        )
        assert decision == DECISION_REJECT
        assert "regular video upload" in reason

    def test_ended_broadcast_is_rejected(self):
        decision, reason = classify_stream(
            {
                "url": "https://www.youtube.com/live/xyz789",
                "is_live_content": True,
                "actual_end": "2026-09-13T22:05:00Z",
            },
            now=NOW,
        )
        assert decision == DECISION_REJECT
        assert "already ended" in reason

    def test_fixed_duration_while_not_live_is_rejected(self):
        decision, reason = classify_stream(
            {
                "url": "https://www.youtube.com/watch?v=abc123",
                "is_live_content": True,
                "duration_seconds": 5400,
            },
            now=NOW,
        )
        assert decision == DECISION_REJECT
        assert "fixed duration" in reason

    def test_past_scheduled_start_is_rejected(self):
        decision, reason = classify_stream(
            {
                "url": "https://www.youtube.com/@fiba/live",
                "live_broadcast_content": "upcoming",
                "scheduled_start": "2026-09-14T07:00:00Z",
            },
            now=NOW,
        )
        assert decision == DECISION_REJECT
        assert "not greater than now" in reason

    def test_missing_scheduled_start_is_rejected(self):
        decision, reason = classify_stream(
            {
                "url": "https://www.youtube.com/@fiba/live",
                "live_broadcast_content": "upcoming",
            },
            now=NOW,
        )
        assert decision == DECISION_REJECT
        assert "no scheduled start" in reason

    def test_inconsistent_future_actual_start_is_rejected(self):
        decision, reason = classify_stream(
            {
                "url": "https://www.youtube.com/@fiba/live",
                "is_live_now": True,
                "actual_start": "2026-09-20T10:00:00Z",
            },
            now=NOW,
        )
        assert decision == DECISION_REJECT
        assert "inconsistent payload" in reason

    @pytest.mark.parametrize(
        "candidate",
        [
            {},
            {"url": ""},
            {"url": "https://www.youtube.com/channel/UCxyz"},
        ],
    )
    def test_missing_or_rejected_url(self, candidate):
        decision, _ = classify_stream(candidate, now=NOW)
        assert decision == DECISION_REJECT


class TestFilterCandidates:
    def test_keeps_only_promotable_and_annotates(self):
        candidates = [
            {
                "url": "https://www.youtube.com/@EuroLeague/live",
                "live_broadcast_content": "upcoming",
                "scheduled_start": "2026-09-16T20:30:00+02:00",
            },
            {"url": "https://www.youtube.com/watch?v=abc", "live_broadcast_content": "none"},
            {"url": "https://www.youtube.com/live/ended", "actual_end": "2026-09-01T00:00:00Z"},
        ]
        promoted = filter_candidates(candidates, now=NOW)
        assert len(promoted) == 1
        assert promoted[0]["_decision"] == DECISION_SCHEDULED
        assert "_reason" in promoted[0]

    def test_non_dict_entries_are_ignored(self):
        assert filter_candidates([None, "x", 7], now=NOW) == []


class TestUrlBuilders:
    def test_live_search_url_carries_live_filter(self):
        url = build_live_search_url("EuroLeague live")
        assert url.startswith("https://www.youtube.com/results?search_query=")
        assert f"sp={LIVE_SEARCH_SP}" in url
        assert "EuroLeague+live" in url

    def test_api_url_is_live_only(self):
        url = build_api_live_search_url("BBL live", api_key="KEY")
        assert "eventType=live" in url
        assert "type=video" in url
        assert "key=KEY" in url


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestCli:
    def test_print_urls_exits_zero(self):
        result = _run(["--print-urls", "EuroLeague live"])
        assert result.returncode == 0
        assert "OK: html:" in result.stdout
        assert "OK: api:" in result.stdout

    def test_promotable_fixture_exits_zero(self, tmp_path):
        fixture = tmp_path / "c.json"
        fixture.write_text(
            json.dumps(
                [
                    {
                        "url": "https://www.youtube.com/@fiba/live",
                        "live_broadcast_content": "upcoming",
                        "scheduled_start": "2026-09-16T20:30:00+02:00",
                    }
                ]
            ),
            encoding="utf-8",
        )
        result = _run(
            ["--input", str(fixture), "--now", "2026-09-14T09:00:00Z"]
        )
        assert result.returncode == 0
        assert "OK: youtube_live:" in result.stdout

    def test_all_rejected_exits_one(self, tmp_path):
        fixture = tmp_path / "c.json"
        fixture.write_text(
            json.dumps(
                [{"url": "https://www.youtube.com/watch?v=a", "live_broadcast_content": "none"}]
            ),
            encoding="utf-8",
        )
        result = _run(
            ["--input", str(fixture), "--now", "2026-09-14T09:00:00Z"]
        )
        assert result.returncode == 1
        assert "FAIL: youtube_live:" in result.stderr

    def test_missing_input_file_is_usage_error(self):
        result = _run(["--input", "does-not-exist.json"])
        assert result.returncode == 2

    def test_malformed_json_is_usage_error(self, tmp_path):
        fixture = tmp_path / "bad.json"
        fixture.write_text("{not json", encoding="utf-8")
        result = _run(["--input", str(fixture)])
        assert result.returncode == 2

    def test_bad_now_is_usage_error(self, tmp_path):
        fixture = tmp_path / "c.json"
        fixture.write_text("[]", encoding="utf-8")
        result = _run(["--input", str(fixture), "--now", "yesterday"])
        assert result.returncode == 2

    def test_json_mode_emits_report(self, tmp_path):
        fixture = tmp_path / "c.json"
        fixture.write_text(
            json.dumps(
                [
                    {
                        "url": "https://www.youtube.com/@fiba/live",
                        "live_broadcast_content": "upcoming",
                        "scheduled_start": "2026-09-16T20:30:00+02:00",
                    }
                ]
            ),
            encoding="utf-8",
        )
        result = _run(
            ["--input", str(fixture), "--now", "2026-09-14T09:00:00Z", "--json"]
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["now"] == "2026-09-14T09:00:00+00:00"
        assert len(payload["promoted"]) == 1
