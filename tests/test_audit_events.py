"""Tests for scripts/audit_events.py.

Phase 3 is what makes "prefer recall early, precision later" safe: uncertain
events are created freely, then resolved the next day. Three rules are pinned
here because breaking any of them poisons the audit:

1. **Absence of evidence is not evidence.** A missing key yields
   `INCONCLUSIVE`; only an explicit `live_confirmed: false` on a *finished*
   broadcast yields `WRONG`. Marking a game wrong because a scraper failed would
   generate bogus eval cases and then "learn" from them.
2. **`WRONG` is terminal.** A later run never promotes it back.
3. **Nothing is deleted, nothing is mutated.** The verdict is a separate record.

Offline and pure — no network, no credentials.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.audit_events import (
    VERDICT_INCONCLUSIVE,
    VERDICT_VERIFIED,
    VERDICT_WRONG,
    VERDICTS,
    audit_event,
    latest_per_event,
    parse_dt,
    summarise,
    sweep,
    telemetry_rows,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "audit_events.py"

NOW = datetime(2026, 9, 15, 8, 30, tzinfo=timezone.utc)


def _event(**overrides) -> dict:
    row = {
        "event_id": "evt-1",
        "game_key": "BBL|ALBA Berlin|FC Bayern Muenchen|2026-09-14T18:00Z",
        "league_color_id": "6",
        "state": "UNVERIFIED",
        "summary": "BBL: ALBA Berlin vs FC Bayern Muenchen",
        "start": "2026-09-14T18:00:00Z",
        "end": "2026-09-14T20:00:00Z",
    }
    row.update(overrides)
    return row


class TestParseDt:
    @pytest.mark.parametrize("value", [None, "", "nope", 7])
    def test_bad_input_returns_none(self, value):
        assert parse_dt(value) is None


class TestVerdicts:
    def test_only_three_verdicts(self):
        assert VERDICTS == (VERDICT_VERIFIED, VERDICT_WRONG, VERDICT_INCONCLUSIVE)


class TestAuditEvent:
    def test_unfinished_broadcast_is_inconclusive(self):
        result = audit_event(_event(end="2026-09-15T20:00:00Z"), None, now=NOW)
        assert result["verdict"] == VERDICT_INCONCLUSIVE
        assert "not finished" in result["reason"]

    def test_not_yet_started_is_inconclusive(self):
        result = audit_event(
            _event(start="2026-09-16T18:00:00Z", end=None), None, now=NOW
        )
        assert result["verdict"] == VERDICT_INCONCLUSIVE
        assert "not started" in result["reason"]

    def test_finished_without_evidence_is_inconclusive(self):
        for evidence in (None, {}):
            result = audit_event(_event(), evidence, now=NOW)
            assert result["verdict"] == VERDICT_INCONCLUSIVE
            assert "no evidence" in result["reason"]

    def test_paid_is_wrong(self):
        result = audit_event(_event(), {"paid": True}, now=NOW)
        assert result["verdict"] == VERDICT_WRONG
        assert result["new_state"] == "WRONG"
        assert result["new_color_id"] == "7"
        assert result["new_title"].startswith("[WRONG] ")

    def test_provably_not_live_is_wrong(self):
        result = audit_event(_event(), {"live_confirmed": False}, now=NOW)
        assert result["verdict"] == VERDICT_WRONG
        assert "no live broadcast" in result["reason"]

    def test_both_confirmed_is_verified(self):
        result = audit_event(
            _event(), {"live_confirmed": True, "free_confirmed": True}, now=NOW
        )
        assert result["verdict"] == VERDICT_VERIFIED
        assert result["new_state"] == "VERIFIED"
        assert result["new_color_id"] == "6"
        assert result["actionable"]

    def test_live_but_free_unproven_stays_inconclusive(self):
        result = audit_event(
            _event(), {"live_confirmed": True, "free_confirmed": False}, now=NOW
        )
        assert result["verdict"] == VERDICT_INCONCLUSIVE
        assert "free access still unproven" in result["reason"]

    def test_unknown_live_status_is_inconclusive_not_wrong(self):
        """A scraper failure must never read as 'was never live'."""
        result = audit_event(_event(), {"free_confirmed": True}, now=NOW)
        assert result["verdict"] == VERDICT_INCONCLUSIVE
        assert result["verdict"] != VERDICT_WRONG
        assert "live status unknown" in result["reason"]

    def test_inconclusive_is_never_actionable(self):
        for evidence in (None, {}, {"free_confirmed": True}):
            result = audit_event(_event(), evidence, now=NOW)
            assert not result["actionable"]
            assert result["new_title"] == ""
            assert result["new_color_id"] == ""

    def test_wrong_is_terminal(self):
        result = audit_event(
            _event(state="WRONG"),
            {"live_confirmed": True, "free_confirmed": True},
            now=NOW,
        )
        assert result["verdict"] == VERDICT_INCONCLUSIVE
        assert "terminal" in result["reason"]
        assert not result["actionable"]

    def test_already_verified_is_not_reported_actionable(self):
        result = audit_event(
            _event(state="VERIFIED"),
            {"live_confirmed": True, "free_confirmed": True},
            now=NOW,
        )
        assert result["verdict"] == VERDICT_VERIFIED
        assert not result["actionable"]

    def test_league_colour_is_kept_on_promotion(self):
        result = audit_event(
            _event(league_color_id="11", summary="EuroLeague Final"),
            {"live_confirmed": True, "free_confirmed": True},
            now=NOW,
        )
        assert result["new_color_id"] == "11"

    def test_promotion_drops_the_unverified_prefix(self):
        result = audit_event(
            _event(summary="[UNVERIFIED] BBL: ALBA vs Bayern"),
            {"live_confirmed": True, "free_confirmed": True},
            now=NOW,
        )
        assert result["new_title"] == "BBL: ALBA vs Bayern"

    def test_event_is_never_mutated(self):
        event = _event()
        snapshot = copy.deepcopy(event)
        audit_event(event, {"paid": True}, now=NOW)
        assert event == snapshot

    def test_naive_now_is_treated_as_utc(self):
        result = audit_event(
            _event(), {"live_confirmed": False}, now=datetime(2026, 9, 15, 8, 30)
        )
        assert result["verdict"] == VERDICT_WRONG

    def test_accepts_alternative_key_names(self):
        result = audit_event(
            {
                "id": "evt-9",
                "startTime": "2026-09-14T18:00:00Z",
                "endTime": "2026-09-14T20:00:00Z",
                "state": "UNVERIFIED",
                "summary": "x",
            },
            {"paid": True},
            now=NOW,
        )
        assert result["event_id"] == "evt-9"
        assert result["verdict"] == VERDICT_WRONG


class TestSweep:
    def test_evidence_is_looked_up_by_event_id(self):
        events = [_event(event_id="a"), _event(event_id="b")]
        evidence = {"b": {"paid": True}}
        results = sweep(events, evidence, now=NOW)
        assert [row["verdict"] for row in results] == [
            VERDICT_INCONCLUSIVE,
            VERDICT_WRONG,
        ]

    def test_non_dict_events_are_skipped(self):
        assert sweep(["junk", None], {}, now=NOW) == []

    def test_sweep_is_idempotent(self):
        events = [
            _event(event_id="a"),
            _event(event_id="b"),
            _event(event_id="c", state="WRONG"),
        ]
        evidence = {
            "a": {"live_confirmed": True, "free_confirmed": True},
            "b": {"paid": True},
            "c": {"live_confirmed": True, "free_confirmed": True},
        }
        first = sweep(events, evidence, now=NOW)
        second = sweep(events, evidence, now=NOW)
        assert first == second

    def test_wrong_never_reverts_to_verified(self):
        events = [
            {"event_id": "a", "state": "WRONG", "summary": "x",
             "start": "2026-09-14T18:00:00Z", "end": "2026-09-14T20:00:00Z"}
        ]
        results = sweep(
            events,
            {"a": {"live_confirmed": True, "free_confirmed": True}},
            now=NOW,
        )
        assert results[0]["new_state"] == "WRONG"


class TestSummarise:
    def test_counts_verdicts_and_actionable(self):
        results = [
            {"verdict": VERDICT_VERIFIED, "actionable": True},
            {"verdict": VERDICT_WRONG, "actionable": True},
            {"verdict": VERDICT_INCONCLUSIVE, "actionable": False},
        ]
        assert summarise(results) == {
            VERDICT_VERIFIED: 1,
            VERDICT_WRONG: 1,
            VERDICT_INCONCLUSIVE: 1,
            "actionable": 2,
        }

    def test_empty_sweep_is_all_zero(self):
        assert summarise([]) == {
            VERDICT_VERIFIED: 0,
            VERDICT_WRONG: 0,
            VERDICT_INCONCLUSIVE: 0,
            "actionable": 0,
        }


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestTelemetryRows:
    def test_prefix_keys_come_first(self):
        rows = telemetry_rows(
            [audit_event(_event(), {"paid": True}, now=NOW)],
            run_id="2026-09-15T08:30Z",
            now=NOW,
        )
        assert list(rows[0])[:2] == ["ts", "run_id"]
        assert rows[0]["run_id"] == "2026-09-15T08:30Z"
        assert rows[0]["verdict"] == VERDICT_WRONG

    def test_inconclusive_rows_are_recorded_as_evidence(self):
        # "We checked and could not tell" is evidence: it distinguishes a quiet
        # day from a day the audit silently did nothing.
        rows = telemetry_rows(
            [audit_event(_event(), {}, now=NOW)], run_id="r", now=NOW
        )
        assert rows[0]["verdict"] == VERDICT_INCONCLUSIVE
        assert rows[0]["actionable"] is False

    def test_empty_results_yield_no_rows(self):
        assert telemetry_rows([], run_id="r", now=NOW) == []


class TestLatestPerEvent:
    """One row per event, because the ledger keeps every write.

    `events.jsonl` is append-only forever (§10.3), so an event the runtime
    updated on five days has five rows. Auditing all of them would judge one
    event five times and append five verdicts for it, inflating `audit.jsonl` and
    the precision denominator with copies of a single finding.
    """

    def test_a_repeat_observation_collapses_to_the_last_one(self):
        rows, dropped = latest_per_event(
            [_event(state="UNVERIFIED"), _event(state="VERIFIED")]
        )
        assert dropped == 1
        assert len(rows) == 1
        assert rows[0]["state"] == "VERIFIED"

    def test_distinct_events_are_all_kept(self):
        rows, dropped = latest_per_event(
            [_event(), _event(event_id="evt-2")]
        )
        assert dropped == 0
        assert len(rows) == 2

    def test_an_event_keeps_its_first_position(self):
        """Stable ordering, so the report does not reshuffle run to run."""
        rows, _ = latest_per_event(
            [_event(), _event(event_id="evt-2"), _event(state="VERIFIED")]
        )
        assert [row["event_id"] for row in rows] == ["evt-1", "evt-2"]

    def test_a_row_without_an_id_is_kept_not_dropped(self):
        """Dropping it would hide a write we cannot name."""
        rows, dropped = latest_per_event([_event(event_id="")])
        assert dropped == 0
        assert len(rows) == 1

    def test_the_sweep_judges_the_event_once(self):
        rows, _ = latest_per_event(
            [
                _event(state="UNVERIFIED"),
                _event(state="VERIFIED", summary="BBL: A vs B"),
            ]
        )
        verdicts = sweep(rows, {"evt-1": {"live_confirmed": True}}, now=NOW)
        assert len(verdicts) == 1
        # The latest row's summary is the one that gets the new title.
        assert verdicts[0]["prior_state"] == "VERIFIED"

    @pytest.mark.parametrize("row", ["nonsense", None, 7])
    def test_non_dict_rows_are_ignored(self, row):
        rows, _ = latest_per_event([_event(), row])
        assert len(rows) == 1

    def test_the_cli_says_what_it_collapsed(self, tmp_path):
        # Two rows for one event, both still UNVERIFIED but observed separately.
        # The verdict is a promotion, so the collapsed run is actionable and the
        # command exits 0 — and it must say what it collapsed, or "one event
        # audited" looks like a mistake in the ledger.
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            "\n".join(
                json.dumps(_event(summary=summary))
                for summary in ("BBL: A vs B", "BBL: A vs B (re-observed)")
            )
            + "\n",
            encoding="utf-8",
        )
        evidence_file = tmp_path / "evidence.json"
        evidence_file.write_text(
            json.dumps(
                {"evt-1": {"live_confirmed": True, "free_confirmed": True}}
            ),
            encoding="utf-8",
        )
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file),
             "--now", "2026-09-15T08:30:00Z"]
        )
        assert result.returncode == 0, result.stderr
        assert "superseded" in result.stderr
        assert "verified=1" in result.stdout

        # The latest row's summary is the one that gets the promoted title: the
        # superseded row must not be able to lend its text to the verdict.
        payload = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file),
             "--now", "2026-09-15T08:30:00Z", "--json"]
        )
        verdict = json.loads(payload.stdout)["verdicts"][0]
        assert verdict["new_title"] == "BBL: A vs B (re-observed)"
        assert verdict["prior_state"] == "UNVERIFIED"


class TestCli:
    def _write(self, tmp_path, events, evidence):
        events_file = tmp_path / "events.json"
        evidence_file = tmp_path / "evidence.json"
        events_file.write_text(json.dumps({"events": events}), encoding="utf-8")
        evidence_file.write_text(
            json.dumps({"evidence": evidence}), encoding="utf-8"
        )
        return events_file, evidence_file

    def test_wrong_verdict_is_reported(self, tmp_path):
        events_file, evidence_file = self._write(
            tmp_path, [_event()], {"evt-1": {"paid": True}}
        )
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file),
             "--now", "2026-09-15T08:30:00Z"]
        )
        assert result.returncode == 0
        assert "WRONG" in result.stdout
        assert "wrong=1" in result.stdout

    def test_json_mode_is_parseable(self, tmp_path):
        events_file, evidence_file = self._write(
            tmp_path, [_event()], {"evt-1": {"paid": True}}
        )
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file),
             "--now", "2026-09-15T08:30:00Z", "--json"]
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["summary"]["WRONG"] == 1

    def test_all_inconclusive_exits_one(self, tmp_path):
        events_file, evidence_file = self._write(tmp_path, [_event()], {})
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file),
             "--now", "2026-09-15T08:30:00Z"]
        )
        assert result.returncode == 1
        assert "no actionable verdicts" in result.stderr

    def test_bad_now_is_a_usage_error(self, tmp_path):
        events_file, evidence_file = self._write(tmp_path, [_event()], {})
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file),
             "--now", "yesterday"]
        )
        assert result.returncode == 2

    def test_missing_file_is_a_usage_error(self, tmp_path):
        _, evidence_file = self._write(tmp_path, [_event()], {})
        result = _run(
            ["--events", str(tmp_path / "nope.json"),
             "--evidence", str(evidence_file)]
        )
        assert result.returncode == 2

    def test_non_object_evidence_is_a_usage_error(self, tmp_path):
        events_file = tmp_path / "events.json"
        evidence_file = tmp_path / "evidence.json"
        events_file.write_text("[]", encoding="utf-8")
        evidence_file.write_text("[1, 2]", encoding="utf-8")
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file)]
        )
        assert result.returncode == 2

    def test_out_appends_audit_rows(self, tmp_path):
        events_file, evidence_file = self._write(
            tmp_path, [_event()], {"evt-1": {"paid": True}}
        )
        out = tmp_path / "audit.jsonl"
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file),
             "--now", "2026-09-15T08:30:00Z", "--out", str(out),
             "--run-id", "2026-09-15T08:30Z"]
        )
        assert result.returncode == 0
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        assert len(rows) == 1
        assert list(rows[0])[:2] == ["ts", "run_id"]
        assert rows[0]["verdict"] == VERDICT_WRONG

    def test_out_appends_rather_than_truncates(self, tmp_path):
        events_file, evidence_file = self._write(
            tmp_path, [_event()], {"evt-1": {"paid": True}}
        )
        out = tmp_path / "audit.jsonl"
        args = ["--events", str(events_file), "--evidence", str(evidence_file),
                "--now", "2026-09-15T08:30:00Z", "--out", str(out)]
        _run(args)
        _run(args)
        assert len(out.read_text().splitlines()) == 2

    def test_out_dry_run_writes_nothing(self, tmp_path):
        events_file, evidence_file = self._write(
            tmp_path, [_event()], {"evt-1": {"paid": True}}
        )
        out = tmp_path / "audit.jsonl"
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file),
             "--now", "2026-09-15T08:30:00Z", "--out", str(out), "--dry-run"]
        )
        assert result.returncode == 0
        assert "would append" in result.stdout
        assert not out.exists()

    def test_jsonl_events_file_is_accepted(self, tmp_path):
        """The documented input is `events.jsonl` from the telemetry branch."""
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps(_event(event_id="a"))
            + "\n\n"
            + json.dumps(_event(event_id="b"))
            + "\n",
            encoding="utf-8",
        )
        evidence_file = tmp_path / "evidence.json"
        evidence_file.write_text(
            json.dumps({"a": {"paid": True}}), encoding="utf-8"
        )
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file),
             "--now", "2026-09-15T08:30:00Z", "--json"]
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert [row["event_id"] for row in payload["verdicts"]] == ["a", "b"]
        assert payload["summary"]["WRONG"] == 1

    def test_jsonl_with_a_bad_line_is_a_usage_error(self, tmp_path):
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            json.dumps(_event()) + "\nnot json\n", encoding="utf-8"
        )
        evidence_file = tmp_path / "evidence.json"
        evidence_file.write_text("{}", encoding="utf-8")
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file)]
        )
        assert result.returncode == 2

    def test_multi_row_jsonl_evidence_is_rejected_as_non_object(self, tmp_path):
        # Evidence must be a mapping keyed by event_id; a JSONL ledger is a
        # list, so it is a usage error rather than a silent no-op audit.
        events_file, _ = self._write(tmp_path, [_event()], {})
        evidence_file = tmp_path / "evidence.jsonl"
        evidence_file.write_text(
            json.dumps({"a": 1}) + "\n" + json.dumps({"b": 2}) + "\n",
            encoding="utf-8",
        )
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file)]
        )
        assert result.returncode == 2

    def test_json_mode_stays_parseable_when_out_is_used(self, tmp_path):
        events_file, evidence_file = self._write(
            tmp_path, [_event()], {"evt-1": {"paid": True}}
        )
        result = _run(
            ["--events", str(events_file), "--evidence", str(evidence_file),
             "--now", "2026-09-15T08:30:00Z", "--json",
             "--out", str(tmp_path / "audit.jsonl")]
        )
        assert result.returncode == 0
        assert json.loads(result.stdout)["summary"]["WRONG"] == 1
