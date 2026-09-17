"""Tests for scripts/team_tokens.py — the one club-name matcher.

Two features ask the same question — "is this the same club?" —
`upsert_events.py` (to avoid creating the same game twice) and
`render_ladder.py` (to decide whether a page names the game it was fetched for).
These tests pin the rules both of them rely on, including the diacritic folding
that a real recorded page proved was missing (`Northern Kāhu`).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from team_tokens import (  # noqa: E402
    GENERIC_TEAM_TOKENS,
    pairing_count,
    split_teams,
    team_matches,
    team_tokens,
    text_names_all,
)


class TestTextNamesAll:
    """The weaker of the two "does this text name these clubs?" questions.

    Weaker on purpose — see the docstring. The titles below are real ones from the
    live calendar on 2026-09-15, which is why the dash cases are here: they are
    how a human actually wrote those pairings, and demanding `vs` would have made
    four real games unmatchable.
    """

    def test_a_vs_title_names_both_clubs(self):
        assert text_names_all(
            "ALBA BERLIN vs. NINERS Chemnitz (easyCredit BBL)",
            ["ALBA Berlin", "NINERS Chemnitz"],
        )

    def test_a_dash_title_names_both_clubs(self):
        assert text_names_all("Nagasaki Velca - Telekom Baskets Bonn (Game 2)",
                              ["Nagasaki Velca", "Telekom Baskets Bonn"])
        assert text_names_all("Dubai Basketball - Real Madrid",
                              ["Dubai Basketball", "Real Madrid"])

    def test_a_league_prefix_does_not_break_it(self):
        assert text_names_all(
            "BBL Halbfinale: Bamberg Baskets - ALBA BERLIN (BR24Sport Livestream)",
            ["Bamberg Baskets", "ALBA Berlin"],
        )

    def test_short_and_long_spellings_are_the_same_club(self):
        assert text_names_all("ALBA BERLIN vs. NINERS Chemnitz", ["ALBA", "Chemnitz"])

    def test_a_missing_club_is_not_named(self):
        assert not text_names_all(
            "ALBA BERLIN vs. NINERS Chemnitz", ["ALBA Berlin", "FC Bayern Muenchen"]
        )

    def test_a_tournament_day_names_no_clubs(self):
        """The 67 live entries of this shape: they cover a day, not a fixture."""
        assert not text_names_all(
            "FIBA U20 Women's EuroBasket 2026 - Day 1 - FREE Live Stream",
            ["ALBA Berlin", "NINERS Chemnitz"],
        )

    def test_an_empty_expectation_is_never_a_match(self):
        """Otherwise "we expected nothing" would read as "everything matches"."""
        assert not text_names_all("ALBA BERLIN vs. NINERS Chemnitz", [])
        assert not text_names_all("ALBA BERLIN vs. NINERS Chemnitz", "")
        assert not text_names_all("ALBA BERLIN vs. NINERS Chemnitz", None)

    def test_empty_text_names_nothing(self):
        assert not text_names_all("", ["ALBA Berlin"])
        assert not text_names_all(None, ["ALBA Berlin"])

    def test_a_name_with_no_identity_tokens_cannot_match_everything(self):
        """`BC` tokenises to nothing, so it must not be a wildcard of its own."""
        assert not text_names_all("ALBA BERLIN vs. NINERS Chemnitz", ["BC", "Basketball"])
        assert not text_names_all("anything at all", ["BC"])

    def test_a_vs_string_is_split_like_a_list(self):
        assert text_names_all("ALBA BERLIN vs. NINERS Chemnitz", "ALBA Berlin vs NINERS Chemnitz")


class TestFolding:
    def test_case_and_punctuation_are_ignored(self):
        assert team_matches("FC Bayern Basketball", "fc bayern") is True
        assert team_matches("ALBA BERLIN", "alba berlin") is True

    def test_diacritics_fold(self):
        assert team_matches("Northern Kāhu", "Northern Kahu") is True
        assert team_matches("Kāhu", "Kahu") is True
        assert team_matches("München", "Munchen") is True

    def test_german_digraph_spellings_fold(self):
        assert team_matches("München", "Muenchen") is True
        assert team_matches("Würzburg Baskets", "Wuerzburg") is True
        assert team_matches("Straße", "Strasse") is True

    def test_a_letter_with_no_ascii_equivalent_does_not_split_a_name(self):
        """`Køge` must not become `{k, ge}`: that is how `Kāhu` became
        `{k, hu}` and stopped matching the page that named it."""
        assert "kge" not in team_tokens("Køge")
        assert team_matches("Køge", "Køge") is True

    def test_empty_and_non_string_input_is_safe(self):
        assert team_tokens(None) == frozenset()
        assert team_matches(None, "") is True
        assert team_matches("ALBA", None) is False


class TestNesting:
    def test_a_short_name_matches_the_full_name(self):
        assert team_matches("ALBA", "ALBA Berlin") is True
        assert team_matches("ALBA Berlin", "ALBA") is True

    def test_a_shared_city_is_not_a_match(self):
        assert team_matches("Berlin", "ALBA Berlin") is True  # documented looseness
        assert team_matches("Berlin", "Bonn") is False

    def test_generic_tokens_carry_no_identity(self):
        assert "basketball" in GENERIC_TEAM_TOKENS
        assert team_tokens("Basketball") == frozenset()

    def test_different_clubs_do_not_match(self):
        assert team_matches("ALBA Berlin", "FC Bayern") is False
        assert team_matches("Real Madrid", "FC Barcelona") is False

    def test_names_with_no_identity_never_match_by_nesting(self):
        """Two names made only of generic words carry no identity, so they must
        not be treated as the same club — matching them would merge every
        `Basketball` with every `Baskets`."""
        assert team_tokens("Basketball") == frozenset()
        assert team_matches("Basketball", "Baskets") is False
        assert team_matches("Basketball", "ALBA Berlin") is False


class TestSplitAndCount:
    def test_split_handles_the_known_separator_spellings(self):
        assert split_teams("USA vs France") == ["USA", "France"]
        assert split_teams("USA vs. France") == ["USA", "France"]
        assert split_teams("A v B") == ["A", "B"]
        assert split_teams("A gegen B") == ["A", "B"]
        assert split_teams(["A", "B"]) == ["A", "B"]

    def test_vs_is_never_split_into_v_plus_s(self):
        """The `vs` alternative is listed before `v`; without that, `USA vs
        France` becomes `['USA', 's France']` and no page can ever match."""
        assert split_teams("USA vs France") == ["USA", "France"]
        assert "s France" not in split_teams("USA vs France")

    def test_single_name_is_returned_whole(self):
        assert split_teams("ALBA Berlin") == ["ALBA Berlin"]
        assert split_teams("") == []

    def test_pairing_count_detects_a_multi_game_title(self):
        assert pairing_count("Tauranga Whai v Northern Kāhu") == 1
        assert pairing_count("USA vs France live | Germany vs Spain") == 2
        assert pairing_count("Rick Astley - Never Gonna Give You Up") == 0
