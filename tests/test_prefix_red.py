"""prefix_red.py — the pre-fix-red demonstration, tested end to end.

The property under test, stated precisely: a synthesised case is RED against
the transcript of the run that made the misjudgement, red *for the right
reason*. The run's own answer claims the opposite of what the case now demands
(`freeAccess=PASS` for a game the audit proved was paid), so the case's needle
(`freeAccess=FAIL`) is absent from reality.

Two properties here are load-bearing and easy to lose:

* **Scoping is not decoration.** A run's answer covers several games, and a
  correctly-rejected game carries the very needle the case demands. Graded
  whole-text, the misjudged game's case would look *satisfied* by another
  game's correct refusal — a false "not red" that reads as "the case is fine"
  when it means nothing of the sort. The exact-summary slice must exclude the
  other game's line.
* **Red alone is not the demonstration.** A needle absent from an unrelated
  transcript is also "red". `claimed` — the opposite needle PRESENT in the
  run's own answer — is what makes it the misjudgement.

Everything is offline: the transcript here is hand-authored (inputs may be
authored, per tests/fixtures/README.md); the property it stands in for is the
one the daily runtime records on the telemetry branch.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "prefix_red.py"

# The misjudged game's Step 7 row, exactly as the run's answer records it:
# freeAccess passed on evidence the audit later proved wrong.
MISJUDGED_LINE = (
    "BBL: ALBA BERLIN vs BMA365 Bamberg Baskets | 2026-10-03 18:30 | "
    "Decision=CREATE; checks: freeAccess=PASS, liveContent=PASS, officialSource=PASS"
)
# A correctly-rejected game in the SAME run — the trap: its line carries the
# needle the case demands, so a whole-text grade would find it and report the
# case as satisfied.
REJECTED_LINE = (
    "BBL: Hamburg Towers vs Ludwigsburg | 2026-10-04 19:00 | "
    "Decision=SKIP; checks: freeAccess=FAIL"
)
TRANSCRIPT = (
    '{"type":"message","part":{"text":"Step 7 table:\\n'
    + MISJUDGED_LINE.replace('"', '\\"')
    + "\\n"
    + REJECTED_LINE.replace('"', '\\"')
    + '"}}\n'
)

SUMMARY = "BBL: ALBA BERLIN vs BMA365 Bamberg Baskets"
CASES = {
    "cases": [
        {
            "id": 40,
            "prompt": "Audit regression (synthesised from ev-1): paid",
            "expected_output": (
                "Decision=SKIP; checks: freeAccess=FAIL, eventCreated=FAIL, "
                "regression=PASS"
            ),
            "assertions": [
                "freeAccess expected FAIL",
                "eventCreated expected FAIL",
                "regression expected PASS",
            ],
            "synthesised_from": "ev-1",
        }
    ]
}


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def _scene(tmp_path: Path, *, run_id: str = "2026-10-02T0900Z") -> None:
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "cases.json").write_text(json.dumps(CASES), encoding="utf-8")
    (tmp_path / "events.jsonl").write_text(
        json.dumps(
            {
                "event_id": "ev-1",
                "run_id": run_id,
                "game_key": "gk1",
                "summary": SUMMARY,
                "state": "VERIFIED",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "transcripts" / f"{run_id}.json").write_text(
        TRANSCRIPT, encoding="utf-8"
    )


class TestTheDemonstration:
    def test_red_for_the_right_reason(self, tmp_path):
        _scene(tmp_path)
        result = _run(
            "--cases", str(tmp_path / "cases.json"),
            "--events", str(tmp_path / "events.jsonl"),
            "--transcripts", str(tmp_path / "transcripts"),
            "--out", str(tmp_path / "out.json"),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "red against run 2026-10-02T0900Z" in result.stdout
        record = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
        row = record["results"][0]
        assert row["demonstrated"] is True
        assert row["scoped"] is True
        assert row["needle"] == "freeAccess=FAIL"
        assert row["claimed_needle"] == "freeAccess=PASS"

    def test_scoping_is_not_decoration(self, tmp_path):
        """Whole-text, the OTHER game's `freeAccess=FAIL` satisfies the needle
        and the misjudged game's case looks fine. The slice must exclude it."""
        _scene(tmp_path)
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        try:
            import prefix_red
        finally:
            sys.path.remove(str(REPO_ROOT / "scripts"))
        text = prefix_red.load_transcript(
            tmp_path / "transcripts" / "2026-10-02T0900Z.json"
        )
        scoped, was_scoped = prefix_red.scope_lines(SUMMARY, text)
        assert was_scoped
        assert MISJUDGED_LINE in scoped
        assert REJECTED_LINE not in scoped, (
            "the rejected game's line carries the needle the case demands; "
            "included in the slice it reads as the case being satisfied"
        )
        # And the difference it makes, stated outright:
        assert "freeAccess=FAIL" not in scoped
        assert "freeAccess=FAIL" in text

    def test_the_last_row_per_event_wins(self, tmp_path):
        """A re-planned event is recorded again; the newest row is the run to
        judge — same rule the audit applies to its verdicts."""
        _scene(tmp_path)
        with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "event_id": "ev-1",
                        "run_id": "2026-10-02T1200Z",
                        "game_key": "gk1",
                        "summary": SUMMARY,
                        "state": "VERIFIED",
                    }
                )
                + "\n"
            )
        result = _run(
            "--cases", str(tmp_path / "cases.json"),
            "--events", str(tmp_path / "events.jsonl"),
            "--transcripts", str(tmp_path / "transcripts"),
            "--list-needed",
        )
        assert result.stdout.strip() == "2026-10-02T1200Z"


