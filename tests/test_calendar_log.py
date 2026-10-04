"""Tests for scripts/calendar_log.py — the producer of the decision log.

The defect these pin. `.tmp/plan.json` holds every decision the planner made —
which game, what matched it, what changed, and the planner's own `reason` — and
it is scratch in the runner's working directory, uploaded by nothing and
discarded when the job ends. `events.jsonl` records only the `create`/`update`
rows that reached the calendar, so a `skip` (or an `unchanged` row) was recorded
nowhere and its reason could not be read by anything after the process exited.
`agenda.md` calls an artifact with no producer a bug; this is the same shape with
the polarity flipped — a decision with no artifact.

The properties worth more than the feature:

* **Every plan game gets a row.** Including `skip` and `unchanged`.
* **An event id is never invented.** A create in a dry run has none and gets
  none; a skip carries the id the planner read back from the calendar.
* **`dry_run` is carried, not assumed.** A dry run's rows are plans, not writes.
* **`reason` is copied, not re-derived**, so the reason has one owner.
* **`fields_changed` distinguishes "nothing differs" from "not compared".** A
  list may be empty; `null` means no comparison was made.

The round trip at the end feeds the log rows produced by the REAL planner
(`upsert_events.plan_upsert`) and the REAL apply path
(`calendar_io.apply_plan` in its no-HTTP dry-run mode) through the writer, rather
than asserting against a hand-built dict that supplies a field the pipeline
never writes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.calendar_io import apply_plan, parse_event
from scripts.calendar_log import (
    LOG_NAME,
    append_rows,
    build_rows,
    fields_changed,
    load_applied,
    load_existing,
    load_plan,
)
from scripts.upsert_events import plan_upsert

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "calendar_log.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"

NOW = datetime(2026, 10, 4, 8, 30, tzinfo=timezone.utc)
GAME_KEY = "BBL|ALBA Berlin|FC Bayern|2026-10-04T17:00Z"


def _plan_row(**overrides) -> dict:
    row = {
        "action": "create",
        "reason": "no existing event for this slot",
        "game_key": GAME_KEY,
        "event_id": "",
        "state": "UNVERIFIED",
        "title": "[UNVERIFIED] BBL: ALBA Berlin vs FC Bayern",
        "color_id": "5",
        "start": "2026-10-04T19:00:00+02:00",
        "end": "",
        "league": "BBL",
        "teams": ["ALBA Berlin", "FC Bayern"],
        "league_color_id": "6",
    }
    row.update(overrides)
    return row


def _applied(*actions, dry_run: bool = False) -> dict:
    return {
        "dry_run": dry_run,
        "created": sum(1 for a in actions if a.get("action") == "create"),
        "updated": sum(1 for a in actions if a.get("action") == "update"),
        "skipped": 0,
        "unchanged": 0,
        "failed": [],
        "actions": list(actions),
    }


def _action(game_key: str = GAME_KEY, event_id: str = "evt-1", action: str = "create") -> dict:
    return {
        "action": action,
        "game_key": game_key,
        "event_id": event_id,
        "body": {
            "summary": "[UNVERIFIED] BBL: ALBA Berlin vs FC Bayern",
            "start": {"dateTime": "2026-10-04T19:00:00+02:00", "timeZone": "Europe/Berlin"},
            "end": {"dateTime": "2026-10-04T21:30:00+02:00", "timeZone": "Europe/Berlin"},
            "colorId": "5",
        },
    }


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestRowShape:
    def test_every_plan_game_gets_a_row_in_plan_order(self):
        plan = [
            _plan_row(),
            _plan_row(action="update", event_id="evt-b", game_key="B"),
            _plan_row(action="skip", event_id="evt-c", game_key="C"),
            _plan_row(action="unchanged", event_id="evt-d", game_key="D"),
        ]
        rows, refusals, _ = build_rows(plan, _applied(_action()), run_id="r", now=NOW)
        assert refusals == []
        assert [row["action"] for row in rows] == ["create", "update", "skip", "unchanged"]
        assert [row["game_key"] for row in rows] == [GAME_KEY, "B", "C", "D"]

    def test_the_reason_is_the_planners_own_string(self):
        reason = "audit verdict WRONG stands (recorded 2026-10-03T08:30Z)"
        rows, _, _ = build_rows(
            [_plan_row(action="skip", event_id="evt-c", reason=reason)],
            _applied(),
            run_id="r",
            now=NOW,
        )
        assert rows[0]["reason"] == reason

    def test_the_state_is_normalised(self):
        rows, _, _ = build_rows(
            [_plan_row(state="verified")], _applied(_action()), run_id="r", now=NOW
        )
        assert rows[0]["state"] == "VERIFIED"

    def test_ts_and_run_id_come_first(self):
        rows, _, _ = build_rows([_plan_row()], _applied(_action()), run_id="r", now=NOW)
        assert list(rows[0])[:2] == ["ts", "run_id"]
        assert rows[0]["ts"] == "2026-10-04T08:30:00+00:00"


class TestEventIdIsNeverInvented:
    def test_a_write_takes_its_id_from_the_apply_result(self):
        rows, _, _ = build_rows([_plan_row()], _applied(_action()), run_id="r", now=NOW)
        assert rows[0]["event_id"] == "evt-1"

    def test_a_dry_run_create_has_no_id(self):
        rows, _, _ = build_rows(
            [_plan_row()], _applied(_action(event_id=""), dry_run=True), run_id="r", now=NOW
        )
        assert rows[0]["event_id"] == ""

    def test_a_skip_carries_the_id_the_planner_matched(self):
        """The planner read it back from the calendar; this is not a guess."""
        rows, _, _ = build_rows(
            [_plan_row(action="skip", event_id="evt-c")], _applied(), run_id="r", now=NOW
        )
        assert rows[0]["event_id"] == "evt-c"

    def test_a_dry_run_never_records_a_created_fact(self):
        rows, _, _ = build_rows(
            [_plan_row()], _applied(_action(), dry_run=True), run_id="r", now=NOW
        )
        assert rows[0]["dry_run"] is True


class TestFieldsChanged:
    def test_null_when_there_is_no_snapshot_to_compare_against(self):
        rows, _, _ = build_rows([_plan_row()], _applied(_action()), run_id="r", now=NOW)
        assert rows[0]["fields_changed"] is None

    def test_a_create_has_no_stored_event_so_it_is_null(self):
        assert fields_changed(_plan_row(), _action(), None) is None

    def test_a_create_with_a_snapshot_but_no_match_is_still_null(self):
        """A create matched no stored event, so there is nothing to diff."""
        stored = {"event_id": "evt-other", "state": "UNVERIFIED"}
        rows, _, _ = build_rows(
            [_plan_row()], _applied(_action()), run_id="r", now=NOW, existing=[stored]
        )
        assert rows[0]["fields_changed"] is None

    def test_an_unchanged_row_compares_and_finds_nothing(self):
        stored = {
            "event_id": "evt-1",
            "summary": "[UNVERIFIED] BBL: ALBA Berlin vs FC Bayern",
            "state": "UNVERIFIED",
            "league_color_id": "5",
            "start": "2026-10-04T19:00:00+02:00",
            "end": "2026-10-04T21:30:00+02:00",
            "teams": ["ALBA Berlin", "FC Bayern"],
            "links": [],
        }
        plan = _plan_row(action="unchanged", event_id="evt-1", direct_links=[])
        rows, _, _ = build_rows(
            [plan], _applied(), run_id="r", now=NOW, existing=[stored]
        )
        # An empty list is a real answer: compared, and nothing differs.
        assert rows[0]["fields_changed"] == []

    def test_a_promotion_names_the_fields_it_changed(self):
        stored = {
            "event_id": "evt-1",
            "summary": "[UNVERIFIED] BBL: ALBA Berlin vs FC Bayern",
            "state": "UNVERIFIED",
            "league_color_id": "5",
            "start": "2026-10-04T19:00:00+02:00",
            "end": "2026-10-04T21:30:00+02:00",
            "teams": ["ALBA Berlin", "FC Bayern"],
            "links": [],
        }
        plan = _plan_row(state="VERIFIED", event_id="evt-1")
        promoted = _action(event_id="evt-1", action="update")
        promoted["body"]["summary"] = "BBL: ALBA Berlin vs FC Bayern"
        promoted["body"]["colorId"] = "6"
        rows, _, _ = build_rows(
            [plan], _applied(promoted), run_id="r", now=NOW, existing=[stored]
        )
        assert rows[0]["fields_changed"] == ["summary", "state", "color_id"]

    def test_a_skip_does_not_claim_a_change_it_never_proposed(self):
        """A skip carries no title/colour, so an empty value is not a change."""
        stored = {
            "event_id": "evt-c",
            "summary": "[VERIFIED] BBL: ALBA Berlin vs FC Bayern",
            "state": "VERIFIED",
            "league_color_id": "6",
            "start": "2026-10-04T19:00:00+02:00",
            "end": "2026-10-04T21:30:00+02:00",
            "teams": ["ALBA Berlin", "FC Bayern"],
            "links": [],
        }
        plan = _plan_row(action="skip", event_id="evt-c", state="VERIFIED", title="", color_id="")
        rows, _, _ = build_rows(
            [plan], _applied(), run_id="r", now=NOW, existing=[stored]
        )
        assert rows[0]["fields_changed"] == []


class TestAppendOnly:
    def test_rows_are_appended_not_rewritten(self, tmp_path):
        append_rows(tmp_path, [_plan_row() | {"ts": "t1"}])
        append_rows(tmp_path, [_plan_row() | {"ts": "t2"}])
        lines = (tmp_path / LOG_NAME).read_text().splitlines()
        assert len(lines) == 2
        assert [json.loads(line)["ts"] for line in lines] == ["t1", "t2"]

    def test_no_rows_writes_no_file(self, tmp_path):
        assert append_rows(tmp_path, []) == 0
        assert not (tmp_path / LOG_NAME).exists()

    def test_one_json_object_per_line(self, tmp_path):
        append_rows(tmp_path, [build_rows([_plan_row()], _applied(_action()), run_id="r", now=NOW)[0][0]])
        for line in (tmp_path / LOG_NAME).read_text().splitlines():
            assert json.loads(line)["game_key"] == GAME_KEY


class TestLiveWriteWithoutAnIdIsNamed:
    def test_it_is_recorded_and_exits_one(self, tmp_path):
        (tmp_path / "plan.json").write_text(
            json.dumps({"plan": [_plan_row()]}), encoding="utf-8"
        )
        (tmp_path / "applied.json").write_text(
            json.dumps(_applied()), encoding="utf-8"
        )
        result = _run(
            "record",
            "--plan", str(tmp_path / "plan.json"),
            "--applied", str(tmp_path / "applied.json"),
            "--dest", str(tmp_path),
        )
        assert result.returncode == 1
        assert "no event_id" in result.stderr
        # The decision is still observable: these rows are decisions, not the
        # audit's verdict keys, so withholding them would lose the observability
        # the file exists for.
        assert (tmp_path / LOG_NAME).is_file()


class TestCli:
    def _write(self, tmp_path: Path, plan: list[dict], applied: dict) -> tuple[Path, Path]:
        plan_path = tmp_path / "plan.json"
        applied_path = tmp_path / "applied.json"
        plan_path.write_text(json.dumps({"plan": plan}), encoding="utf-8")
        applied_path.write_text(json.dumps(applied), encoding="utf-8")
        return plan_path, applied_path

    def test_json_prints_the_payload_alone(self, tmp_path):
        plan_path, applied_path = self._write(tmp_path, [_plan_row()], _applied(_action()))
        result = _run(
            "record",
            "--plan", str(plan_path), "--applied", str(applied_path),
            "--dest", str(tmp_path), "--run-id", "r", "--json",
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)  # raises if anything else is on stdout
        assert payload["written"] == 1
        assert payload["rows"][0]["event_id"] == "evt-1"

    def test_the_fixture_plan_yields_a_row_for_every_action(self, tmp_path):
        result = _run(
            "record",
            "--plan", str(FIXTURES / "calendar_log_plan.json"),
            "--applied", str(FIXTURES / "calendar_log_applied.json"),
            "--existing", str(FIXTURES / "calendar_log_existing.json"),
            "--dest", str(tmp_path), "--run-id", "r", "--now", "2026-10-04T08:30:00Z",
            "--json",
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["written"] == 4
        assert {row["action"] for row in payload["rows"]} == {
            "create", "update", "skip", "unchanged",
        }
        assert {row["dry_run"] for row in payload["rows"]} == {False}

    def test_the_dry_run_fixture_carries_dry_run_and_no_created_id(self, tmp_path):
        result = _run(
            "record",
            "--plan", str(FIXTURES / "calendar_log_plan.json"),
            "--applied", str(FIXTURES / "calendar_log_applied_dry.json"),
            "--dest", str(tmp_path), "--run-id", "r", "--now", "2026-10-04T08:30:00Z",
            "--json",
        )
        assert result.returncode == 0, result.stderr
        rows = json.loads(result.stdout)["rows"]
        creates = [row for row in rows if row["action"] == "create"]
        assert creates[0]["event_id"] == ""
        assert all(row["dry_run"] is True for row in rows)
        assert all(row["fields_changed"] is None for row in rows)

    def test_dry_run_mode_writes_nothing(self, tmp_path):
        plan_path, applied_path = self._write(tmp_path, [_plan_row()], _applied(_action()))
        result = _run(
            "record",
            "--plan", str(plan_path), "--applied", str(applied_path),
            "--dest", str(tmp_path), "--dry-run",
        )
        assert result.returncode == 0
        assert "nothing written" in result.stdout
        assert not (tmp_path / LOG_NAME).exists()

    def test_markdown_renders_the_skips(self, tmp_path):
        plan_path, applied_path = self._write(
            tmp_path,
            [_plan_row(action="skip", event_id="evt-c")],
            _applied(),
        )
        result = _run(
            "record",
            "--plan", str(plan_path), "--applied", str(applied_path),
            "--dest", str(tmp_path), "--markdown",
        )
        assert result.returncode == 0
        assert "| `skip` | 1 |" in result.stdout
        assert not (tmp_path / LOG_NAME).exists()

    def test_an_empty_plan_is_stated_not_silent(self, tmp_path):
        plan_path, applied_path = self._write(tmp_path, [], _applied())
        result = _run(
            "record",
            "--plan", str(plan_path), "--applied", str(applied_path),
            "--dest", str(tmp_path),
        )
        assert result.returncode == 0
        assert "plan is empty" in result.stdout

    def test_a_missing_applied_file_is_a_usage_error(self, tmp_path):
        plan_path, _ = self._write(tmp_path, [_plan_row()], _applied(_action()))
        result = _run(
            "record",
            "--plan", str(plan_path), "--applied", str(tmp_path / "nope.json"),
            "--dest", str(tmp_path),
        )
        assert result.returncode == 2
        assert "not found" in result.stderr


class TestRoundTripThroughTheRealPipeline:
    def test_a_real_plan_and_apply_result_produce_the_decisions(self):
        candidate = {
            "summary": "BBL: ALBA Berlin vs FC Bayern",
            "league": "BBL",
            "teams": ["ALBA Berlin", "FC Bayern"],
            "start": "2026-10-11T19:00:00+02:00",
            "state": "UNVERIFIED",
            "directLink": "https://www.magentasport.de/live/1",
            "evidence": {"live_confirmed": True, "free_confirmed": True},
        }
        plan = plan_upsert([], [candidate])
        assert len(plan) == 1
        # `live=False` is `apply_plan`'s documented default and performs no HTTP.
        applied = apply_plan(plan, live=False)

        rows, refusals, _ = build_rows(plan, applied, run_id="2026-10-04T08:30Z", now=NOW)
        assert refusals == []
        assert len(rows) == len(plan)
        row = rows[0]
        assert row["action"] == plan[0]["action"]
        assert row["reason"] == plan[0]["reason"]
        assert row["state"] == "UNVERIFIED"
        # The real dry run wrote nothing, so no id exists and none is invented.
        assert row["event_id"] == ""
        assert row["dry_run"] is True

    def test_a_matched_stored_event_keeps_its_real_id(self):
        stored = parse_event(
            {
                "id": "evt-stored-1",
                "summary": "[UNVERIFIED] BBL: ALBA Berlin vs FC Bayern",
                "description": "League: BBL\nTeams: ALBA Berlin vs FC Bayern\n",
                "start": {"dateTime": "2026-10-11T19:00:00+02:00"},
                "end": {"dateTime": "2026-10-11T21:30:00+02:00"},
                "colorId": "5",
            }
        )
        candidate = {
            "summary": "BBL: ALBA Berlin vs FC Bayern",
            "league": "BBL",
            "teams": ["ALBA Berlin", "FC Bayern"],
            "start": "2026-10-11T19:00:00+02:00",
            "state": "UNVERIFIED",
        }
        plan = plan_upsert([stored], [candidate])
        assert len(plan) == 1
        applied = apply_plan(plan, live=False)
        rows, _, _ = build_rows(plan, applied, run_id="r", now=NOW, existing=[stored])
        # The id is the planner's matched stored event — a real id, not one this
        # writer generated.
        assert rows[0]["event_id"] == plan[0]["event_id"] == "evt-stored-1"
        assert rows[0]["action"] == plan[0]["action"]


class TestLoaders:
    def test_plan_accepts_a_bare_list(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps([_plan_row()]), encoding="utf-8")
        assert load_plan(path) == [_plan_row()]

    def test_a_non_object_applied_result_is_rejected(self, tmp_path):
        path = tmp_path / "a.json"
        path.write_text(json.dumps([1]), encoding="utf-8")
        with pytest.raises(ValueError):
            load_applied(path)

    def test_existing_accepts_the_calendar_io_wrapper(self, tmp_path):
        path = tmp_path / "e.json"
        path.write_text(json.dumps({"events": [{"event_id": "evt-1"}]}), encoding="utf-8")
        assert load_existing(path) == [{"event_id": "evt-1"}]

    def test_existing_rejects_an_unusable_shape(self, tmp_path):
        path = tmp_path / "e.json"
        path.write_text(json.dumps({"events": "nope"}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_existing(path)
