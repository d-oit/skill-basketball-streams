"""Tests for scripts/upsert_events.py.

Phase 2 writes to a **public** calendar, so the acceptance criteria in
`live-stream-runtime-spec.md` §16 are tested literally:

* a game with confirmed free access is created `VERIFIED` / colour `6`;
* a plausible live stream without free confirmation is created `UNVERIFIED`
  (`[UNVERIFIED]` prefix, colour `5`) — surfaced, but visibly uncertain;
* a re-run over the same window plans **zero** creates;
* verified events are left byte-identical (the plan carries empty title/colour,
  so a writer has nothing to send).

Everything is pure: `plan_upsert` never touches a network or a credential.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.upsert_events import (
    ACTION_CREATE,
    ACTION_SKIP,
    ACTION_UPDATE,
    MATCH_WINDOW,
    STATE_WRONG,
    _team_matches,
    events_match,
    game_key,
    load_verdicts,
    parse_dt,
    plan_upsert,
    summarise,
    verdict_wrong,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "upsert_events.py"


def _candidate(**overrides) -> dict:
    row = {
        "league": "BBL",
        "teams": ["ALBA Berlin", "FC Bayern Muenchen"],
        "start": "2026-09-16T19:00:00+02:00",
        "state": "UNVERIFIED",
        "summary": "BBL: ALBA Berlin vs FC Bayern Muenchen",
    }
    row.update(overrides)
    return row


def _existing(**overrides) -> dict:
    row = {
        "event_id": "evt-1",
        "league": "BBL",
        "teams": ["ALBA Berlin", "FC Bayern Muenchen"],
        "start": "2026-09-16T17:00:00+00:00",
        "state": "UNVERIFIED",
        "summary": "BBL: ALBA Berlin vs FC Bayern Muenchen",
    }
    row.update(overrides)
    return row


# A stored entry that records no teams — the shape 78 of 102 live calendar events
# had on 2026-09-15, most of them predating the description writer.
def _teams_less(**overrides) -> dict:
    row = {
        "event_id": "evt-live",
        "league": "",
        "teams": [],
        "start": "2026-09-16T19:00:00+02:00",
        "state": "VERIFIED",
        "summary": "ALBA BERLIN vs. NINERS Chemnitz (easyCredit BBL)",
    }
    row.update(overrides)
    return row


class TestParseDt:
    def test_parses_z_and_offset_forms(self):
        assert parse_dt("2026-09-16T17:00:00Z") == datetime(
            2026, 9, 16, 17, tzinfo=timezone.utc
        )
        assert parse_dt("2026-09-16T19:00:00+02:00").astimezone(timezone.utc) == (
            datetime(2026, 9, 16, 17, tzinfo=timezone.utc)
        )

    def test_naive_input_is_assumed_utc(self):
        assert parse_dt("2026-09-16T17:00:00").tzinfo is timezone.utc

    @pytest.mark.parametrize("value", [None, "", "   ", "not-a-date", 12345])
    def test_bad_input_returns_none(self, value):
        assert parse_dt(value) is None


class TestGameKey:
    def test_team_order_does_not_change_the_key(self):
        forward = game_key("BBL", ["ALBA", "Bayern"], "2026-09-16T19:00:00+02:00")
        reverse = game_key("BBL", ["Bayern", "ALBA"], "2026-09-16T19:00:00+02:00")
        assert forward == reverse

    def test_two_games_share_a_key_across_timezones(self):
        cest = game_key("BBL", ["ALBA", "Bayern"], "2026-09-16T19:00:00+02:00")
        utc = game_key("BBL", ["ALBA", "Bayern"], "2026-09-16T17:00:00Z")
        assert cest == utc

    def test_different_games_get_different_keys(self):
        first = game_key("BBL", ["ALBA", "Bayern"], "2026-09-16T19:00:00+02:00")
        second = game_key("BBL", ["ALBA", "Bonn"], "2026-09-16T19:00:00+02:00")
        assert first != second

    def test_vs_string_form_is_accepted(self):
        assert game_key("BBL", "ALBA vs Bayern", "2026-09-16T19:00:00Z") == game_key(
            "BBL", ["Bayern", "ALBA"], "2026-09-16T19:00:00Z"
        )


class TestEventsMatch:
    def test_same_slot_same_teams_matches(self):
        assert events_match(_existing(), _candidate())

    def test_outside_the_window_does_not_match(self):
        late = _candidate(start="2026-09-16T21:00:00+02:00")
        assert not events_match(_existing(), late)

    def test_exactly_on_the_window_edge_matches(self):
        edge = parse_dt("2026-09-16T17:00:00Z") + MATCH_WINDOW
        assert events_match(
            _existing(), _candidate(start=edge.isoformat())
        )

    def test_different_teams_do_not_match(self):
        assert not events_match(_existing(), _candidate(teams=["ALBA", "Bonn"]))

    def test_only_one_shared_team_does_not_match(self):
        # Two different games can share one team name on a double-header day.
        assert not events_match(
            _existing(teams=["ALBA", "Bayern"]), _candidate(teams=["ALBA", "Ulm"])
        )

    def test_different_leagues_do_not_match(self):
        assert not events_match(_existing(), _candidate(league="EuroLeague"))

    def test_missing_start_never_matches(self):
        assert not events_match(_existing(start=None), _candidate())
        assert not events_match(_existing(), _candidate(start=None))

    def test_alternative_key_names_are_accepted(self):
        assert events_match(
            {"startTime": "2026-09-16T17:00:00Z", "teams": ["ALBA", "Bayern"]},
            _candidate(),
        )

    @pytest.mark.parametrize(
        ("short", "long"),
        [
            ("ALBA", "ALBA Berlin"),
            ("Bayern", "FC Bayern Muenchen"),
            ("Bonn", "Telekom Baskets Bonn"),
            ("FC Bayern Basketball", "FC Bayern"),
        ],
    )
    def test_short_and_long_club_names_are_the_same_club(self, short, long):
        # The §17 requirement is *zero* duplicate events: one source writing the
        # short name and another the full name must not create two events.
        assert _team_matches(short, long)
        assert _team_matches(long, short)

    def test_shared_city_alone_does_not_merge_two_clubs(self):
        # Nested tokens are allowed, but "Berlin" is not ALBA Berlin's identity
        # anyway — this asserts the *pair* rule still guards the match.
        assert not events_match(
            _existing(teams=["ALBA Berlin", "Bamberg"]),
            _candidate(teams=["ALBA Berlin", "Bonn"]),
        )


class TestATeamslessEventIsNotAWildcard:
    """The defect, as measured on the live calendar on 2026-09-15.

    Of 102 events, **78 carried no `Teams:` line** — most predate the description
    writer. `events_match` skipped the teams check entirely whenever either side
    was empty, so the 30-minute window was the only gate, and the league check
    only applied when the stored event happened to carry a `League:` line too
    (11 of the 78 had neither, so nothing stood between a candidate and a
    match). Every one of the 78 is `VERIFIED`, and a `VERIFIED` match is a
    **skip** — so the effect was not a duplicate but a **silently dropped game**.

    These tests are the shape of that measurement, shrunk to a fixture.
    """

    def _alba_chemnitz(self, **overrides) -> dict:
        """A candidate for the game `_teams_less`'s title actually names."""
        row = _candidate(
            league="",
            teams=["ALBA Berlin", "NINERS Chemnitz"],
            summary="BBL: ALBA Berlin vs NINERS Chemnitz",
        )
        row.update(overrides)
        return row

    def test_an_entry_that_names_the_game_still_matches_it(self):
        assert events_match(_teams_less(), self._alba_chemnitz())

    def test_a_different_game_in_the_same_slot_no_longer_matches(self):
        """The whole point: this used to match, and the candidate was dropped."""
        other = _candidate(
            league="", teams=["Bonn", "Ulm"], summary="Bonn vs Ulm"
        )
        assert not events_match(_teams_less(), other)

    def test_a_tournament_day_entry_no_longer_swallows_a_fixture(self):
        """67 of the 78 live entries look like this and cover a whole day."""
        day = _teams_less(
            summary="FIBA U20 Women's EuroBasket 2026 - Day 1 - FREE Live Stream",
            league="FIBA U20 Women's EuroBasket 2026",
        )
        assert not events_match(day, _candidate(league="FIBA U20 Women's EuroBasket 2026"))

    def test_a_dash_pairing_is_read_like_any_other(self):
        """Four live entries write the pairing `A - B`. A `vs`-only rule would
        have made those games unmatchable and created real duplicates."""
        entry = _teams_less(summary="Nagasaki Velca - Telekom Baskets Bonn (Game 2)")
        assert events_match(
            entry,
            _candidate(league="", teams=["Nagasaki Velca", "Telekom Baskets Bonn"]),
        )

    def test_short_and_long_spellings_still_match_with_no_teams_line(self):
        entry = _teams_less(summary="ALBA BERLIN vs. NINERS Chemnitz (easyCredit BBL)")
        assert events_match(entry, self._alba_chemnitz(teams=["ALBA", "Chemnitz"]))

    def test_the_description_is_read_as_well_as_the_summary(self):
        """Either side may be the only one present, so both are consulted."""
        entry = _teams_less(
            summary="A fixture with no club names",
            description="Watch ALBA Berlin vs NINERS Chemnitz live",
        )
        assert events_match(entry, self._alba_chemnitz())

    def test_a_candidate_with_no_teams_is_unchanged(self):
        """Nothing to check it against — `extract_candidates.py` refuses these
        before they reach the planner, so the temporal gate is all that applies."""
        assert events_match(_teams_less(), _candidate(league="", teams=[], summary=""))

    def test_a_teams_line_on_both_sides_still_wins_over_the_text(self):
        """The recorded Teams line is the primary identity, not the summary."""
        entry = _teams_less(
            teams=["Bonn", "Ulm"],
            summary="ALBA BERLIN vs. NINERS Chemnitz",
        )
        assert not events_match(entry, self._alba_chemnitz())

    def test_the_leagues_still_have_to_agree(self):
        entry = _teams_less(
            teams=["ALBA Berlin", "NINERS Chemnitz"],
            league="BBL",
        )
        assert not events_match(entry, self._alba_chemnitz(league="EuroLeague"))

    def test_a_verified_entry_that_names_a_different_game_plans_a_create(self):
        """The product-level consequence, not just the predicate.

        Before this change the plan row was `skip` — "verified event exists" — and
        the fixture never reached the calendar.
        """
        plan = plan_upsert(
            [_teams_less()],
            [_candidate(league="", teams=["Bonn", "Ulm"], summary="Bonn vs Ulm")],
        )
        assert plan[0]["action"] == ACTION_CREATE
        assert plan[0]["state"] == "UNVERIFIED"

    def test_a_verified_entry_that_names_the_same_game_still_skips(self):
        """The half that must not regress: a confirmed game is never rewritten."""
        plan = plan_upsert([_teams_less()], [self._alba_chemnitz()])
        assert plan[0]["action"] == ACTION_SKIP
        assert "verified" in plan[0]["reason"]


