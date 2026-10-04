"""The idempotency contract: a matching event that changed is written, and one
that did not is not.

The requirement this pins is "always check if already exists and change if
anything changes". The old planner answered only half of it. `events_match`
decides *identity* — is this the same game? — and on a match the planner emitted
`update` unconditionally. So every UNVERIFIED event was re-`PATCH`ed on every
run, whether or not anything about it had changed, and `event_ledger` recorded an
`action: "update"` row claiming the calendar had changed. The comparison that was
missing is *equality*: `calendar_io.body_matches_event` compares the full payload
the calendar would end up holding (title, start, end, description, colour)
against the stored event, and a match that changes nothing now emits `unchanged`.

Two things make these tests worth more than a hand-built dict:

* the per-field cases are fixtures, so the decision is asserted against a
  documented input rather than an inline literal that supplies a field the
  pipeline never writes; and
* the round-trip case builds the stored event through the **real** writer
  (`plan_upsert` -> `build_event_body` + `description_for`) and the **real**
  reader (`parse_event`), which is the pair whose disagreement this whole change
  exists to close. A hand-built "identical" event would prove the test, not the
  round trip.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import calendar_io
from scripts.calendar_io import (
    TOOL_PATCH_EVENT,
    ACTION_UNCHANGED,
    apply_plan,
    body_matches_event,
    build_event_body,
    description_for,
    parse_event,
)
from scripts.candidates import outcome_rows
from scripts.event_ledger import build_rows
from scripts.upsert_events import (
    ACTION_CREATE,
    ACTION_UPDATE,
    plan_upsert,
    summarise,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Every fixture's documented outcome, so a fixture that drifts fails here rather
# than in the CLI where it would read as a planner bug.
FIXTURE_ACTIONS = {
    "upsert_unchanged_identical.json": ACTION_UNCHANGED,
    "upsert_unchanged_title.json": ACTION_UPDATE,
    "upsert_unchanged_description.json": ACTION_UPDATE,
    "upsert_unchanged_colour.json": ACTION_UPDATE,
    "upsert_unchanged_end.json": ACTION_UPDATE,
    "upsert_unchanged_legacy_description.json": ACTION_UPDATE,
    "upsert_unchanged_promotion.json": ACTION_UPDATE,
}


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _plan(name: str) -> list[dict]:
    payload = _fixture(name)
    return plan_upsert(payload["events"], payload["candidates"])


def _candidate(**overrides) -> dict:
    row = {
        "league": "BBL",
        "teams": ["ALBA Berlin", "FC Bayern Muenchen"],
        "start": "2026-10-05T18:00:00Z",
        "end": "2026-10-05T20:30:00Z",
        "state": "UNVERIFIED",
        "summary": "BBL: ALBA Berlin vs FC Bayern Muenchen",
        "league_color_id": "6",
    }
    row.update(overrides)
    return row


def _round_trip(candidate: dict, *, event_id: str = "evt-roundtrip") -> dict:
    """The stored event a real run leaves behind, via the real writer and reader.

    A create row goes through `build_event_body` + `description_for` — the exact
    functions `apply_plan` calls — and the result is fed back through
    `parse_event`, which is what the next run's `existing.json` contains. Nothing
    here is hand-built, so the assertion is about the pair rather than about this
    file's idea of the pair.
    """
    first = plan_upsert([], [candidate])
    assert [row["action"] for row in first] == [ACTION_CREATE]
    body = build_event_body(
        first[0], description=description_for(first[0]), visibility="public"
    )
    raw = {
        "id": event_id,
        "summary": body["summary"],
        "description": body["description"],
        "start": body["start"],
        "end": body["end"],
        "colorId": body["colorId"],
        "status": "confirmed",
    }
    return parse_event(raw)


class TestFixtureDecisions:
    @pytest.mark.parametrize("name,action", sorted(FIXTURE_ACTIONS.items()))
    def test_the_fixture_decides_the_documented_action(self, name, action):
        plan = _plan(name)
        assert [row["action"] for row in plan] == [action]

    def test_every_unchanged_fixture_says_why_it_sends_nothing(self):
        plan = _plan("upsert_unchanged_identical.json")
        assert "nothing to send" in plan[0]["reason"]

    def test_an_update_fixture_is_not_reported_as_unchanged(self):
        """The control: if `body_matches_event` matched everything, the case
        above would pass for the wrong reason."""
        changed = _plan("upsert_unchanged_description.json")
        assert changed[0]["action"] == ACTION_UPDATE


class TestTheRoundTripThroughTheRealFunctions:
    """The class of bug a hand-built fixture cannot catch.

    The stored side is produced by `build_event_body` + `description_for` and read
    back by `parse_event`; if any of the three disagreed about a field, the next
    run would see a spurious change (or miss a real one) forever.
    """

    def test_a_written_event_reads_back_as_unchanged(self):
        candidate = _candidate()
        stored = _round_trip(candidate)
        plan = plan_upsert([stored], [candidate])
        assert [row["action"] for row in plan] == [ACTION_UNCHANGED]

    def test_it_is_unchanged_on_the_second_and_third_run_too(self):
        """Idempotence is a property of the *pair*, not of one hop: the second
        run must not decide `update` and then the third decide `unchanged`
        because the second wrote a different description."""
        candidate = _candidate()
        calendar = [_round_trip(candidate)]
        for _ in range(3):
            plan = plan_upsert(calendar, [candidate])
            assert [row["action"] for row in plan] == [ACTION_UNCHANGED]
            # Nothing is written, so the stored event does not move between runs.
            assert plan[0]["start"] == calendar[0]["start"]

    def test_a_new_link_reads_back_as_a_change(self):
        candidate = _candidate()
        stored = _round_trip(candidate)
        changed = {
            **candidate,
            "directLinks": [
                {"source": "bbl", "url": "https://www.basketball-bundesliga.de/live"}
            ],
        }
        plan = plan_upsert([stored], [changed])
        assert [row["action"] for row in plan] == [ACTION_UPDATE]

    def test_a_source_reannouncing_a_game_later_does_not_move_the_event(self):
        """The preserved rule: the stored event's own start wins, so a source
        that re-announces the same game ten minutes later must not silently move
        an event subscribers already have. With the start held, nothing changes,
        so the plan is `unchanged` rather than an update that rewrites the slot."""
        candidate = _candidate()
        stored = _round_trip(candidate)
        later = {**candidate, "start": "2026-10-05T18:10:00Z"}
        plan = plan_upsert([stored], [later])
        assert plan[0]["action"] == ACTION_UNCHANGED
        assert plan[0]["start"] == stored["start"]

    def test_the_planned_body_is_what_the_comparison_compared(self):
        """`plan_upsert` and `apply_plan` must build the body the same way."""
        candidate = _candidate()
        stored = _round_trip(candidate)
        row = plan_upsert([stored], [candidate])[0]
        body = build_event_body(row, description=description_for(row))
        assert body_matches_event(body, stored) is True


class TestBodyMatchesEventRefusesBlanks:
    def test_a_blank_stored_description_is_not_a_match(self):
        body = {
            "summary": "x",
            "start": {"dateTime": "2026-10-05T18:00:00Z"},
            "end": {"dateTime": "2026-10-05T20:30:00Z"},
            "colorId": "5",
            "description": "League: BBL\n",
        }
        stored = {
            "summary": "x",
            "start": "2026-10-05T18:00:00Z",
            "end": "2026-10-05T20:30:00Z",
            "league_color_id": "5",
            "description": "",
        }
        assert body_matches_event(body, stored) is False

    def test_a_blank_stored_time_is_not_a_match(self):
        body = {
            "summary": "x",
            "start": {"dateTime": "2026-10-05T18:00:00Z"},
            "end": {"dateTime": "2026-10-05T20:30:00Z"},
            "colorId": "5",
            "description": "",
        }
        stored = {
            "summary": "x",
            "start": "",
            "end": "2026-10-05T20:30:00Z",
            "league_color_id": "5",
            "description": "",
        }
        assert body_matches_event(body, stored) is False

    def test_the_same_instant_in_a_different_spelling_is_a_match(self):
        """Google may echo an offset the source did not spell; comparing strings
        would report a change on every run and re-introduce the wasted PATCH."""
        body = {
            "summary": "x",
            "start": {"dateTime": "2026-10-05T20:00:00+02:00"},
            "end": {"dateTime": "2026-10-05T22:30:00+02:00"},
            "colorId": "5",
            "description": "",
        }
        stored = {
            "summary": "x",
            "start": "2026-10-05T18:00:00Z",
            "end": "2026-10-05T20:30:00Z",
            "league_color_id": "5",
            "description": "",
        }
        assert body_matches_event(body, stored) is True


class TestApplyPlanSendsNothing:
    def test_an_unchanged_row_never_reaches_the_transport(self, monkeypatch):
        calls: list = []

        def _boom(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("an unchanged row must not issue a request")

        monkeypatch.setattr(calendar_io, "_execute_tool", _boom)
        plan = _plan("upsert_unchanged_identical.json")
        result = apply_plan(
            plan, api_key="k", scope={"user_id": "u"}, calendar_id="cal", live=True
        )
        assert calls == []
        assert result["unchanged"] == 1
        assert (result["created"], result["updated"], result["skipped"]) == (0, 0, 0)
        assert result["failed"] == []
        # Nothing was sent, so there is no written action for a ledger to record.
        assert result["actions"] == []

    def test_a_changed_description_does_issue_exactly_one_patch(self, monkeypatch):
        """The other half: `unchanged` must not be a blanket no-op. A fixture
        that differs issues exactly the one write it needs, which is what makes
        the zero above meaningful."""
        calls: list = []

        def _fake(tool, arguments, *, api_key, scope):
            calls.append(tool)
            return {"id": str(arguments.get("event_id") or "")}

        monkeypatch.setattr(calendar_io, "_execute_tool", _fake)
        plan = _plan("upsert_unchanged_description.json")
        result = apply_plan(
            plan, api_key="k", scope={"user_id": "u"}, calendar_id="cal", live=True
        )
        assert calls == [TOOL_PATCH_EVENT]
        assert (result["updated"], result["unchanged"]) == (1, 0)
        assert [item["action"] for item in result["actions"]] == [ACTION_UPDATE]

    def test_the_result_shape_stays_additive(self):
        result = apply_plan([], live=False)
        for key in ("dry_run", "created", "updated", "skipped", "failed", "actions"):
            assert key in result
        assert result["unchanged"] == 0


class TestTheConsumersOfTheNewAction:
    def test_summarise_reports_unchanged_even_when_it_is_zero(self):
        assert summarise([]) == {
            "create": 0,
            "update": 0,
            "unchanged": 0,
            "skip": 0,
        }

    def test_summarise_counts_an_unchanged_plan(self):
        assert summarise(_plan("upsert_unchanged_identical.json"))["unchanged"] == 1

    def test_an_unchanged_run_records_no_ledger_row(self):
        """Deliberate: the event already has a row from the run that wrote it, and
        `events.jsonl` is keyed by `event_id`, so Phase 3 can still resolve it. A
        fresh row every quiet day would claim a write that did not happen."""
        plan = _plan("upsert_unchanged_identical.json")
        applied = {
            "dry_run": False,
            "created": 0,
            "updated": 0,
            "unchanged": 1,
            "skipped": 0,
            "failed": [],
            "actions": [],
        }
        rows, refusals, reason = build_rows(plan, applied, run_id="r")
        assert rows == []
        assert refusals == []
        assert "unchanged" in reason

    def test_an_unchanged_action_in_the_result_is_never_a_written_row(self):
        plan = _plan("upsert_unchanged_identical.json")
        applied = {
            "dry_run": False,
            "actions": [
                {
                    "action": ACTION_UNCHANGED,
                    "game_key": plan[0]["game_key"],
                    "event_id": plan[0]["event_id"],
                }
            ],
        }
        rows, refusals, _ = build_rows(plan, applied, run_id="r")
        assert rows == []
        assert refusals == []

    def test_an_unchanged_game_is_a_duplicate_not_a_retry(self):
        """Scoring it `unverifiable` would put an on-calendar game back in the
        retry queue and count it as unreached — the same class of error as
        scoring a condemned game as captured."""
        plan = _plan("upsert_unchanged_identical.json")
        rows = outcome_rows(plan, [])
        assert [row["disposition"] for row in rows] == ["skipped_duplicate"]
