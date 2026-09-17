"""Unit tests for scripts/color_mapping.py."""
import json
import re
from pathlib import Path

import pytest

from scripts.color_mapping import (
    DEFAULT_LEAGUE_COLOR_ID,
    get_color_id,
    COLOR_MAPPING_TABLE,
)
from scripts.verification import FORBIDDEN_LEAGUE_COLOR_IDS

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestGetColorId:
    """Test cases for the get_color_id function."""

    # Test default color (Tangerine/Orange "6") for BBL
    def test_bbl_regular(self):
        assert get_color_id("BBL", "regular") == "6"

    def test_bbl_playoff(self):
        assert get_color_id("BBL", "playoff") == "6"

    def test_bbl_final(self):
        assert get_color_id("BBL", "final") == "6"

    # Test EuroLeague regular season -> default "6"
    def test_euroleague_regular(self):
        assert get_color_id("EuroLeague", "regular") == "6"

    def test_euroleague_playoff(self):
        assert get_color_id("EuroLeague", "playoff") == "6"

    # Test EuroLeague finals -> Tomato/Red "11"
    def test_euroleague_final(self):
        assert get_color_id("EuroLeague", "final") == "11"

    def test_euroleague_finals(self):
        assert get_color_id("EuroLeague", "finals") == "11"

    def test_euroleague_semifinal(self):
        assert get_color_id("EuroLeague", "semifinal") == "11"

    def test_euroleague_semifinals(self):
        assert get_color_id("EuroLeague", "semifinals") == "11"

    def test_euroleague_championship(self):
        assert get_color_id("EuroLeague", "championship") == "11"

    # Test FIBA -> Sage/Green "2"
    def test_fiba_regular(self):
        assert get_color_id("FIBA", "regular") == "2"

    def test_fiba_international(self):
        assert get_color_id("FIBA", "international") == "2"

    def test_fiba_world_cup(self):
        assert get_color_id("FIBA", "world cup") == "2"

    def test_fiba_eurobasket(self):
        assert get_color_id("FIBA", "eurobasket") == "2"

    # Test Basketball Champions League
    def test_bcl_regular(self):
        assert get_color_id("Basketball Champions League", "regular") == "6"

    def test_bcl_final(self):
        assert get_color_id("Basketball Champions League", "final") == "11"

    def test_bcl_finals(self):
        assert get_color_id("Basketball Champions League", "finals") == "11"

    # Test case insensitivity
    def test_case_insensitive_league(self):
        assert get_color_id("bbl", "regular") == "6"
        assert get_color_id("BBL", "regular") == "6"
        assert get_color_id("Bbl", "regular") == "6"

    def test_case_insensitive_event_type(self):
        assert get_color_id("EuroLeague", "FINAL") == "11"
        assert get_color_id("EuroLeague", "Final") == "11"
        assert get_color_id("EuroLeague", "FINALS") == "11"

    # Test default event_type
    def test_default_event_type(self):
        assert get_color_id("BBL") == "6"
        assert get_color_id("EuroLeague") == "6"
        assert get_color_id("FIBA") == "2"

    # Test edge cases
    def test_whitespace_handling(self):
        assert get_color_id("  BBL  ", "  regular  ") == "6"
        assert get_color_id("EuroLeague", " final ") == "11"

    # Test error handling
    def test_empty_league_raises(self):
        with pytest.raises(ValueError, match="league must be a non-empty string"):
            get_color_id("", "regular")

    def test_none_league_raises(self):
        with pytest.raises(ValueError, match="league must be a non-empty string"):
            get_color_id(None, "regular")

    # Test alternative league names
    def test_euro_league_with_space(self):
        assert get_color_id("Euro League", "final") == "11"

    def test_bcl_abbreviation(self):
        assert get_color_id("BCL", "final") == "11"


