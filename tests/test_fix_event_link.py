"""Tests for scripts/fix_event_link.py, the one-off dead-link repair.

The repair writes to a public calendar, so these tests pin the refusals as
carefully as the write: it must not change an event it cannot identify, and
must not write without `--live`.
"""

from __future__ import annotations

from datetime import date

import pytest

from scripts import fix_event_link as fel

EVENT_ID = "dspqr1tt7fvlpq5vvcbbdtb1lk"
OLD = "https://www.sporteurope.tv/en/video/nbbl-science-city-jena-vs-sg-junior-loewen-braunschweig"
NEW = "https://sporteurope.tv/en/sc-jena/nbbl-science-city-jena-vs-sg-junior-loewen-braunschweig"
ENV = {
    "BASKETBALL_CALENDAR_ID": "cal@example.com",
    "COMPOSIO_API_KEY": "key",
    "COMPOSIO_USER_ID": "user",
}


def _event(description: str) -> dict:
    return {
        "event_id": EVENT_ID,
        "summary": "[UNVERIFIED] NBBL",
        "description": description,
        "start": "2026-10-24T13:45:00+02:00",
    }


class TestPureHelpers:
    def test_replace_link_swaps_the_old_url(self):
        assert fel.replace_link(f"Link: {OLD}\n", OLD, NEW) == f"Link: {NEW}\n"

    def test_replace_link_refuses_when_old_url_is_absent(self):
        assert fel.replace_link("Teams: A vs B", OLD, NEW) is None

    def test_replace_link_refuses_an_empty_old_url(self):
        assert fel.replace_link("anything", "", NEW) is None

    def test_day_window_covers_the_day_in_utc(self):
        assert fel.day_window(date(2026, 10, 24)) == (
            "2026-10-23T00:00:00Z",
            "2026-10-26T00:00:00Z",
        )

    def test_find_event_matches_by_id_only(self):
        events = [_event("x")]
        assert fel.find_event(events, EVENT_ID) is events[0]
        assert fel.find_event(events, "other") is None


def _run(monkeypatch, argv, listings, patches=None):
    """Run `main` with the calendar stubbed: `listings` is one list per call."""
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    calls = {"list": 0, "patch": []}

    def fake_list(api_key, scope, calendar_id, *, time_min, time_max, **_):
        calls["list"] += 1
        return listings[min(calls["list"] - 1, len(listings) - 1)]

    def fake_execute(slug, arguments, *, api_key, scope, **_):
        calls["patch"].append((slug, arguments))
        return {}

    monkeypatch.setattr(fel.cio, "list_events", fake_list)
    monkeypatch.setattr(fel.cio, "_execute_tool", fake_execute)
    base = ["--event-id", EVENT_ID, "--old-url", OLD, "--new-url", NEW, "--day", "2026-10-24"]
    code = fel.main(base + argv)
    return code, calls


class TestMain:
    def test_dry_run_writes_nothing(self, monkeypatch, capsys):
        code, calls = _run(monkeypatch, [], [[_event(f"Link: {OLD}")]])
        assert code == 0
        assert calls["patch"] == []
        assert "DRY RUN" in capsys.readouterr().out

    def test_refuses_when_the_event_is_not_in_the_window(self, monkeypatch, capsys):
        code, calls = _run(monkeypatch, ["--live"], [[]])
        assert code == 1
        assert calls["patch"] == []
        assert "not found" in capsys.readouterr().err

    def test_refuses_when_the_old_link_is_not_in_the_description(self, monkeypatch, capsys):
        code, calls = _run(monkeypatch, ["--live"], [[_event("Teams: A vs B")]])
        assert code == 1
        assert calls["patch"] == []
        assert "refusing to write" in capsys.readouterr().err

    def test_refuses_a_non_https_new_url(self, monkeypatch):
        for key, value in ENV.items():
            monkeypatch.setenv(key, value)
        code = fel.main(
            ["--event-id", EVENT_ID, "--old-url", OLD, "--new-url", "http://x", "--day", "2026-10-24"]
        )
        assert code == 1

    def test_live_patches_only_the_description_and_reads_back(self, monkeypatch, capsys):
        before = _event(f"Link: {OLD}")
        after = _event(f"Link: {NEW}")
        code, calls = _run(monkeypatch, ["--live"], [[before], [after]])
        assert code == 0
        assert len(calls["patch"]) == 1
        slug, arguments = calls["patch"][0]
        assert slug == fel.cio.TOOL_PATCH_EVENT
        assert arguments == {
            "calendar_id": "cal@example.com",
            "event_id": EVENT_ID,
            "description": f"Link: {NEW}",
            "send_updates": "none",
        }
        assert "OK: fix_event_link" in capsys.readouterr().out

    def test_fails_when_the_patch_changed_the_title_or_start(self, monkeypatch, capsys):
        before = _event(f"Link: {OLD}")
        moved = dict(_event(f"Link: {NEW}"), start="2026-10-25T13:45:00+02:00")
        code, _ = _run(monkeypatch, ["--live"], [[before], [moved]])
        assert code == 1
        assert "title or start changed" in capsys.readouterr().err

    def test_fails_when_the_read_back_still_shows_the_old_link(self, monkeypatch, capsys):
        before = _event(f"Link: {OLD}")
        code, calls = _run(monkeypatch, ["--live"], [[before], [before]])
        assert code == 1
        assert len(calls["patch"]) == 1
        assert "read-back" in capsys.readouterr().err

    def test_missing_credentials_refuse_before_any_call(self, monkeypatch):
        monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
        monkeypatch.delenv("COMPOSIO_USER_ID", raising=False)
        monkeypatch.setenv("BASKETBALL_CALENDAR_ID", "cal@example.com")
        code = fel.main(
            ["--event-id", EVENT_ID, "--old-url", OLD, "--new-url", NEW, "--day", "2026-10-24"]
        )
        assert code == 1


@pytest.mark.parametrize("bad", ["2026-13-01", "tomorrow"])
def test_rejects_an_invalid_day(bad):
    with pytest.raises(SystemExit):
        fel.main(
            ["--event-id", EVENT_ID, "--old-url", OLD, "--new-url", NEW, "--day", bad]
        )
