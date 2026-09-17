#!/usr/bin/env python3
"""evidence.py — the free-access/live evidence contract, in one place.

`docs/runtime.md` documents `telemetry/evidence.json` as

    {event_id: {live_confirmed, free_confirmed, paid}}

and `scripts/audit_events.py` (Phase 3) is the only consumer. It draws a hard
line that this module exists to protect:

    a missing key is INCONCLUSIVE, an explicit `false` is a WRONG verdict.

So the *presence* of a key is the claim, and the three values have different
weight: `paid: true` or `live_confirmed: false` condemns the event, while
`free_confirmed: false` on its own does not. That makes it unacceptable for two
modules to disagree about what "evidence" is — one defaulting a key to `false`
where the other treats it as absent would relabel live games as wrong. Hence a
single coercion function, used by the extractor (which must carry the fields
through) and the ledger writer (which must record them), with no defaults and no
guessing:

* an absent key stays absent;
* only an unambiguous flag survives — a real bool, or the strings `true`/`false`
  (with `yes`/`no`/`1`/`0` accepted, because a model writes those);
* anything else (`"unknown"`, `null`, a number, a nested object) means *not
  recorded*, never `false`. Failing safe here is the difference between "we
  checked and it was fine" and "we could not check", and only the audit may
  decide which of those labels an event deserves.
"""
from __future__ import annotations

from collections.abc import Mapping

# The three keys the audit reads. Order is the order they were documented in.
EVIDENCE_KEYS = ("live_confirmed", "free_confirmed", "paid")

# Strings that mean a definite yes/no. Deliberately small: `unknown`, `maybe`
# and `partial` are not evidence, and coercing them to either bool would put a
# verdict on the calendar that no observation supports.
_TRUE_WORDS = frozenset({"true", "yes", "y", "1"})
_FALSE_WORDS = frozenset({"false", "no", "n", "0"})


def coerce_flag(value: object) -> bool | None:
    """A definite bool, or `None` when the value is not a clear yes/no.

    `None` means "not recorded" — not `False`. Callers must drop the key rather
    than store the absence, because the audit distinguishes the two.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
    return None


def coerce_evidence(source: object) -> dict:
    """The evidence flags recorded in `source`, or `{}` if none are.

    `source` may be a candidate object carrying the flags at the top level, or
    an `evidence.json` entry carrying them directly. A nested `evidence` mapping
    is read too, and wins over a flat key of the same name: the nested form is
    the explicit namespaced one, so it is the more specific statement.
    """
    if not isinstance(source, Mapping):
        return {}
    evidence: dict = {}
    for key in EVIDENCE_KEYS:
        flag = coerce_flag(source.get(key))
        if flag is not None:
            evidence[key] = flag
    nested = source.get("evidence")
    if isinstance(nested, Mapping):
        for key in EVIDENCE_KEYS:
            flag = coerce_flag(nested.get(key))
            if flag is not None:
                evidence[key] = flag
    return evidence


def describe(evidence: object) -> str:
    """A one-line human reading of a flag set, for logs and summaries."""
    if not isinstance(evidence, Mapping) or not evidence:
        return "no evidence recorded"
    parts = []
    for key in EVIDENCE_KEYS:
        if key in evidence:
            parts.append(f"{key}={'true' if evidence[key] else 'false'}")
    return ", ".join(parts) if parts else "no evidence recorded"


if __name__ == "__main__":  # pragma: no cover - demonstration
    for sample in (
        {"live_confirmed": True, "free_confirmed": True},
        {"live_confirmed": "false"},
        {"evidence": {"paid": "yes"}},
        {"live_confirmed": "unknown"},
        {},
    ):
        print(f"{sample!r} -> {coerce_evidence(sample)!r} ({describe(coerce_evidence(sample))})")
