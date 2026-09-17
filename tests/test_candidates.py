"""Tests for scripts/candidates.py — the recall denominator."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.candidates import (
    append_rows,
    distinct_fixtures,
    fixture_recall,
    normalise_row,
    read_ledger,
    recall_metrics,
    select_retry,
    unseen_keys,
    valid_disposition,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "candidates.py"


def _row(game_key="BBL|A|B|2026-09-16T19:00+02:00", **overrides):
    row = {
        "ts": "2026-09-14T08:30:00+00:00",
        "run_id": "2026-09-14T08:30Z",
        "url": "https://example.com/live",
        "backend": "exa-mcp",
        "game_key": game_key,
        "live_signal": True,
        "disposition": "unverifiable",
    }
    row.update(overrides)
    return row


class TestValidDisposition:
    @pytest.mark.parametrize(
        "value",
        ["created", "skipped_duplicate", "unverifiable", "rejected_freeAccess",
         "rejected_directStreamVerification"],
    )
    def test_accepted(self, value):
        assert valid_disposition(value) is True

    @pytest.mark.parametrize("value", ["", "creatd", "skip", None, 7])
    def test_rejected(self, value):
        assert valid_disposition(value) is False


class TestNormaliseRow:
    def test_fills_required_fields(self):
        row = normalise_row({"url": "https://x.test/live", "disposition": "created"})
        assert row["url"] == "https://x.test/live"
        assert row["run_id"] == ""
        assert row["live_signal"] is False
        assert row["ts"]

    def test_game_key_only_is_enough(self):
        row = normalise_row({"game_key": "BBL|A|B|t", "disposition": "created"})
        assert row["game_key"] == "BBL|A|B|t"

    def test_missing_url_and_game_key_raises(self):
        with pytest.raises(ValueError, match="at least one of"):
            normalise_row({"disposition": "created"})

    def test_bad_disposition_raises(self):
        with pytest.raises(ValueError, match="invalid disposition"):
            normalise_row({"url": "https://x.test", "disposition": "nope"})

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="must be an object"):
            normalise_row("not-a-row")  # type: ignore[arg-type]

    def test_explicit_ts_wins_over_payload(self):
        row = normalise_row(
            {"url": "https://x.test", "disposition": "created",
             "ts": "1999-01-01T00:00:00+00:00"},
            ts="2026-09-14T08:30:00+00:00",
        )
        assert row["ts"] == "2026-09-14T08:30:00+00:00"


class TestRecallMetrics:
    def test_created_over_eligible(self):
        rows = [
            _row(disposition="created"),
            _row(game_key="BBL|C|D|t"),
            _row(game_key="BBL|E|F|t", disposition="created"),
            _row(game_key="BBL|G|H|t"),
        ]
        metrics = recall_metrics(rows)
        assert (metrics["eligible"], metrics["captured"]) == (4, 2)
        assert metrics["recall"] == 0.5
        assert metrics["missed"] == 2

    def test_rows_without_live_signal_are_never_eligible(self):
        rows = [_row(live_signal=False), _row(game_key="BBL|C|D|t", live_signal=False)]
        metrics = recall_metrics(rows)
        assert metrics["eligible"] == 0
        assert metrics["recall"] is None

    def test_same_game_from_many_backends_counts_once(self):
        rows = [
            _row(backend="exa-mcp", disposition="created"),
            _row(backend="tinyfish", disposition="created"),
            _row(backend="firecrawl", disposition="created"),
        ]
        metrics = recall_metrics(rows)
        assert (metrics["rows"], metrics["unique_games"], metrics["captured"]) == (3, 1, 1)
        assert metrics["recall"] == 1.0

    def test_one_created_row_captures_the_whole_game(self):
        rows = [_row(disposition="unverifiable"), _row(disposition="created")]
        assert recall_metrics(rows)["captured"] == 1

    def test_unverifiable_counts_only_when_not_captured(self):
        rows = [
            _row(game_key="A", disposition="unverifiable"),
            _row(game_key="B", disposition="unverifiable"),
            _row(game_key="B", disposition="created"),
        ]
        metrics = recall_metrics(rows)
        assert metrics["unverifiable"] == 1
        assert metrics["unverifiable_keys"] == ["A"]

    def test_empty_ledger(self):
        metrics = recall_metrics([])
        assert metrics["eligible"] == 0 and metrics["recall"] is None


class TestSelectRetry:
    def test_retries_unverifiable_uncaptured_games_only(self):
        rows = [
            _row(game_key="A", disposition="unverifiable"),
            _row(game_key="B", disposition="created"),
            _row(game_key="C", disposition="rejected_freeAccess"),
        ]
        retry = select_retry(rows)
        assert [item["game_key"] for item in retry] == ["A"]
        assert retry[0]["attempts"] == 1

    def test_game_captured_later_is_not_retried(self):
        rows = [
            _row(game_key="A", disposition="unverifiable"),
            _row(game_key="A", disposition="created"),
        ]
        assert select_retry(rows) == []

    def test_before_run_excludes_current_run_rows(self):
        rows = [
            _row(game_key="A", disposition="unverifiable",
                 first_seen_run="2026-09-14T08:30Z"),
            _row(game_key="B", disposition="unverifiable",
                 first_seen_run="2026-09-10T08:30Z"),
        ]
        retry = select_retry(rows, before_run="2026-09-14T08:30Z")
        assert [item["game_key"] for item in retry] == ["B"]

    def test_no_live_signal_is_not_retried(self):
        rows = [_row(game_key="A", disposition="unverifiable", live_signal=False)]
        assert select_retry(rows) == []


class TestIO:
    def test_append_then_read_roundtrip(self, tmp_path):
        ledger = tmp_path / "candidates.jsonl"
        written = append_rows([_row(), _row(game_key="X")], ledger)
        assert written == 2
        assert len(read_ledger(ledger)) == 2

    def test_read_skips_malformed_lines(self, tmp_path):
        ledger = tmp_path / "candidates.jsonl"
        ledger.write_text('{"a": 1}\n\nnot json\n[1,2]\n', encoding="utf-8")
        assert len(read_ledger(ledger)) == 1

    def test_read_missing_file_is_empty(self, tmp_path):
        assert read_ledger(tmp_path / "nope.jsonl") == []


def _fixture(game_key="BBL|A|B|2026-09-16T19:00Z", **overrides):
    fixture = {
        "league": "BBL",
        "teams": ["A", "B"],
        "start": "2026-09-16T19:00:00+02:00",
        "game_key": game_key,
        "source": "bbl",
    }
    fixture.update(overrides)
    return fixture


class TestUnseenKeys:
    """Phase 5: the games no backend surfaced at all."""

    def test_a_fixture_absent_from_the_ledger_is_unseen(self):
        rows = [_row(game_key="G1")]
        fixtures = [_fixture(game_key="G1"), _fixture(game_key="G2")]
        assert unseen_keys(rows, fixtures) == ["G2"]

    def test_a_fixture_that_only_was_rejected_is_still_seen(self):
        # `seen` means "a backend surfaced it", not "we captured it". Those are
        # different questions, and conflating them would hide the harder one.
        rows = [_row(game_key="G1", disposition="rejected_freeAccess")]
        assert unseen_keys(rows, [_fixture(game_key="G1")]) == []

    def test_results_are_sorted_for_stable_diffing(self):
        rows: list[dict] = []
        fixtures = [_fixture(game_key="Z"), _fixture(game_key="A")]
        assert unseen_keys(rows, fixtures) == ["A", "Z"]

    def test_a_fixture_without_a_game_key_is_not_unseen(self):
        # A fixture that failed to normalise is a parsing problem. Counting it
        # here would report a phantom missed game and deflate recall.
        assert unseen_keys([], [_fixture(game_key=""), {"game_key": None}]) == []

    def test_non_dict_rows_and_fixtures_are_ignored(self):
        assert unseen_keys(["junk", None], [_fixture()]) == ["BBL|A|B|2026-09-16T19:00Z"]
        assert unseen_keys([], ["junk", None]) == []  # type: ignore[list-item]

    def test_empty_ledger_means_every_fixture_is_unseen(self):
        assert len(unseen_keys([], [_fixture(game_key="A"), _fixture(game_key="B")])) == 2


class TestFixtureRecall:
    def test_counts_and_ratio(self):
        report = fixture_recall(
            [_row(game_key="G1")],
            [_fixture(game_key="G1"), _fixture(game_key="G2")],
        )
        assert report["fixtures"] == 2
        assert report["surfaced"] == 1
        assert report["unseen"] == 1
        assert report["fixture_recall"] == 0.5
        assert report["unseen_keys"] == ["G2"]

    def test_no_fixtures_gives_a_null_ratio_not_zero(self):
        # 0.0 would read as "we found nothing"; null reads as "we did not look".
        report = fixture_recall([], [])
        assert report["fixtures"] == 0
        assert report["fixture_recall"] is None

    def test_perfect_coverage(self):
        report = fixture_recall([_row(game_key="G1")], [_fixture(game_key="G1")])
        assert report["fixture_recall"] == 1.0
        assert report["unseen_keys"] == []

    def test_repeated_observations_do_not_inflate_the_denominator(self):
        # The telemetry job re-observes the same 7-day window every day. If the
        # denominator counted rows instead of distinct games, the metric would
        # depend on how long the file happened to be rather than on coverage.
        once = fixture_recall([_row(game_key="G1")], [_fixture(game_key="G1")])
        thrice = fixture_recall(
            [_row(game_key="G1")],
            [_fixture(game_key="G1"), _fixture(game_key="G1"), _fixture(game_key="G1")],
        )
        assert thrice["fixtures"] == once["fixtures"] == 1
        assert thrice["fixture_recall"] == once["fixture_recall"] == 1.0

    def test_duplicates_and_parse_failures_are_reported_separately(self):
        # They mean opposite things: a duplicate is a re-observation (harmless),
        # a missing game_key is a parser regression (alarming).
        report = fixture_recall(
            [_row(game_key="G1")],
            [
                _fixture(game_key="G1"),
                _fixture(game_key="G1"),   # duplicate
                _fixture(game_key=""),     # parse failure
                _fixture(game_key="G2"),
            ],
        )
        assert report["rows"] == 4
        assert report["fixtures"] == 2   # G1, G2
        assert report["dropped"] == 1    # the empty game_key
        assert report["duplicates"] == 1 # the repeated G1
        assert report["fixture_recall"] == 0.5

    def test_distinct_fixtures_keeps_first_occurrence_in_order(self):
        fixtures = [
            _fixture(game_key="B"),
            _fixture(game_key="A"),
            _fixture(game_key="B"),
        ]
        assert [f["game_key"] for f in distinct_fixtures(fixtures)] == ["B", "A"]

    def test_distinct_fixtures_drops_junk(self):
        assert distinct_fixtures(["junk", None, {"game_key": None}]) == []  # type: ignore[list-item]


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestCli:
    def test_append_and_recall(self, tmp_path):
        rows_file = tmp_path / "rows.json"
        rows_file.write_text(
            json.dumps(
                [
                    {"url": "https://a.test/live", "game_key": "G1",
                     "live_signal": True, "disposition": "created"},
                    {"url": "https://b.test/live", "game_key": "G2",
                     "live_signal": True, "disposition": "unverifiable"},
                ]
            ),
            encoding="utf-8",
        )
        ledger = tmp_path / "candidates.jsonl"
        result = _run(["append", "--rows", str(rows_file), "--ledger", str(ledger)])
        assert result.returncode == 0
        assert "appended 2 rows" in result.stdout

        recall = _run(["recall", "--ledger", str(ledger)])
        assert recall.returncode == 0
        assert "recall=0.5" in recall.stdout
        assert "missed=1" in recall.stdout

    def test_empty_ledger_recall_exits_one(self, tmp_path):
        result = _run(["recall", "--ledger", str(tmp_path / "empty.jsonl")])
        assert result.returncode == 1
        assert "denominator is empty" in result.stderr

    def test_invalid_row_exits_usage(self, tmp_path):
        rows_file = tmp_path / "rows.json"
        rows_file.write_text(json.dumps([{"disposition": "created"}]), encoding="utf-8")
        result = _run(
            ["append", "--rows", str(rows_file),
             "--ledger", str(tmp_path / "c.jsonl")]
        )
        assert result.returncode == 2

    def test_allow_invalid_skips_bad_rows(self, tmp_path):
        rows_file = tmp_path / "rows.json"
        rows_file.write_text(
            json.dumps(
                [
                    {"url": "https://a.test", "disposition": "created"},
                    {"url": "https://b.test", "disposition": "bogus"},
                ]
            ),
            encoding="utf-8",
        )
        result = _run(
            ["append", "--rows", str(rows_file), "--allow-invalid",
             "--ledger", str(tmp_path / "c.jsonl")]
        )
        assert result.returncode == 0
        assert "skipped 1" in result.stdout

    def test_dry_run_writes_nothing(self, tmp_path):
        rows_file = tmp_path / "rows.json"
        rows_file.write_text(
            json.dumps([{"url": "https://a.test", "disposition": "created"}]),
            encoding="utf-8",
        )
        ledger = tmp_path / "c.jsonl"
        result = _run(
            ["append", "--rows", str(rows_file), "--ledger", str(ledger), "--dry-run"]
        )
        assert result.returncode == 0
        assert "dry-run" in result.stdout
        assert not ledger.exists()

    def test_retry_lists_and_exits_one_when_empty(self, tmp_path):
        ledger = tmp_path / "c.jsonl"
        append_rows([_row(game_key="A", disposition="unverifiable")], ledger)
        listed = _run(["retry", "--ledger", str(ledger)])
        assert listed.returncode == 0
        assert "retry A" in listed.stdout

        empty = tmp_path / "empty.jsonl"
        assert _run(["retry", "--ledger", str(empty)]).returncode == 1

    def test_missing_rows_file_is_usage_error(self, tmp_path):
        result = _run(
            ["append", "--rows", str(tmp_path / "nope.json"), "--ledger", "x.jsonl"]
        )
        assert result.returncode == 2

    def test_unseen_mode_reports_the_gap(self, tmp_path):
        ledger = tmp_path / "c.jsonl"
        append_rows([_row(game_key="G1")], ledger)
        fixtures = tmp_path / "fixtures.jsonl"
        fixtures.write_text(
            json.dumps(_fixture(game_key="G1"))
            + "\n"
            + json.dumps(_fixture(game_key="G2"))
            + "\n",
            encoding="utf-8",
        )
        result = _run(
            ["unseen", "--ledger", str(ledger), "--fixtures", str(fixtures)]
        )
        assert result.returncode == 0
        assert "UNSEEN G2" in result.stdout
        assert "unseen=1" in result.stdout
        assert "fixture_recall=0.5" in result.stdout

    def test_unseen_mode_without_fixtures_exits_one(self, tmp_path):
        ledger = tmp_path / "c.jsonl"
        append_rows([_row()], ledger)
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        result = _run(
            ["unseen", "--ledger", str(ledger), "--fixtures", str(empty)]
        )
        assert result.returncode == 1
        assert "nothing to measure against" in result.stderr

    def test_recall_with_fixtures_adds_the_fixture_line(self, tmp_path):
        ledger = tmp_path / "c.jsonl"
        append_rows([_row(game_key="G1", disposition="created")], ledger)
        fixtures = tmp_path / "fixtures.jsonl"
        fixtures.write_text(
            json.dumps(_fixture(game_key="G1"))
            + "\n"
            + json.dumps(_fixture(game_key="G2"))
            + "\n",
            encoding="utf-8",
        )
        result = _run(
            ["recall", "--ledger", str(ledger), "--fixtures", str(fixtures)]
        )
        assert result.returncode == 0
        assert "recall=1.0" in result.stdout
        assert "fixture_recall=0.5" in result.stdout
        assert "unseen=1" in result.stdout

    def test_recall_without_fixtures_is_unchanged(self, tmp_path):
        ledger = tmp_path / "c.jsonl"
        append_rows([_row(game_key="G1", disposition="created")], ledger)
        result = _run(["recall", "--ledger", str(ledger)])
        assert result.returncode == 0
        assert "fixture_recall" not in result.stdout