class TestPlanUpsert:
    def test_empty_calendar_creates(self):
        plan = plan_upsert([], [_candidate(state="VERIFIED")])
        assert [row["action"] for row in plan] == [ACTION_CREATE]
        assert plan[0]["color_id"] == "6"
        assert plan[0]["title"] == "BBL: ALBA Berlin vs FC Bayern Muenchen"

    def test_unconfirmed_free_access_is_labelled_not_dropped(self):
        plan = plan_upsert([], [_candidate(state="UNVERIFIED")])
        assert plan[0]["action"] == ACTION_CREATE
        assert plan[0]["color_id"] == "5"
        assert plan[0]["title"].startswith("[UNVERIFIED] ")

    def test_verified_event_is_never_touched(self):
        plan = plan_upsert([_existing(state="VERIFIED")], [_candidate()])
        assert plan[0]["action"] == ACTION_SKIP
        # Empty title/colour is what makes "never touched" enforceable: a writer
        # given these values has nothing to send.
        assert plan[0]["title"] == ""
        assert plan[0]["color_id"] == ""

    def test_wrong_verdict_survives_a_fresh_candidate(self):
        plan = plan_upsert([_existing(state="WRONG")], [_candidate()])
        assert plan[0]["action"] == ACTION_SKIP
        assert "WRONG" in plan[0]["reason"]

    def test_unverified_is_promoted_when_access_is_confirmed(self):
        plan = plan_upsert(
            [_existing(state="UNVERIFIED")], [_candidate(state="VERIFIED")]
        )
        assert plan[0]["action"] == ACTION_UPDATE
        assert plan[0]["state"] == "VERIFIED"
        assert plan[0]["color_id"] == "6"
        assert not plan[0]["title"].startswith("[")
        assert "promoted" in plan[0]["reason"]

    def test_unverified_is_refreshed_without_promotion(self):
        plan = plan_upsert([_existing()], [_candidate()])
        assert plan[0]["action"] == ACTION_UPDATE
        assert plan[0]["color_id"] == "5"

    def test_unverified_is_replaced_but_never_demoted_from_wrong(self):
        plan = plan_upsert([_existing(state="WRONG")], [_candidate(state="WRONG")])
        assert plan[0]["action"] == ACTION_SKIP

    def test_league_colour_override_survives_promotion(self):
        plan = plan_upsert(
            [],
            [
                _candidate(
                    state="VERIFIED",
                    league="EuroLeague",
                    league_color_id="11",
                    summary="EuroLeague Final: Real vs Olympiacos",
                )
            ],
        )
        assert plan[0]["color_id"] == "11"

    def test_uncertain_state_overrides_the_league_colour(self):
        plan = plan_upsert(
            [],
            [
                _candidate(
                    state="UNVERIFIED",
                    league="EuroLeague",
                    league_color_id="11",
                )
            ],
        )
        assert plan[0]["color_id"] == "5"

    def test_the_league_colour_is_carried_alongside_the_state_colour(self):
        """`color_id` alone cannot be reversed: Banana is the state, not the league.

        A later `VERIFIED` promotion has to restore the league colour, so the
        plan row carries it separately for `scripts/event_ledger.py` to record.
        """
        plan = plan_upsert(
            [], [_candidate(state="UNVERIFIED", league="EuroLeague", league_color_id="11")]
        )
        assert plan[0]["color_id"] == "5"
        assert plan[0]["league_color_id"] == "11"

    def test_the_league_colour_defaults_only_when_absent(self):
        assert plan_upsert([], [_candidate()])[0]["league_color_id"] == "6"

    def test_evidence_is_carried_on_the_plan_row(self):
        """The ledger records it from the plan, so it must survive planning."""
        plan = plan_upsert(
            [],
            [_candidate(evidence={"live_confirmed": True, "free_confirmed": True})],
        )
        assert plan[0]["evidence"] == {
            "live_confirmed": True,
            "free_confirmed": True,
        }

    def test_a_candidate_with_no_evidence_puts_no_key_on_the_plan(self):
        assert "evidence" not in plan_upsert([], [_candidate()])[0]

    def test_a_promotion_keeps_the_new_evidence(self):
        """The promotion is the moment the evidence is worth recording."""
        plan = plan_upsert(
            [_existing(state="UNVERIFIED")],
            [
                _candidate(
                    state="VERIFIED",
                    evidence={"live_confirmed": True, "free_confirmed": True},
                )
            ],
        )
        assert plan[0]["action"] == ACTION_UPDATE
        assert plan[0]["evidence"]["free_confirmed"] is True

    def test_non_dict_candidates_are_skipped(self):
        assert plan_upsert([], ["nonsense", None]) == []

    def test_rerun_over_the_applied_plan_plans_zero_creates(self):
        """The Phase 2 acceptance test: no duplicate events on a re-run."""
        candidates = [
            _candidate(),
            _candidate(league="EuroLeague", summary="EuroLeague: Real vs Olympiacos"),
        ]
        calendar: list[dict] = []
        first = plan_upsert(calendar, candidates)
        assert [row["action"] for row in first] == [ACTION_CREATE, ACTION_CREATE]

        # Apply the plan the way a writer would: the written event carries the
        # planned state and keeps the candidate's identity fields.
        for row, candidate in zip(first, candidates):
            calendar.append(
                {**candidate, "state": row["state"], "event_id": row["game_key"]}
            )

        second = plan_upsert(calendar, candidates)
        assert all(row["action"] != ACTION_CREATE for row in second)
        assert [row["action"] for row in second] == [ACTION_UPDATE, ACTION_UPDATE]

    def test_verified_event_is_stable_across_repeated_runs(self):
        calendar = [_existing(state="VERIFIED", summary="stable")]
        for _ in range(3):
            plan = plan_upsert(calendar, [_candidate(state="VERIFIED")])
            assert plan[0]["action"] == ACTION_SKIP
            assert (plan[0]["title"], plan[0]["color_id"]) == ("", "")


