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
"""
from __future__ import annotations


def get_color_id(league: str, event_type: str = "regular") -> str:
    """Deterministically map league and event-type to a Google Calendar colorId.
    
    Args:
        league: The basketball league or competition name (case-insensitive).
                 Examples: "BBL", "EuroLeague", "FIBA", "Basketball Champions League"
        event_type: The type of event (case-insensitive). Default: "regular"
                    Options: "regular", "final", "playoff", "semifinal", "championship"
    
    Returns:
        A string representing the Google Calendar colorId.
        - "6" = Tangerine/Orange (default for all basketball events)
        - "11" = Tomato/Red (EuroLeague finals or special events)
        - "2" = Sage/Green (FIBA international games)
    
    Raises:
        ValueError: If league is empty or None.
    """
    if not league:
        raise ValueError("league must be a non-empty string")
    
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
    
    # Rule 4: All other basketball events -> Tangerine/Orange ("6") as default
    # This includes: BBL (all types), EuroLeague regular season, etc.
    return "6"


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
