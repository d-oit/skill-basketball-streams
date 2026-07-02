#!/usr/bin/env python3
"""calendar_config.py — Helper module to read calendar configuration.

This module provides centralized access to calendar configuration with support
for environment variable overrides (12-factor config practice).

Usage:
    from calendar_config import get_calendar_config, get_calendar_id
    
    # Get full config dict
    config = get_calendar_config()
    
    # Get calendar ID with env var override
    calendar_id = get_calendar_id()

Configuration sources (in order of precedence):
1. Environment variable BASKETBALL_CALENDAR_ID (for calendarId only)
2. config/calendar.json file
3. Default values (for validation purposes only)

The calendarId from config/calendar.json is used as the fallback if
BASKETBALL_CALENDAR_ID is not set.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Default values for validation (should match config/calendar.json when present)
DEFAULT_CONFIG = {
    "calendarId": "",  # Empty string = must be configured
    "timezone": "Europe/Berlin",
    "defaultColorId": "6",
    "visibility": "public",
}

# Environment variable name for calendar ID override
CALENDAR_ID_ENV_VAR = "BASKETBALL_CALENDAR_ID"


def get_config_path(root: Path | None = None) -> Path:
    """Get the path to the calendar config file.
    
    Args:
        root: Optional project root path. If None, uses the directory
             containing this script's parent as the project root.
    
    Returns:
        Path to config/calendar.json
    """
    if root is None:
        # Default to the directory containing scripts/
        script_dir = Path(__file__).parent.resolve()
        root = script_dir.parent.resolve()
    return root / "config" / "calendar.json"


def load_config(root: Path | None = None) -> dict[str, Any]:
    """Load calendar configuration from config/calendar.json.
    
    Args:
        root: Optional project root path.
    
    Returns:
        Dictionary with calendar configuration.
    
    Raises:
        FileNotFoundError: If config file doesn't exist.
        json.JSONDecodeError: If config file contains invalid JSON.
    """
    config_path = get_config_path(root)
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Calendar config file not found: {config_path}"
        )
    
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_calendar_config(root: Path | None = None) -> dict[str, Any]:
    """Get calendar configuration with environment variable override for calendarId.
    
    Args:
        root: Optional project root path.
    
    Returns:
        Dictionary with calendar configuration. The calendarId value
        will be overridden by BASKETBALL_CALENDAR_ID env var if set.
    
    Note:
        Only calendarId is overridden by environment variable. Other
        settings (timezone, defaultColorId, visibility) always come
        from the config file.
    """
    try:
        config = load_config(root)
    except (FileNotFoundError, json.JSONDecodeError):
        # Return defaults if config file is missing or invalid
        # This allows validation to catch the issue
        config = DEFAULT_CONFIG.copy()
    
    # Apply environment variable override for calendarId
    env_calendar_id = os.environ.get(CALENDAR_ID_ENV_VAR)
    if env_calendar_id:
        config["calendarId"] = env_calendar_id
    
    return config


def get_calendar_id(root: Path | None = None) -> str:
    """Get the calendar ID, with environment variable override.
    
    Args:
        root: Optional project root path.
    
    Returns:
        The calendar ID string. If BASKETBALL_CALENDAR_ID env var is set,
        it takes precedence over the config file value.
    """
    config = get_calendar_config(root)
    return config.get("calendarId", "")


def get_timezone(root: Path | None = None) -> str:
    """Get the timezone from config.
    
    Args:
        root: Optional project root path.
    
    Returns:
        The timezone string (e.g., "Europe/Berlin").
    """
    config = get_calendar_config(root)
    return config.get("timezone", DEFAULT_CONFIG["timezone"])


def get_default_color_id(root: Path | None = None) -> str:
    """Get the default color ID from config.
    
    Args:
        root: Optional project root path.
    
    Returns:
        The default colorId string (e.g., "6").
    """
    config = get_calendar_config(root)
    return config.get("defaultColorId", DEFAULT_CONFIG["defaultColorId"])


def get_visibility(root: Path | None = None) -> str:
    """Get the default visibility from config.
    
    Args:
        root: Optional project root path.
    
    Returns:
        The visibility string (e.g., "public").
    """
    config = get_calendar_config(root)
    return config.get("visibility", DEFAULT_CONFIG["visibility"])


if __name__ == "__main__":
    # Demonstration
    import sys
    
    root = Path.cwd()
    try:
        config = get_calendar_config(root)
        print("Calendar Configuration:")
        print(f"  calendarId: {config.get('calendarId', 'NOT SET')}")
        print(f"  timezone: {config.get('timezone', 'NOT SET')}")
        print(f"  defaultColorId: {config.get('defaultColorId', 'NOT SET')}")
        print(f"  visibility: {config.get('visibility', 'NOT SET')}")
        
        env_override = os.environ.get(CALENDAR_ID_ENV_VAR)
        if env_override:
            print(f"\nEnvironment override active: {CALENDAR_ID_ENV_VAR}={env_override}")
        else:
            print(f"\nNo environment override for {CALENDAR_ID_ENV_VAR}")
            
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)
