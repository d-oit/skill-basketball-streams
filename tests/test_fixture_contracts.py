"""Pins the repo fixtures that CI invokes directly.

`.github/workflows/validate.yml` runs the upsert planner and the audit against
the files in `tests/fixtures/`. A workflow step that passes because a fixture
went stale — or because it silently emitted nothing — is worse than no step, so
the *documented outcome* of each fixture is asserted here.

`tests/fixtures/README.md` states the rule these files follow: **inputs may be
hand-written, outputs must be captured.** All four files below are inputs.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
UPSERT = REPO_ROOT / "scripts" / "upsert_events.py"
AUDIT = REPO_ROOT / "scripts" / "audit_events.py"
CANDIDATES = REPO_ROOT / "scripts" / "candidates.py"


def _run(script: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True
    )


class TestUpsertFixture:
    """One verified event must be skipped, one new game created as UNVERIFIED."""

    def test_fixture_files_exist(self):
        for name in ("upsert_existing.json", "upsert_candidates.json"):
            assert (FIXTURES / name).is_file(), name

    def test_plan_is_one_create_and_one_skip(self):
        result = _run(
            UPSERT,
            [
                "--existing", str(FIXTURES / "upsert_existing.json"),
                "--candidates", str(FIXTURES / "upsert_candidates.json"),
                "--json",
            ],
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["summary"] == {"create": 1, "update": 0, "skip": 1}

    def test_the_short_name_candidate_skips_the_verified_event(self):
        # The fixture deliberately writes ["ALBA", "Bayern"] against a stored
        # ["ALBA Berlin", "FC Bayern Muenchen"] — if club-name normalisation
        # regresses, this becomes a second event on a public calendar.
        result = _run(
            UPSERT,
            [
                "--existing", str(FIXTURES / "upsert_existing.json"),
                "--candidates", str(FIXTURES / "upsert_candidates.json"),
                "--json",
            ],
        )
        plan = json.loads(result.stdout)["plan"]
        skipped = [row for row in plan if row["action"] == "skip"]
        assert len(skipped) == 1
        # A skipped row sends nothing: empty title and colour.
        assert skipped[0]["title"] == "" and skipped[0]["color_id"] == ""

    def test_the_new_game_is_created_unverified(self):
        result = _run(
            UPSERT,
            [
                "--existing", str(FIXTURES / "upsert_existing.json"),
                "--candidates", str(FIXTURES / "upsert_candidates.json"),
                "--json",
            ],
        )
        created = [
            row for row in json.loads(result.stdout)["plan"]
            if row["action"] == "create"
        ]
        assert len(created) == 1
        assert created[0]["state"] == "UNVERIFIED"
        assert created[0]["color_id"] == "5"
        assert created[0]["title"].startswith("[UNVERIFIED] ")


class TestVerdictFixture:
    """The audit ledger, honoured by the planner. Two events, one condemned.

    This is the `upsert-verdicts` sensor's exact command line, so the outcome
    belongs here: a sensor that passes because a fixture went stale, or because
    the ledger silently contributed nothing, is worse than no sensor. Both
    directions are asserted, because the failure this guards against is the
    *promotion* — a plan that looks healthy while an event the audit proved was
    paid goes back on the calendar as confirmed.
    """

    def test_fixture_files_exist(self):
        for name in (
            "upsert_verdict_existing.json",
            "upsert_verdict_candidates.json",
            "upsert_verdicts.jsonl",
        ):
            assert (FIXTURES / name).is_file(), name

    def _plan(self, *extra: str) -> dict:
        result = _run(
            UPSERT,
            [
                "--existing", str(FIXTURES / "upsert_verdict_existing.json"),
                "--candidates", str(FIXTURES / "upsert_verdict_candidates.json"),
                *extra,
                "--json",
            ],
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def test_without_the_ledger_both_events_would_be_promoted(self):
        """The control. This is what the guarantee is protecting against."""
        assert self._plan()["summary"] == {"create": 0, "update": 2, "skip": 0}
        assert {
            row["state"] for row in self._plan()["plan"]
        } == {"VERIFIED"}

    def test_the_ledger_holds_the_condemned_event(self):
        plan = self._plan("--verdicts", str(FIXTURES / "upsert_verdicts.jsonl"))
        assert plan["summary"] == {"create": 0, "update": 1, "skip": 1}
        held = [row for row in plan["plan"] if row["action"] == "skip"]
        assert held[0]["event_id"] == "evt-paid-bonn"
        assert held[0]["state"] == "WRONG"
        assert "audit verdict WRONG" in held[0]["reason"]

    def test_an_uncondemned_event_is_still_promoted(self):
        """The other event's verdict is INCONCLUSIVE, so it is not held. A rule
        that held everything would look like a stricter gate while silently
        freezing the whole calendar."""
        plan = self._plan("--verdicts", str(FIXTURES / "upsert_verdicts.jsonl"))
        promoted = [row for row in plan["plan"] if row["action"] == "update"]
        assert [row["event_id"] for row in promoted] == ["evt-legacy-berlin"]


class TestAuditFixture:
    """One WRONG, one promotion, one honestly-inconclusive row."""

    def test_fixture_files_exist(self):
        for name in ("audit_events.json", "audit_evidence.json"):
            assert (FIXTURES / name).is_file(), name

    def test_verdicts_match_the_documented_outcome(self):
        result = _run(
            AUDIT,
            [
                "--events", str(FIXTURES / "audit_events.json"),
                "--evidence", str(FIXTURES / "audit_evidence.json"),
                "--now", "2026-09-15T08:30:00Z",
                "--json",
            ],
        )
        assert result.returncode == 0, result.stderr
        summary = json.loads(result.stdout)["summary"]
        assert summary == {
            "VERIFIED": 1,
            "WRONG": 1,
            "INCONCLUSIVE": 1,
            "actionable": 2,
        }

    def test_paid_event_is_relabelled_never_removed(self):
        result = _run(
            AUDIT,
            [
                "--events", str(FIXTURES / "audit_events.json"),
                "--evidence", str(FIXTURES / "audit_evidence.json"),
                "--now", "2026-09-15T08:30:00Z",
                "--json",
            ],
        )
        verdicts = {row["event_id"]: row for row in json.loads(result.stdout)["verdicts"]}
        paid = verdicts["evt-paid-bayern"]
        assert paid["verdict"] == "WRONG"
        assert paid["new_color_id"] == "7"
        assert paid["new_title"].startswith("[WRONG] ")
        # The event still exists — a verdict relabels, it does not delete.
        assert paid["event_id"] == "evt-paid-bayern"

    def test_unknown_live_status_never_becomes_wrong(self):
        result = _run(
            AUDIT,
            [
                "--events", str(FIXTURES / "audit_events.json"),
                "--evidence", str(FIXTURES / "audit_evidence.json"),
                "--now", "2026-09-15T08:30:00Z",
                "--json",
            ],
        )
        verdicts = {row["event_id"]: row for row in json.loads(result.stdout)["verdicts"]}
        unknown = verdicts["evt-unknown-bonn"]
        assert unknown["verdict"] == "INCONCLUSIVE"
        assert unknown["actionable"] is False
        assert (unknown["new_title"], unknown["new_color_id"]) == ("", "")

    def test_events_fixture_is_jsonl(self):
        # The documented input is the telemetry branch's JSONL ledger, not JSON.
        text = (FIXTURES / "audit_events.json").read_text(encoding="utf-8")
        assert len(text.strip().splitlines()) == 3
        for line in text.strip().splitlines():
            assert json.loads(line)["event_id"]


class TestCandidatesFixture:
    """Recall must have a non-zero denominator, or the CI step proves nothing."""

    def test_ledger_fixture_exists_and_is_jsonl(self):
        text = (FIXTURES / "candidates_ledger.jsonl").read_text(encoding="utf-8")
        rows = [json.loads(line) for line in text.strip().splitlines()]
        assert len(rows) == 2
        assert {row["disposition"] for row in rows} == {"created", "unverifiable"}

    def test_recall_is_half_not_vacuous(self):
        # An empty ledger exits 1 (correct, but useless as a CI gate), so the
        # fixture exists precisely to make the step assert a real number.
        result = _run(
            CANDIDATES,
            ["recall", "--ledger", str(FIXTURES / "candidates_ledger.jsonl")],
        )
        assert result.returncode == 0, result.stderr
        assert "recall=0.5" in result.stdout
        assert "captured=1/2" in result.stdout

    def test_the_unverifiable_row_is_offered_for_retry(self):
        result = _run(
            CANDIDATES,
            ["retry", "--ledger", str(FIXTURES / "candidates_ledger.jsonl")],
        )
        assert result.returncode == 0, result.stderr
        assert "retry EuroLeague|" in result.stdout
        # The captured row must NOT be retried — it is already handled.
        assert "retry Basketball Champions League|" not in result.stdout
