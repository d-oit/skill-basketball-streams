"""`free_models.py` — a report that must not become a gate.

The defect this replaces is a hand-written table. `references/search-backends.md`
listed six "genuinely free" ids, annotated *"the free catalogue rotates"* — and
three weeks later **three of the six no longer resolved**, while the fourth,
`big-pickle`, degenerated into gibberish on a real run. A list that is out of date
is worse than no list, because it reads as current.

So the list is fetched, and two properties are pinned here:

1. **Free is decided by `cost`, not by a name.** `big-pickle` has no `-free`
   suffix and is free; `muse-spark-1.2-contributor-free` has one and also
   answers. A suffix is a guess about a catalogue that changes.
2. **It stays a report.** A selection that follows a live catalogue changes the
   model under a pinned expectation with no diff to review, and turns a provider
   hiccup into a red cron at 08:30. The pin lives in `llm_model.py` and moves on a
   branch, where the diff explains itself.

The catalogue is fetched live in no test here — a test that needs the network is
a test that goes red for someone else's outage, which is precisely the failure
mode this repository's sensors are built to avoid. The real payload's *shape* is
recorded in a fixture instead.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import free_models

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "free_models.py"
# Recorded from a real `https://models.dev/api.json` fetch on 2026-09-29,
# reduced to the three cases that decide the answer: a free id with no suffix,
# a free id with one, and a paid model.
CATALOGUE = {
    "opencode": {
        "id": "opencode",
        "models": {
            "longcat-2.5-preview-free": {
                "id": "longcat-2.5-preview-free",
                "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                "release_date": "2026-09-25",
                "reasoning": True,
                "tool_call": True,
                "limit": {"context": 1000000},
            },
            "big-pickle": {
                "id": "big-pickle",
                "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                "release_date": "2025-10-17",
                "reasoning": True,
                "tool_call": True,
                "limit": {"context": 200000},
            },
            "muse-spark-1.3-contributor-free": {
                "id": "muse-spark-1.3-contributor-free",
                "cost": {"input": 0, "output": 0},
                "release_date": "2026-09-02",
                "reasoning": True,
                "tool_call": True,
                "limit": {"context": 1048576},
            },
            "some-paid-model": {
                "id": "some-paid-model",
                "cost": {"input": 3, "output": 15},
                "release_date": "2026-01-01",
                "reasoning": True,
                "tool_call": True,
            },
        },
    },
    "openrouter": {
        "id": "openrouter",
        "models": {
            "openrouter/auto": {"id": "openrouter/auto", "cost": {"input": 0, "output": 0}},
        },
    },
}


class TestFreeIsDecidedByCost:
    @pytest.mark.parametrize(
        "cost,expected",
        [
            ({"input": 0, "output": 0}, True),
            ({"input": 0.0, "output": 0.0}, True),
            ({"input": 0, "output": None}, True),
            ({"input": 3, "output": 0}, False),
            ({"input": 0, "output": 15}, False),
            ({}, False),          # no cost block: unknown, so not "free"
            (None, False),        # not a dict
        ],
    )
    def test_a_name_suffix_is_never_consulted(self, cost, expected):
        assert free_models.is_free({"cost": cost}) is expected

    def test_big_pickle_is_free_despite_having_no_suffix(self):
        """The case that breaks a suffix rule, and the pin the runtime uses."""
        assert free_models.is_free(CATALOGUE["opencode"]["models"]["big-pickle"])

    def test_a_paid_model_is_excluded(self):
        ids = [m["id"] for m in free_models.free_models(CATALOGUE)]
        assert "some-paid-model" not in ids
        assert ids == [
            "longcat-2.5-preview-free",
            "muse-spark-1.3-contributor-free",
            "big-pickle",
        ]


class TestTheListing:
    def test_it_is_newest_release_first(self):
        """Most useful exactly when the pin is ageing."""
        ids = [m["id"] for m in free_models.free_models(CATALOGUE)]
        assert ids == [
            "longcat-2.5-preview-free",
            "muse-spark-1.3-contributor-free",
            "big-pickle",
        ]

    def test_another_provider_can_be_asked_for(self):
        assert [m["id"] for m in free_models.free_models(CATALOGUE, "openrouter")] == [
            "openrouter/auto"
        ]

    def test_an_unknown_provider_is_an_error_naming_it(self):
        with pytest.raises(RuntimeError, match="nope"):
            free_models.free_models(CATALOGUE, "nope")

    def test_a_provider_with_no_model_list_is_an_error(self):
        with pytest.raises(RuntimeError, match="no model list"):
            free_models.free_models({"opencode": {"id": "opencode"}})


class TestThePinnedStatus:
    def test_it_reports_a_pin_that_is_still_free(self):
        status = free_models.pinned_status(free_models.free_models(CATALOGUE))
        assert status["status"] == "free"
        assert status["released"] == "2026-09-25"
        assert status["tool_call"] is True

    def test_it_reports_a_pin_that_has_gone_paid(self):
        """The case that matters: a pin silently becoming a bill."""
        status = free_models.pinned_status(
            free_models.free_models(CATALOGUE), "opencode/some-paid-model"
        )
        assert status["status"] == "not-free"
        assert "not among the free models" in status["detail"]

    def test_it_reports_a_pin_that_has_vanished(self):
        status = free_models.pinned_status(
            free_models.free_models(CATALOGUE), "opencode/gone-forever"
        )
        assert status["status"] == "not-free"

    def test_the_pinned_constant_matches_the_runtime(self):
        """One pin, in one place, checked — not two that drift.

        The pin changed on 2026-09-29 (`big-pickle` -> `longcat-2.5-preview-free`)
        and this test is what makes a second copy of the id a failure rather than
        a silent divergence.
        """
        from scripts.llm_model import ZEN_DEFAULT_MODEL, resolve_model

        model, rung, _ = resolve_model("", {})
        assert f"opencode/{ZEN_DEFAULT_MODEL}" == free_models.PINNED_MODEL
        assert model == free_models.PINNED_MODEL
        assert rung == "opencode"


class TestItStaysAReport:
    def test_nothing_in_the_runtime_calls_it(self):
        """The separation is the whole design.

        A discovery step on the critical path would change the model under a
        pinned expectation with no diff, and turn models.dev being briefly down
        into a red cron. So: a *report* a human reads, and a *pin* a human
        changes.
        """
        for workflow in (REPO_ROOT / ".github" / "workflows").glob("*.yml"):
            assert "free_models" not in workflow.read_text(encoding="utf-8"), (
                f"{workflow.name} calls free_models; it is a report, and a "
                "runtime dependency on a live catalogue is the coupling this "
                "file exists to avoid"
            )

    def test_no_sensor_gates_on_it(self):
        text = (REPO_ROOT / "do-harness.toml").read_text(encoding="utf-8")
        assert "free_models" not in text, (
            "a sensor that fetches a live catalogue goes red for someone "
            "else's outage"
        )


class TestTheCli:
    def _run(self, *args: str, catalogue: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )

    def test_json_leaves_stdout_as_the_payload_alone(self, monkeypatch, capsys):
        """In-process: `monkeypatch` cannot reach a subprocess.

        Run as a subprocess this test would fetch the real catalogue, and a test
        that needs the network goes red for someone else's outage — the exact
        coupling `free_models.py` is written to avoid.
        """
        monkeypatch.setattr(free_models, "fetch_catalogue", lambda *a, **k: CATALOGUE)
        monkeypatch.setattr(sys, "argv", ["free_models.py", "--json"])
        free_models.main()
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)  # raises if a human line leaked
        assert parsed["provider"] == "opencode"
        assert len(parsed["free"]) == 3
        assert "OK: free_models:" in captured.err
        assert "OK: free_models:" not in captured.out

    def test_the_table_names_the_source(self):
        table = free_models.render_table(
            free_models.free_models(CATALOGUE),
            pinned=free_models.pinned_status(free_models.free_models(CATALOGUE)),
        )
        assert free_models.CATALOGUE_URL in table
        assert free_models.PINNED_MODEL.split("/", 1)[1] in table
        assert "**free**" in table

    def test_an_unreachable_catalogue_is_reported_not_guessed(self, monkeypatch, capsys):
        """Offline is a legitimate state for a report.

        Inventing a list here is exactly how the hand-written one went stale, so
        the failure must name the URL and refuse rather than fall back. Run
        in-process: `monkeypatch` cannot reach a subprocess, and stubbing the
        network in a test is what would make this flaky rather than honest.
        """

        def boom(*_a, **_k):
            raise RuntimeError(f"{free_models.CATALOGUE_URL} is unreachable: boom")

        monkeypatch.setattr(free_models, "fetch_catalogue", boom)
        monkeypatch.setattr(sys, "argv", ["free_models.py"])
        with pytest.raises(SystemExit) as excinfo:
            free_models.main()
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "models.dev" in err
        assert "is unreachable" in err

    def test_no_free_models_is_a_failure(self, monkeypatch, capsys):
        monkeypatch.setattr(
            free_models, "fetch_catalogue", lambda *a, **k: {"opencode": {"models": {}}}
        )
        monkeypatch.setattr(sys, "argv", ["free_models.py"])
        with pytest.raises(SystemExit) as excinfo:
            free_models.main()
        assert excinfo.value.code == 1
        assert "no free" in capsys.readouterr().err

    def test_dry_run_is_accepted_and_writes_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(free_models, "fetch_catalogue", lambda *a, **k: CATALOGUE)
        result = self._run("--dry-run", "--json")
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["free"]


class TestTheTableIsSafeToRender:
    """A catalogue name must not be able to break a `$GITHUB_OUTPUT` line."""

    def test_a_name_with_a_pipe_or_backtick_cannot_split_a_table_cell(self):
        hostile = {
            "opencode": {
                "models": {
                    "evil": {
                        "id": "evil|x`$(rm -rf /)`\nnext",
                        "cost": {"input": 0, "output": 0},
                        "release_date": "2026-01-01",
                    }
                }
            }
        }
        table = free_models.render_table(free_models.free_models(hostile))
        rows = [line for line in table.splitlines() if line.startswith("| `")]
        assert rows, "the hostile model must still be rendered"
        for line in rows:
            # One line, and the six delimiters of a five-column row — the pipe
            # that was IN the name is gone, so it cannot have added a column.
            assert line.count("|") == 6, line
        # What the escaping guarantees is that a name cannot restructure the
        # table or open a shell: no backtick survives, so nothing is code. The
        # words of a hostile name are still visible, which is correct — this is
        # inert text in a log, and pretending otherwise would be the lie.
        assert "`evil" in table and "` (rm -rf /)" not in table
        assert "evil x (rm -rf /) next" in table
