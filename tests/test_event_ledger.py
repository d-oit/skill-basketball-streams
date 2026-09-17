"""Tests for scripts/event_ledger.py — the writer Phase 3 was missing.

The defect these pin. `telemetry/events.jsonl` and `telemetry/evidence.json` are
documented as Phase 3's inputs and the audit job in `runtime-daily.yml` reads
them — but nothing wrote them. The audit found its inputs absent, printed a
notice, and did nothing on every run: no verdict was ever recorded, precision
stayed `n/a` for the life of the project, and `self-improve` never had a ledger
to learn from. A green workflow that could not observe anything.

The properties worth more than the feature, each of which is a way to get this
wrong:

* A dry run records nothing — the event does not exist, so a verdict about it
  would be a finding about nothing.
* A written event with no id is *refused*, not recorded with an empty key: the
  audit resolves a verdict back to an event by id, so that row could never be
  acted on.
* Evidence is copied from explicit flags and never derived. A missing key is
  INCONCLUSIVE and an explicit `false` is WRONG, so a default here relabels live
  games as wrong.

The last test in this file is the important one: it feeds what the ledger wrote
into `audit_events` and asserts the verdicts. Producer and consumer are tested
against each other, not against their own documentation.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.audit_events import (
    VERDICT_INCONCLUSIVE,
    VERDICT_VERIFIED,
    VERDICT_WRONG,
    sweep,
)
from scripts.event_ledger import (
    LEDGER_NAME,
    EVIDENCE_NAME,
    append_rows,
    build_rows,
    evidence_for,
    ledger_row,
    load_applied,
    load_plan,
    merge_evidence,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "event_ledger.py"

GAME_KEY = "BBL|ALBA Berlin|FC Bayern|2026-09-14T17:00Z"
NOW = "2026-09-15T08:30:00+00:00"


def _plan_row(**overrides) -> dict:
    row = {
        "action": "create",
        "reason": "no existing event for this slot",
        "game_key": GAME_KEY,
        "event_id": "",
        "state": "UNVERIFIED",
        "title": "[UNVERIFIED] BBL: ALBA Berlin vs FC Bayern",
        "color_id": "5",
        "start": "2026-09-14T19:00:00+02:00",
        "end": "",
        "league": "BBL",
        "teams": ["ALBA Berlin", "FC Bayern"],
        "league_color_id": "6",
    }
    row.update(overrides)
    return row


def _action(event_id: str = "evt-1", **overrides) -> dict:
    action = {
        "action": "create",
        "game_key": GAME_KEY,
        "event_id": event_id,
        "body": {
            "summary": "[UNVERIFIED] BBL: ALBA Berlin vs FC Bayern",
            "start": {
                "dateTime": "2026-09-14T19:00:00+02:00",
                "timeZone": "Europe/Berlin",
            },
            "end": {
                "dateTime": "2026-09-14T21:30:00+02:00",
                "timeZone": "Europe/Berlin",
            },
            "colorId": "5",
            "description": "League: BBL\nTeams: ALBA Berlin vs FC Bayern\n",
        },
    }
    action.update(overrides)
    return action


def _applied(*actions, dry_run: bool = False, **overrides) -> dict:
    result = {
        "dry_run": dry_run,
        "created": len(actions),
        "updated": 0,
        "skipped": 0,
        "failed": [],
        "actions": list(actions),
    }
    result.update(overrides)
    return result


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestRowShape:
    def test_ts_and_run_id_come_first(self):
        row = ledger_row(_plan_row(), _action(), run_id="run-1")
        assert list(row)[:2] == ["ts", "run_id"]

    def test_the_state_is_normalised(self):
        row = ledger_row(_plan_row(state="verified"), _action(), run_id="r")
        assert row["state"] == "VERIFIED"

    def test_the_summary_is_unprefixed(self):
        """So re-applying a state prefix is idempotent, and a reader sees a name."""
        row = ledger_row(_plan_row(), _action(), run_id="r")
        assert row["summary"] == "BBL: ALBA Berlin vs FC Bayern"
        assert not row["summary"].startswith("[UNVERIFIED]")

    def test_times_come_from_the_applied_body_not_the_plan(self):
        """The body is what was sent; a missing `end` is filled in there.

        Reading the plan instead would leave every event with no end time, and
        the audit decides "has this broadcast finished?" from exactly that.
        """
        row = ledger_row(
            _plan_row(end=""), _action(), run_id="r"
        )
        assert row["start"] == "2026-09-14T19:00:00+02:00"
        assert row["end"] == "2026-09-14T21:30:00+02:00"

    def test_a_plan_row_with_no_end_still_gets_one_from_the_body(self):
        plan = _plan_row(end="")
        action = _action()
        action["body"] = {
            "summary": "BBL: A vs B",
            "start": {"dateTime": "2026-09-14T19:00:00+02:00"},
            "end": {"dateTime": "2026-09-14T21:30:00+02:00"},
        }
        assert ledger_row(plan, action, run_id="r")["end"].endswith("21:30:00+02:00")

    def test_the_league_colour_is_recorded_for_a_later_promotion(self):
        """`color_id` is the *state* colour, so the league one is carried too."""
        row = ledger_row(_plan_row(league_color_id="11"), _action(), run_id="r")
        assert row["league_color_id"] == "11"

    def test_the_league_colour_falls_back_when_absent(self):
        row = ledger_row(_plan_row(league_color_id=""), _action(), run_id="r")
        assert row["league_color_id"] == "6"

    def test_it_is_not_a_dry_run_row(self):
        assert ledger_row(_plan_row(), _action(), run_id="r")["dry_run"] is False


class TestBuildRows:
    def test_a_dry_run_records_nothing_and_says_why(self):
        rows, refusals, reason = build_rows(
            [_plan_row()], _applied(_action(), dry_run=True), run_id="r"
        )
        assert rows == []
        assert refusals == []
        assert "dry run" in reason

    def test_a_written_create_becomes_a_row(self):
        rows, refusals, _ = build_rows(
            [_plan_row()], _applied(_action()), run_id="r"
        )
        assert refusals == []
        assert [row["event_id"] for row in rows] == ["evt-1"]

    def test_an_update_is_recorded_too(self):
        rows, _, _ = build_rows(
            [_plan_row(action="update", event_id="evt-9")],
            _applied(_action(event_id="evt-9", action="update")),
            run_id="r",
        )
        assert rows[0]["event_id"] == "evt-9"
        assert rows[0]["action"] == "update"

    def test_a_written_event_with_no_id_is_refused(self):
        """A write the calendar accepted but will not name is not recordable."""
        rows, refusals, _ = build_rows(
            [_plan_row()], _applied(_action(event_id="")), run_id="r"
        )
        assert rows == []
        assert len(refusals) == 1
        assert "no event_id" in refusals[0]

    def test_a_partial_refusal_keeps_the_good_rows(self):
        rows, refusals, _ = build_rows(
            [_plan_row(), _plan_row(game_key="G2")],
            _applied(
                _action(),
                _action(event_id="", game_key="G2"),
            ),
            run_id="r",
        )
        assert [row["event_id"] for row in rows] == ["evt-1"]
        assert len(refusals) == 1

    def test_a_plan_row_missing_still_yields_a_row(self):
        """The applied result is the authority; a gap in the plan is not fatal."""
        rows, refusals, _ = build_rows(
            [], _applied(_action()), run_id="r"
        )
        assert refusals == []
        assert len(rows) == 1

    def test_actions_that_are_not_writes_are_ignored(self):
        rows, _, reason = build_rows(
            [_plan_row()],
            _applied({"action": "skip", "game_key": GAME_KEY, "event_id": "evt-1"}),
            run_id="r",
        )
        assert rows == []
        assert reason

    def test_no_actions_at_all_is_stated_not_silent(self):
        _, _, reason = build_rows([_plan_row()], _applied(), run_id="r")
        assert reason


class TestEvidence:
    def test_flags_are_copied_from_the_plan_row(self):
        plan = [_plan_row(evidence={"live_confirmed": True, "free_confirmed": True})]
        rows, _, _ = build_rows(plan, _applied(_action()), run_id="r")
        assert evidence_for(rows, plan) == {
            "evt-1": {"live_confirmed": True, "free_confirmed": True}
        }

    def test_no_flags_means_no_entry(self):
        plan = [_plan_row()]
        rows, _, _ = build_rows(plan, _applied(_action()), run_id="r")
        assert evidence_for(rows, plan) == {}

    def test_an_unknown_value_does_not_become_a_false(self):
        """`false` would be a WRONG verdict; `unknown` is not a finding."""
        plan = [_plan_row(evidence={"live_confirmed": "unknown"})]
        rows, _, _ = build_rows(plan, _applied(_action()), run_id="r")
        assert evidence_for(rows, plan) == {}

    def test_only_written_events_get_an_entry(self):
        plan = [_plan_row(evidence={"paid": True})]
        rows, _, _ = build_rows(
            plan, _applied(_action(event_id="")), run_id="r"
        )
        assert rows == []
        assert evidence_for(rows, plan) == {}

    def test_touching_an_event_replaces_its_entry(self):
        """A union would let yesterday's observation outlive today's."""
        merged = merge_evidence(
            {"evt-1": {"live_confirmed": True, "free_confirmed": True}},
            {"evt-1": {"paid": True}},
        )
        assert merged["evt-1"] == {"paid": True}

    def test_an_event_this_run_did_not_touch_is_preserved(self):
        merged = merge_evidence(
            {"evt-old": {"live_confirmed": True}}, {"evt-new": {"paid": False}}
        )
        assert set(merged) == {"evt-old", "evt-new"}


class TestAppendOnly:
    def test_rows_are_appended_not_rewritten(self, tmp_path):
        append_rows(tmp_path, [ledger_row(_plan_row(), _action(), run_id="r1")])
        append_rows(tmp_path, [ledger_row(_plan_row(), _action(), run_id="r2")])
        lines = (tmp_path / LEDGER_NAME).read_text().splitlines()
        assert len(lines) == 2
        assert [json.loads(line)["run_id"] for line in lines] == ["r1", "r2"]

    def test_a_rerun_of_the_same_run_appends_a_second_row(self, tmp_path):
        """History is kept; the audit collapses it rather than the writer."""
        append_rows(tmp_path, [ledger_row(_plan_row(), _action(), run_id="r")])
        append_rows(tmp_path, [ledger_row(_plan_row(), _action(), run_id="r")])
        assert len((tmp_path / LEDGER_NAME).read_text().splitlines()) == 2

    def test_no_rows_writes_no_file(self, tmp_path):
        assert append_rows(tmp_path, []) == 0
        assert not (tmp_path / LEDGER_NAME).exists()


class TestLoaders:
    def test_plan_accepts_a_bare_list(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps([_plan_row()]), encoding="utf-8")
        assert load_plan(path) == [_plan_row()]

    def test_plan_accepts_a_wrapped_document(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps({"plan": [_plan_row()]}), encoding="utf-8")
        assert len(load_plan(path)) == 1

    def test_a_non_list_plan_is_rejected(self, tmp_path):
        path = tmp_path / "p.json"
        path.write_text(json.dumps({"plan": "nope"}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_plan(path)

    def test_applied_must_be_an_object(self, tmp_path):
        path = tmp_path / "a.json"
        path.write_text(json.dumps([1, 2]), encoding="utf-8")
        with pytest.raises(ValueError):
            load_applied(path)


class TestCli:
    def _write(self, tmp_path: Path, plan, applied) -> None:
        (tmp_path / "plan.json").write_text(json.dumps({"plan": plan}), encoding="utf-8")
        (tmp_path / "applied.json").write_text(
            json.dumps(applied), encoding="utf-8"
        )

    def test_record_writes_both_files(self, tmp_path):
        self._write(
            tmp_path,
            [_plan_row(evidence={"live_confirmed": True, "free_confirmed": True})],
            _applied(_action()),
        )
        result = _run(
            [
                "record",
                "--plan", str(tmp_path / "plan.json"),
                "--applied", str(tmp_path / "applied.json"),
                "--dest", str(tmp_path),
                "--run-id", "2026-09-15T08:30Z",
            ]
        )
        assert result.returncode == 0, result.stderr
        rows = [
            json.loads(line)
            for line in (tmp_path / LEDGER_NAME).read_text().splitlines()
        ]
        assert rows[0]["event_id"] == "evt-1"
        assert rows[0]["run_id"] == "2026-09-15T08:30Z"
        evidence = json.loads((tmp_path / EVIDENCE_NAME).read_text())
        assert evidence == {
            "evt-1": {"live_confirmed": True, "free_confirmed": True}
        }

    def test_a_dry_run_writes_nothing_and_exits_zero(self, tmp_path):
        self._write(tmp_path, [_plan_row()], _applied(_action(), dry_run=True))
        result = _run(
            [
                "record",
                "--plan", str(tmp_path / "plan.json"),
                "--applied", str(tmp_path / "applied.json"),
                "--dest", str(tmp_path),
            ]
        )
        assert result.returncode == 0
        assert "nothing recorded" in result.stdout
        assert not (tmp_path / LEDGER_NAME).exists()

    def test_an_unkeyable_write_exits_one_and_writes_nothing(self, tmp_path):
        self._write(tmp_path, [_plan_row()], _applied(_action(event_id="")))
        result = _run(
            [
                "record",
                "--plan", str(tmp_path / "plan.json"),
                "--applied", str(tmp_path / "applied.json"),
                "--dest", str(tmp_path),
            ]
        )
        assert result.returncode == 1
        assert "no event_id" in result.stderr
        # A partially recorded run is the state where the audit looks complete
        # and is not, so nothing at all is written.
        assert not (tmp_path / LEDGER_NAME).exists()

    def test_a_missing_applied_file_is_a_usage_error(self, tmp_path):
        self._write(tmp_path, [_plan_row()], _applied(_action()))
        result = _run(
            [
                "record",
                "--plan", str(tmp_path / "plan.json"),
                "--applied", str(tmp_path / "nope.json"),
                "--dest", str(tmp_path),
            ]
        )
        assert result.returncode == 2
        assert "not found" in result.stderr

    def test_json_stdout_is_the_payload_and_nothing_else(self, tmp_path):
        self._write(tmp_path, [_plan_row()], _applied(_action()))
        result = _run(
            [
                "record",
                "--plan", str(tmp_path / "plan.json"),
                "--applied", str(tmp_path / "applied.json"),
                "--dest", str(tmp_path),
                "--json",
            ]
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["written"] == 1

    def test_markdown_exits_zero_and_writes_nothing(self, tmp_path):
        self._write(tmp_path, [_plan_row()], _applied(_action()))
        result = _run(
            [
                "record",
                "--plan", str(tmp_path / "plan.json"),
                "--applied", str(tmp_path / "applied.json"),
                "--dest", str(tmp_path),
                "--markdown",
            ]
        )
        assert result.returncode == 0
        assert "evt-1" in result.stdout
        assert not (tmp_path / LEDGER_NAME).exists()

    def test_the_markdown_view_names_events_with_no_evidence(self, tmp_path):
        self._write(tmp_path, [_plan_row()], _applied(_action()))
        result = _run(
            [
                "record",
                "--plan", str(tmp_path / "plan.json"),
                "--applied", str(tmp_path / "applied.json"),
                "--dest", str(tmp_path),
                "--markdown",
            ]
        )
        assert "INCONCLUSIVE" in result.stdout


class TestTheAuditCanActuallyReadIt:
    """The round trip. Producer and consumer, tested against each other.

    This is the test that would have failed before: the audit's inputs were
    never written by anything, so `sweep` was only ever exercised on fixtures.
    """

    def _ledger(self, tmp_path, plan, applied):
        self._write(tmp_path, plan, applied)
        result = _run(
            [
                "record",
                "--plan", str(tmp_path / "plan.json"),
                "--applied", str(tmp_path / "applied.json"),
                "--dest", str(tmp_path),
            ]
        )
        assert result.returncode == 0, result.stderr
        events = [
            json.loads(line)
            for line in (tmp_path / LEDGER_NAME).read_text().splitlines()
        ]
        return events, json.loads((tmp_path / EVIDENCE_NAME).read_text())

    def _write(self, tmp_path: Path, plan, applied) -> None:
        (tmp_path / "plan.json").write_text(json.dumps({"plan": plan}), encoding="utf-8")
        (tmp_path / "applied.json").write_text(json.dumps(applied), encoding="utf-8")

    def test_free_and_live_promotes_to_verified(self, tmp_path):
        events, evidence = self._ledger(
            tmp_path,
            [_plan_row(evidence={"live_confirmed": True, "free_confirmed": True})],
            _applied(_action()),
        )
        verdicts = sweep(events, evidence)
        assert [v["verdict"] for v in verdicts] == [VERDICT_VERIFIED]
        assert verdicts[0]["new_color_id"] == "6"

    def test_paid_access_is_wrong(self, tmp_path):
        events, evidence = self._ledger(
            tmp_path, [_plan_row(evidence={"paid": True})], _applied(_action())
        )
        verdicts = sweep(events, evidence)
        assert [v["verdict"] for v in verdicts] == [VERDICT_WRONG]
        assert verdicts[0]["new_title"].startswith("[WRONG] ")

    def test_no_evidence_is_inconclusive_not_wrong(self, tmp_path):
        """The failure mode this whole chain exists to avoid."""
        events, evidence = self._ledger(
            tmp_path, [_plan_row()], _applied(_action())
        )
        verdicts = sweep(events, evidence)
        assert [v["verdict"] for v in verdicts] == [VERDICT_INCONCLUSIVE]

    def test_an_event_that_has_not_finished_is_left_alone(self, tmp_path):
        future = _action()
        future["body"]["start"] = {"dateTime": "2026-09-16T19:00:00+02:00"}
        future["body"]["end"] = {"dateTime": "2026-09-16T21:30:00+02:00"}
        events, evidence = self._ledger(
            tmp_path,
            [_plan_row(evidence={"live_confirmed": True, "free_confirmed": True})],
            _applied(future),
        )
        verdicts = sweep(
            events, evidence, now=__import__("datetime").datetime.fromisoformat(NOW)
        )
        assert [v["verdict"] for v in verdicts] == [VERDICT_INCONCLUSIVE]
        assert "finished" in verdicts[0]["reason"]