class TestColorMappingTable:
    """Test that COLOR_MAPPING_TABLE matches the function behavior."""

    @pytest.mark.parametrize("league,event_type,expected", [
        ("BBL", "regular", "6"),
        ("BBL", "playoff", "6"),
        ("BBL", "final", "6"),
        ("EuroLeague", "regular", "6"),
        ("EuroLeague", "playoff", "6"),
        ("EuroLeague", "final", "11"),
        ("EuroLeague", "finals", "11"),
        ("EuroLeague", "semifinal", "11"),
        ("EuroLeague", "championship", "11"),
        ("FIBA", "regular", "2"),
        ("FIBA", "international", "2"),
        ("FIBA", "world cup", "2"),
        ("FIBA", "eurobasket", "2"),
        ("Basketball Champions League", "regular", "6"),
        ("Basketball Champions League", "final", "11"),
        ("Basketball Champions League", "finals", "11"),
    ])
    def test_mapping_table_consistency(self, league, event_type, expected):
        """Verify that the mapping table matches function behavior."""
        assert COLOR_MAPPING_TABLE.get((league, event_type)) == expected
        assert get_color_id(league, event_type) == expected


class TestLeagueDefaultColourContract:
    """The league default is a constant, not a setting.

    Pinned because the defect it replaces was invisible from either end: the
    config offered a `defaultColorId` that no code path read, while four modules
    each carried their own `"6"`. Both halves are asserted — one definition, and
    no way to configure it into a colour the state machine has reserved.
    """

    def test_default_is_not_a_reserved_colour(self):
        """A league default in the UNVERIFIED or WRONG colour would make the
        calendar assert something false about a game nobody disproved."""
        from scripts.verification import DEFAULT_LEAGUE_COLOR_ID as canonical

        assert DEFAULT_LEAGUE_COLOR_ID == canonical
        assert DEFAULT_LEAGUE_COLOR_ID not in FORBIDDEN_LEAGUE_COLOR_IDS
        # Pinned exactly: a guard written against an empty (or shrunken) set is
        # the vacuous pass this repository keeps re-learning to avoid.
        assert FORBIDDEN_LEAGUE_COLOR_IDS == frozenset({"2", "5", "7", "11"})

    def test_reserved_colours_still_derive_from_the_state_machine(self):
        """The forbidden set is derived, not restated — a second list would be
        free to drift away from the colours the states actually use."""
        from scripts.verification import (
            LEAGUE_OVERRIDE_COLOR_IDS,
            STATE_COLOR_IDS,
            STATE_UNVERIFIED,
            STATE_WRONG,
        )

        assert FORBIDDEN_LEAGUE_COLOR_IDS == (
            {
                STATE_COLOR_IDS[STATE_UNVERIFIED],
                STATE_COLOR_IDS[STATE_WRONG],
            }
            | LEAGUE_OVERRIDE_COLOR_IDS
        )

    def test_the_default_has_one_definition(self):
        """A second literal is how the four copies drifted apart in the first
        place — each was local, so nothing compared them."""
        redeclared = sorted(
            path.name
            for path in (REPO_ROOT / "scripts").glob("*.py")
            if re.search(
                r"^\s*DEFAULT_LEAGUE_COLOR\s*=",
                path.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )
        assert redeclared == []

    def test_the_four_callers_import_it_rather_than_restate_it(self):
        """The mapping fallback and the three plan/audit/ledger fallbacks are the
        same value, reached by import."""
        from scripts import audit_events, event_ledger, upsert_events
        from scripts.verification import DEFAULT_LEAGUE_COLOR_ID as canonical

        assert get_color_id("Some Unknown League") == canonical
        for module in (upsert_events, audit_events, event_ledger):
            assert module.DEFAULT_LEAGUE_COLOR_ID == canonical

    def test_default_color_id_is_gone_from_the_config_surface(self):
        """Pins the removal so the field cannot come back unnoticed. It was
        removed rather than wired up, because a colour a user can set to `"7"`
        (Peacock = WRONG) is a colour contract that can be made to lie."""
        from scripts import calendar_config

        assert not hasattr(calendar_config, "get_default_color_id")
        assert "defaultColorId" not in calendar_config.DEFAULT_CONFIG
        written = json.loads(
            (REPO_ROOT / "config" / "calendar.json").read_text(encoding="utf-8")
        )
        assert "defaultColorId" not in written