def _verdict(event_id: str = "evt-1", **overrides) -> dict:
    row = {
        "ts": "2026-09-18T08:31:00+00:00",
        "run_id": "2026-09-17T08:30Z",
        "event_id": event_id,
        "verdict": "WRONG",
        "reason": "audit found paid access, not a free stream",
        "actionable": True,
        "new_state": "WRONG",
        "new_color_id": "7",
    }
    row.update(overrides)
    return row


class TestVerdictWarnings:
    """The §17 hard requirement, enforced from the record rather than a prefix.

    `plan_upsert` reads a stored `WRONG` off the calendar, and the calendar
    recovers it from the `[WRONG] ` title prefix — which nothing writes.
    `audit_events` computes that title and puts it in `audit.jsonl`, and no
    component applied it, so the `WRONG` rows in the policy table were
    unreachable on the real path: an event the audit had proved was paid stayed
    on the calendar looking like a game awaiting confirmation, and the next
    run's fresh guess was free to promote it to `VERIFIED`.
    """

    def test_without_a_verdict_a_promotion_still_happens(self):
        """The control: this is the behaviour the ledger exists to stop."""
        plan = plan_upsert(
            [_existing()], [_candidate(state="VERIFIED")], verdicts={}
        )
        assert plan[0]["action"] == ACTION_UPDATE
        assert plan[0]["state"] == "VERIFIED"

    def test_a_wrong_verdict_holds_the_event(self):
        plan = plan_upsert(
            [_existing()],
            [_candidate(state="VERIFIED")],
            verdicts={"evt-1": _verdict()},
        )
        assert plan[0]["action"] == ACTION_SKIP
        assert plan[0]["state"] == STATE_WRONG

    def test_the_skip_carries_nothing_a_writer_could_send(self):
        """A skip row is inert: empty title and colour, so it cannot relabel."""
        plan = plan_upsert(
            [_existing()], [_candidate()], verdicts={"evt-1": _verdict()}
        )
        assert plan[0]["title"] == ""
        assert plan[0]["color_id"] == ""

    def test_the_reason_names_when_the_verdict_was_recorded(self):
        """A human has to be able to find the audit row that decided this."""
        plan = plan_upsert(
            [_existing()], [_candidate()], verdicts={"evt-1": _verdict()}
        )
        assert "WRONG" in plan[0]["reason"]
        assert "2026-09-18T08:31:00+00:00" in plan[0]["reason"]

    def test_it_outranks_the_calendars_own_verified_label(self):
        """The audit condemned it; the calendar's label is what was wrong."""
        plan = plan_upsert(
            [_existing(state="VERIFIED")],
            [_candidate(state="VERIFIED")],
            verdicts={"evt-1": _verdict()},
        )
        assert plan[0]["action"] == ACTION_SKIP

    @pytest.mark.parametrize(
        "verdict", ["INCONCLUSIVE", "VERIFIED", "", None]
    )
    def test_only_wrong_holds_an_event(self, verdict):
        plan = plan_upsert(
            [_existing()],
            [_candidate(state="VERIFIED")],
            verdicts={"evt-1": _verdict(verdict=verdict)},
        )
        assert plan[0]["action"] == ACTION_UPDATE

    def test_a_verdict_for_another_event_is_not_applied(self):
        plan = plan_upsert(
            [_existing()],
            [_candidate(state="VERIFIED")],
            verdicts={"evt-other": _verdict(event_id="evt-other")},
        )
        assert plan[0]["action"] == ACTION_UPDATE

    def test_a_verdict_cannot_block_a_create(self):
        """No match means no event_id, so no verdict can be joined to it."""
        plan = plan_upsert(
            [], [_candidate()], verdicts={"evt-1": _verdict()}
        )
        assert plan[0]["action"] == ACTION_CREATE

    def test_case_does_not_matter(self):
        assert verdict_wrong({"verdict": "wrong"}) is True
        assert verdict_wrong({"verdict": " Wrong "}) is True
        assert verdict_wrong({}) is False
        assert verdict_wrong(None) is False

    def test_the_decision_is_made_per_event(self):
        """One condemned event must not hold the others in the same plan."""
        plan = plan_upsert(
            [_existing(), _existing(event_id="evt-2", teams=["A", "B"])],
            [_candidate(state="VERIFIED"), _candidate(state="VERIFIED", teams=["A", "B"])],
            verdicts={"evt-1": _verdict()},
        )
        assert [row["action"] for row in plan] == [ACTION_SKIP, ACTION_UPDATE]


