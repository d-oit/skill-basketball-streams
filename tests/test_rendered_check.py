"""Tests for scripts/rendered_check.py and the pre-create gate in calendar_io.

A dead stream page returned HTTP 200 with a "Page not found" body, so the
status check alone let it through. These tests pin the rendered-text check,
the fail-closed refusals, and that the gate runs only on live creates.
"""

from __future__ import annotations

from scripts import calendar_io as cio
from scripts import rendered_check as rc

DEAD = "https://sporteurope.tv/en/sc-jena/nbbl-old-match"
LIVE = "https://sporteurope.tv/en/sc-jena/nbbl-new-match"


def _row(url: str) -> dict:
    return {
        "game_key": "g1",
        "action": cio.ACTION_CREATE,
        "league": "NBBL",
        "teams": ["Jena", "Braunschweig"],
        "start": "2026-10-24T13:45:00+02:00",
        "direct_links": [{"source": "SporteEurope", "url": url}],
    }


class TestDeadPageReason:
    def test_page_not_found_text_is_refused(self):
        assert rc.dead_page_reason("Page not found\n\nGo home") is not None

    def test_match_text_is_accepted(self):
        assert rc.dead_page_reason("SC Jena vs Braunschweig · Live 13:45") is None

    def test_empty_render_is_refused(self):
        assert rc.dead_page_reason("   ") is not None

    def test_phrase_match_ignores_case_and_line_breaks(self):
        assert rc.dead_page_reason("PAGE\nNOT   FOUND") is not None


class TestRejectReason:
    def test_refuses_a_row_whose_link_renders_dead(self):
        reason = rc.reject_reason(_row(DEAD), render=lambda url: "rendered page says 'page not found'")
        assert reason is not None
        assert DEAD in reason

    def test_accepts_a_row_whose_link_renders_fine(self):
        assert rc.reject_reason(_row(LIVE), render=lambda url: None) is None

    def test_a_row_with_no_links_is_not_refused_by_the_gate(self):
        row = {"game_key": "g1", "action": cio.ACTION_CREATE}
        assert rc.reject_reason(row, render=lambda url: "should not be called") is None


class TestRenderFailsClosed:
    def test_missing_patchright_refuses_the_link(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_patchright(name, *args, **kwargs):
            if name.startswith("patchright"):
                raise ImportError("no patchright")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_patchright)
        reason = rc.render_reason(LIVE)
        assert reason is not None
        assert "patchright" in reason


class TestApplyPlanGate:
    def _stub_tools(self, monkeypatch, calls):
        def fake_execute(slug, arguments, **kwargs):
            calls.append(slug)
            return {"id": "new-id"}

        monkeypatch.setattr(cio, "_execute_tool", fake_execute)

    def test_live_create_is_refused_when_the_gate_refuses(self, monkeypatch):
        calls: list = []
        self._stub_tools(monkeypatch, calls)
        result = cio.apply_plan(
            [_row(DEAD)],
            api_key="k",
            scope={"user_id": "u"},
            calendar_id="cal",
            live=True,
            link_gate=lambda row: "dead link",
        )
        assert calls == []
        assert result["created"] == 0
        assert result["failed"] == [{"game_key": "g1", "reason": "dead link"}]

    def test_live_create_proceeds_when_the_gate_accepts(self, monkeypatch):
        calls: list = []
        self._stub_tools(monkeypatch, calls)
        result = cio.apply_plan(
            [_row(LIVE)],
            api_key="k",
            scope={"user_id": "u"},
            calendar_id="cal",
            live=True,
            link_gate=lambda row: None,
        )
        assert calls == [cio.TOOL_CREATE_EVENT]
        assert result["created"] == 1

    def test_dry_run_never_consults_the_gate(self):
        def must_not_run(row):
            raise AssertionError("the gate ran in a dry run")

        result = cio.apply_plan([_row(DEAD)], live=False, link_gate=must_not_run)
        assert result["dry_run"] is True
        assert result["created"] == 1
