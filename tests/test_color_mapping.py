"""Unit tests for scripts/color_mapping.py."""
import pytest

from scripts.color_mapping import get_color_id, COLOR_MAPPING_TABLE


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
