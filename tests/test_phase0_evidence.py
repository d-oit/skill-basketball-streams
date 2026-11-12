"""Tests for the Phase 0 evidence pre-read (`build_phase0_evidence`).

The daily agent is restricted to `webfetch` + `websearch`, which read neither
YouTube (boilerplate on every tab, 404s on all 7 RSS feeds — measured
2026-11-12) nor the magenta.tv SPA shell — while the runner reads both
(stdlib `urllib` for the server-rendered tab/RSS/watch pages, `patchright`
for the arena). This records both on the runner minutes before the agent
runs; the agent cites the file verbatim as its Check 6/7 proof for exactly
the URLs named there.

Offline throughout: discovery and the page render are injected fakes, and
the arena HTML is the real recorded 2026-11-11 render, not an invented page.
"""
from __future__ import annotations

import gzip
import re
from datetime import datetime, timezone
from pathlib import Path

from scripts.run_daily import build_phase0_evidence

REPO_ROOT = Path(__file__).resolve().parent.parent

NOW = datetime(2026, 11, 12, 7, 45, tzinfo=timezone.utc)


def _channels():
    return [
        {
            "handle": "enbleague",
            "channel_id": "UC4nW9T_6d6e4DKa4A9_3Ouw",
            "videos": [
                {
                    "url": "https://www.youtube.com/watch?v=4DBGImaLVE0",
                    "video_id": "4DBGImaLVE0",
                    "title": "TalTech/ALEXELA v BC Spartak Pleven |Nov 11, 2026, 6:30 PM|",
                    "published": "2026-11-09T08:00:00+00:00",
                    "scheduled_start": "2026-11-11T16:30:00+00:00",
                    "is_live_now": False,
                },
                {
                    "url": "https://www.youtube.com/watch?v=oldVOD00001",
                    "video_id": "oldVOD00001",
                    "title": "Highlights: Riga v Voluntari",
                    "published": "2026-10-01T08:00:00+00:00",
                    "scheduled_start": None,
                    "is_live_now": False,
                },
            ],
        }
    ]


def _arena_html() -> str:
    path = REPO_ROOT / "tests" / "fixtures" / "magenta_arena" / "sport-rendered.html.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return handle.read()


def _render_ok(url: str):
    html = _arena_html()
    assert url == "https://www.magenta.tv/sport"
    return (
        "KOSTENLOS & OHNE LOGIN KOSTENLOS",
        html,
        "",
    )


