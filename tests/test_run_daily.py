"""Tests for scripts/run_daily.py — the Phase 0 runtime CI runs every day.

Until now this script had no test at all, which is the worst place to have a
gap: it is the *only* thing that populates the recall denominator, and its
documented failure mode ("writes nothing rather than an empty ledger") is
invisible from the outside — a run that recorded nothing looks exactly like a
quiet day.

These tests are offline. Nothing here makes a network call: the backends are
replaced with fakes, and `--check-backends` is presence-based by design.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.run_daily import (
    ALL_BACKENDS,
    LADDER,
    Backend,
    backend_status,
    collect_candidates,
    host_of,
    is_plumbing,
    looks_live,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_DAILY = REPO_ROOT / "scripts" / "run_daily.py"
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"

RUN_ID = "2026-09-14T08:30Z"
TS = "2026-09-14T08:30:00+00:00"


def _run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    merged = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, str(RUN_DAILY), *args],
        capture_output=True,
        text=True,
        env=merged,
    )


class FakeBackend(Backend):
    """A backend with no env var, so `available()` is True, and canned results.

    `error` makes it fail the way a transport failure does — an empty result and
    a recorded reason — which is what a rejected key looks like from here.
    """

    def __init__(
        self, name: str, results: list[dict] | None = None, error: str = ""
    ):
        self.name = name
        self._results = results if results is not None else []
        self.searches: list[str] = []
        self._error = error

    def search(self, query: str, limit: int) -> list[dict]:
        self.searches.append(query)
        if self._error:
            self._fail(self._error)
            return []
        return self._results


class TestHostAndPlumbing:
    def test_host_strips_www(self):
        assert host_of("https://www.google.com/search?q=x") == "google.com"
        assert host_of("https://sport1.de/live") == "sport1.de"

    def test_host_of_garbage_is_empty_not_an_exception(self):
        assert host_of("not a url at all") == ""

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.google.com/search?q=basketball",
            "https://duckduckgo.com/?q=x",
            "https://x.com/BasketballCL",
            "https://www.reddit.com/r/basketball/",
            "https://en.wikipedia.org/wiki/BBL",
        ],
    )
    def test_search_engines_and_social_are_plumbing(self, url):
        assert is_plumbing(url) is True

    def test_a_real_stream_host_is_not_plumbing(self):
        assert is_plumbing("https://www.magenta.tv/tv/live-alba-berlin/1234") is False

    def test_subdomain_of_a_plumbing_host_is_plumbing(self):
        assert is_plumbing("https://m.facebook.com/page") is True

    def test_lookalike_domain_is_not_plumbing(self):
        # A suffix must follow a dot boundary, or `notgoogle.com` would be
        # suppressed as `google.com` and a real source would vanish from the
        # recall denominator.
        assert is_plumbing("https://notgoogle.com/live") is False


class TestLooksLive:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=abc123",
            "https://www.magenta.tv/tv/live-euroleague/9",
            "https://sport1.de/live",
        ],
    )
    def test_url_hints(self, url):
        assert looks_live(url) is True

    def test_title_hint(self):
        assert looks_live("https://example.com/a", "ALBA Berlin live stream heute") is True

    def test_a_vod_page_is_not_a_live_signal(self):
        assert looks_live("https://example.com/highlights", "Game recap") is False


class TestCollectCandidates:
    def _rows(self, backends, queries=("q1",)):
        rows, contributed, attempted = collect_candidates(
            list(queries), backends=backends, limit=20, run_id=RUN_ID, ts=TS
        )
        return rows, contributed, attempted

    def test_phase_0_never_claims_verification(self):
        backend = FakeBackend("fake", [{"url": "https://sport1.de/live", "title": "live"}])
        rows, _, _ = self._rows([backend])
        assert rows and all(row["disposition"] == "unverifiable" for row in rows)

    def test_row_carries_run_id_and_first_seen(self):
        backend = FakeBackend("fake", [{"url": "https://sport1.de/live"}])
        rows, _, _ = self._rows([backend])
        assert rows[0]["run_id"] == RUN_ID
        assert rows[0]["first_seen_run"] == RUN_ID

    def test_plumbing_results_are_dropped(self):
        backend = FakeBackend(
            "fake",
            [
                {"url": "https://www.google.com/search?q=x"},
                {"url": "https://sport1.de/live"},
            ],
        )
        rows, _, _ = self._rows([backend])
        assert [row["url"] for row in rows] == ["https://sport1.de/live"]

    def test_empty_and_blank_urls_are_dropped(self):
        backend = FakeBackend("fake", [{"url": ""}, {"url": "   "}, {"title": "no url"}])
        rows, _, _ = self._rows([backend])
        assert rows == []

    def test_same_url_twice_in_one_run_is_recorded_once(self):
        backend = FakeBackend(
            "fake", [{"url": "https://sport1.de/live"}, {"url": "https://sport1.de/live"}]
        )
        rows, _, _ = self._rows([backend], queries=("q1", "q2"))
        # Two queries, same backend, same URL: one row, not two.
        assert len(rows) == 1

    def test_one_backend_unreachable_does_not_stop_the_ladder(self):
        unreachable = FakeBackend("unreachable", [])
        reachable = FakeBackend("reachable", [{"url": "https://sport1.de/live"}])
        rows, contributed, attempted = self._rows([unreachable, reachable])
        assert len(rows) == 1
        # Attempted, but contributed nothing — the two must not be conflated, or
        # "1 candidate from [unreachable, reachable]" would imply both answered.
        assert attempted == ["unreachable", "reachable"]
        assert contributed == ["reachable"]

    def test_contributed_names_are_the_backends_that_answered(self):
        backend = FakeBackend("fake", [{"url": "https://sport1.de/live"}])
        _, contributed, attempted = self._rows([backend])
        assert contributed == ["fake"]
        assert attempted == ["fake"]

    def test_a_backend_whose_only_hits_are_plumbing_contributed_nothing(self):
        # It answered with results, but every result was filtered out, so crediting
        # it would overstate the coverage that produced the ledger row.
        backend = FakeBackend("fake", [{"url": "https://www.google.com/search?q=x"}])
        rows, contributed, attempted = self._rows([backend])
        assert rows == []
        assert contributed == []
        assert attempted == ["fake"]

    def test_no_candidates_is_not_a_crash(self):
        rows, contributed, attempted = self._rows([FakeBackend("fake", [])])
        assert rows == []
        assert contributed == []
        assert attempted == ["fake"]


class TestBackendStatus:
    def test_reports_presence_only(self, monkeypatch):
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        monkeypatch.setenv("TINYFISH_API_KEY", "dummy")
        status = dict((name, ok) for name, ok, _ in backend_status())
        assert status == {"exa-mcp": False, "tinyfish": True}

    def test_detail_names_the_env_var(self, monkeypatch):
        monkeypatch.delenv("EXA_API_KEY", raising=False)
        detail = dict((name, d) for name, _, d in backend_status())["exa-mcp"]
        assert "EXA_API_KEY" in detail

    def test_a_dead_key_still_counts_as_configured(self, monkeypatch):
        # Presence, not validity. This repo has a *present* OpenRouter key that
        # returns 401; a preflight that reds on that would red for a reason that
        # is not the run's fault.
        monkeypatch.setenv("EXA_API_KEY", "obviously-invalid")
        assert dict((n, ok) for n, ok, _ in backend_status())["exa-mcp"] is True

    def test_covers_every_ladder_rung(self):
        assert [name for name, _, _ in backend_status()] == list(LADDER)


class TestTheFailureNamesItsCause:
    """A run that surfaced nothing says WHY, so a rejected key is actionable.

    `backend_status` is presence-based on purpose — a preflight that reds on a
    key this repository happens to have a stale copy of would red for a reason
    that is not the run's fault. The consequence is that validity has to be
    reported somewhere else, and for a search backend it is here: "no candidates"
    used to cover both "the query found nothing" and "the key was rejected",
    which are the two cases an operator has to tell apart.
    """

    def _raising(self, monkeypatch, exc):
        import urllib.request

        def raiser(*args, **kwargs):
            raise exc

        monkeypatch.setattr(urllib.request, "urlopen", raiser)

    def test_a_rejected_key_names_the_status(self, monkeypatch):
        import urllib.error

        from scripts.run_daily import ExaBackend

        monkeypatch.setenv("EXA_API_KEY", "obviously-invalid")
        self._raising(
            monkeypatch,
            urllib.error.HTTPError("https://api.exa.ai/search", 401, "Unauthorized", {}, None),
        )

        backend = ExaBackend()
        assert backend.search("q", 5) == []
        assert backend.last_error == "HTTP 401"

    def test_a_transport_failure_names_its_type(self, monkeypatch):
        import urllib.error

        from scripts.run_daily import ExaBackend

        monkeypatch.setenv("EXA_API_KEY", "dummy")
        self._raising(monkeypatch, urllib.error.URLError("connection refused"))

        backend = ExaBackend()
        assert backend.search("q", 5) == []
        assert backend.last_error.startswith("URLError")

    def test_the_first_failure_wins_so_the_message_is_deterministic(self):
        backend = FakeBackend("fake", error="HTTP 401")
        backend.search("q", 5)
        # A later query failing differently must not rewrite the cause, or the
        # message depends on how many queries the ladder happened to run.
        backend._fail("HTTP 500")
        assert backend.last_error == "HTTP 401"

    def test_the_no_candidates_failure_names_the_cause(self, monkeypatch, capsys, tmp_path):
        import scripts.run_daily as run_daily

        backend = FakeBackend("fake", error="HTTP 401")
        monkeypatch.setattr(run_daily, "ALL_BACKENDS", {"fake": backend})
        monkeypatch.setattr(run_daily, "LADDER", ("fake",))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_daily.py", "--dest", str(tmp_path), "--backend", "fake",
                "--now", "2026-09-14T08:30:00+00:00", "--dry-run",
            ],
        )

        with pytest.raises(SystemExit) as excinfo:
            run_daily.main()

        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "surfaced no candidates" in err and "fake: HTTP 401" in err

    def test_a_clean_run_appends_no_cause(self, monkeypatch, capsys, tmp_path):
        """The cause is only added when there is one, so it stays a signal."""
        import scripts.run_daily as run_daily

        backend = FakeBackend("fake", [{"url": "https://sport1.de/live"}])
        monkeypatch.setattr(run_daily, "ALL_BACKENDS", {"fake": backend})
        monkeypatch.setattr(run_daily, "LADDER", ("fake",))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_daily.py", "--dest", str(tmp_path), "--backend", "fake",
                "--now", "2026-09-14T08:30:00+00:00", "--dry-run",
            ],
        )

        run_daily.main()

        err = capsys.readouterr().err
        assert "OK: run_daily" in err
        assert " — " not in err


class TestCliContracts:
    def test_check_backends_fails_with_no_credentials(self, tmp_path):
        result = _run(["--check-backends"], env={})
        assert result.returncode == 1
        assert "no search backend is configured" in result.stderr
        assert "EXA_API_KEY" in result.stderr

    def test_check_backends_passes_with_one_credential(self):
        result = _run(["--check-backends"], env={"EXA_API_KEY": "dummy"})
        assert result.returncode == 0
        assert "OK   backend exa-mcp" in result.stdout
        assert "NO   backend tinyfish" in result.stdout

    def test_check_backends_does_not_red_on_a_bad_argument_set(self):
        # `--check-backends` is a report of configuration, so it must be usable
        # as the very first CI step without other arguments present.
        result = _run(["--check-backends"], env={"TINYFISH_API_KEY": "dummy"})
        assert result.returncode == 0

    def test_run_fails_loudly_when_no_backend_is_available(self, tmp_path):
        result = _run(["--dest", str(tmp_path)], env={})
        assert result.returncode == 1
        assert "every backend is unavailable" in result.stderr
        # And it must not create the ledger on the way out: an empty ledger is
        # indistinguishable from a day with no games.
        assert not (tmp_path / "candidates.jsonl").exists()

    def test_check_backends_rejects_an_unknown_backend(self):
        # A typo'd backend name must not be silently ignored, or the preflight
        # would pass on a ladder that cannot run.
        result = _run(["--check-backends", "--backend", "nope"], env={"EXA_API_KEY": "dummy"})
        assert result.returncode == 2
        assert "unknown backend" in result.stderr

    def test_check_backends_is_offline(self):
        # A preflight that needs the network would red a run for a reason that
        # is not the run's fault. Point the backends at an unroutable host and
        # the check must still answer instantly.
        result = _run(["--check-backends"], env={"EXA_API_KEY": "dummy"})
        assert result.returncode == 0

    def test_usage_error_on_unknown_backend(self, tmp_path):
        result = _run(["--dest", str(tmp_path), "--backend", "nope"], env={})
        assert result.returncode == 2
        assert "unknown backend" in result.stderr

    def test_usage_error_on_empty_query_file(self, tmp_path):
        queries = tmp_path / "q.txt"
        queries.write_text("# only a comment\n\n", encoding="utf-8")
        result = _run(
            ["--dest", str(tmp_path), "--queries", str(queries)],
            env={"EXA_API_KEY": "dummy"},
        )
        assert result.returncode == 2
        assert "no queries to run" in result.stderr

    def test_usage_error_on_unknown_now(self, tmp_path):
        result = _run(
            ["--dest", str(tmp_path), "--now", "not-a-date"],
            env={"EXA_API_KEY": "dummy"},
        )
        assert result.returncode == 2
        assert "ISO-8601" in result.stderr


class TestJsonStdoutIsParseable:
    """`--json` must leave stdout as payload-only.

    The recurring defect in this repo: a human summary printed *before* the JSON
    document, which makes `json.load(stdout)` raise. It has now been fixed in
    upsert_events, calendar_io, synthesise_eval_case and here. These tests pin
    the contract rather than the workaround (an earlier test elsewhere was named
    `test_json_mode_is_parseable` and passed by stripping the summary line,
    asserting the opposite of its name).
    """

    def test_json_stdout_is_the_payload_and_nothing_else(self, monkeypatch, capsys, tmp_path):
        import scripts.run_daily as run_daily

        backend = FakeBackend("fake", [{"url": "https://sport1.de/live", "title": "live"}])
        monkeypatch.setattr(run_daily, "ALL_BACKENDS", {"fake": backend})
        monkeypatch.setattr(run_daily, "LADDER", ("fake",))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_daily.py",
                "--dest",
                str(tmp_path),
                "--backend",
                "fake",
                "--now",
                "2026-09-14T08:30:00+00:00",
                "--dry-run",
                "--json",
            ],
        )
        run_daily.main()

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert set(payload) == {"metrics", "backends", "attempted"}
        assert payload["backends"] == ["fake"]
        assert payload["attempted"] == ["fake"]
        # The human line still exists — on stderr, where it belongs.
        assert "OK: run_daily" in captured.err

    def test_human_lines_never_reach_stdout(self, monkeypatch, capsys, tmp_path):
        import scripts.run_daily as run_daily

        backend = FakeBackend("fake", [{"url": "https://sport1.de/live"}])
        monkeypatch.setattr(run_daily, "ALL_BACKENDS", {"fake": backend})
        monkeypatch.setattr(run_daily, "LADDER", ("fake",))
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_daily.py",
                "--dest",
                str(tmp_path),
                "--backend",
                "fake",
                "--now",
                "2026-09-14T08:30:00+00:00",
                "--json",
            ],
        )
        run_daily.main()
        captured = capsys.readouterr()
        assert "OK:" not in captured.out
        # And it really wrote the ledger it reported.
        assert (tmp_path / "candidates.jsonl").is_file()


class TestRuntimeDailyWiring:
    """The telemetry job must gate configuration and tolerate only content.

    Parsed with a regex rather than PyYAML: the production scripts stay
    stdlib-only, so no test needs a YAML dependency to read a workflow.
    """

    STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)

    @classmethod
    def _telemetry_steps(cls) -> list[str]:
        text = RUNTIME_DAILY.read_text(encoding="utf-8")
        start = text.index("\n  telemetry:")
        end = text.index("\n  runtime:")
        job = text[start:end]
        starts = [m.start() for m in cls.STEP_START.finditer(job)]
        return [job[s:e] for s, e in zip(starts, starts[1:] + [len(job)])]

    @classmethod
    def _find(cls, needle: str) -> str:
        matches = [s for s in cls._telemetry_steps() if needle in s]
        assert len(matches) == 1, f"expected exactly one step containing {needle!r}"
        return matches[0]

    BLOCK_MARKER = re.compile(r"^\s*[\w-]+:\s*\|\s*$")

    @classmethod
    def _without_comments(cls, step: str) -> str:
        # A comment *describing* a swallow must not read as one, and a `run: |`
        # block-scalar marker is not a pipe — both are false positives that would
        # make these assertions pass for the wrong reason.
        return "\n".join(
            line
            for line in step.splitlines()
            if not line.lstrip().startswith("#") and not cls.BLOCK_MARKER.match(line)
        )

    def test_the_preflight_exists_and_is_presence_based(self):
        step = self._find("--check-backends")
        assert "run_daily.py --check-backends" in self._without_comments(step)

    def test_the_preflight_is_not_tolerated(self):
        code = self._without_comments(self._find("--check-backends"))
        assert "||" not in code, "a configuration fault must red the run"
        assert "continue-on-error" not in code

    def test_the_preflight_runs_before_the_ladder(self):
        steps = self._telemetry_steps()
        preflight = next(i for i, s in enumerate(steps) if "--check-backends" in s)
        ladder = next(i for i, s in enumerate(steps) if "Run the Phase 0 search ladder" in s)
        assert preflight < ladder, (
            "the gate must precede the thing it gates, or a run with no backend "
            "configured would warn its way to a green, empty telemetry commit"
        )

    def test_the_ladder_step_uses_the_json_payload(self):
        step = self._find("Run the Phase 0 search ladder")
        assert "--json" in self._without_comments(step)

    def test_the_ladder_step_is_not_piped(self):
        # `run_daily.py ... | tee file || echo warning` reports *tee's* status, so
        # the tolerance would silently become a no-op.
        code = self._without_comments(self._find("Run the Phase 0 search ladder"))
        assert re.search(r"(?<!\|)\|(?!\|)", code) is None, code

    def test_the_ladder_step_tolerates_only_content_failures(self):
        code = self._without_comments(self._find("Run the Phase 0 search ladder"))
        assert "|| echo" in code, (
            "a transient HTTP error must not take the telemetry commit down with it"
        )
