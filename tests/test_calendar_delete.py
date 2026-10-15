"""Tests for the `delete` action in `scripts/calendar_io.apply_plan`.

Three properties are pinned:

1. A dry run sends no HTTP request, and reports the delete it would make.
2. A live delete calls the Composio delete tool with the calendar and event id.
3. A delete row without an event id is refused rather than sent with no target.

The transport (`_execute_tool`) is monkeypatched; no test touches the network.
"""
from __future__ import annotations

import pytest

from scripts import calendar_io
from scripts.calendar_io import (
    ACTION_DELETE,
    CalendarError,
    TOOL_DELETE_EVENT,
    apply_plan,
)


def _delete_row(**overrides) -> dict:
    row = {"action": ACTION_DELETE, "game_key": "BBL|A|B|2026-09-16T17:00Z", "event_id": "evt-9"}
    row.update(overrides)
    return row


def _boom(*args, **kwargs):  # pragma: no cover - must not be called
    raise AssertionError("a dry run must not make an HTTP request")


def test_dry_run_sends_nothing_and_counts_the_delete(monkeypatch):
    monkeypatch.setattr(calendar_io, "_execute_tool", _boom)
    result = apply_plan([_delete_row()], live=False, calendar_id="cal")
    assert result["deleted"] == 1
    assert result["failed"] == []
    assert result["actions"][0]["event_id"] == "evt-9"


def test_live_delete_calls_the_delete_tool(monkeypatch):
    calls: list = []

    def fake(slug, arguments, *, api_key, scope, timeout=30):
        calls.append({"slug": slug, "arguments": arguments})
        return {}

    monkeypatch.setattr(calendar_io, "_execute_tool", fake)
    result = apply_plan(
        [_delete_row()], live=True, api_key="k", scope={"user_id": "u"}, calendar_id="cal"
    )
    assert calls == [
        {
            "slug": TOOL_DELETE_EVENT,
            "arguments": {"calendar_id": "cal", "event_id": "evt-9", "send_updates": "none"},
        }
    ]
    assert result["deleted"] == 1
    assert result["failed"] == []


def test_live_delete_failure_is_recorded_not_counted(monkeypatch):
    def fake(slug, arguments, *, api_key, scope, timeout=30):
        raise CalendarError(404, "not found")

    monkeypatch.setattr(calendar_io, "_execute_tool", fake)
    result = apply_plan([_delete_row()], live=True, calendar_id="cal")
    assert result["deleted"] == 0
    assert result["failed"][0]["status"] == 404
    assert result["actions"] == []


def test_delete_without_event_id_is_refused(monkeypatch):
    monkeypatch.setattr(calendar_io, "_execute_tool", _boom)
    result = apply_plan([_delete_row(event_id="")], live=True, calendar_id="cal")
    assert result["deleted"] == 0
    assert result["failed"][0]["reason"] == "delete row without an event_id"


@pytest.mark.parametrize("live", [False, True])
def test_delete_does_not_touch_create_or_update_counters(monkeypatch, live):
    def fake(*args, **kwargs):
        return {}

    monkeypatch.setattr(calendar_io, "_execute_tool", fake)
    result = apply_plan([_delete_row()], live=live, calendar_id="cal")
    assert result["created"] == 0
    assert result["updated"] == 0
