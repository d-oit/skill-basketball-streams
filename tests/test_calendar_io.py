"""Tests for scripts/calendar_io.py.

Three properties here are load-bearing and are tested as such:

1. **A dry run performs no HTTP at all.** Not "skips the write" — makes no
   request. The dry-run tests replace the transport with one that raises, so any
   leak is a failure rather than a comment.
2. **The read path tells the truth about state.** `list` must recover
   `VERIFIED` / `UNVERIFIED` / `WRONG` from the title prefix, because that is the
   only thing standing between the planner and rewriting an event a human
   already confirmed.
3. **The Composio quirks are encoded, not remembered.** The transport changed from
   the Calendar API to Composio's tool-execution endpoint, and four of the new
   behaviours only exist because they were observed against the live API:
   `CREATE_EVENT` ignores `color_id`, adds a Meet link and adds the connected user
   as an attendee unless told not to, and nests its result one level deeper than
   `EVENTS_LIST` does. Each is pinned below, because each is invisible in a
   passing run.

No test touches the network: the transport (`_execute_tool`) is monkeypatched,
which is the single function this module uses to reach Composio.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import calendar_io
from scripts.calendar_io import (
    ACTION_CREATE,
    ACTION_SKIP,
    ACTION_UPDATE,
    TOOL_CREATE_EVENT,
    TOOL_EVENTS_LIST,
    TOOL_PATCH_EVENT,
    CalendarError,
    _payload,
    _window,
    apply_plan,
    build_event_body,
    create_arguments,
    description_for,
    league_from_description,
    list_events,
    parse_event,
    patch_arguments,
    scope_arguments,
    teams_from_description,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "calendar_io.py"
# The account every live-path call is scoped to, as the arguments dict Composio
# wants. A `user_id` and a `connected_account_id` go in *different fields*, so
# tests pass the same shape the CLI builds rather than a bare string.
SCOPE = {"user_id": "user-1"}
API_KEY = "test-key"


def _api_event(**overrides) -> dict:
    raw = {
        "id": "evt-1",
        "summary": "BBL: ALBA Berlin vs FC Bayern Muenchen",
        "colorId": "6",
        "status": "confirmed",
        "start": {"dateTime": "2026-09-16T19:00:00+02:00"},
        "end": {"dateTime": "2026-09-16T21:30:00+02:00"},
        "description": "League: BBL\nTeams: ALBA Berlin vs FC Bayern Muenchen\n",
    }
    raw.update(overrides)
    return raw


def _plan_row(**overrides) -> dict:
    row = {
        "action": ACTION_CREATE,
        "game_key": "BBL|A|B|2026-09-16T17:00Z",
        "event_id": "",
        "state": "UNVERIFIED",
        "title": "[UNVERIFIED] BBL: A vs B",
        "color_id": "5",
        "start": "2026-09-16T19:00:00+02:00",
        "end": "",
    }
    row.update(overrides)
    return row


class TestTeamsFromDescription:
    def test_reads_the_teams_line(self):
        assert teams_from_description(
            "League: BBL\nTeams: ALBA Berlin vs FC Bayern Muenchen\nAccess: free"
        ) == ["ALBA Berlin", "FC Bayern Muenchen"]

    @pytest.mark.parametrize(
        "text",
        ["", None, "Teams: only one club", "Teams: a vs b vs c", "no teams line"],
    )
    def test_unusable_lines_yield_nothing(self, text):
        assert teams_from_description(text) == []

    def test_case_insensitive_key(self):
        assert teams_from_description("teams: A vs B") == ["A", "B"]


class TestDescriptionFor:
    """The `League:`/`Teams:` block, and why its absence was a real defect.

    `list_events` reads the `Teams:` line back out of a stored event and
    `upsert_events.events_match` matches on it. Nothing ever wrote one, so every
    event the runtime created carried `description: ""` — and because
    `events_match` skips the teams check whenever either side is empty, a stored
    event matched **any** candidate inside the 30-minute window.
    """

    def test_it_writes_both_documented_lines(self):
        assert description_for(
            {"league": "BBL", "teams": ["ALBA Berlin", "FC Bayern"]}
        ) == "League: BBL\nTeams: ALBA Berlin vs FC Bayern\n"

    def test_a_league_only_row_still_gets_its_line(self):
        assert description_for({"league": "BCL"}) == "League: BCL\n"

    def test_a_row_with_nothing_to_say_produces_nothing(self):
        assert description_for({}) == ""

    def test_three_teams_are_not_a_pairing(self):
        """A `Teams:` line the reader cannot parse back is worse than none."""
        assert description_for({"league": "BBL", "teams": ["A", "B", "C"]}) == (
            "League: BBL\n"
        )

    def test_it_round_trips_through_the_reader(self):
        row = {"league": "BCL", "teams": ["Tenerife", "Bonn"]}
        text = description_for(row)
        assert teams_from_description(text) == ["Tenerife", "Bonn"]
        assert league_from_description(text) == "BCL"

    def test_the_league_reader_is_symmetric(self):
        assert league_from_description("League: EuroLeague\n") == "EuroLeague"
        assert league_from_description("no league line") == ""


class TestParseEvent:
    def test_full_event(self):
        event = parse_event(_api_event())
        assert event["event_id"] == "evt-1"
        assert event["state"] == "VERIFIED"
        assert event["teams"] == ["ALBA Berlin", "FC Bayern Muenchen"]
        assert event["league_color_id"] == "6"

    def test_state_is_recovered_from_the_prefix(self):
        assert parse_event(_api_event(summary="[UNVERIFIED] BBL: A vs B"))["state"] == (
            "UNVERIFIED"
        )
        assert parse_event(_api_event(summary="[WRONG] BBL: A vs B"))["state"] == "WRONG"

    def test_a_pre_state_machine_event_reads_as_verified(self):
        # Events created before the state machine existed carry no prefix and
        # were treated as confirmed. Reading them as UNVERIFIED would repaint a
        # calendar full of amber on the first run.
        assert parse_event(_api_event())["state"] == "VERIFIED"

    def test_all_day_event_uses_date(self):
        event = parse_event(
            _api_event(start={"date": "2026-09-16"}, end={"date": "2026-09-17"})
        )
        assert event["start"] == "2026-09-16"
        assert event["end"] == "2026-09-17"

    def test_missing_fields_do_not_raise(self):
        event = parse_event({})
        assert event["event_id"] == ""
        assert event["start"] == ""
        assert event["state"] == "VERIFIED"

    def test_non_dict_is_rejected(self):
        with pytest.raises(ValueError):
            parse_event("junk")  # type: ignore[arg-type]


class TestBuildEventBody:
    def test_missing_end_is_filled_from_the_default_duration(self):
        body = build_event_body(_plan_row())
        assert body["end"]["dateTime"] == "2026-09-16T21:30:00+02:00"

    def test_explicit_end_is_kept(self):
        body = build_event_body(_plan_row(end="2026-09-16T23:00:00+02:00"))
        assert body["end"]["dateTime"] == "2026-09-16T23:00:00+02:00"

    def test_summary_and_colour_come_from_the_plan_verbatim(self):
        body = build_event_body(_plan_row())
        assert body["summary"] == "[UNVERIFIED] BBL: A vs B"
        assert body["colorId"] == "5"

    def test_timezone_is_applied(self):
        assert build_event_body(_plan_row())["start"]["timeZone"] == "Europe/Berlin"

    def test_no_start_leaves_no_end(self):
        body = build_event_body(_plan_row(start=""))
        assert body["start"]["dateTime"] == ""
        assert body["end"]["dateTime"] == ""


class TestApplyPlanDryRun:
    def _boom(self, *args, **kwargs):
        raise AssertionError("dry run must not make an HTTP request")

    def test_dry_run_makes_no_http_request(self, monkeypatch):
        monkeypatch.setattr(calendar_io, "_execute_tool", self._boom)
        result = apply_plan([_plan_row()], live=False)
        assert result["dry_run"] is True
        assert result["created"] == 1

    def test_skip_rows_send_nothing(self, monkeypatch):
        monkeypatch.setattr(calendar_io, "_execute_tool", self._boom)
        result = apply_plan([_plan_row(action=ACTION_SKIP)], live=False)
        assert result["skipped"] == 1
        assert result["actions"] == []
        assert result["created"] == 0

    def test_unknown_action_is_reported_not_written(self, monkeypatch):
        monkeypatch.setattr(calendar_io, "_execute_tool", self._boom)
        result = apply_plan([_plan_row(action="demolish")], live=False)
        assert len(result["failed"]) == 1
        assert "unknown action" in result["failed"][0]["reason"]

    def test_non_dict_rows_are_ignored(self, monkeypatch):
        monkeypatch.setattr(calendar_io, "_execute_tool", self._boom)
        assert apply_plan(["junk", None], live=False)["actions"] == []

    def test_update_rows_are_counted_separately(self, monkeypatch):
        monkeypatch.setattr(calendar_io, "_execute_tool", self._boom)
        result = apply_plan(
            [_plan_row(action=ACTION_UPDATE, event_id="evt-1")], live=False
        )
        assert result["updated"] == 1
        assert result["created"] == 0

    def test_descriptions_are_attached_by_game_key(self, monkeypatch):
        monkeypatch.setattr(calendar_io, "_execute_tool", self._boom)
        result = apply_plan(
            [_plan_row()],
            live=False,
            descriptions={_plan_row()["game_key"]: "League: BBL"},
        )
        assert result["actions"][0]["body"]["description"] == "League: BBL"


class TestTheWrittenEventCanBeMatchedBack:
    """The round trip that was broken: write an event, then find it next run."""

    def _boom(self, *args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("a dry run must not make an HTTP request")

    def test_the_body_carries_the_teams_the_matcher_uses(self, monkeypatch):
        monkeypatch.setattr(calendar_io, "_execute_tool", self._boom)
        result = apply_plan(
            [_plan_row(league="BBL", teams=["ALBA Berlin", "FC Bayern"])],
            live=False,
        )
        body = result["actions"][0]["body"]
        stored = parse_event({**_api_event(), "description": body["description"]})
        assert stored["teams"] == ["ALBA Berlin", "FC Bayern"]
        assert stored["league"] == "BBL"

    def test_an_empty_description_would_match_anything(self):
        """The defect, stated: no Teams line means the check is skipped."""
        assert parse_event(_api_event(description=""))["teams"] == []
        assert parse_event(_api_event(description=""))["league"] == ""

    def test_an_explicit_description_still_wins(self, monkeypatch):
        monkeypatch.setattr(calendar_io, "_execute_tool", self._boom)
        result = apply_plan(
            [_plan_row()],
            live=False,
            descriptions={_plan_row()["game_key"]: "League: BBL\n"},
        )
        assert result["actions"][0]["body"]["description"] == "League: BBL\n"

    def test_a_row_with_no_league_or_teams_gets_no_description(self, monkeypatch):
        monkeypatch.setattr(calendar_io, "_execute_tool", self._boom)
        result = apply_plan(
            [_plan_row(league="", teams=[])], live=False
        )
        assert result["actions"][0]["body"]["description"] == ""


class TestApplyPlanLive:
    def _recorder(self, calls: list, payload: dict | None = None):
        """Stand in for one Composio tool execution, recording how it was called."""

        def fake(slug, arguments, *, api_key, scope, timeout=30):
            calls.append(
                {
                    "slug": slug,
                    "arguments": arguments,
                    "api_key": api_key,
                    "scope": scope,
                }
            )
            return {"id": "new-1"} if payload is None else payload

        return fake

    def test_create_calls_the_create_tool(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(calendar_io, "_execute_tool", self._recorder(calls))
        result = apply_plan(
            [_plan_row()],
            api_key=API_KEY,
            scope=SCOPE,
            calendar_id="cal@example.com",
            live=True,
        )
        assert result["created"] == 1
        assert calls[0]["slug"] == TOOL_CREATE_EVENT
        assert calls[0]["arguments"]["summary"] == "[UNVERIFIED] BBL: A vs B"
        assert calls[0]["arguments"]["calendar_id"] == "cal@example.com"
        assert calls[0]["api_key"] == API_KEY
        assert calls[0]["scope"] == SCOPE

    def test_create_uses_the_tools_own_parameter_names(self, monkeypatch):
        """`start_datetime`, not the `start: {dateTime: ...}` object the API wanted."""
        calls: list = []
        monkeypatch.setattr(calendar_io, "_execute_tool", self._recorder(calls))
        apply_plan(
            [_plan_row()], api_key=API_KEY, scope=SCOPE, calendar_id="cal", live=True
        )
        arguments = calls[0]["arguments"]
        assert arguments["start_datetime"] == "2026-09-16T19:00:00+02:00"
        assert arguments["end_datetime"]
        assert "start" not in arguments

    def test_the_colour_is_a_second_call_because_create_ignores_it(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(calendar_io, "_execute_tool", self._recorder(calls))
        result = apply_plan(
            [_plan_row()], api_key=API_KEY, scope=SCOPE, calendar_id="cal", live=True
        )
        assert [call["slug"] for call in calls] == [TOOL_CREATE_EVENT, TOOL_PATCH_EVENT]
        assert calls[1]["arguments"] == {
            "calendar_id": "cal",
            "event_id": "new-1",
            "color_id": "5",
            "send_updates": "none",
        }
        assert result["failed"] == []

    def test_a_row_with_no_colour_makes_one_call(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(calendar_io, "_execute_tool", self._recorder(calls))
        apply_plan(
            [_plan_row(color_id="")],
            api_key=API_KEY,
            scope=SCOPE,
            calendar_id="cal",
            live=True,
        )
        assert [call["slug"] for call in calls] == [TOOL_CREATE_EVENT]

    def test_a_failed_colour_patch_is_reported_and_the_event_still_recorded(self, monkeypatch):
        """The event exists in the wrong colour: the audit needs its id, the run must fail."""

        def flaky(slug, arguments, *, api_key, scope, timeout=30):
            if slug == TOOL_PATCH_EVENT:
                raise CalendarError(403, "insufficient authentication scopes")
            return {"id": "new-1"}

        monkeypatch.setattr(calendar_io, "_execute_tool", flaky)
        result = apply_plan(
            [_plan_row()], api_key=API_KEY, scope=SCOPE, calendar_id="cal", live=True
        )
        assert result["created"] == 1
        assert result["actions"][0]["event_id"] == "new-1"
        assert result["failed"][0]["status"] == 403
        assert "colour" in result["failed"][0]["reason"]

    def test_update_patches_the_event(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(calendar_io, "_execute_tool", self._recorder(calls))
        apply_plan(
            [_plan_row(action=ACTION_UPDATE, event_id="evt-9")],
            api_key=API_KEY,
            scope=SCOPE,
            calendar_id="cal",
            live=True,
        )
        assert calls[0]["slug"] == TOOL_PATCH_EVENT
        assert calls[0]["arguments"]["event_id"] == "evt-9"

    def test_update_without_an_event_id_fails_loudly(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(calendar_io, "_execute_tool", self._recorder(calls))
        result = apply_plan(
            [_plan_row(action=ACTION_UPDATE, event_id="")],
            api_key=API_KEY,
            scope=SCOPE,
            calendar_id="cal",
            live=True,
        )
        assert calls == []
        assert "without an event_id" in result["failed"][0]["reason"]

    def test_api_error_is_recorded_with_its_status(self, monkeypatch):
        def failing(slug, arguments, *, api_key, scope, timeout=30):
            raise CalendarError(403, "insufficient scope")

        monkeypatch.setattr(calendar_io, "_execute_tool", failing)
        result = apply_plan(
            [_plan_row()], api_key=API_KEY, scope=SCOPE, calendar_id="cal", live=True
        )
        assert result["created"] == 0
        assert result["failed"][0]["status"] == 403

    def test_one_failure_does_not_stop_the_rest(self, monkeypatch):
        calls: list = []

        def flaky(slug, arguments, *, api_key, scope, timeout=30):
            calls.append(slug)
            if len(calls) == 1:
                raise CalendarError(500, "backend error")
            return {"id": "new-2"}

        monkeypatch.setattr(calendar_io, "_execute_tool", flaky)
        result = apply_plan(
            [_plan_row(color_id=""), _plan_row(game_key="G2", color_id="")],
            api_key=API_KEY,
            scope=SCOPE,
            calendar_id="cal",
            live=True,
        )
        assert result["created"] == 1
        assert len(result["failed"]) == 1
        assert len(calls) == 2


class TestListEvents:
    def test_paginates(self, monkeypatch):
        pages = [
            {"items": [_api_event(id="a")], "nextPageToken": "p2"},
            {"items": [_api_event(id="b")]},
        ]
        seen: list[dict] = []

        def fake(slug, arguments, *, api_key, scope, timeout=30):
            assert slug == TOOL_EVENTS_LIST
            seen.append(arguments)
            return pages[len(seen) - 1]

        monkeypatch.setattr(calendar_io, "_execute_tool", fake)
        events = list_events(API_KEY, SCOPE, "cal", time_min="A", time_max="B")
        assert [event["event_id"] for event in events] == ["a", "b"]
        assert "pageToken" not in seen[0]
        assert seen[1]["pageToken"] == "p2"

    def test_the_window_and_calendar_reach_the_tool(self, monkeypatch):
        """This is the assertion that outlives a toolkit-version regression.

        Composio's guide records that older pinned Google Calendar toolkit
        versions can drop or remap `timeMin` / `timeMax` before Google sees them.
        A dropped filter is not an error — it silently returns the wrong window,
        which is how a duplicate event gets planned.
        """
        seen: list[dict] = []

        def fake(slug, arguments, *, api_key, scope, timeout=30):
            seen.append(arguments)
            return {"items": []}

        monkeypatch.setattr(calendar_io, "_execute_tool", fake)
        list_events(API_KEY, SCOPE, "cal@example.com", time_min="A", time_max="B")
        assert seen[0]["calendarId"] == "cal@example.com"
        assert seen[0]["timeMin"] == "A"
        assert seen[0]["timeMax"] == "B"

    def test_filters_are_json_booleans(self, monkeypatch):
        """Typed JSON, not the `"true"` / `"false"` strings the direct API wanted."""
        seen: list[dict] = []

        def fake(slug, arguments, *, api_key, scope, timeout=30):
            seen.append(arguments)
            return {"items": []}

        monkeypatch.setattr(calendar_io, "_execute_tool", fake)
        list_events(API_KEY, SCOPE, "cal", time_min="A", time_max="B")
        assert seen[0]["singleEvents"] is True
        assert seen[0]["showDeleted"] is False

    def test_skips_non_dict_items(self, monkeypatch):
        monkeypatch.setattr(
            calendar_io,
            "_execute_tool",
            lambda *a, **k: {"items": ["junk", _api_event()]},
        )
        assert len(list_events(API_KEY, SCOPE, "cal", time_min="A", time_max="B")) == 1

    def test_a_tool_refusal_surfaces_rather_than_looking_empty(self, monkeypatch):
        """An empty calendar and a rejected call must not read the same."""

        def refusing(*args, **kwargs):
            raise CalendarError(0, "no connected account for user_id user-1")

        monkeypatch.setattr(calendar_io, "_execute_tool", refusing)
        with pytest.raises(CalendarError) as excinfo:
            list_events(API_KEY, SCOPE, "cal", time_min="A", time_max="B")
        assert "no connected account" in str(excinfo.value)

    def test_http_error_propagates_as_calendar_error(self, monkeypatch):
        import urllib.error

        def failing(*args, **kwargs):
            raise urllib.error.HTTPError("u", 401, "nope", None, None)

        monkeypatch.setattr(calendar_io.urllib.request, "urlopen", failing)
        with pytest.raises(CalendarError) as excinfo:
            list_events(API_KEY, SCOPE, "cal", time_min="A", time_max="B")
        assert excinfo.value.status == 401


class TestWindow:
    def test_covers_today_plus_seven_days_inclusive(self):
        time_min, time_max = _window(7, now=datetime(2026, 9, 14, 8, 30, tzinfo=timezone.utc))
        assert time_min == "2026-09-14T00:00:00Z"
        assert time_max == "2026-09-22T00:00:00Z"

    def test_start_is_midnight(self):
        time_min, _ = _window(3, now=datetime(2026, 9, 14, 23, 59, tzinfo=timezone.utc))
        assert time_min.endswith("T00:00:00Z")


def _run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the CLI with a fixed environment.

    The environment is replaced, not extended, so a developer's real
    `COMPOSIO_API_KEY` cannot make a credential test pass (or a write reach the
    live API). `PATH` is kept because the interpreter is launched by name.
    """
    base = {"PATH": "/usr/bin", "BASKETBALL_CALENDAR_ID": "cal@example.com"}
    base.update(env or {})
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=base,
    )


