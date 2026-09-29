"""`calendar_config.py` — a value that is present but unusable.

The defect this covers is the most expensive kind in this repository's history:
not a missing credential, but a **truncated** one. `BASKETBALL_CALENDAR_ID` was
set to `f8a14c4037d9ab411f93...` — a 23-character paste of a 90-character id,
the elision a UI shows when it cannot fit a value on screen.

Everything about it was silent in the way that matters:

* `gh variable list` shows the value, so it *looked* configured;
* the env var was non-empty, so the override branch ran and the config file's
  correct 90-character id was never consulted;
* the run reached the far end of a long chain — install, preflight, CLI proof,
  server, model resolution, agent creation, and a seven-minute skill run — and
  was finally told by *Google* that the calendar did not exist. The error named
  the calendar, four steps from the cause.

A calendar id is hex, optionally followed by `@group.calendar.google.com`. It
can never contain an ellipsis, so the check is exact rather than heuristic, and
it fires locally: a remote 404 becomes a local error that names the variable.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.calendar_config import (
    CALENDAR_ID_ENV_VAR,
    CalendarConfigError,
    get_calendar_config,
    get_calendar_id,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "config" / "calendar.json"
TRUNCATED = "f8a14c4037d9ab411f93..."
REAL = json.loads(CONFIG.read_text(encoding="utf-8"))["calendarId"]


class TestATruncatedIdIsRefused:
    @pytest.mark.parametrize(
        "value",
        [
            TRUNCATED,
            f"{REAL}...",
            f"{REAL[:40]}…",          # the single-character ellipsis
            "…",
        ],
    )
    def test_it_raises_rather_than_forwarding(self, monkeypatch, value):
        monkeypatch.setenv(CALENDAR_ID_ENV_VAR, value)
        with pytest.raises(CalendarConfigError) as excinfo:
            get_calendar_id()
        message = str(excinfo.value)
        assert CALENDAR_ID_ENV_VAR in message, "the error must name the variable"
        assert "truncated" in message

    def test_it_does_not_silently_fall_back_to_the_config(self, monkeypatch):
        """Falling back would be the friendlier behaviour and the wrong one.

        `config/calendar.json` holds the correct id, so "ignore the bad override
        and carry on" would make this repository write to a calendar the operator
        did not name. A refusal says which variable is wrong.
        """
        monkeypatch.setenv(CALENDAR_ID_ENV_VAR, TRUNCATED)
        with pytest.raises(CalendarConfigError):
            get_calendar_id()


class TestARealIdIsAccepted:
    def test_the_env_value_wins_when_it_is_whole(self, monkeypatch):
        monkeypatch.setenv(CALENDAR_ID_ENV_VAR, REAL)
        assert get_calendar_id() == REAL

    def test_unset_falls_back_to_the_config_file(self, monkeypatch):
        monkeypatch.delenv(CALENDAR_ID_ENV_VAR, raising=False)
        assert get_calendar_id() == REAL

    def test_an_empty_value_is_not_an_override(self, monkeypatch):
        """GitHub renders an unset secret as `""`, and that is not a value."""
        monkeypatch.setenv(CALENDAR_ID_ENV_VAR, "")
        assert get_calendar_id() == REAL

    def test_other_settings_are_never_overridden(self, monkeypatch):
        """Only `calendarId` is env-overridable; the rest are the product's."""
        monkeypatch.setenv(CALENDAR_ID_ENV_VAR, REAL)
        config = get_calendar_config()
        on_disk = json.loads(CONFIG.read_text(encoding="utf-8"))
        assert config["timezone"] == on_disk["timezone"]
        assert config["visibility"] == on_disk["visibility"]


class TestTheErrorSurvivesTheCli:
    """`calendar_io.py` is the consumer, so the refusal must reach it.

    A refusal raised in a library that nothing catches is a traceback in a
    workflow log — the same unreadable failure this replaces, one layer up.
    """

    def _run(self, value: str) -> subprocess.CompletedProcess:
        import os

        env = dict(os.environ)
        env[CALENDAR_ID_ENV_VAR] = value
        return subprocess.run(
            [sys.executable, "scripts/calendar_io.py", "list", "--days", "7"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=env,
        )

    def test_a_truncated_id_fails_with_the_named_reason(self):
        result = self._run(TRUNCATED)
        assert result.returncode != 0
        assert CALENDAR_ID_ENV_VAR in result.stderr
        assert "truncated" in result.stderr
        # Crucially, it never reaches the network.
        assert "GOOGLECALENDAR" not in result.stderr


class TestTheRealIdIsNotElided:
    def test_the_configured_id_has_no_ellipsis(self):
        """The fixture this repository ships must pass its own guard."""
        assert "..." not in REAL and "…" not in REAL
        assert len(REAL) > 23, (
            "a 23-character value is the shape the truncation produced; if the "
            "real id is this short the guard needs a different tell"
        )
