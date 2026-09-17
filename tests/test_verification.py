"""Tests for scripts/verification.py.

The state model is the single source of truth for how an uncertain stream is
labelled, so these tests pin the two properties that make the labelling honest:

1. **Uncertainty wins over the league colour.** An unconfirmed final must not
   keep Tomato red just because colour 11 was "reserved" — the amber *is* the
   signal.
2. **Marking is idempotent.** Re-labelling a title that already carries a prefix
   must not stack `[UNVERIFIED] [UNVERIFIED]`, and switching state must replace
   the old prefix rather than append to it.

Everything here is pure and offline.
"""
from __future__ import annotations

import pytest

from scripts.verification import (
    LEAGUE_OVERRIDE_COLOR_IDS,
    STATE_COLOR_IDS,
    STATE_PREFIXES,
    STATE_UNVERIFIED,
    STATE_VERIFIED,
    STATE_WRONG,
    STATES,
    StateError,
    color_for,
    describe,
    is_verified,
    normalise_state,
    prefix_for,
    state_from_title,
    strip_prefix,
    title_for,
)

FINAL = "[UNVERIFIED] EuroLeague Final Four: Real Madrid vs Olympiacos"


class TestStates:
    def test_four_states_ship_no_fifth_live_now(self):
        # Round 5 chose "Wrong = Peacock" over adding a LIVE_NOW state.
        assert STATES == (STATE_VERIFIED, STATE_UNVERIFIED, STATE_WRONG)
        assert "LIVE_NOW" not in STATES

    def test_every_state_has_a_colour_and_prefix(self):
        for state in STATES:
            assert state in STATE_COLOR_IDS
            assert state in STATE_PREFIXES

    def test_only_verified_has_no_prefix(self):
        assert prefix_for(STATE_VERIFIED) == ""
        assert prefix_for(STATE_UNVERIFIED)
        assert prefix_for(STATE_WRONG)

    def test_colours_match_the_spec(self):
        assert STATE_COLOR_IDS == {
            STATE_VERIFIED: "6",    # Tangerine
            STATE_UNVERIFIED: "5",   # Banana
            STATE_WRONG: "7",        # Peacock
        }


class TestNormaliseState:
    def test_known_states_round_trip(self):
        for state in STATES:
            assert normalise_state(state) == state

    def test_is_case_and_whitespace_insensitive(self):
        assert normalise_state("  unverified ") == STATE_UNVERIFIED
        assert normalise_state("wrong") == STATE_WRONG

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_falls_back_to_default(self, value):
        assert normalise_state(value) == STATE_UNVERIFIED

    def test_empty_can_fall_back_to_an_explicit_default(self):
        assert normalise_state(None, default=STATE_VERIFIED) == STATE_VERIFIED

    def test_unknown_state_raises(self):
        with pytest.raises(StateError, match="unknown state"):
            normalise_state("PROBABLY")

    def test_never_degrades_an_unknown_value_to_verified(self):
        # Failing safe means failing cautious: a garbled upstream payload must
        # not be silently promoted to a confirmed stream.
        try:
            normalise_state("yes-live-free-4k")
        except StateError:
            pass
        else:  # pragma: no cover - defensive
            pytest.fail("an unrecognised state must raise, not default")


class TestPrefixing:
    def test_title_gets_the_state_prefix(self):
        assert title_for(STATE_UNVERIFIED, "BBL: ALBA vs Bayern") == (
            "[UNVERIFIED] BBL: ALBA vs Bayern"
        )
        assert title_for(STATE_WRONG, "BBL: ALBA vs Bayern") == (
            "[WRONG] BBL: ALBA vs Bayern"
        )

    def test_verified_title_is_unchanged(self):
        assert title_for(STATE_VERIFIED, "BBL: ALBA vs Bayern") == "BBL: ALBA vs Bayern"

    def test_applying_the_same_state_twice_is_idempotent(self):
        once = title_for(STATE_UNVERIFIED, "BBL: ALBA vs Bayern")
        twice = title_for(STATE_UNVERIFIED, once)
        assert twice == once
        assert twice.count("[UNVERIFIED]") == 1

    def test_switching_state_replaces_the_prefix(self):
        assert title_for(STATE_VERIFIED, FINAL) == (
            "EuroLeague Final Four: Real Madrid vs Olympiacos"
        )
        assert title_for(STATE_WRONG, FINAL) == (
            "[WRONG] EuroLeague Final Four: Real Madrid vs Olympiacos"
        )

    def test_strip_prefix_removes_exactly_one_known_prefix(self):
        assert strip_prefix(FINAL) == (
            "EuroLeague Final Four: Real Madrid vs Olympiacos"
        )
        assert strip_prefix("no prefix here") == "no prefix here"
        assert strip_prefix("") == ""

    def test_strip_prefix_handles_none(self):
        assert strip_prefix(None) == ""  # type: ignore[arg-type]


class TestStateFromTitle:
    def test_round_trips_every_state(self):
        for state in STATES:
            title = title_for(state, "BBL: ALBA vs Bayern")
            assert state_from_title(title) == state

    def test_an_unprefixed_title_is_verified(self):
        # Events created before the state machine existed carry no prefix and
        # were treated as confirmed. Reading them as UNVERIFIED would have the
        # runtime repaint a whole calendar amber on its first run.
        assert state_from_title("BBL: ALBA Berlin vs FC Bayern Muenchen") == STATE_VERIFIED

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_input_is_verified_not_an_error(self, value):
        assert state_from_title(value) == STATE_VERIFIED  # type: ignore[arg-type]

    def test_a_prefix_must_be_at_the_start(self):
        assert state_from_title("BBL: A vs B [UNVERIFIED]") == STATE_VERIFIED

    def test_round_trip_survives_repeated_marking(self):
        title = title_for(STATE_WRONG, title_for(STATE_UNVERIFIED, "BBL: A vs B"))
        assert state_from_title(title) == STATE_WRONG


class TestColourPrecedence:
    def test_verified_keeps_the_league_colour(self):
        assert color_for(STATE_VERIFIED, "11") == "11"
        assert color_for(STATE_VERIFIED, "2") == "2"

    def test_verified_without_a_league_colour_defaults_to_tangerine(self):
        assert color_for(STATE_VERIFIED, "") == "6"
        assert color_for(STATE_VERIFIED) == "6"

    def test_uncertain_states_override_the_league_colour(self):
        for league_colour in sorted(LEAGUE_OVERRIDE_COLOR_IDS):
            assert color_for(STATE_UNVERIFIED, league_colour) == "5"
            assert color_for(STATE_WRONG, league_colour) == "7"

    def test_wrong_and_unverified_are_distinguishable(self):
        assert color_for(STATE_UNVERIFIED) != color_for(STATE_WRONG)


class TestHelpers:
    def test_is_verified_only_for_verified(self):
        assert is_verified(STATE_VERIFIED)
        assert not is_verified(STATE_UNVERIFIED)
        assert not is_verified(None)

    def test_describe_is_human_readable_for_every_state(self):
        for state in STATES:
            assert describe(state)

    def test_describe_raises_on_unknown(self):
        with pytest.raises(StateError):
            describe("NOPE")

    def test_module_runs_as_a_script(self):
        # The __main__ block is a human eyeball check; it must not explode.
        import subprocess
        import sys
        from pathlib import Path

        script = Path(__file__).resolve().parent.parent / "scripts" / "verification.py"
        result = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "VERIFIED" in result.stdout
