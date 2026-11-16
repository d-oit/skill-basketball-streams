"""Search-to-calendar contract: every valid search is used, and the same game is
never created twice.

Two properties the daily runtime depends on:

1. **Every candidate is accounted for.** ``plan_upsert`` returns exactly one
   plan row per candidate that reaches it (a ``create``/``update``/``skip`` /
   ``unchanged`` row with an explicit reason). A candidate must never vanish
   silently between the skill's search and the calendar write.

2. **A re-run creates nothing.** Once the ``create`` rows of a plan are treated
   as stored events, planning the same candidates again must yield zero
   ``create`` rows for the same games (``skip``/``unchanged`` instead). This is
   what "never create the same calendar event again when it already exists"
   means in code.
"""
from __future__ import annotations

from datetime import datetime, timezone

from scripts.upsert_events import (
    ACTION_CREATE,
    ACTION_SKIP,
    plan_upsert,
)


def _candidate(**overrides) -> dict:
    row = {
        "league": "BBL",
        "teams": ["ALBA Berlin", "FC Bayern Muenchen"],
        "start": "2026-09-16T19:00:00+02:00",
        "state": "UNVERIFIED",
        "summary": "BBL: ALBA Berlin vs FC Bayern Muenchen",
        "directLink": "https://stream.example/live/game",
        "sourceReference": "https://official.example/schedule/game",
        "access": "Free",
        "validationTimestamp": "2026-09-15T08:00:00Z",
        "validationNotes": "Official event page confirms free live viewing.",
        "evidence": {"free_confirmed": True, "live_confirmed": True},
    }
    row.update(overrides)
    return row


NOW = datetime(2026, 9, 15, 8, 30, tzinfo=timezone.utc)


def _stored_from_create_row(row: dict, event_id: str) -> dict:
    """The stored event the next run would list for a just-created row."""
    return {
        "event_id": event_id,
        "game_key": row["game_key"],
        "league": row.get("league", ""),
        "teams": list(row.get("teams") or []),
        "start": row.get("start", ""),
        "state": row.get("state", "UNVERIFIED"),
        "summary": row.get("title", ""),
    }


class TestEverySearchIsUsed:
    def test_every_candidate_gets_exactly_one_plan_row(self):
        candidates = [
            _candidate(),
            _candidate(
                league="Basketball Champions League",
                teams=["Lenovo Tenerife", "Telekom Baskets Bonn"],
                start="2026-09-17T20:00:00+02:00",
                summary="BCL: Lenovo Tenerife vs Telekom Baskets Bonn",
            ),
        ]
        plan = plan_upsert([], candidates, now=NOW, window_days=10)
        assert len(plan) == len(candidates)
        for row in plan:
            assert row["action"] in ("create", "update", "skip", "unchanged")
            assert row.get("reason"), "every row must say why"

    def test_no_valid_candidate_is_silently_dropped(self):
        candidates = [_candidate()]
        plan = plan_upsert([], candidates, now=NOW, window_days=10)
        assert [r["action"] for r in plan] == [ACTION_CREATE]


class TestNeverCreateTheSameEventTwice:
    def test_second_run_over_same_window_creates_nothing(self):
        candidates = [_candidate()]
        first = plan_upsert([], candidates, now=NOW, window_days=10)
        assert [r["action"] for r in first] == [ACTION_CREATE]
        stored = [_stored_from_create_row(first[0], "evt-1")]
        second = plan_upsert(stored, candidates, now=NOW, window_days=10)
        assert [r["action"] for r in second] != [ACTION_CREATE]
        assert all(r["action"] != ACTION_CREATE for r in second)

    def test_verified_match_is_skipped_not_recreated(self):
        candidates = [_candidate(state="VERIFIED")]
        stored = [
            {
                "event_id": "evt-verified",
                "league": "BBL",
                "teams": ["ALBA Berlin", "FC Bayern Muenchen"],
                "start": "2026-09-16T17:00:00Z",
                "state": "VERIFIED",
                "summary": "BBL: ALBA Berlin vs FC Bayern Muenchen",
            }
        ]
        plan = plan_upsert(stored, candidates, now=NOW, window_days=10)
        assert [r["action"] for r in plan] == [ACTION_SKIP]


class TestApplyTimeSecondNet:
    """`calendar_io.apply_plan(existing=...)` demotes a stale `create` to
    `skip` before any HTTP, so a plan made from an old snapshot can never
    duplicate a public event at write time."""

    def test_stale_create_against_existing_snapshot_sends_nothing(self, monkeypatch):
        from scripts import calendar_io

        calls: list = []

        def _boom(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("a duplicate create must not issue a request")

        monkeypatch.setattr(calendar_io, "_execute_tool", _boom)
        stored = [
            {
                "event_id": "evt-1",
                "game_key": "BBL|ALBA Berlin|FC Bayern Muenchen|2026-09-16T17:00Z",
                "league": "BBL",
                "teams": ["ALBA Berlin", "FC Bayern Muenchen"],
                "start": "2026-09-16T17:00:00Z",
                "state": "UNVERIFIED",
                "summary": "BBL: ALBA Berlin vs FC Bayern Muenchen",
            }
        ]
        # A hand-built stale plan: the planner would have skipped this, but
        # the guard must hold even when the plan was made without the snapshot.
        stale_plan = [
            {
                "action": "create",
                "game_key": "BBL|ALBA Berlin|FC Bayern Muenchen|2026-09-16T17:00Z",
                "league": "BBL",
                "teams": ["ALBA Berlin", "FC Bayern Muenchen"],
                "start": "2026-09-16T17:00:00Z",
                "state": "UNVERIFIED",
                "title": "BBL: ALBA Berlin vs FC Bayern Muenchen",
                "color_id": "5",
            }
        ]
        result = calendar_io.apply_plan(
            stale_plan, calendar_id="cal", live=True, existing=stored
        )
        assert calls == []
        assert result["created"] == 0
        assert result["skipped"] == 1
        assert result["failed"] == []
        assert result["actions"] == []

    def test_genuinely_new_create_still_writes(self, monkeypatch):
        from scripts import calendar_io

        calls: list = []

        def _fake(tool, arguments, *, api_key, scope):
            calls.append(tool)
            return {"id": "evt-new"}

        monkeypatch.setattr(calendar_io, "_execute_tool", _fake)
        result = calendar_io.apply_plan(
            [
                {
                    "action": "create",
                    "game_key": "BBL|A|B|2026-09-16T17:00Z",
                    "league": "BBL",
                    "teams": ["A", "B"],
                    "start": "2026-09-16T17:00:00Z",
                    "state": "UNVERIFIED",
                    "title": "BBL: A vs B",
                    "color_id": "5",
                }
            ],
            calendar_id="cal",
            live=True,
            existing=[],
        )
        assert result["created"] == 1
        # A create is two calls: CREATE_EVENT plus the follow-up colour PATCH
        # (CREATE ignores color_id). What matters is that a genuinely new game
        # still writes, while the duplicate case above sends nothing.
        assert calls == [calendar_io.TOOL_CREATE_EVENT, calendar_io.TOOL_PATCH_EVENT]
