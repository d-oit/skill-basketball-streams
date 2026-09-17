"""Tests for scripts/evidence.py — the evidence contract.

This module exists because the whole point of Phase 3 is a distinction that is
easy to destroy by accident:

    a missing key is INCONCLUSIVE, an explicit `false` is a WRONG verdict.

`audit_events` reads a `WRONG` verdict as "this game was never live or was
paid", which relabels a calendar event a subscriber can see. So a default in the
coercion — treating an unparseable value as `False`, say — would relabel live
games as wrong on the strength of a model writing `"unknown"`. Every test here
pins the *absence* of a default.
"""
from __future__ import annotations

import pytest

from scripts.evidence import EVIDENCE_KEYS, coerce_evidence, coerce_flag, describe


class TestCoerceFlag:
    @pytest.mark.parametrize("value", [True, False])
    def test_real_bools_survive(self, value):
        assert coerce_flag(value) is value

    @pytest.mark.parametrize("text", ["true", "True", " TRUE ", "yes", "y", "1"])
    def test_a_decisive_yes(self, text):
        assert coerce_flag(text) is True

    @pytest.mark.parametrize("text", ["false", "False", " no ", "n", "0"])
    def test_a_decisive_no(self, text):
        assert coerce_flag(text) is False

    @pytest.mark.parametrize(
        "value",
        [
            "unknown",
            "maybe",
            "partial",
            "unclear",
            None,
            1,  # a number is not a flag
            0,
            [],
            {},
            "",
        ],
    )
    def test_anything_else_is_not_recorded(self, value):
        """Not `False`. `False` is a claim; this is the absence of one."""
        assert coerce_flag(value) is None


class TestCoerceEvidence:
    def test_reads_the_documented_keys(self):
        assert coerce_evidence(
            {"live_confirmed": True, "free_confirmed": True, "paid": False}
        ) == {"live_confirmed": True, "free_confirmed": True, "paid": False}

    def test_only_the_keys_that_were_present(self):
        assert coerce_evidence({"live_confirmed": True}) == {"live_confirmed": True}
        assert "free_confirmed" not in coerce_evidence({"live_confirmed": True})

    def test_an_unknown_value_drops_the_key_rather_than_defaulting_it(self):
        assert coerce_evidence({"live_confirmed": "unknown"}) == {}

    def test_nested_evidence_is_read(self):
        assert coerce_evidence({"evidence": {"paid": True}}) == {"paid": True}

    def test_nested_wins_over_a_flat_key_of_the_same_name(self):
        """The nested form is the explicit namespaced statement."""
        assert coerce_evidence(
            {"paid": True, "evidence": {"paid": False}}
        ) == {"paid": False}

    @pytest.mark.parametrize("source", [None, "text", 7, [], ()])
    def test_a_non_mapping_is_no_evidence(self, source):
        assert coerce_evidence(source) == {}

    def test_junk_beside_a_good_flag_does_not_remove_it(self):
        assert coerce_evidence(
            {"live_confirmed": True, "notes": "checked sport1.de"}
        ) == {"live_confirmed": True}

    def test_the_three_keys_are_the_documented_ones(self):
        assert EVIDENCE_KEYS == ("live_confirmed", "free_confirmed", "paid")


class TestDescribe:
    def test_names_the_flags_it_has(self):
        assert describe({"live_confirmed": True, "paid": False}) == (
            "live_confirmed=true, paid=false"
        )

    def test_empty_is_stated_not_blank(self):
        assert describe({}) == "no evidence recorded"
        assert describe(None) == "no evidence recorded"
