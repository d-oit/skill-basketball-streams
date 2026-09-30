"""Tests for scripts/relevance.py — the fail-open pre-filter.

The property under test is not "does it catch paint-balling streams", which is
easy. It is that it **fails open**: a real basketball game that the vocabulary
does not recognise must survive. A pre-filter that guesses rejects a game
silently, and a rejected game is not an error — it is a stream nobody gets, which
is the exact failure this repo exists to prevent. So the fail-open cases are the
load-bearing ones here, and they are asserted as such.

Every off-topic string below is a real rejection from `logs/run-log.jsonl`
(2026-09-28), taken verbatim rather than invented, so the test pins a behaviour
that was observed and not one imagined.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.relevance import (
    REASON_IRRELEVANT,
    BASKETBALL_RE,
    looks_like_basketball,
    relevance_reason,
)
from scripts.youtube_live import classify_stream

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestFailsOpen:
    """Unjudgeable means "pass", always. This is the whole safety argument."""

    def test_no_text_at_all(self):
        assert looks_like_basketball("", "", "") is True
        assert looks_like_basketball() is True

    def test_none_and_non_string_values(self):
        assert looks_like_basketball(None, 42, [], {}) is True

    def test_whitespace_only(self):
        assert looks_like_basketball("   ", "\n\t") is True

    def test_an_unrecognised_but_real_looking_stream_survives(self):
        # A lower-league or youth game the vocabulary does not cover. It must
        # pass: the authoritative basketball-specificity check is Check 4, and
        # this filter is only allowed to remove obvious noise.
        assert looks_like_basketball("Regionalliga Nord Spieltag 12 LIVE", "SVclub") is True

    @pytest.mark.parametrize(
        "title",
        [
            "Regionalliga Nord Spieltag 12 LIVE",
            "2. Bundesliga Spieltag 4: RSV vsets",
            "DNI Spieltag 3 - U16",
            "Oberliga Nordost live",
            "FIBA EuroBasket 2027 Qualifier",
        ],
    )
    def test_the_german_pyramid_is_covered(self, title):
        """Regression guard for the vocabulary, not for the regex.

        The first draft of this list was missing `regionalliga`, and this is the
        test that caught a real Regionalliga Nord fixture being refused as
        off-topic noise. A fixed vocabulary can only be kept safe by adding to
        it when a league is discovered, so the German pyramid is pinned
        explicitly — otherwise the next edit can quietly narrow it again.
        """
        assert looks_like_basketball(title, "unknown channel") is True


class TestRejectsObviousNoise:
    """Strings taken from the 2026-09-28 run log, not invented."""

    @pytest.mark.parametrize(
        "title,channel",
        [
            ("Pop The Balloon 3 - LIVE", "Pop The Balloon"),
            ("Acerting Art live session", "Acerting Art"),
            ("Melbourne Luxury Accommodation Live", "Melbourne Luxury Accommodation"),
            ("Wings Ganesh Bhakti Live", "Wings Ganesh Bhakti"),
        ],
    )
    def test_recorded_off_topic_channels(self, title, channel):
        assert looks_like_basketball(title, channel) is False

    def test_substrings_do_not_count(self):
        # Word boundaries, so a longer word that merely contains a marker is
        # not a basketball stream.
        assert looks_like_basketball("Xbasketry night", "Xbasketry") is False
        assert BASKETBALL_RE.search("Xbasketry") is None

    def test_bare_three_letter_bbl_is_absent(self):
        # `bbl` on its own is deliberately NOT an alternative: at three letters it
        # collides with unrelated words often enough that a word boundary does not
        # make it safe. It is allowed only inside the full phrase
        # `easycredit bbl`, which cannot collide — so the assertion is about the
        # bare alternative, not about the substring ever appearing.
        import re as _re

        alternatives = BASKETBALL_RE.pattern
        # Every alternative, split at the top-level pipes.
        for alt in alternatives.split("|"):
            assert _re.sub(r"[\\\s*?()\[\]{}<>=!+^$]", "", alt).lower() != "bbl", (
                f"bare `bbl` reappeared as an alternative: {alt!r}"
            )
        # And the guarded phrase is still recognised.
        assert looks_like_basketball("easyCredit BBL Spieltag 3", "") is True


class TestRecognisesRealStreams:
    @pytest.mark.parametrize(
        "title",
        [
            "ALBA Berlin vs FC Bayern Muenchen",
            "EuroLeague: Real Madrid vs Barcelona",
            "FIBA Women’s World Cup Qualifier",
            "easyCredit BBL: ratiopharm Ulm vs ALBA",
            "Vechta vs Braunschweig - Live",
        ],
    )
    def test_real_titles_pass(self, title):
        assert looks_like_basketball(title, "some channel") is True

    def test_channel_alone_is_enough(self):
        assert looks_like_basketball("Game 12", "ALBA BERLIN") is True

    def test_case_and_punctuation_insensitive(self):
        assert looks_like_basketball("euroleague", "") is True
        assert looks_like_basketball("EURO-LEAGUE", "") is True
        assert looks_like_basketball("Euro League", "") is True
        assert looks_like_basketball("BASKETBALL", "") is True
        assert looks_like_basketball("Münster", "") is True


class TestReasonString:
    def test_empty_when_passing(self):
        assert relevance_reason("ALBA vs Bayern", "ALBA") == ""

    def test_stable_reason_when_rejecting(self):
        assert relevance_reason("Pop The Balloon", "Pop The Balloon") == (
            REASON_IRRELEVANT
        )

    def test_reason_is_greppable(self):
        # The run log and any sensor match on this token, so it must not drift.
        assert "not a basketball stream" in REASON_IRRELEVANT


class TestWiringIntoClassifyStream:
    """The pair: the pre-filter and the gate that uses it.

    A unit test on `looks_like_basketball` proves the reader works. These prove
    the *wiring* — that the filter actually fires inside `classify_stream`, and
    that it does not fire where it would be wrong.
    """

    def _candidate(self, **overrides):
        base = {
            "url": "https://www.youtube.com/live/abc123XYZ",
            "title": "Pop The Balloon 3 - LIVE",
            "channel": "Pop The Balloon",
            "live_broadcast_content": "live",
            "is_live_now": True,
        }
        base.update(overrides)
        return base

    def test_a_handle_less_off_topic_live_is_rejected_early(self):
        decision, reason = classify_stream(self._candidate())
        assert decision == "REJECT"
        assert reason == REASON_IRRELEVANT

    def test_a_handle_less_real_game_still_passes_the_prefilter(self):
        decision, reason = classify_stream(
            self._candidate(title="ALBA Berlin vs Bayern", channel="Some Channel")
        )
        # It may still be rejected downstream for other reasons, but not for
        # relevance — which is the property that matters.
        assert REASON_IRRELEVANT not in reason

    def test_an_approved_channel_is_never_second_guessed(self):
        """The override that makes the filter safe to ship.

        The BBL channel is basketball by definition; if it ever streams a press
        conference titled "Medienkonferenz", the filter must stand down. This is
        why the check sits *after* the allow-list gate, which returns first.
        """
        decision, reason = classify_stream(
            self._candidate(
                url="https://www.youtube.com/@basketballbundesliga/live",
                title="Medienkonferenz vor dem Spiel",
                channel="easyCredit Basketball Bundesliga",
            )
        )
        assert REASON_IRRELEVANT not in reason
        assert decision in {"LIVE", "SCHEDULED"}

    def test_an_unapproved_channel_is_rejected_by_the_allowlist_not_the_filter(self):
        """The two rejections must stay distinguishable in the run log.

        Mixing them would pollute the per-source hit rate that
        `source_learning.py score` computes: "wrong channel" and "not a sport"
        are different facts about a source.
        """
        decision, reason = classify_stream(
            self._candidate(url="https://www.youtube.com/@FIBAWorld/live")
        )
        assert decision == "REJECT"
        assert "allow-list" in reason
        assert REASON_IRRELEVANT not in reason


class TestRunLogVocabulary:
    """The reason must be JSON-serialisable into the run log as written."""

    def test_reason_survives_a_json_round_trip(self):
        row = {
            "outcome": "reject",
            "reason": relevance_reason("Pop The Balloon", "Pop The Balloon"),
        }
        assert json.loads(json.dumps(row))["reason"] == REASON_IRRELEVANT
