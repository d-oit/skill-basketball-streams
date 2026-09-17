"""Tests for scripts/rung_health.py and the daily rung-health jobs.

The gap these close. `live-stream-runtime-spec.md` §10 lists `rungs.json` in the
telemetry branch and §18.4 promises that "a rung parked for N consecutive days
raises an issue" — but `RungHealth` lives and dies inside one process, so nothing
was ever persisted and the promise could not be kept even in principle. A
backend could be retired (as GitHub Models was on 2026-07-30) with the only
trace being one `failed` line in one morning's log.

Four properties are worth more than the feature, and each is pinned below:

* **A page body never reaches the ledger.** Rows are built from named fields, not
  from `FetchResult.__dict__`, because `--probe --json` dumps whole documents.
* **Availability is not failure.** A rung with no key is `skipped`, which breaks
  a strike streak; otherwise a keyless repository would "park" the hosted rungs
  and a later key would start from a fake history.
* **The target's WAF is not our bug.** Only `fileable` failures accrue a filable
  streak, so a CDN tightening its rules does not open a weekly issue.
* **A finding that cannot be filed is loud**, and a run with nothing parked never
  reaches `gh` at all — asserted with a stub `gh` on `PATH` that records every
  call it receives.

Offline throughout: the rungs are fakes, `gh` is a shell stub, and the workflow
is read as YAML.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import render_ladder  # noqa: E402
from render_ladder import (  # noqa: E402
    LICENCE_AGPL,
    LICENCE_MIT,
    MAX_STRIKES,
    FetchResult,
    LicenceError,
    Rung,
    RungHealth,
)

from rung_health import (  # noqa: E402
    LEDGER_NAME,
    PARKED_DAYS,
    SNAPSHOT_NAME,
    TITLE_MARKER,
    append_rows,
    attempt_rows,
    build_body,
    build_markdown,
    build_snapshot,
    day_of,
    fileable,
    last_run_per_day,
    observed_rungs,
    parked_streaks,
    probe,
    read_ledger,
    render_probe,
    write_snapshot,
)

SCRIPT = REPO_ROOT / "scripts" / "rung_health.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"

EVIDENCE = "<video src='x.m3u8'></video><span>Jetzt live</span>"
ROW_KEYS = {
    "ts", "run_id", "url", "host", "rung", "kind", "state",
    "status", "elapsed_ms", "error", "strikes", "parked",
}


def _run(args: list[str], *, env: dict | None = None) -> subprocess.CompletedProcess:
    # Every argument is a string, deliberately: `subprocess` encodes each one and
    # an int raises from inside `_fork_exec`, where the message names no
    # argument at all.
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


class FakeRung(Rung):
    """A rung that scripts its own outcome."""

    def __init__(self, name, *, ok=False, blocked=False, skipped=False, text="",
                 error="", kind="hosted", requires_js=False, license=LICENCE_MIT,
                 opt_in=False, installed=True):
        self.name = name
        self.kind = kind
        self.requires_js = requires_js
        self.license = license
        self.opt_in = opt_in
        self._ok = ok
        self._blocked = blocked
        self._skipped = skipped
        self._text = text
        self._error = error
        self._installed = installed
        self.calls = 0

    def installed(self):
        return self._installed

    def fetch(self, url, *, timeout=20.0):
        self.calls += 1
        return FetchResult(
            ok=self._ok, blocked=self._blocked, skipped=self._skipped,
            text=self._text, error=self._error, rung=self.name,
            status=403 if self._blocked else (200 if self._ok else None),
        )


def _row(day, run_id, rung, state, *, parked=False, kind="hosted", host="magentasport.de",
         error="", strikes=1):
    return {
        "ts": f"{day}T08:30:00+00:00",
        "run_id": run_id,
        "url": f"https://{host}/",
        "host": host,
        "rung": rung,
        "kind": kind,
        "state": state,
        "status": 403 if state == "blocked" else (200 if state == "evidence" else None),
        "elapsed_ms": 12,
        "error": error,
        "strikes": strikes,
        "parked": parked,
    }


class TestAttemptRows:
    """The ledger is a few hundred bytes per run, and says what happened."""

    def test_a_page_body_never_reaches_the_ledger(self):
        # The real trap: `render_ladder --probe --json` carries every attempt's
        # `text` and `html`, which on a YouTube page is more than a megabyte.
        row = attempt_rows(
            run_id="r1",
            url="https://www.magentasport.de/",
            attempts=[FetchResult(ok=True, text=EVIDENCE * 20_000, rung="urllib")],
            tracker=RungHealth(),
            ts="2026-09-15T08:30:00+00:00",
        )[0]
        assert set(row) == ROW_KEYS
        assert len(json.dumps(row)) < 1_000, "a body leaked into the ledger"

    def test_the_state_comes_from_render_ladder(self):
        attempts = [
            FetchResult(ok=True, text=EVIDENCE, rung="a"),
            FetchResult(ok=True, text="<html>shell</html>", rung="b"),
            FetchResult(ok=False, blocked=True, status=403, rung="c"),
            FetchResult(ok=False, error="boom", rung="d"),
            FetchResult(ok=False, skipped=True, error="no key", rung="e"),
        ]
        rows = attempt_rows(
            run_id="r1", url="https://example.com/", attempts=attempts,
            tracker=RungHealth(), ts="2026-09-15T08:30:00+00:00",
        )
        assert [row["state"] for row in rows] == [
            "evidence", "ok-no-evidence", "blocked", "failed", "skipped"
        ]
        # The state label is the shared one, not a second implementation.
        assert render_ladder.attempt_state(attempts[-1]) == rows[-1]["state"]

    def test_a_row_knows_whether_its_rung_is_hosted(self):
        rows = attempt_rows(
            run_id="r1", url="https://example.com/",
            attempts=[FetchResult(ok=False, error="boom", rung="firecrawl")],
            tracker=RungHealth(), ts="2026-09-15T08:30:00+00:00",
        )
        assert rows[0]["kind"] == render_ladder.ALL_RUNGS["firecrawl"].kind

    def test_the_host_is_normalised(self):
        rows = attempt_rows(
            run_id="r1", url="https://www.magentasport.de/x",
            attempts=[FetchResult(ok=True, rung="urllib")],
            tracker=RungHealth(), ts="2026-09-15T08:30:00+00:00",
        )
        assert rows[0]["host"] == "magentasport.de"


class TestLedger:
    def test_append_then_read_round_trips(self, tmp_path):
        rows = [_row("2026-09-15", "r1", "firecrawl", "blocked")]
        assert append_rows(tmp_path, rows) == 1
        assert read_ledger(tmp_path) == rows

    def test_a_retried_run_does_not_inflate_the_history(self, tmp_path):
        rows = [_row("2026-09-15", "r1", "firecrawl", "blocked")]
        assert append_rows(tmp_path, rows) == 1
        assert append_rows(tmp_path, rows) == 0
        assert len(read_ledger(tmp_path)) == 1

    def test_a_second_run_on_the_same_day_is_a_second_row(self, tmp_path):
        append_rows(tmp_path, [_row("2026-09-15", "r1", "firecrawl", "blocked")])
        assert append_rows(tmp_path, [_row("2026-09-15", "r2", "firecrawl", "blocked")]) == 1
        assert len(read_ledger(tmp_path)) == 2

    def test_a_malformed_line_does_not_break_the_reader(self, tmp_path):
        (tmp_path / LEDGER_NAME).write_text(
            '{"rung": "a"}\nnot json\n{"rung": "b"}\n', encoding="utf-8"
        )
        assert [row["rung"] for row in read_ledger(tmp_path)] == ["a", "b"]

    def test_a_missing_ledger_is_empty_not_an_error(self, tmp_path):
        assert read_ledger(tmp_path) == []


class TestSnapshot:
    def test_skipped_does_not_extend_a_streak(self):
        # The key semantic: three failures park a rung, but a rung that was never
        # available resets the count instead of adding to it.
        rows = [
            _row("2026-09-15", "r1", "firecrawl", "blocked"),
            _row("2026-09-15", "r1", "firecrawl", "blocked"),
            _row("2026-09-15", "r1", "firecrawl", "skipped", error="no key"),
        ]
        snapshot = build_snapshot(rows, run_id="r1", ts="2026-09-15T09:00:00+00:00")
        assert snapshot["rungs"]["firecrawl"]["consecutive_failures"] == 0

    def test_three_failures_counts_as_the_threshold(self):
        rows = [_row("2026-09-15", "r1", "firecrawl", "blocked") for _ in range(MAX_STRIKES)]
        snapshot = build_snapshot(rows, run_id="r1", ts="t")
        entry = snapshot["rungs"]["firecrawl"]
        assert entry["consecutive_failures"] == MAX_STRIKES
        assert entry["max_strikes"] == MAX_STRIKES

    def test_parked_is_the_recorded_verdict_not_a_re_derivation(self):
        rows = [
            _row("2026-09-15", "r1", "firecrawl", "blocked", strikes=2),
            _row("2026-09-15", "r1", "firecrawl", "failed", parked=True, strikes=3,
                 error="parked after 3 consecutive failures"),
        ]
        snapshot = build_snapshot(rows, run_id="r1", ts="t")
        assert snapshot["parked"] == ["firecrawl"]
        assert snapshot["rungs"]["firecrawl"]["last_error"].startswith("parked after")

    def test_only_the_latest_run_drives_the_current_state(self):
        rows = [
            _row("2026-09-14", "r0", "firecrawl", "blocked", parked=True),
            _row("2026-09-15", "r1", "firecrawl", "evidence", strikes=0),
        ]
        snapshot = build_snapshot(rows, run_id="r1", ts="t")
        assert snapshot["parked"] == []
        assert snapshot["rungs"]["firecrawl"]["failures"] == 1
        assert snapshot["rungs"]["firecrawl"]["successes"] == 1
        assert snapshot["ledger_rows"] == 2

    def test_the_snapshot_is_derived_and_regenerates(self, tmp_path):
        rows = [_row("2026-09-15", "r1", "firecrawl", "blocked")]
        first = build_snapshot(rows, run_id="r1", ts="t")
        write_snapshot(tmp_path, first)
        write_snapshot(tmp_path, build_snapshot(rows, run_id="r1", ts="t2"))
        written = json.loads((tmp_path / SNAPSHOT_NAME).read_text())
        assert written["derived"] is True and written["ts"] == "t2"

    def test_every_state_is_counted_somewhere(self):
        rows = [
            _row("2026-09-15", "r1", "a", "evidence"),
            _row("2026-09-15", "r1", "a", "ok-no-evidence"),
            _row("2026-09-15", "r1", "a", "blocked"),
            _row("2026-09-15", "r1", "a", "failed"),
            _row("2026-09-15", "r1", "a", "skipped"),
        ]
        entry = build_snapshot(rows, run_id="r1", ts="t")["rungs"]["a"]
        assert entry["attempts"] == 5
        assert (entry["successes"], entry["ok_no_evidence"], entry["failures"],
                entry["skipped"]) == (1, 1, 2, 1)


class TestFileable:
    """Which failures are a finding about us, and which are about the target."""

    def test_a_hosted_rung_failing_is_ours(self):
        assert fileable({"kind": "hosted", "state": "blocked"}) is True

    def test_a_local_rung_refused_is_the_targets_waf(self):
        assert fileable({"kind": "local", "state": "blocked"}) is False

    def test_a_local_rung_that_cannot_connect_is_ours(self):
        assert fileable({"kind": "local", "state": "failed"}) is True

    def test_a_row_without_a_kind_falls_back_to_the_state(self):
        assert fileable({"state": "failed"}) is True
        assert fileable({"state": "skipped"}) is False


class TestParkedStreaks:
    def test_three_consecutive_days_is_the_default(self):
        rows = [
            _row(day, f"{day}T08:30Z", "firecrawl", "blocked", parked=True)
            for day in ("2026-09-13", "2026-09-14", "2026-09-15")
        ]
        streaks = parked_streaks(rows)
        assert streaks["firecrawl"]["streak"] == PARKED_DAYS
        assert streaks["firecrawl"]["as_of"] == "2026-09-15"

    def test_a_gap_breaks_the_streak_and_is_named(self):
        # 12th parked, 13th missing, 14th and 15th parked: the streak is two, and
        # the gap is reported so a dead backend can be told from a dead cron.
        rows = [
            _row("2026-09-12", "2026-09-12T08:30Z", "firecrawl", "blocked", parked=True),
            _row("2026-09-14", "2026-09-14T08:30Z", "firecrawl", "blocked", parked=True),
            _row("2026-09-15", "2026-09-15T08:30Z", "firecrawl", "blocked", parked=True),
        ]
        streaks = parked_streaks(rows)
        assert streaks["firecrawl"]["streak"] == 2
        assert streaks["firecrawl"]["gaps"] == ["2026-09-13"]

    def test_the_last_run_of_the_day_decides(self):
        # Parked at 08:30 and working at 20:30 means it is fixed, not dead.
        rows = [
            _row("2026-09-15", "2026-09-15T08:30Z", "firecrawl", "blocked", parked=True),
            _row("2026-09-15", "2026-09-15T20:30Z", "firecrawl", "evidence", strikes=0),
        ]
        assert "firecrawl" not in parked_streaks(rows)

    def test_a_streak_that_ended_is_not_a_current_finding(self):
        rows = [
            _row(day, f"{day}T08:30Z", "firecrawl", "blocked", parked=True)
            for day in ("2026-09-10", "2026-09-11", "2026-09-12")
        ] + [_row("2026-09-15", "2026-09-15T08:30Z", "firecrawl", "evidence", strikes=0)]
        streaks = parked_streaks(rows)
        assert "firecrawl" not in streaks

    def test_a_recovered_rung_is_not_reported(self):
        # The streak ends at the latest day *observed*, not the latest day that
        # was parked. Otherwise a rung fixed today re-reports last week's streak
        # forever and the issue can never close.
        rows = [
            _row("2026-09-13", "2026-09-13T08:30Z", "firecrawl", "blocked", parked=True),
            _row("2026-09-14", "2026-09-14T08:30Z", "firecrawl", "blocked", parked=True),
            _row("2026-09-15", "2026-09-15T08:30Z", "firecrawl", "evidence", strikes=0),
        ]
        assert "firecrawl" not in parked_streaks(rows)

    def test_a_local_rung_refused_everywhere_never_reaches_a_finding(self):
        # Parked in the ledger, absent from the finding: a 403 from the target is
        # what the ladder is designed to climb past.
        rows = [
            _row(day, f"{day}T08:30Z", "curl_cffi", "blocked", parked=True, kind="local")
            for day in ("2026-09-13", "2026-09-14", "2026-09-15")
        ]
        assert "curl_cffi" not in parked_streaks(rows)
        # ...but the snapshot still records it, so the ledger keeps the picture.
        assert build_snapshot(rows, run_id="2026-09-15T08:30Z", ts="t")["parked"] == [
            "curl_cffi"
        ]

    def test_days_are_utc_calendar_days(self):
        assert day_of("2026-09-15T23:59:00+00:00") == "2026-09-15"
        assert day_of("") == "" and day_of(None) == ""

    def test_last_run_per_day_picks_the_latest_run_id(self):
        rows = [
            _row("2026-09-15", "2026-09-15T08:30Z", "a", "blocked"),
            _row("2026-09-15", "2026-09-15T20:30Z", "a", "evidence"),
        ]
        picked = last_run_per_day(rows)
        assert [row["run_id"] for row in picked["2026-09-15"]] == ["2026-09-15T20:30Z"]

    def test_rows_without_a_day_are_ignored(self):
        assert last_run_per_day([{"ts": "", "run_id": "r"}]) == {}


class TestProbeOffline:
    """`probe()` with fake rungs: the whole write path, no network."""

    def test_one_tracker_parks_a_dead_hosted_rung(self, tmp_path):
        # Three hosts failing once each must reach three strikes. A tracker per
        # host would never park anything — that is why the CLI takes every URL in
        # one invocation.
        urls = [f"https://host{n}.example/" for n in range(4)]
        rows = probe(
            urls,
            dest=tmp_path,
            run_id="r1",
            ladder_factory=lambda url: [FakeRung("firecrawl", blocked=True, error="HTTP 401")],
        )
        assert len(rows) == 4
        assert [row["strikes"] for row in rows] == [1, 2, 3, MAX_STRIKES]
        assert rows[-1]["parked"] is True
        assert rows[-1]["error"].startswith("parked after")
        snapshot = json.loads((tmp_path / SNAPSHOT_NAME).read_text())
        assert snapshot["parked"] == ["firecrawl"]

    def test_a_rung_that_is_never_available_never_parks(self, tmp_path):
        rows = probe(
            [f"https://host{n}.example/" for n in range(4)],
            dest=tmp_path,
            run_id="r1",
            ladder_factory=lambda url: [FakeRung("firecrawl", skipped=True, error="no key")],
        )
        assert {row["state"] for row in rows} == {"skipped"}
        assert {row["strikes"] for row in rows} == {0}
        assert json.loads((tmp_path / SNAPSHOT_NAME).read_text())["parked"] == []

    def test_a_dry_run_writes_nothing(self, tmp_path):
        rows = probe(
            ["https://host1.example/"],
            dest=tmp_path,
            run_id="r1",
            ladder_factory=lambda url: [FakeRung("firecrawl", blocked=True)],
            dry_run=True,
        )
        assert rows and list(tmp_path.iterdir()) == []

    def test_the_licence_guard_still_applies_to_a_probe(self, tmp_path):
        with pytest.raises(LicenceError, match="opt-in"):
            probe(
                ["https://host1.example/"],
                dest=tmp_path,
                run_id="r1",
                ladder_factory=lambda url: [FakeRung("nodriver", license=LICENCE_AGPL)],
            )

    def test_a_probe_writes_a_ledger_row_per_attempt(self, tmp_path):
        # A rung that returns the app shell keeps the climb going, and both
        # attempts are recorded — the ledger is a transcript, not just the
        # winner.
        probe(
            ["https://host1.example/"],
            dest=tmp_path,
            run_id="r1",
            ladder_factory=lambda url: [
                FakeRung("urllib", ok=True, text="<html>shell</html>", kind="local"),
                FakeRung("firecrawl", ok=True, text=EVIDENCE),
            ],
        )
        assert [row["state"] for row in read_ledger(tmp_path)] == [
            "ok-no-evidence", "evidence"
        ]

    def test_the_probe_report_names_the_parked_rung(self):
        rows = [
            _row("2026-09-15", "r1", "firecrawl", "failed", parked=True,
                 error="parked after 3 consecutive failures"),
        ]
        report = render_probe(rows)
        assert f"PARKED: rung_health: firecrawl reached {MAX_STRIKES}" in report
        assert render_probe([]).endswith("no rung was parked in this run")


class TestRenderers:
    def test_the_markdown_states_the_policy_when_nothing_is_parked(self):
        markdown = build_markdown({}, days=3, as_of="2026-09-15", observed=4)
        assert "no rows" in markdown and "gap" in markdown
        # "0 rungs observed" would read as "nothing was tried" on an all-clear
        # summary where four backends answered.
        assert "| Rungs tried on the latest day | 4 |" in markdown

    def test_observed_rungs_counts_the_latest_day(self):
        rows = [
            _row("2026-09-14", "2026-09-14T08:30Z", "a", "blocked"),
            _row("2026-09-15", "2026-09-15T08:30Z", "a", "evidence"),
            _row("2026-09-15", "2026-09-15T08:30Z", "b", "skipped"),
        ]
        assert observed_rungs(rows) == 2
        assert observed_rungs([]) == 0

    def test_the_markdown_is_pure_and_lists_the_streak(self):
        streaks = {"firecrawl": {"streak": 4, "as_of": "2026-09-15", "parked_days": [],
                                 "last_error": "HTTP 403", "host": "x", "gaps": []}}
        markdown = build_markdown(streaks, days=3, as_of="2026-09-15", observed=3)
        assert "| `firecrawl` | 4 | HTTP 403 |" in markdown

    def test_the_title_carries_no_date_or_rung(self):
        assert TITLE_MARKER == "[rungs] render rung parked"

    def test_the_body_names_the_retired_backend_lesson_and_both_fixes(self):
        streaks = {"firecrawl": {"streak": 4, "as_of": "2026-09-15",
                                 "parked_days": ["2026-09-15"], "last_error": "HTTP 403",
                                 "host": "magentasport.de", "gaps": []}}
        body = build_body(streaks, days=3, as_of="2026-09-15")
        assert "2026-07-30" in body
        assert "Replace the rung" in body and "Rotate it" in body
        assert "never a failure" in body
        assert "rung-attempts.jsonl" in body

    def test_the_body_explains_a_gap(self):
        streaks = {"firecrawl": {"streak": 4, "as_of": "2026-09-15",
                                 "parked_days": ["2026-09-15"], "last_error": "HTTP 403",
                                 "host": "x", "gaps": ["2026-09-13"]}}
        body = build_body(streaks, days=3, as_of="2026-09-15")
        assert "2026-09-13" in body


class TestCli:
    def _ledger(self, tmp_path: Path, days: int = 3) -> Path:
        rows = [
            _row(day, f"{day}T08:30Z", "firecrawl", "blocked", parked=True, error="HTTP 401")
            for day in ("2026-09-12", "2026-09-13", "2026-09-14")[:days]
        ]
        append_rows(tmp_path, rows)
        return tmp_path

    def test_probe_without_a_url_is_a_usage_error(self, tmp_path):
        completed = _run(["probe", "--dest", str(tmp_path)])
        assert completed.returncode == 2

    def test_an_unknown_mode_is_a_usage_error(self):
        assert _run(["wat"]).returncode == 2

    def test_parked_without_a_telemetry_dir_is_a_usage_error(self, tmp_path):
        completed = _run(["parked", "--dest", str(tmp_path / "nope")])
        assert completed.returncode == 2
        assert "no ledger to read" in completed.stderr

    def test_zero_days_is_a_usage_error(self, tmp_path):
        assert _run(["parked", "--dest", str(tmp_path), "--days", "0"]).returncode == 2

    def test_file_and_dry_run_are_exclusive(self, tmp_path):
        self._ledger(tmp_path)
        completed = _run(
            ["parked", "--dest", str(tmp_path), "--days", "3", "--file", "--dry-run"]
        )
        assert completed.returncode == 2

    def test_without_file_it_only_reports(self, tmp_path):
        self._ledger(tmp_path)
        completed = _run(["parked", "--dest", str(tmp_path), "--days", "3"])
        assert completed.returncode == 0
        assert "PARKED: rung_health: firecrawl parked for 3" in completed.stdout

    def test_dry_run_prints_exactly_what_would_be_sent(self, tmp_path):
        self._ledger(tmp_path)
        completed = _run(["parked", "--dest", str(tmp_path), "--days", "3", "--dry-run"])
        assert completed.returncode == 0
        assert completed.stdout.startswith(TITLE_MARKER)
        assert "never a failure" in completed.stdout

    def test_json_output_is_parseable_on_its_own(self, tmp_path):
        self._ledger(tmp_path)
        for args in (
            ["parked", "--dest", str(tmp_path), "--days", "3", "--json"],
            ["snapshot", "--dest", str(tmp_path), "--json"],
        ):
            completed = _run(args)
            assert completed.returncode == 0, completed.stderr
            json.loads(completed.stdout)  # a stray prose line would raise

    def test_no_ledger_means_nothing_parked_and_no_failure(self, tmp_path):
        completed = _run(["parked", "--dest", str(tmp_path), "--days", "3"])
        assert completed.returncode == 0
        assert "no rung parked" in completed.stdout

    def test_snapshot_without_any_ledger_still_writes(self, tmp_path):
        completed = _run(["snapshot", "--dest", str(tmp_path)])
        assert completed.returncode == 0
        assert (tmp_path / SNAPSHOT_NAME).exists()

    def test_a_missing_gh_is_loud(self, tmp_path):
        self._ledger(tmp_path)
        completed = _run(
            ["parked", "--dest", str(tmp_path), "--days", "3", "--file"],
            env={"PATH": "", "PYTHONPATH": os.environ.get("PYTHONPATH", "")},
        )
        assert completed.returncode == 1
        assert "`gh` is not installed" in completed.stderr


class TestGithubIsOnlyTouchedWhenThereIsAFinding:
    """Stub `gh` on `PATH`: the negative is the property that matters."""

    def _stub_gh(self, tmp_path: Path, *, list_output: str = "[]") -> Path:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        calls = tmp_path / "gh-calls.txt"
        script = bin_dir / "gh"
        script.write_text(
            "#!/bin/sh\n"
            f'echo "$@" >> "{calls}"\n'
            'if [ "$1" = "issue" ] && [ "$2" = "list" ]; then\n'
            f"  echo '{list_output}'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return bin_dir

    def _env(self, bin_dir: Path) -> dict:
        return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    def _ledger(self, tmp_path: Path) -> Path:
        append_rows(
            tmp_path,
            [
                _row(day, f"{day}T08:30Z", "firecrawl", "blocked", parked=True)
                for day in ("2026-09-13", "2026-09-14", "2026-09-15")
            ],
        )
        return tmp_path

    def test_a_quiet_day_never_invokes_gh(self, tmp_path):
        bin_dir = self._stub_gh(tmp_path)
        dest = tmp_path / "dest"
        dest.mkdir()
        completed = _run(
            ["parked", "--dest", str(dest), "--days", "3", "--file"], env=self._env(bin_dir)
        )
        assert completed.returncode == 0
        assert not (tmp_path / "gh-calls.txt").exists(), "gh was called on a quiet day"

    def test_a_finding_opens_one_issue(self, tmp_path):
        bin_dir = self._stub_gh(tmp_path)
        dest = self._ledger(tmp_path / "dest")
        completed = _run(
            ["parked", "--dest", str(dest), "--days", "3", "--file"], env=self._env(bin_dir)
        )
        assert completed.returncode == 0
        calls = (tmp_path / "gh-calls.txt").read_text()
        assert "issue create" in calls and TITLE_MARKER in calls
        assert "issue comment" not in calls

    def test_a_persistent_finding_comments_instead_of_piling_up(self, tmp_path):
        existing = json.dumps([{"number": 12, "title": TITLE_MARKER}])
        bin_dir = self._stub_gh(tmp_path, list_output=existing)
        dest = self._ledger(tmp_path / "dest")
        completed = _run(
            ["parked", "--dest", str(dest), "--days", "3", "--file"], env=self._env(bin_dir)
        )
        assert completed.returncode == 0
        calls = (tmp_path / "gh-calls.txt").read_text()
        assert "issue comment 12" in calls
        assert "issue create" not in calls


class TestWorkflow:
    """Structural pins. The YAML is the safety model, so it is read as data."""

    def _document(self):
        yaml = pytest.importorskip("yaml")
        return yaml.safe_load(WORKFLOW.read_text())

    def _jobs(self) -> dict:
        return self._document()["jobs"]

    def _scripts(self, job: str) -> str:
        return "\n".join(
            step.get("run", "") for step in self._jobs()[job].get("steps") or []
        )

    def test_the_probe_job_is_not_gated_on_calendar_writes(self):
        job = self._jobs()["rung-health"]
        assert "if" not in job, "a dead render backend must be recorded anyway"
        assert "needs" not in job, "the search preflight must not block it"

    def test_the_probe_job_holds_no_calendar_credential(self):
        job = self._jobs()["rung-health"]
        assert job["permissions"] == {"contents": "write"}
        scripts = self._scripts("rung-health")
        assert "calendar_io" not in scripts and "--live" not in scripts
        assert "id-token" not in json.dumps(job["permissions"])

    def test_the_probe_uses_one_invocation_and_every_host(self):
        scripts = self._scripts("rung-health")
        probe = [line for line in scripts.splitlines() if "rung_health.py probe" in line]
        assert len(probe) == 1
        assert scripts.count("--url") >= PARKED_DAYS
        # `--allow-opt-in` would put an AGPL rung in the ladder.
        assert "--allow-opt-in" not in scripts

    def test_every_writer_of_the_telemetry_branch_serialises_on_one_group(self):
        writers = {
            name: job
            for name, job in self._jobs().items()
            if "git push origin telemetry" in self._scripts(name)
        }
        assert "telemetry" in writers and "rung-health" in writers
        for name, job in writers.items():
            concurrency = job.get("concurrency") or {}
            assert concurrency.get("group") == "telemetry-branch", name
            assert concurrency["cancel-in-progress"] is False, name
            # `queue: max` is load bearing, not decoration: the default is
            # `queue: single`, documented as cancel-and-replace for a *pending*
            # job in the group. A third writer eligible at the same moment would
            # therefore silently cancel a pending ledger job, and a cancelled
            # job looks exactly like a quiet day.
            assert concurrency.get("queue") == "max", name

    def test_the_filing_job_cannot_write_the_branch(self):
        job = self._jobs()["rung-issues"]
        assert job["permissions"] == {"contents": "read", "issues": "write"}
        scripts = self._scripts("rung-issues")
        assert "git push" not in scripts
        assert "_API_KEY" not in scripts and "secrets." not in scripts.replace(
            "secrets.GITHUB_TOKEN", ""
        )

    def test_the_filing_step_is_the_only_one_that_passes_file(self):
        # `--file` is also how the runtime agent is handed SKILL.md, so the
        # script has to be named: the capability is what is being pinned, not
        # the flag in general.
        filings = [
            (name, step.get("name"))
            for name, job in self._jobs().items()
            for step in job.get("steps") or []
            if "rung_health.py" in step.get("run", "")
            and "--file" in step.get("run", "")
        ]
        assert len(filings) == 1, filings
        assert filings[0][0] == "rung-issues"

    def test_filing_cannot_run_without_the_ledger_and_has_a_token(self):
        job = self._jobs()["rung-issues"]
        step = next(s for s in job["steps"] if "--file" in s.get("run", ""))
        assert step["if"] == "steps.ledger.outputs.ready == 'true'"
        assert step["env"]["GH_TOKEN"]  # required by gh

    def test_the_filing_job_runs_after_the_probe(self):
        assert self._jobs()["rung-issues"]["needs"] == "rung-health"

    def test_the_summaries_reuse_the_renderer(self):
        for job in ("rung-health", "rung-issues"):
            scripts = self._scripts(job)
            summary = scripts.split("Job summary")[-1]
            assert "--markdown" in summary, job
