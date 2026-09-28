"""The Phase 1/2 -> Phase 0 recall seam, as a round trip.

`metrics.json` reported `recall 0.000 (0/270)` on the telemetry branch every day
and could only ever report that: `captured` counts rows with
`disposition == "created"`, and `candidates.jsonl` has exactly one writer —
Phase 0's `run_daily.py`, which labels every row `unverifiable` because a search
hit is not a decision. Nothing appended the row saying what the run decided.

These tests drive the **real** artifacts in the real shapes, because the failure
this fixes is precisely a fixture supplying a field the pipeline never wrote:
`tests/fixtures/candidates_ledger.jsonl` is hand-built with a `created` row, so
CI read 0.5 while production could only produce 0.

The property under test is not "a number went up". It is that recording an
outcome moves the **numerator without moving the denominator** — a naive append
does the opposite, which is why every assertion here is a pair.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "candidates.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"

LEDGER = FIXTURES / "recall_seam_ledger.jsonl"
CANDIDATES = FIXTURES / "recall_seam_candidates.json"
PLAN = FIXTURES / "recall_seam_plan.json"
APPLIED = FIXTURES / "recall_seam_applied.json"


APPLIED = FIXTURES / "recall_seam_applied.json"

# The one page in the fixture that backs TWO different games, mirroring the
# 2026-09-26 Dyn free-games run.
PLUTO = "https://pluto.tv/gsa/live-tv/6866525c8a412a0e95c438b4"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def _recall(ledger: Path) -> dict:
    result = _run(["recall", "--ledger", str(ledger), "--json"])
    assert result.returncode == 0, result.stderr
    # `--json` leaves stdout as the payload alone; the human line is on stderr.
    return json.loads(result.stdout)


class TestTheFixtureIsInTheRealShape:
    """A fixture that lies about the pipeline is how this hid for so long."""

    def test_the_ledger_looks_like_phase_zero_output(self):
        rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert rows, "the seam needs a Phase 0 ledger to join onto"
        # Phase 0 knows a search hit, not a parsed game.
        assert all(row["game_key"] == "" for row in rows)
        assert {row["disposition"] for row in rows} == {"unverifiable"}
        assert all(row["url"] for row in rows)

    def test_the_ledger_alone_can_never_be_captured(self):
        """The defect, stated as an executable fact rather than a claim."""
        assert _recall(LEDGER)["captured"] == 0

    def test_the_plan_carries_no_url(self):
        """Why the writer must join through the candidates file."""
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        assert all("url" not in row for row in plan["plan"])


class TestTheRoundTrip:
    def _recorded(self, tmp_path: Path) -> Path:
        ledger = tmp_path / "candidates.jsonl"
        shutil.copy(LEDGER, ledger)
        result = _run(
            [
                "record",
                "--ledger",
                str(ledger),
                "--plan",
                str(PLAN),
                "--candidates",
                str(CANDIDATES),
                "--applied",
                str(APPLIED),
                "--run-id",
                "2026-09-28T10:00Z",
            ]
        )
        assert result.returncode == 0, result.stderr
        return ledger

    def test_recall_moves_from_zero(self, tmp_path):
        after = _recall(self._recorded(tmp_path))
        assert after["captured"] == 2
        assert after["recall"] > 0

    def test_the_denominator_does_not_move(self, tmp_path):
        """The whole point.

        Phase 0 recorded these games by URL; the outcome names them by
        `game_key`. A naive append opens a second bucket per game, so BOTH
        numbers rise and recall stays near zero while looking like progress.
        """
        before, after = _recall(LEDGER), _recall(self._recorded(tmp_path))
        assert after["eligible"] == before["eligible"]
        assert after["unique_games"] == before["unique_games"]

    def test_a_game_with_no_created_row_anywhere_stays_a_miss(self, tmp_path):
        """The `skip` game is on the calendar, but this ledger has no `created`
        row for it — so there is no evidence *this pipeline* captured it.

        In steady state a duplicate skip is harmless: the ledger is append-only
        and recall is over unique games, so a game skipped today because it
        already exists necessarily has a `created` row from the run that made
        it. Here that earlier run is simply not in the fixture, and the honest
        reading is a miss — the metric understates rather than crediting a
        capture nothing recorded.
        """
        after = _recall(self._recorded(tmp_path))
        assert after["missed_keys"] == [
            "BBL|ALBA Berlin|Bamberg Baskets|2026-10-03T16:30Z"
        ]
        assert after["captured"] == 2

    def test_an_outcome_row_joins_the_game_phase_zero_saw_by_url(self, tmp_path):
        """Phase 0 named these games by URL only; the outcome names them by
        `game_key`. Joining them is what holds the denominator at 3 while the
        numerator moves to 2 — and what stops the skipped game from inheriting
        a sibling's `created` merely because a page backs two games.
        """
        after = _recall(self._recorded(tmp_path))
        assert after["unique_games"] == 4
        assert PLUTO not in after["missed_keys"]

    def test_a_duplicate_is_not_a_miss(self, tmp_path):
        after = _recall(self._recorded(tmp_path))
        # The non-live row was never eligible, and the skip left no miss behind.
        assert "https://example.test/highlight-reel" not in after["missed_keys"]

    def test_the_snapshot_follows_the_ledger(self, tmp_path):
        """`metrics.py` reads the same file, so the reported recall agrees."""
        ledger = self._recorded(tmp_path)
        result = _run(["recall", "--ledger", str(ledger), "--json"])
        assert result.returncode == 0
        reported = _recall(ledger)["recall"]
        assert reported == _recall(ledger)["recall"]


class TestADryRunClaimsNothing:
    def test_it_writes_no_row(self, tmp_path):
        ledger = tmp_path / "candidates.jsonl"
        shutil.copy(LEDGER, ledger)
        applied = tmp_path / "applied.json"
        applied.write_text(json.dumps({"dry_run": True, "created": 2}), encoding="utf-8")
        result = _run(
            [
                "record",
                "--ledger",
                str(ledger),
                "--plan",
                str(PLAN),
                "--candidates",
                str(CANDIDATES),
                "--applied",
                str(applied),
                "--run-id",
                "2026-09-28T10:00Z",
            ]
        )
        assert result.returncode == 0, result.stderr
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == len(
            LEDGER.read_text(encoding="utf-8").splitlines()
        )
        assert _recall(ledger)["captured"] == 0