class TestBuildPhase0Evidence:
    def test_youtube_videos_carry_watch_proof(self):
        import scripts.run_daily as run_daily

        seen = {}

        def _discover(*, max_watch: int = 0, fetch=None):
            seen["max_watch"] = max_watch
            return _channels()

        orig_discover = run_daily._discover_youtube_tabs
        run_daily._discover_youtube_tabs = _discover
        try:
            evidence = build_phase0_evidence(
                render=lambda url: ("", "", "no renderer in this test"),
                now=NOW,
            )
        finally:
            run_daily._discover_youtube_tabs = orig_discover
        assert evidence["recorded_at"] == "2026-11-12T07:45:00+00:00"
        assert seen["max_watch"] == 6, (
            "placeholder videos need watch-page starts; IDs alone are what "
            "kept ENBL games out of the calendar"
        )
        (channel,) = evidence["youtube"]["channels"]
        assert channel["handle"] == "enbleague"
        by_id = {v["video_id"]: v for v in channel["videos"]}
        assert by_id["4DBGImaLVE0"]["scheduled_start"] == "2026-11-11T16:30:00+00:00"
        assert by_id["4DBGImaLVE0"]["is_live_now"] is False
        assert by_id["oldVOD00001"]["scheduled_start"] is None

    def test_arena_render_carries_markers_text_and_per_game_links(self):
        import scripts.run_daily as run_daily

        orig_discover = run_daily._discover_youtube_tabs
        orig_render = run_daily._render_page
        orig_gate = run_daily._patchright_importable
        run_daily._discover_youtube_tabs = lambda *, max_watch=0, fetch=None: []
        run_daily._render_page = _render_ok
        # The builder gates the render on an importable renderer (CI runners
        # have no browser); the seam under test is what happens after the gate.
        run_daily._patchright_importable = lambda: True
        try:
            evidence = build_phase0_evidence(now=NOW)
        finally:
            run_daily._discover_youtube_tabs = orig_discover
            run_daily._render_page = orig_render
            run_daily._patchright_importable = orig_gate
        arena = evidence["magenta_arena"]
        assert arena["url"] == "https://www.magenta.tv/sport"
        assert arena["rendered_at"] == "2026-11-12T07:45:00+00:00"
        assert arena["markers"]["KOSTENLOS"] >= 1
        assert "KOSTENLOS" in arena["text"]
        urls = [game["url"] for game in arena["games"]]
        assert (
            "https://www.magenta.tv/tv/live-deutschland-ungarn/03316f8e" in urls
        ), "the pre-read must name the per-game URL the agent could not find"
        assert all(url.startswith("https://www.magenta.tv/tv/") for url in urls)
        assert all(game["title"].strip() for game in arena["games"])

    def test_a_dead_render_is_a_named_error_not_an_empty_arena(self):
        import scripts.run_daily as run_daily

        orig_discover = run_daily._discover_youtube_tabs
        orig_render = run_daily._render_page
        orig_gate = run_daily._patchright_importable
        run_daily._discover_youtube_tabs = lambda *, max_watch=0, fetch=None: []
        run_daily._render_page = lambda url: ("", "", "Error: browser closed")
        run_daily._patchright_importable = lambda: True
        try:
            evidence = build_phase0_evidence(now=NOW)
        finally:
            run_daily._discover_youtube_tabs = orig_discover
            run_daily._render_page = orig_render
            run_daily._patchright_importable = orig_gate
        arena = evidence["magenta_arena"]
        assert arena["games"] == []
        assert "Error: browser closed" in arena["error"]
        assert arena["rendered_at"] is None

    def test_no_renderer_means_no_render_attempt(self):
        import scripts.run_daily as run_daily

        calls: list[str] = []
        orig_discover = run_daily._discover_youtube_tabs
        orig_render = run_daily._render_page
        orig_gate = run_daily._patchright_importable
        run_daily._discover_youtube_tabs = lambda *, max_watch=0, fetch=None: []
        run_daily._render_page = lambda url: calls.append(url) or ("", "", "")
        run_daily._patchright_importable = lambda: False
        try:
            evidence = build_phase0_evidence(now=NOW)
        finally:
            run_daily._discover_youtube_tabs = orig_discover
            run_daily._render_page = orig_render
            run_daily._patchright_importable = orig_gate
        assert calls == [], "without a renderer there is nothing to call"
        assert "patchright unavailable" in evidence["magenta_arena"]["error"]

    def test_total_youtube_failure_degrades_to_empty_channels(self):
        import scripts.run_daily as run_daily

        def _raising(*, max_watch: int = 0, fetch=None):
            raise OSError("no route to host")

        orig_discover = run_daily._discover_youtube_tabs
        orig_render = run_daily._render_page
        run_daily._discover_youtube_tabs = _raising
        run_daily._render_page = lambda url: ("", "", "no renderer in this test")
        try:
            evidence = build_phase0_evidence(now=NOW)
        finally:
            run_daily._discover_youtube_tabs = orig_discover
            run_daily._render_page = orig_render
        assert evidence["youtube"]["channels"] == []
        assert "OSError" in evidence["youtube"]["error"]


class TestPhase0EvidenceWiring:
    """The agent job pre-reads the bot-blocked surfaces and attaches them."""

    @classmethod
    def _workflow(cls) -> str:
        return (
            REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"
        ).read_text(encoding="utf-8")

    def test_the_agent_job_records_evidence_before_the_skill_runs(self):
        workflow = self._workflow()
        assert "run_daily.py --evidence-out .tmp/phase0-evidence.json" in workflow
        evidence_at = workflow.index("--evidence-out .tmp/phase0-evidence.json")
        assert workflow.index("opencode run --port") > evidence_at, (
            "the pre-read must be recorded before the agent that reads it runs"
        )

    def test_the_evidence_file_reaches_the_agent_prompt(self):
        workflow = self._workflow()
        assert "--file .tmp/phase0-evidence.json" in workflow
        lowered = workflow.lower()
        assert "attached phase0-evidence.json was recorded" in lowered
        assert "do not re-fetch these urls with webfetch" in lowered.replace(
            "(`", " "
        ).replace("`", ""), (
            "the agent must be told the recorded evidence IS the link test, "
            "or a webfetch shell reads as a failed Check 6"
        )

    def test_a_failed_pre_read_degrades_to_an_empty_file_not_a_red_run(self):
        workflow = self._workflow()
        assert "phase-0 evidence unavailable" in workflow
        assert "[ ! -f .tmp/phase0-evidence.json ]" in workflow, (
            "opencode exits `File not found` on a missing --file, so the "
            "fallback must guarantee the path exists"
        )

    def test_the_agent_gets_the_current_time_for_its_timestamps(self):
        workflow = self._workflow()
        assert 'NOW_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"' in workflow
        assert "NOW_UTC=$NOW_UTC" in workflow
        assert "Use NOW_UTC verbatim as validationTimestamp" in workflow, (
            "the agent has no clock and rounds up; the planner skips "
            "future-stamped candidates, so the exact time must be handed over"
        )

    def test_the_prompt_names_the_gitignore_trap(self):
        workflow = self._workflow()
        assert "is gitignored, so `glob` will never list this file" in workflow
        assert "treat a completed `read` as proof of presence" in workflow

    def test_help_names_the_evidence_mode(self):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "run_daily.py"),
                "--help",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--evidence-out" in result.stdout