CREDENTIALS = {"COMPOSIO_API_KEY": "k", "COMPOSIO_USER_ID": "u"}


class TestCli:
    def test_an_unknown_visibility_is_a_usage_error(self, tmp_path):
        """Refused here, not forwarded: the API's rejection names its own enum."""
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"plan": [_plan_row()]}), encoding="utf-8")
        result = _run(["apply", "--plan", str(plan), "--visibility", "unlisted"])
        assert result.returncode == 2
        assert "is not one of" in result.stderr
        assert "confidential" in result.stderr

    def test_an_explicit_visibility_overrides_the_config(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"plan": [_plan_row()]}), encoding="utf-8")
        result = _run(
            ["apply", "--plan", str(plan), "--visibility", "private", "--json"]
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["actions"][0]["body"]["visibility"] == "private"

    def test_the_default_visibility_comes_from_the_config_file(self, tmp_path):
        """Offline, credential-free, and it is the config's value that is applied."""
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"plan": [_plan_row()]}), encoding="utf-8")
        result = _run(["apply", "--plan", str(plan), "--json"])
        assert result.returncode == 0, result.stderr
        configured = json.loads(
            (REPO_ROOT / "config" / "calendar.json").read_text(encoding="utf-8")
        )["visibility"]
        body = json.loads(result.stdout)["actions"][0]["body"]
        assert body["visibility"] == configured == "public"

    def test_dry_run_apply_needs_no_credential(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"plan": [_plan_row()]}), encoding="utf-8")
        result = _run(["apply", "--plan", str(plan)])
        assert result.returncode == 0, result.stderr
        assert "dry_run=True" in result.stdout
        assert "WOULD create" in result.stdout

    def test_live_apply_without_an_api_key_is_a_usage_error(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"plan": [_plan_row()]}), encoding="utf-8")
        result = _run(["apply", "--plan", str(plan), "--live"], {"COMPOSIO_USER_ID": "u"})
        assert result.returncode == 2
        assert "no Composio API key" in result.stderr

    def test_live_apply_without_an_account_is_a_usage_error(self, tmp_path):
        """A key alone is not a credential: a call must be scoped to an account."""
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"plan": [_plan_row()]}), encoding="utf-8")
        result = _run(["apply", "--plan", str(plan), "--live"], {"COMPOSIO_API_KEY": "k"})
        assert result.returncode == 2
        assert "no account to act as" in result.stderr

    def test_list_without_an_api_key_is_a_usage_error(self):
        result = _run(["list"])
        assert result.returncode == 2
        assert "no Composio API key" in result.stderr

    def test_both_scopes_at_once_is_a_usage_error(self, tmp_path):
        """The request would be ambiguous about whose calendar a write belongs to."""
        result = _run(
            ["list"],
            {**CREDENTIALS, "COMPOSIO_CONNECTED_ACCOUNT_ID": "ca_1"},
        )
        assert result.returncode == 2
        assert "not both" in result.stderr

    def test_missing_calendar_id_is_a_usage_error(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"plan": []}), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "apply", "--plan", str(plan)],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin"},
        )
        assert result.returncode == 2
        assert "no calendar id" in result.stderr

    def test_apply_requires_a_plan(self):
        assert _run(["apply"]).returncode == 2

    def test_missing_plan_file_is_a_usage_error(self, tmp_path):
        assert _run(["apply", "--plan", str(tmp_path / "nope.json")]).returncode == 2

    def test_plan_must_be_a_list(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"plan": "nope"}), encoding="utf-8")
        assert _run(["apply", "--plan", str(plan)]).returncode == 2

    def test_a_plan_with_nothing_to_do_exits_one(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text(
            json.dumps({"plan": [_plan_row(action=ACTION_SKIP)]}), encoding="utf-8"
        )
        result = _run(["apply", "--plan", str(plan)])
        assert result.returncode == 1
        assert "nothing to create or update" in result.stderr

    def test_bad_now_is_a_usage_error(self):
        assert _run(["list", "--now", "soon"]).returncode == 2

    def test_dry_run_json_is_parseable(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"plan": [_plan_row()]}), encoding="utf-8")
        result = _run(["apply", "--plan", str(plan), "--json"])
        assert result.returncode == 0
        assert json.loads(result.stdout)["created"] == 1

    def test_out_writes_the_applied_result(self, tmp_path):
        """Phase 3 keys its verdicts to the ids in this file."""
        plan = tmp_path / "plan.json"
        plan.write_text(json.dumps({"plan": [_plan_row()]}), encoding="utf-8")
        out = tmp_path / "applied.json"
        result = _run(["apply", "--plan", str(plan), "--out", str(out)])
        assert result.returncode == 0, result.stderr
        payload = json.loads(out.read_text())
        assert payload["dry_run"] is True
        assert payload["actions"][0]["game_key"] == _plan_row()["game_key"]