class TestLoadVerdicts:
    def _ledger(self, tmp_path: Path, rows: list[dict]) -> Path:
        path = tmp_path / "audit.jsonl"
        path.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
        )
        return path

    def test_jsonl_is_read(self, tmp_path):
        path = self._ledger(tmp_path, [_verdict()])
        assert set(load_verdicts(path)) == {"evt-1"}

    def test_a_single_row_ledger_is_read_as_a_row_not_a_wrapper(self, tmp_path):
        """One row is valid JSON, so it must not be read as `{"verdicts": ...}`.

        Getting this wrong yields *no* verdicts, which is precisely the state in
        which a condemned event gets promoted: a silent empty, not a cosmetic
        one.
        """
        path = self._ledger(tmp_path, [_verdict()])
        loaded = load_verdicts(path)
        assert loaded["evt-1"]["verdict"] == "WRONG"
        plan = plan_upsert(
            [_existing()], [_candidate(state="VERIFIED")], verdicts=loaded
        )
        assert plan[0]["action"] == ACTION_SKIP

    def test_the_last_row_for_an_event_wins(self, tmp_path):
        path = self._ledger(
            tmp_path,
            [
                _verdict(verdict="WRONG"),
                _verdict(verdict="INCONCLUSIVE", ts="2026-09-19T08:31:00+00:00"),
            ],
        )
        loaded = load_verdicts(path)
        assert loaded["evt-1"]["verdict"] == "INCONCLUSIVE"
        # And the planner therefore does not hold the event.
        plan = plan_upsert(
            [_existing()], [_candidate(state="VERIFIED")], verdicts=loaded
        )
        assert plan[0]["action"] == ACTION_UPDATE

    def test_a_json_document_is_accepted(self, tmp_path):
        path = tmp_path / "audit.json"
        path.write_text(json.dumps({"verdicts": [_verdict()]}), encoding="utf-8")
        assert set(load_verdicts(path)) == {"evt-1"}

    def test_rows_without_an_event_id_are_dropped(self, tmp_path):
        path = self._ledger(tmp_path, [_verdict(event_id=""), _verdict()])
        assert set(load_verdicts(path)) == {"evt-1"}

    def test_a_missing_ledger_is_a_usage_error(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            load_verdicts(tmp_path / "nope.jsonl")
        assert exc.value.code == 2

    def test_a_corrupt_line_is_a_usage_error(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        path.write_text("{not json}\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            load_verdicts(path)
        assert exc.value.code == 2


class TestSummarise:
    def test_counts_each_action(self):
        plan = [
            {"action": ACTION_CREATE},
            {"action": ACTION_UPDATE},
            {"action": ACTION_SKIP},
            {"action": ACTION_SKIP},
        ]
        assert summarise(plan) == {ACTION_CREATE: 1, ACTION_UPDATE: 1, ACTION_SKIP: 2}

    def test_empty_plan_is_all_zero(self):
        assert summarise([]) == {ACTION_CREATE: 0, ACTION_UPDATE: 0, ACTION_SKIP: 0}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestCli:
    def _write(self, tmp_path, existing, candidates):
        existing_file = tmp_path / "existing.json"
        candidate_file = tmp_path / "candidates.json"
        existing_file.write_text(json.dumps({"events": existing}), encoding="utf-8")
        candidate_file.write_text(
            json.dumps({"candidates": candidates}), encoding="utf-8"
        )
        return existing_file, candidate_file

    def test_plan_is_reported_without_applying(self, tmp_path):
        existing_file, candidate_file = self._write(tmp_path, [], [_candidate()])
        result = _run(
            ["--existing", str(existing_file), "--candidates", str(candidate_file)]
        )
        assert result.returncode == 0
        assert "create=1" in result.stdout
        assert "SKIP" not in result.stdout

    def test_json_mode_emits_the_plan(self, tmp_path):
        existing_file, candidate_file = self._write(
            tmp_path, [_existing(state="VERIFIED")], [_candidate()]
        )
        result = _run(
            ["--existing", str(existing_file), "--candidates", str(candidate_file),
             "--json"]
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["summary"]["skip"] == 1

    def test_the_verdict_ledger_changes_the_plan(self, tmp_path):
        existing_file, candidate_file = self._write(
            tmp_path, [_existing()], [_candidate(state="VERIFIED")]
        )
        ledger = tmp_path / "audit.jsonl"
        ledger.write_text(json.dumps(_verdict()) + "\n", encoding="utf-8")
        result = _run(
            [
                "--existing", str(existing_file),
                "--candidates", str(candidate_file),
                "--verdicts", str(ledger),
            ]
        )
        assert result.returncode == 0
        assert "audit verdict WRONG stands" in result.stdout
        assert "skip=1" in result.stdout

    def test_missing_verdict_ledger_is_a_usage_error(self, tmp_path):
        existing_file, candidate_file = self._write(tmp_path, [], [_candidate()])
        result = _run(
            [
                "--existing", str(existing_file),
                "--candidates", str(candidate_file),
                "--verdicts", str(tmp_path / "nope.jsonl"),
            ]
        )
        assert result.returncode == 2
        assert "not found" in result.stderr

    def test_no_candidates_exits_one(self, tmp_path):
        existing_file, candidate_file = self._write(tmp_path, [], [])
        result = _run(
            ["--existing", str(existing_file), "--candidates", str(candidate_file)]
        )
        assert result.returncode == 1
        assert "no candidates" in result.stderr

    def test_missing_file_is_a_usage_error(self, tmp_path):
        _, candidate_file = self._write(tmp_path, [], [_candidate()])
        result = _run(
            ["--existing", str(tmp_path / "nope.json"),
             "--candidates", str(candidate_file)]
        )
        assert result.returncode == 2

    def test_malformed_json_is_a_usage_error(self, tmp_path):
        existing_file, candidate_file = self._write(tmp_path, [], [_candidate()])
        existing_file.write_text("{not json", encoding="utf-8")
        result = _run(
            ["--existing", str(existing_file), "--candidates", str(candidate_file)]
        )
        assert result.returncode == 2

    def test_bare_list_payload_is_accepted(self, tmp_path):
        existing_file = tmp_path / "existing.json"
        candidate_file = tmp_path / "candidates.json"
        existing_file.write_text("[]", encoding="utf-8")
        candidate_file.write_text(json.dumps([_candidate()]), encoding="utf-8")
        result = _run(
            ["--existing", str(existing_file), "--candidates", str(candidate_file)]
        )
        assert result.returncode == 0
