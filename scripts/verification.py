#!/usr/bin/env python3
"""verification.py — the calendar event verification state machine.

Single source of truth for the four-state model in `live-stream-runtime-spec.md`
§6, shared by `upsert_events.py` (Phase 2) and `audit_events.py` (Phase 3) so the
states, titles and colours cannot drift apart.

| State | Meaning | Title | colorId |
|---|---|---|---|
| `VERIFIED` | Free access AND a live stream confirmed | unchanged | league colour (`6` default) |
| `UNVERIFIED` | Live stream plausible, free access **not** confirmed | `[UNVERIFIED] …` | `5` Banana |
| `WRONG` | Audit proved it was never live, or was paid | `[WRONG] …` | `7` Peacock |

Deliberate rule: **an uncertain state overrides the league colour.** Losing a
final's Tomato-red because colour `11` was "reserved" would hide exactly the
uncertainty this state machine exists to expose. `VERIFIED` keeps the league
colour, so FIBA stays Sage and finals stay Tomato once confirmed.

`WRONG` is never set by this module — only `audit_events.py` may reach that
verdict, and never by deleting an event.
"""
from __future__ import annotations

STATE_VERIFIED = "VERIFIED"
STATE_UNVERIFIED = "UNVERIFIED"
STATE_WRONG = "WRONG"

STATES = (STATE_VERIFIED, STATE_UNVERIFIED, STATE_WRONG)

STATE_COLOR_IDS = {
    STATE_VERIFIED: "6",   # Tangerine (default; league rules may override)
    STATE_UNVERIFIED: "5",  # Banana  — amber reads as caution
    STATE_WRONG: "7",       # Peacock — noisy enough to notice
}

STATE_PREFIXES = {
    STATE_VERIFIED: "",
    STATE_UNVERIFIED: "[UNVERIFIED] ",
    STATE_WRONG: "[WRONG] ",
}

LEAGUE_OVERRIDE_COLOR_IDS = frozenset({"2", "11"})

# The league colour a `VERIFIED` event carries, and the fallback for a league the
# mapping table does not know. **One definition, imported everywhere.** It lived as
# four independent copies (`upsert_events`, `audit_events`, `event_ledger`, and
# `color_mapping`'s fallback rule) until 2026-09-16; the copies could not drift
# loudly, because each was a local literal that nothing compared against the others.
#
# Note this is deliberately **not** `color_for(state, ...)`: that returns the *state*
# colour (Banana for an unverified event), so using it where a promotion restores the
# league colour would repaint a freshly verified event amber.
DEFAULT_LEAGUE_COLOR_ID = STATE_COLOR_IDS[STATE_VERIFIED]

# Colours the state machine and the league-override rules already speak for. No
# *league default* may be one of them: a calendar whose confirmed games render in the
# UNVERIFIED or WRONG colour says something false, and one that renders every league
# in a final's Tomato silently claims a final. This is why the value is a constant
# rather than a `config/calendar.json` setting — the field that used to offer all
# eleven colours (`defaultColorId`) was removed for exactly this reason, since a
# configuration that can be set to `"7"` is a colour contract that can be made to lie.
FORBIDDEN_LEAGUE_COLOR_IDS = frozenset(
    {STATE_COLOR_IDS[STATE_UNVERIFIED], STATE_COLOR_IDS[STATE_WRONG]}
    | LEAGUE_OVERRIDE_COLOR_IDS
)


class StateError(ValueError):
    """Raised for an unknown verification state."""


def normalise_state(value: object, *, default: str = STATE_UNVERIFIED) -> str:
    """Coerce to a known state. Empty input yields `default`.

    Failing safe matters here: an unparseable state from an upstream payload
    must degrade to the *cautious* label, never to VERIFIED.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    candidate = str(value).strip().upper()
    if candidate not in STATES:
        raise StateError(
            f"unknown state {value!r} (expected one of {', '.join(STATES)})"
        )
    return candidate


def is_verified(state: object) -> bool:
    return normalise_state(state) == STATE_VERIFIED


def prefix_for(state: object) -> str:
    return STATE_PREFIXES[normalise_state(state)]


def strip_prefix(summary: str) -> str:
    """Remove any known state prefix so re-applying one is idempotent."""
    text = summary or ""
    for prefix in STATE_PREFIXES.values():
        if prefix and text.startswith(prefix):
            return text[len(prefix):]
    return text


def title_for(state: object, summary: str) -> str:
    """Prefix the summary according to the state, idempotently."""
    return f"{prefix_for(state)}{strip_prefix(summary)}"


def state_from_title(summary: str) -> str:
    """Which state a stored event's title declares.

    **No known prefix means `VERIFIED`, deliberately.** Events created before
    this state machine existed carry no prefix and were treated as confirmed at
    the time. Reading them as `UNVERIFIED` would have the runtime rewrite every
    legacy event and repaint a calendar full of amber; leaving them alone is the
    conservative choice, and once the machine owns an event the marking is
    explicit either way.

    This is the inverse of `title_for` for the three known states, and the
    reason `upsert_events` can honour "never touch a verified event" using only
    what the calendar already returns.
    """
    text = summary or ""
    for state, prefix in STATE_PREFIXES.items():
        if prefix and text.startswith(prefix):
            return state
    return STATE_VERIFIED


def color_for(state: object, league_color: str = "6") -> str:
    """Uncertain states win over the league colour; VERIFIED keeps it."""
    normalised = normalise_state(state)
    if normalised == STATE_VERIFIED:
        return league_color or STATE_COLOR_IDS[STATE_VERIFIED]
    return STATE_COLOR_IDS[normalised]


def describe(state: object) -> str:
    return {
        STATE_VERIFIED: "free access and live stream both confirmed",
        STATE_UNVERIFIED: "free access not confirmed",
        STATE_WRONG: "audit proved it was never live or was paid",
    }[normalise_state(state)]


if __name__ == "__main__":  # pragma: no cover - demonstration
    for state in STATES:
        title = title_for(state, "BBL ALBA Berlin vs Bayern")
        print(
            f"{state:11s} color={color_for(state)} title={title!r} "
            f"-> state_from_title={state_from_title(title)}"
        )
    # An unprefixed title is a legacy or confirmed event, never a rewrite target.
    print(f"{'legacy':11s} state_from_title={state_from_title('BBL: ALBA vs Bayern')}")