class TestAppliedEventIds:
    """The id the tool returns is the only key Phase 3 can resolve a verdict on.

    It used to be discarded — the transport returned the parsed body and the
    caller threw it away — so even with a ledger writer there would have been
    nothing to key `events.jsonl` by, and the audit could never act on a verdict.
    """

    def _recorder(self, calls: list, payload: dict):
        def fake(slug, arguments, *, api_key, scope, timeout=30):
            calls.append({"slug": slug, "arguments": arguments})
            return payload

        return fake

    def test_a_live_create_records_the_returned_id(self, monkeypatch):
        monkeypatch.setattr(
            calendar_io,
            "_execute_tool",
            self._recorder([], {"id": "api-12345"}),
        )
        result = apply_plan(
            [_plan_row(color_id="")],
            api_key=API_KEY,
            scope=SCOPE,
            calendar_id="cal",
            live=True,
        )
        assert result["actions"][0]["event_id"] == "api-12345"

    def test_a_live_create_without_an_id_is_a_failure_not_a_create(self, monkeypatch):
        """A returned event with no id exists somewhere and cannot be audited."""
        monkeypatch.setattr(calendar_io, "_execute_tool", self._recorder([], {}))
        result = apply_plan(
            [_plan_row(color_id="")],
            api_key=API_KEY,
            scope=SCOPE,
            calendar_id="cal",
            live=True,
        )
        assert result["created"] == 0
        assert result["actions"] == []
        assert "no event id" in result["failed"][0]["reason"]

    def test_a_live_update_records_the_id_from_the_response(self, monkeypatch):
        monkeypatch.setattr(
            calendar_io,
            "_execute_tool",
            self._recorder([], {"id": "api-12345"}),
        )
        result = apply_plan(
            [_plan_row(action=ACTION_UPDATE, event_id="evt-9")],
            api_key=API_KEY,
            scope=SCOPE,
            calendar_id="cal",
            live=True,
        )
        assert result["actions"][0]["event_id"] == "api-12345"

    def test_an_empty_response_falls_back_to_the_plans_own_id(self, monkeypatch):
        monkeypatch.setattr(calendar_io, "_execute_tool", self._recorder([], {}))
        result = apply_plan(
            [_plan_row(action=ACTION_UPDATE, event_id="evt-9")],
            api_key=API_KEY,
            scope=SCOPE,
            calendar_id="cal",
            live=True,
        )
        assert result["actions"][0]["event_id"] == "evt-9"

    def test_a_failed_write_records_no_action(self, monkeypatch):
        def failing(slug, arguments, *, api_key, scope, timeout=30):
            raise CalendarError(500, "backend error")

        monkeypatch.setattr(calendar_io, "_execute_tool", failing)
        result = apply_plan(
            [_plan_row()], api_key=API_KEY, scope=SCOPE, calendar_id="cal", live=True
        )
        assert result["actions"] == []
        assert result["failed"][0]["status"] == 500