class TestTheHonestNegatives:
    """Not-demonstrated is the pre-1.7.0 world and must be reported with its
    reason, never red — a red here would demand a transcript nobody kept."""

    def test_no_events_row(self, tmp_path):
        _scene(tmp_path)
        (tmp_path / "events.jsonl").write_text(
            json.dumps({"event_id": "other", "run_id": "r1"}) + "\n",
            encoding="utf-8",
        )
        result = _run(
            "--cases", str(tmp_path / "cases.json"),
            "--events", str(tmp_path / "events.jsonl"),
            "--transcripts", str(tmp_path / "transcripts"),
        )
        assert result.returncode == 1
        assert "no events.jsonl row for ev-1" in result.stdout

    def test_no_filed_transcript(self, tmp_path):
        _scene(tmp_path)
        (tmp_path / "transcripts" / "2026-10-02T0900Z.json").unlink()
        result = _run(
            "--cases", str(tmp_path / "cases.json"),
            "--events", str(tmp_path / "events.jsonl"),
            "--transcripts", str(tmp_path / "transcripts"),
        )
        assert result.returncode == 1
        assert "predates the transcript filing" in result.stdout

    def test_a_case_that_is_not_red_is_reported_as_suspect(self, tmp_path):
        """If the run's own answer already carries the needle, the case does
        not encode the misjudgement — that is a warning, not a pass."""
        _scene(tmp_path)
        case = dict(CASES["cases"][0])
        case["assertions"] = ["freeAccess expected PASS"]
        (tmp_path / "cases.json").write_text(
            json.dumps({"cases": [case]}), encoding="utf-8"
        )
        result = _run(
            "--cases", str(tmp_path / "cases.json"),
            "--events", str(tmp_path / "events.jsonl"),
            "--transcripts", str(tmp_path / "transcripts"),
        )
        assert result.returncode == 1
        assert "may not encode the bug" in result.stdout

    def test_a_missing_cases_file_is_usage(self, tmp_path):
        _scene(tmp_path)
        result = _run(
            "--cases", str(tmp_path / "absent.json"),
            "--events", str(tmp_path / "events.jsonl"),
            "--transcripts", str(tmp_path / "transcripts"),
        )
        assert result.returncode == 2


class TestListNeeded:
    def test_lists_only_joined_run_ids(self, tmp_path):
        _scene(tmp_path)
        result = _run(
            "--cases", str(tmp_path / "cases.json"),
            "--events", str(tmp_path / "events.jsonl"),
            "--transcripts", str(tmp_path / "transcripts"),
            "--list-needed",
        )
        assert result.stdout.strip() == "2026-10-02T0900Z"

    def test_nothing_joined_lists_nothing(self, tmp_path):
        _scene(tmp_path)
        (tmp_path / "events.jsonl").unlink()
        result = _run(
            "--cases", str(tmp_path / "cases.json"),
            "--events", str(tmp_path / "events.jsonl"),
            "--transcripts", str(tmp_path / "transcripts"),
            "--list-needed",
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""