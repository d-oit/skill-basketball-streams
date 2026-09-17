#!/usr/bin/env python3
"""test_metrics.py — the derived telemetry snapshot.

The properties worth pinning are the ones that decide whether the numbers can be
trusted at all:

* a missing input yields `None`, never `0.0`, because "no data" and "measured,
  and it was zero" are different claims and only one of them is honest;
* `INCONCLUSIVE` is excluded from the precision denominator, so a quiet day
  cannot look accurate;
* `metrics.json` is derived and rewritable, while the JSONL streams are not.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "metrics.py"

sys.path.insert(0, str(ROOT / "scripts"))

import metrics  # noqa: E402

LEDGER = ROOT / "tests" / "fixtures" / "candidates_ledger.jsonl"
FIXTURES = ROOT / "tests" / "fixtures" / "league_fixtures.jsonl"
VERDICTS = ROOT / "tests" / "fixtures" / "synthesise_verdicts.jsonl"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------
# audit_metrics — the precision denominator
# --------------------------------------------------------------------------


def test_audit_metrics_counts_each_verdict() -> None:
    rows = [
        {"verdict": "VERIFIED"},
        {"verdict": "WRONG"},
        {"verdict": "WRONG"},
        {"verdict": "INCONCLUSIVE"},
    ]
    result = metrics.audit_metrics(rows)
    assert result["verified"] == 1
    assert result["wrong"] == 2
    assert result["inconclusive"] == 1
    assert result["rows"] == 4


def test_audit_precision_excludes_inconclusive_from_the_denominator() -> None:
    """1 / (1 + 1) = 0.5, not 1 / 3. Silence is not correctness."""
    rows = [{"verdict": "VERIFIED"}, {"verdict": "WRONG"}, {"verdict": "INCONCLUSIVE"}]
    result = metrics.audit_metrics(rows)
    assert result["judged"] == 2
    assert result["precision"] == 0.5


def test_audit_precision_is_none_when_nothing_was_judged() -> None:
    """All-inconclusive must be `None`, not `0.0` — the audit reached no
    verdict, which is different from reaching a bad one."""
    rows = [{"verdict": "INCONCLUSIVE"}, {"verdict": "INCONCLUSIVE"}]
    result = metrics.audit_metrics(rows)
    assert result["inconclusive"] == 2
    assert result["precision"] is None


def test_audit_metrics_ignores_unknown_and_missing_verdicts() -> None:
    rows = [{"verdict": "WIBBLE"}, {}, {"verdict": None}]
    result = metrics.audit_metrics(rows)
    assert result["rows"] == 3
    assert result["judged"] == 0
    assert result["precision"] is None


def test_audit_verdict_matching_is_case_insensitive() -> None:
    result = metrics.audit_metrics([{"verdict": "verified"}, {"verdict": "wrong"}])
    assert result["verified"] == 1
    assert result["precision"] == 0.5


# --------------------------------------------------------------------------
# build_snapshot
# --------------------------------------------------------------------------


def test_snapshot_without_fixtures_reports_none_not_zero() -> None:
    """No `fixtures.jsonl` means the measurement was never taken. A `0.0` here
    would read as a catastrophic miss rate on no evidence."""
    snapshot = metrics.build_snapshot(
        metrics.read_ledger(LEDGER), [], [], run_id="t"
    )
    assert snapshot["fixture_recall"] is None
    assert snapshot["source"]["fixtures_rows"] == 0


def test_snapshot_fixture_recall_over_surfaced_games() -> None:
    snapshot = metrics.build_snapshot(
        metrics.read_ledger(LEDGER), metrics.read_ledger(FIXTURES), [], run_id="t"
    )
    fixture = snapshot["fixture_recall"]
    assert fixture["fixtures"] == 3
    assert fixture["surfaced"] == 2
    assert fixture["unseen"] == 1
    assert fixture["fixture_recall"] == round(2 / 3, 3)


def test_snapshot_carries_the_recall_denominator() -> None:
    snapshot = metrics.build_snapshot(
        metrics.read_ledger(LEDGER), [], [], run_id="t"
    )
    recall = snapshot["recall"]
    assert recall["eligible"] == 2
    assert recall["captured"] == 1
    assert recall["recall"] == 0.5


def test_snapshot_from_the_verdict_fixture() -> None:
    """Four rows: two WRONG and two INCONCLUSIVE. Precision is 0.0 over the two
    judged rows, and `judged == 2` (not 4) is what makes that number mean
    something — the two silent ones are excluded rather than counted as wrong."""
    audit = metrics.read_ledger(VERDICTS)
    snapshot = metrics.build_snapshot([], [], audit, run_id="t")
    assert snapshot["audit"]["rows"] == 4
    assert snapshot["audit"]["wrong"] == 2
    assert snapshot["audit"]["inconclusive"] == 2
    assert snapshot["audit"]["judged"] == 2
    assert snapshot["audit"]["precision"] == 0.0


def test_snapshot_is_marked_derived() -> None:
    """The marker is what licenses rewriting this file while the JSONL streams
    stay append-only."""
    snapshot = metrics.build_snapshot([], [], [], run_id="t")
    assert snapshot["derived"] is True


def test_snapshot_is_deterministic_for_a_fixed_clock() -> None:
    from datetime import datetime, timezone

    when = datetime(2026, 9, 14, 8, 30, tzinfo=timezone.utc)
    first = metrics.build_snapshot([], [], [], run_id="r", now=when)
    second = metrics.build_snapshot([], [], [], run_id="r", now=when)
    assert first == second
    assert first["ts"] == "2026-09-14T08:30:00+00:00"


def test_render_prints_na_rather_than_zero_for_missing_metrics() -> None:
    snapshot = metrics.build_snapshot([], [], [], run_id="t")
    line = metrics.render(snapshot)
    assert "recall=n/a" in line
    assert "fixture_recall=n/a" in line
    assert "precision=n/a" in line
    assert "0.000" not in line


# --------------------------------------------------------------------------
# trend — the time series derived from run_id
# --------------------------------------------------------------------------

RUNS = ROOT / "tests" / "fixtures" / "candidates_ledger_runs.jsonl"
AUDIT_RUNS = ROOT / "tests" / "fixtures" / "audit_runs.jsonl"


@pytest.fixture
def run_rows() -> list[dict]:
    return metrics.read_ledger(RUNS)


@pytest.fixture
def audit_rows() -> list[dict]:
    return metrics.read_ledger(AUDIT_RUNS)


def test_run_ids_are_sorted_and_deduplicated(run_rows, audit_rows) -> None:
    ids = metrics.run_ids(run_rows, audit_rows)
    assert ids == sorted(set(ids))
    assert ids[0] == "2026-09-14T08:30Z"
    assert ids[-1] == "2026-09-17T08:30Z"


def test_a_run_present_only_in_the_audit_stream_still_appears(
    run_rows, audit_rows
) -> None:
    """2026-09-17 has audit rows and no candidate rows. Dropping it would hide
    a day the audit ran — which is exactly the day a false positive was found."""
    rows = metrics.trend(run_rows, audit_rows)
    assert [r["run_id"] for r in rows][-1] == "2026-09-17T08:30Z"
    assert rows[-1]["rows"] == 0
    assert rows[-1]["recall"] is None
    assert rows[-1]["audit"]["verified"] == 1


def test_trend_is_ordered_oldest_first(run_rows, audit_rows) -> None:
    ids = [r["run_id"] for r in metrics.trend(run_rows, audit_rows)]
    assert ids == sorted(ids), "run_id is an ISO stamp, so lexical order is chronological"


def test_per_run_recall_moves_with_the_outcome(run_rows, audit_rows) -> None:
    """The fixture captures 0 of 2, then 1 of 2, then 2 of 2. Per-run recall must
    show that direction; the cumulative figure also moves, but this is the number
    that answers 'did this run do better than the last?'."""
    rows = metrics.trend(run_rows, audit_rows)
    recalls = [r["recall"] for r in rows[:3]]
    assert recalls == [0.0, 0.5, 1.0]


def test_trend_carries_per_run_audit_counts(run_rows, audit_rows) -> None:
    rows = metrics.trend(run_rows, audit_rows)
    third = rows[2]
    assert third["audit"]["verified"] == 1
    assert third["audit"]["wrong"] == 1
    assert third["audit"]["inconclusive"] == 1
    assert third["audit"]["judged"] == 2
    assert third["audit"]["precision"] == 0.5


def test_trend_ignores_rows_with_no_run_id() -> None:
    rows = metrics.trend([{"game_key": "G1"}, {"run_id": "", "game_key": "G2"}], [])
    assert rows == []


def test_trend_is_capped_by_limit(run_rows, audit_rows) -> None:
    rows = metrics.trend(run_rows, audit_rows, limit=2)
    assert [r["run_id"] for r in rows] == ["2026-09-16T08:30Z", "2026-09-17T08:30Z"]


def test_trend_of_nothing_is_empty() -> None:
    assert metrics.trend([], []) == []


# --------------------------------------------------------------------------
# sparkline
# --------------------------------------------------------------------------


def test_sparkline_renders_a_climb() -> None:
    line = metrics.sparkline([0.0, 0.5, 1.0])
    assert line[0] == metrics.BLOCKS[0]
    assert line[-1] == metrics.BLOCKS[-1]
    assert line[0] != line[-1]


def test_sparkline_is_absolute_so_a_tiny_move_does_not_look_huge() -> None:
    """Normalising to min/max would render 0.98 vs 0.99 as a dramatic climb,
    which is precisely the misreading a trend is meant to prevent."""
    line = metrics.sparkline([0.980, 0.985])
    assert line[0] == line[1], "a 0.005 move at the top end must look flat"


def test_sparkline_marks_a_gap_rather_than_interpolating() -> None:
    assert metrics.sparkline([0.5, None, 0.5])[1] == "\u00b7"


def test_sparkline_of_all_none_is_all_gaps() -> None:
    assert metrics.sparkline([None, None]) == "\u00b7\u00b7"


def test_sparkline_clamps_out_of_range_values() -> None:
    assert metrics.sparkline([-1.0])[0] == metrics.BLOCKS[0]
    assert metrics.sparkline([2.0])[0] == metrics.BLOCKS[-1]


def test_render_trend_is_readable_and_marks_missing_as_na(
    run_rows, audit_rows
) -> None:
    text = metrics.render_trend(metrics.trend(run_rows, audit_rows))
    assert "2026-09-14T08:30Z" in text
    assert "0.000" in text and "0.500" in text and "1.000" in text
    assert "n/a" in text, "a run with no eligible game must not print 0.000"
    assert "recall" in text and "precision" in text
    assert "absolute" in text, "the scale must be stated, not implied"


def test_render_trend_of_nothing_says_so() -> None:
    assert "no runs recorded" in metrics.render_trend([])


# --------------------------------------------------------------------------
# write_snapshot
# --------------------------------------------------------------------------


def test_write_snapshot_produces_valid_json(tmp_path: Path) -> None:
    snapshot = metrics.build_snapshot(
        metrics.read_ledger(LEDGER), [], [], run_id="t"
    )
    path = metrics.write_snapshot(snapshot, tmp_path)
    assert path.name == "metrics.json"
    assert json.loads(path.read_text(encoding="utf-8")) == snapshot


def test_write_snapshot_creates_the_destination_directory(tmp_path: Path) -> None:
    dest = tmp_path / "telemetry"
    assert not dest.exists()
    metrics.write_snapshot(metrics.build_snapshot([], [], [], run_id="t"), dest)
    assert (dest / "metrics.json").is_file()


def test_write_snapshot_is_idempotent(tmp_path: Path) -> None:
    """A daily job rewrites this file. Rewriting must converge, not accumulate."""
    snapshot = metrics.build_snapshot([], [], [], run_id="t")
    first = metrics.write_snapshot(snapshot, tmp_path).read_text(encoding="utf-8")
    second = metrics.write_snapshot(snapshot, tmp_path).read_text(encoding="utf-8")
    assert first == second


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def test_cli_dry_run_writes_nothing(tmp_path: Path) -> None:
    proc = run(
        "--dest",
        str(tmp_path),
        "--candidates",
        str(LEDGER),
        "--fixtures",
        str(FIXTURES),
        "--dry-run",
    )
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "metrics.json").exists()
    assert "recall=0.500" in proc.stdout


def test_cli_json_stdout_is_parseable(tmp_path: Path) -> None:
    """The payload must be the only thing on stdout, or `json.load` on it
    breaks — the same contract the other writers follow."""
    proc = run(
        "--dest",
        str(tmp_path),
        "--candidates",
        str(LEDGER),
        "--fixtures",
        str(FIXTURES),
        "--run-id",
        "t",
        "--json",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["derived"] is True
    assert payload["run_id"] == "t"


def test_cli_writes_metrics_json(tmp_path: Path) -> None:
    proc = run(
        "--dest",
        str(tmp_path),
        "--candidates",
        str(LEDGER),
        "--fixtures",
        str(FIXTURES),
        "--run-id",
        "t",
    )
    assert proc.returncode == 0, proc.stderr
    written = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert written["fixture_recall"]["fixture_recall"] == round(2 / 3, 3)


def test_cli_missing_inputs_still_succeed(tmp_path: Path) -> None:
    """The first run ever has no ledgers at all. That must be a clean n/a, not
    a crash that reds the telemetry job on day one."""
    proc = run("--dest", str(tmp_path), "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "recall=n/a" in proc.stdout


def test_cli_malformed_lines_are_dropped_not_fatal(tmp_path: Path) -> None:
    ledger = tmp_path / "candidates.jsonl"
    good = LEDGER.read_text(encoding="utf-8")
    ledger.write_text("{not json}\n" + good + "\n", encoding="utf-8")
    proc = run("--dest", str(tmp_path), "--candidates", str(ledger), "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "recall=0.500" in proc.stdout


def test_cli_trend_prints_the_table(tmp_path: Path) -> None:
    proc = run(
        "--dest", str(tmp_path),
        "--candidates", str(RUNS),
        "--audit", str(AUDIT_RUNS),
        "--trend", "--dry-run",
    )
    assert proc.returncode == 0, proc.stderr
    assert "trend (4 run(s)" in proc.stdout
    assert "2026-09-17T08:30Z" in proc.stdout


def test_cli_trend_writes_the_snapshot_with_history(tmp_path: Path) -> None:
    proc = run(
        "--dest", str(tmp_path),
        "--candidates", str(RUNS),
        "--audit", str(AUDIT_RUNS),
        "--trend",
    )
    assert proc.returncode == 0, proc.stderr
    written = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert len(written["trend"]) == 4
    assert written["trend"][0]["run_id"] == "2026-09-14T08:30Z"


def test_cli_snapshot_includes_a_bounded_trend(tmp_path: Path) -> None:
    proc = run(
        "--dest", str(tmp_path),
        "--candidates", str(RUNS),
        "--limit", "2",
        "--json",
    )
    assert proc.returncode == 0, proc.stderr
    assert len(json.loads(proc.stdout)["trend"]) == 2


def test_cli_rejects_a_non_positive_limit(tmp_path: Path) -> None:
    proc = run("--dest", str(tmp_path), "--limit", "0", "--dry-run")
    assert proc.returncode == 2
    assert "--limit" in proc.stderr


def test_cli_rejects_an_invalid_now(tmp_path: Path) -> None:
    proc = run("--dest", str(tmp_path), "--now", "not-a-date", "--dry-run")
    assert proc.returncode == 2
    assert "ISO-8601" in proc.stderr


@pytest.mark.parametrize("flag", ["--candidates", "--fixtures", "--audit"])
def test_cli_accepts_each_path_override(tmp_path: Path, flag: str) -> None:
    proc = run("--dest", str(tmp_path), flag, str(LEDGER), "--dry-run")
    assert proc.returncode == 0, proc.stderr
