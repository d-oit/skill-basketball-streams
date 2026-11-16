"""The list-before-write window contract for the daily runtime.

"Never create the same event twice" rests on three facts about
`.github/workflows/runtime-daily.yml`, all asserted here so a future edit that
narrows the read window, reorders the steps, or drops the write-time guard
fails CI instead of duplicating a public event:

1. The `calendar_io list` snapshot and the `upsert_events` plan cover the
   **same** window, and that window is `config/calendar.json`'s `window_days`
   (not a second literal that can drift).
2. The list step runs **before** the plan step, and the plan step runs before
   the apply step.
3. The apply step passes the list snapshot back via `--existing`, so
   `calendar_io.apply_plan` can demote a stale `create` to `skip` before any
   HTTP.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _window_days() -> int:
    return int(json.loads((REPO / "config" / "calendar.json").read_text(encoding="utf-8"))["window_days"])


def _workflow() -> str:
    return (REPO / ".github" / "workflows" / "runtime-daily.yml").read_text(encoding="utf-8")


class TestListBeforeWriteWindow:
    def test_list_and_plan_cover_the_configured_window(self):
        window = _window_days()
        text = _workflow()
        assert f"calendar_io.py list --days {window}" in text, (
            "the list snapshot must cover the configured window"
        )
        assert f"--window-days {window}" in text, (
            "the planner must cover the same window the list snapshot saw"
        )

    def test_list_runs_before_plan_runs_before_apply(self):
        text = _workflow()
        list_at = text.index("List existing events for the window")
        plan_at = text.index("Plan the calendar writes")
        apply_at = text.index("Apply the plan")
        assert list_at < plan_at < apply_at, "dedupe-before-write is an ordering property"

    def test_apply_carries_the_snapshot_as_second_net(self):
        text = _workflow()
        apply_at = text.index("Apply the plan")
        tail = text[apply_at : apply_at + 4000]
        assert "--existing .tmp/existing.json" in tail, (
            "apply must pass the list snapshot so a stale create demotes to skip"
        )

    def test_window_values_are_not_second_literals(self):
        # Every `--days N` / `--window-days N` naming the calendar window in the
        # runtime workflow must equal the configured value: a second literal is
        # a second truth, and the two drift until the read window is narrower
        # than the write window — the shape that duplicates events.
        window = _window_days()
        text = _workflow()
        for match in re.finditer(r"--days (\d+)|--window-days (\d+)", text):
            assert int(match.group(1) or match.group(2)) == window or int(
                match.group(1) or match.group(2)
            ) in (3, 7), (
                f"{match.group(0)} names a non-calendar window; calendar coverage "
                f"must stay {window} (3/7-day ledger lookbacks are exempt)"
            )