class TestVisibilityReachesTheTool:
    """The setting that had a reader but no writer.

    `config/calendar.json` declares `visibility: "public"`, both the reference and
    `SETUP.md` describe events as public, and `calendar_config.get_visibility()`
    exists — and until this change **nothing called it**, so every event took the
    calendar's own default and the promise was never enforced. These tests follow
    the value from the config file to the tool argument, which is the only path
    where it can be observed: a unit test asserting `build_event_body` echoes a
    parameter it was handed would pass on a pipeline that never passes one.
    """

    def _create_arguments(self, monkeypatch, **kwargs) -> dict:
        calls: list = []
        monkeypatch.setattr(
            calendar_io,
            "_execute_tool",
            lambda slug, arguments, **kw2: (
                calls.append(arguments) or {"id": "new-1"}
            ),
        )
        apply_plan(
            [_plan_row(color_id="")],
            api_key=API_KEY,
            scope=SCOPE,
            calendar_id="cal",
            live=True,
            **kwargs,
        )
        return calls[0]

    def test_a_live_create_carries_the_visibility_it_was_given(self, monkeypatch):
        arguments = self._create_arguments(monkeypatch, visibility="public")
        assert arguments["visibility"] == "public"

    def test_a_live_update_carries_it_too(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(
            calendar_io,
            "_execute_tool",
            lambda slug, arguments, **kw: (calls.append(arguments) or {"id": "evt-1"}),
        )
        apply_plan(
            [_plan_row(action=ACTION_UPDATE, event_id="evt-9")],
            api_key=API_KEY,
            scope=SCOPE,
            calendar_id="cal",
            live=True,
            visibility="public",
        )
        assert calls[0]["visibility"] == "public"

    def test_the_cli_default_comes_from_the_config_file_not_a_literal(self):
        """The whole defect in one assertion: the file that documents it decides it."""
        import re

        source = (REPO_ROOT / "scripts" / "calendar_io.py").read_text(encoding="utf-8")
        match = re.search(
            r'add_argument\(\s*"--visibility",\s*default=([A-Za-z_][\w.]*\(\))', source
        )
        assert match, "--visibility must default to a call, not to a literal"
        assert match.group(1) == "get_visibility()", match.group(1)
        assert "def get_visibility" in (
            REPO_ROOT / "scripts" / "calendar_config.py"
        ).read_text(encoding="utf-8")

    def test_the_repo_config_declares_a_visibility_the_enum_accepts(self):
        """The config value the runtime will send must be one the API accepts."""
        config = json.loads((REPO_ROOT / "config" / "calendar.json").read_text())
        assert config["visibility"] in calendar_io.VISIBILITY_VALUES

    def test_a_dry_run_records_no_id_deliberately(self, monkeypatch):
        """There is no event, so an invented id would key a verdict to nothing."""

        def boom(*args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("a dry run must not make an HTTP request")

        monkeypatch.setattr(calendar_io, "_execute_tool", boom)
        result = apply_plan([_plan_row()], live=False)
        assert result["actions"][0]["event_id"] == ""


class TestComposioTransport:
    """The shapes Composio actually returns, which the docs get wrong.

    Every case here was observed against the live API. They are written down
    because the cost of getting one wrong is not an exception — it is a payload
    that reads as "the calendar is empty", which on this system is the one
    reading that must never be produced by a failure.
    """

    def test_events_list_puts_the_body_straight_under_data(self):
        assert _payload({"successful": True, "data": {"items": [{"id": "a"}]}}) == {
            "items": [{"id": "a"}]
        }

    def test_create_event_nests_the_event_under_response_data(self):
        event = {"id": "evt-1", "summary": "x", "colorId": None}
        envelope = {
            "successful": True,
            "data": {
                "composio_execution_message": None,
                "display_url": "https://www.google.com/calendar/event?eid=...",
                "response_data": event,
            },
        }
        assert _payload(envelope) == event

    def test_data_as_a_json_string_is_accepted(self):
        """The docs type `data` as a string; v3.1 sends an object. Accept both."""
        assert _payload({"data": '{"items": [{"id": "a"}]}'}) == {
            "items": [{"id": "a"}]
        }

    def test_response_data_as_a_json_string_is_accepted(self):
        assert _payload({"data": {"response_data": '{"id": "evt-1"}'}}) == {
            "id": "evt-1"
        }

    def test_a_response_with_no_data_falls_back_to_the_envelope(self):
        """Never `{}` — that is indistinguishable from a calendar with no events."""
        envelope = {"successful": True, "items": [{"id": "a"}], "nextPageToken": "p2"}
        assert _payload(envelope) == envelope

    def test_a_null_data_does_not_mask_the_envelope(self):
        envelope = {"successful": True, "data": None, "items": [{"id": "a"}]}
        assert _payload(envelope) == envelope

    def test_a_non_dict_envelope_is_an_empty_result(self):
        assert _payload("nope") == {}

    def test_a_blank_data_string_is_an_empty_result(self):
        assert _payload({"data": "  "}) == {}

    def test_an_unparseable_data_string_keeps_the_raw_text(self):
        """Better a `raw` key in an error report than a silent empty calendar."""
        assert _payload({"data": "<html>502</html>"}) == {
            "raw": "<html>502</html>"
        }

    def test_scope_prefers_the_connected_account_when_given(self):
        assert scope_arguments("user-1", "ca_1") == {"connected_account_id": "ca_1"}

    def test_scope_defaults_to_the_user_id(self):
        """A `user_id` survives a reconnect; a connected-account id does not."""
        assert scope_arguments("user-1", "") == {"user_id": "user-1"}

    def test_a_connected_account_id_is_never_sent_as_a_user_id(self):
        """They are different fields; the wrong one reads as a broken credential."""
        assert "user_id" not in scope_arguments("", "ca_1")

    def test_create_disables_the_meet_link_and_the_organizer_attendee(self):
        """Both default to the opposite, and both were observed on a throwaway event."""
        arguments = create_arguments(
            build_event_body(_plan_row()), "cal", "Europe/Berlin"
        )
        assert arguments["create_meeting_room"] is False
        assert arguments["exclude_organizer"] is True

    def test_create_does_not_send_color_id(self):
        """The tool ignores it, so sending it promises colour and delivers none."""
        arguments = create_arguments(
            build_event_body(_plan_row()), "cal", "Europe/Berlin"
        )
        assert "color_id" not in arguments

    def test_patch_does_send_color_id(self):
        arguments = patch_arguments(
            build_event_body(_plan_row()), "cal", "evt-1", "Europe/Berlin"
        )
        assert arguments["color_id"] == "5"

    def test_patch_omits_an_empty_colour(self):
        arguments = patch_arguments(
            build_event_body(_plan_row(color_id="")), "cal", "evt-1", "Europe/Berlin"
        )
        assert "color_id" not in arguments

    def test_create_sends_visibility(self):
        arguments = create_arguments(
            build_event_body(_plan_row(), visibility="public"), "cal", "Europe/Berlin"
        )
        assert arguments["visibility"] == "public"

    def test_patch_sends_visibility(self):
        """PATCH replaces what it is given: omitting it would clear what a run set."""
        arguments = patch_arguments(
            build_event_body(_plan_row(), visibility="public"),
            "cal",
            "evt-1",
            "Europe/Berlin",
        )
        assert arguments["visibility"] == "public"

    def test_an_empty_visibility_is_omitted_rather_than_sent_empty(self):
        """`\"\"` is not a member of the enum, so sending it is a rejected write."""
        body = build_event_body(_plan_row())
        assert body["visibility"] == ""
        assert "visibility" not in create_arguments(body, "cal", "Europe/Berlin")
        assert "visibility" not in patch_arguments(body, "cal", "evt-1", "Europe/Berlin")

    def test_the_enum_is_the_one_the_tool_accepts(self):
        """One definition, in `calendar_config.py`, next to the field it constrains.

        `validate.py` restates it because it must stay self-contained, and
        `tests/test_validate.py` asserts the two are equal — so this is a check on
        the value, not on where it lives.
        """
        from scripts.calendar_config import VISIBILITY_VALUES

        assert VISIBILITY_VALUES == (
            "default",
            "public",
            "private",
            "confidential",
        )
        # `==`, not `is`: `calendar_io` is importable both as `calendar_io` and as
        # `scripts.calendar_io`, and under the first path its `calendar_config`
        # import resolves to a second module object. The value is the contract;
        # `tests/test_validate.py` ties the two files that define it.
        assert calendar_io.VISIBILITY_VALUES == VISIBILITY_VALUES

    def test_the_toolkit_version_is_not_pinned(self):
        """A pin can drop `timeMin`/`timeMax` before Google ever sees them."""
        assert calendar_io.COMPOSIO_TOOLKIT_VERSION == "latest"


class TestExecuteTool:
    """The real request builder, with `urlopen` replaced. Still no network."""

    def _response(self, payload: str):
        class Response:
            def read(inner) -> bytes:
                return payload.encode("utf-8")

            def __enter__(inner):
                return inner

            def __exit__(inner, *exc) -> bool:
                return False

        return Response()

    def test_posts_the_tool_slug_with_the_api_key_header(self, monkeypatch):
        seen: dict = {}

        def fake(request, timeout=None):
            seen["url"] = request.full_url
            seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
            seen["body"] = json.loads(request.data.decode("utf-8"))
            return self._response(json.dumps({"successful": True, "data": {"items": []}}))

        monkeypatch.setattr(calendar_io.urllib.request, "urlopen", fake)
        payload = calendar_io._execute_tool(
            TOOL_EVENTS_LIST, {"calendarId": "cal"}, api_key=API_KEY, scope=SCOPE
        )
        assert seen["url"] == (
            "https://backend.composio.dev/api/v3.1/tools/execute/GOOGLECALENDAR_EVENTS_LIST"
        )
        assert seen["headers"]["x-api-key"] == API_KEY
        assert seen["body"] == {
            "user_id": "user-1",
            "arguments": {"calendarId": "cal"},
            "version": "latest",
        }
        assert payload == {"items": []}

    def test_the_scope_is_sent_as_a_sibling_of_arguments(self, monkeypatch):
        """A `user_id` nested inside `arguments` is not read by Composio at all.

        The failure mode is a call that looks well-formed and comes back "no
        connected account", which reads as a broken credential rather than a
        misplaced field.
        """
        seen: dict = {}

        def fake(request, timeout=None):
            seen["body"] = json.loads(request.data.decode("utf-8"))
            return self._response("{}")

        monkeypatch.setattr(calendar_io.urllib.request, "urlopen", fake)
        calendar_io._execute_tool(
            TOOL_EVENTS_LIST,
            {"calendarId": "cal"},
            api_key=API_KEY,
            scope=scope_arguments("", "ca_1"),
        )
        assert seen["body"]["connected_account_id"] == "ca_1"
        assert "user_id" not in seen["body"]
        assert "user_id" not in seen["body"]["arguments"]

    def test_a_refusal_is_an_error_not_an_empty_result(self, monkeypatch):
        """Composio answers 200 with `successful: false`; that is a failure."""
        body = {
            "successful": False,
            "error": "no connected account found for user_id user-1",
            "log_id": "log-1",
        }
        monkeypatch.setattr(
            calendar_io.urllib.request,
            "urlopen",
            lambda request, timeout=None: self._response(json.dumps(body)),
        )
        with pytest.raises(CalendarError) as excinfo:
            calendar_io._execute_tool(
                TOOL_EVENTS_LIST, {}, api_key=API_KEY, scope=SCOPE
            )
        assert "no connected account" in str(excinfo.value)
        assert "log-1" in str(excinfo.value)

    def test_a_non_json_body_is_an_error(self, monkeypatch):
        monkeypatch.setattr(
            calendar_io.urllib.request,
            "urlopen",
            lambda request, timeout=None: self._response("<html>gateway</html>"),
        )
        with pytest.raises(CalendarError) as excinfo:
            calendar_io._execute_tool(
                TOOL_EVENTS_LIST, {}, api_key=API_KEY, scope=SCOPE
            )
        assert excinfo.value.status == 0
        assert "non-JSON" in str(excinfo.value)

    def test_an_empty_body_is_an_empty_result(self, monkeypatch):
        monkeypatch.setattr(
            calendar_io.urllib.request,
            "urlopen",
            lambda request, timeout=None: self._response("   "),
        )
        assert (
            calendar_io._execute_tool(
                TOOL_EVENTS_LIST, {}, api_key=API_KEY, scope=SCOPE
            )
            == {}
        )
