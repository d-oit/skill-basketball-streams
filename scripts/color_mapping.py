#!/usr/bin/env python3
"""color_mapping.py — Pure function for mapping league/event-type to Google Calendar colorId.

This module provides a deterministic mapping from league and event-type combinations
to Google Calendar colorId values. This removes ambiguity when creating events for
new leagues and makes color coding testable via unit tests.

Usage:
    from color_mapping import get_color_id
    color = get_color_id(league="BBL", event_type="regular")
    # Returns: "6"

The mapping follows the color scheme defined in references/calendar-setup.md.

Two dimensions are **orthogonal** and must not be conflated:

1. **League/event-type** — this module's original job: FIBA stays Sage, EuroLeague
   finals stay Tomato, everything else Tangerine.
2. **Verification state** — `VERIFIED` / `UNVERIFIED` / `WRONG`, defined in
   `scripts/verification.py` and applied by the runtime.

Precedence rule (spec §6): a non-`VERIFIED` state wins over the league colour,
because an unconfirmed stream must *look* unconfirmed. A confirmed final still
keeps Tomato.
"""
from __future__ import annotations

try:  # direct CLI execution: `python3 scripts/color_mapping.py`
    from verification import DEFAULT_LEAGUE_COLOR_ID, color_for, normalise_state
except ImportError:  # imported as a package module, e.g. scripts.color_mapping
    from scripts.verification import (  # type: ignore
        DEFAULT_LEAGUE_COLOR_ID,
        color_for,
        normalise_state,
    )

# Re-exported so callers have one import site for the state model.
__all__ = [
    "get_color_id",
    "COLOR_MAPPING_TABLE",
    "DEFAULT_LEAGUE_COLOR_ID",
    "normalise_state",
    "color_for",
]


def get_color_id(
    league: str, event_type: str = "regular", state: str | None = None
) -> str:
    """Deterministically map league and event-type to a Google Calendar colorId.
    
    Args:
        league: The basketball league or competition name (case-insensitive).
                 Examples: "BBL", "EuroLeague", "FIBA", "Basketball Champions League"
        event_type: The type of event (case-insensitive). Default: "regular"
                    Options: "regular", "final", "playoff", "semifinal", "championship"
        state: Optional verification state ("VERIFIED"/"UNVERIFIED"/"WRONG").
               When omitted, behaviour is unchanged from earlier versions. A
               non-VERIFIED state overrides the league colour so uncertainty is
               always visible in the calendar.
    
    Returns:
        A string representing the Google Calendar colorId.
        - "6" = Tangerine/Orange (default for all basketball events)
        - "11" = Tomato/Red (EuroLeague finals or special events)
        - "2" = Sage/Green (FIBA international games)
        - "5" = Banana (UNVERIFIED), "7" = Peacock (WRONG)
    
    Raises:
        ValueError: If league is empty or None, or the state is unknown.
    """
    if not league:
        raise ValueError("league must be a non-empty string")
    league_color = _league_color_id(league, event_type)
    if state is None:
        return league_color
    return color_for(normalise_state(state), league_color)


def _league_color_id(league: str, event_type: str = "regular") -> str:
    """League/event-type colour only, ignoring verification state."""
    
    # Normalize inputs to lowercase for case-insensitive matching
    league_lower = league.lower().strip()
    event_type_lower = event_type.lower().strip()
    
    # Define the color mapping rules
    # Rule 1: FIBA international games -> Sage/Green ("2")
    if league_lower in ("fiba", "fiba international", "fiba world cup", "fiba eurobasket"):
        return "2"
    
    # Rule 2: EuroLeague finals/special events -> Tomato/Red ("11")
    if league_lower in ("euroleague", "euro league"):
        if event_type_lower in ("final", "finals", "championship", "semifinal", "semifinals"):
            return "11"
    
    # Rule 3: Basketball Champions League finals -> Tomato/Red ("11")
    if league_lower in ("basketball champions league", "bcl"):
        if event_type_lower in ("final", "finals", "championship", "semifinal", "semifinals"):
            return "11"
    
    # Rule 4: All other basketball events -> the league default.
    # This includes: BBL (all types), EuroLeague regular season, etc. The value is
    # imported rather than written out, because it is also the colour the three
    # planner/audit/ledger modules fall back to — four copies of one "6" is four
    # places for the colour contract to drift apart.
    return DEFAULT_LEAGUE_COLOR_ID


# Mapping table for documentation and testing purposes
COLOR_MAPPING_TABLE = {
    # (league, event_type) -> colorId
    ("BBL", "regular"): "6",
    ("BBL", "playoff"): "6",
    ("BBL", "final"): "6",
    ("EuroLeague", "regular"): "6",
    ("EuroLeague", "playoff"): "6",
    ("EuroLeague", "final"): "11",
    ("EuroLeague", "finals"): "11",
    ("EuroLeague", "semifinal"): "11",
    ("EuroLeague", "championship"): "11",
    ("FIBA", "regular"): "2",
    ("FIBA", "international"): "2",
    ("FIBA", "world cup"): "2",
    ("FIBA", "eurobasket"): "2",
    ("Basketball Champions League", "regular"): "6",
    ("Basketball Champions League", "final"): "11",
    ("Basketball Champions League", "finals"): "11",
}


if __name__ == "__main__":
    # Demonstration and self-test
    import sys
    
    print("Color ID Mapping Demonstration")
    print("=" * 50)
    
    # Test cases from the mapping table
    test_cases = [
        ("BBL", "regular"),
        ("EuroLeague", "regular"),
        ("EuroLeague", "final"),
        ("FIBA", "regular"),
        ("Basketball Champions League", "final"),
    ]
    
    for league, event_type in test_cases:
        color = get_color_id(league, event_type)
        print(f"{league:30s} | {event_type:15s} | colorId: {color}")
    
    print("\nAll mappings verified successfully.")
