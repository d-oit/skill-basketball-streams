"""Pytest suite for the self-improvement loop, end to end and offline.

The loop is meant to be:

    audit verdict -> synthesise an eval case -> capture a transcript for it
                  -> grade it -> a green gate may open a PR

Three of those four links had never run together, and one of them could not have
worked at all: a synthesised case is numbered after every committed case, so
grading it against `tests/fixtures/runtime_transcripts.json` (ids 1..N) fails
with "no transcript supplied" for the new case — blocking a legitimate PR for a
reason that has nothing to do with it. These tests pin both halves: the loop
works when a transcript is available, and the id arithmetic that makes the
per-id capture necessary.

Everything here is offline. The `replay` runner stands in for a model, which is
the whole reason it exists: the *plumbing* is testable without a credential even
though the *capture* is not.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNTH = REPO_ROOT / "scripts" / "synthesise_eval_case.py"
CAPTURE = REPO_ROOT / "scripts" / "capture_transcripts.py"
RUNTIME_EVAL = REPO_ROOT / "scripts" / "runtime_eval.py"
VERDICTS = REPO_ROOT / "tests" / "fixtures" / "synthesise_verdicts.jsonl"


def _run(script: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def _committed_ids() -> list[int]:
    payload = json.loads(
        (REPO_ROOT / "evals" / "evals.json").read_text(encoding="utf-8")
    )
    return [case["id"] for case in payload["evals"]]


def _repo_with_evals(tmp_path: Path) -> Path:
    """A throwaway root holding a copy of the real eval set."""
    root = tmp_path / "repo"
    (root / "evals").mkdir(parents=True)
    shutil.copy(
        REPO_ROOT / "evals" / "evals.json", root / "evals" / "evals.json"
    )
    return root


def _synthesise(root: Path) -> tuple[subprocess.CompletedProcess, list[dict]]:
    result = _run(
        SYNTH,
        [
            "--evals",
            str(root / "evals" / "evals.json"),
            "--verdicts",
            str(VERDICTS),
            "--now",
            "2026-09-15T08:30:00Z",
            "--json",
        ],
    )
    cases = json.loads(result.stdout)["cases"] if result.returncode == 0 else []
    return result, cases


def _transcript_for(cases: list[dict], path: Path) -> Path:
    """A captured-shape file for the given cases.

    Written from `expected_output` rather than captured, because this is a test
    of the plumbing. It is never a grading fixture for the skill — see
    `tests/fixtures/README.md` for that rule.
    """
    path.write_text(
        json.dumps(
            {
                "transcripts": [
                    {"id": case["id"], "output": case["expected_output"]}
                    for case in cases
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


class TestMachineReadableSynthesis:
    def test_json_mode_emits_the_payload_alone(self, tmp_path):
        """`self-improve.yml` parses stdout to learn which ids to capture.

        The human summary used to be printed *before* the payload, so
        `json.load(stdout)` raised. Same defect, same fix, as `upsert_events.py`
        and `calendar_io.py`.
        """
        root = _repo_with_evals(tmp_path)
        result, cases = _synthesise(root)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["cases"] == cases
        assert cases, "this fixture must produce at least one case"

    def test_the_summary_moves_to_stderr_rather_than_vanishing(self, tmp_path):
        root = _repo_with_evals(tmp_path)
        result, _ = _synthesise(root)
        assert "would append" in result.stderr or "appended" in result.stderr

    def test_non_json_mode_keeps_the_summary_on_stdout(self, tmp_path):
        """The human interface is unchanged; only the machine one was fixed."""
        root = _repo_with_evals(tmp_path)
        result = _run(
            SYNTH,
            [
                "--evals",
                str(root / "evals" / "evals.json"),
                "--verdicts",
                str(VERDICTS),
                "--dry-run",
            ],
        )
        assert result.returncode == 0
        assert "would append" in result.stdout


class TestNewCaseIdsAreOutsideTheCommittedCapture:
    def test_synthesised_ids_are_above_every_committed_case(self, tmp_path):
        """The reason the workflow captures per-id instead of reusing the
        committed set."""
        root = _repo_with_evals(tmp_path)
        _, cases = _synthesise(root)
        assert min(c["id"] for c in cases) > max(_committed_ids())

    def test_the_committed_capture_cannot_grade_a_new_case(self, tmp_path):
        root = _repo_with_evals(tmp_path)
        _, cases = _synthesise(root)
        # A capture covering only the committed cases, i.e. what the fixture
        # would hold on the day a case is synthesised.
        committed = json.loads(
            (REPO_ROOT / "evals" / "evals.json").read_text(encoding="utf-8")
        )["evals"]
        path = _transcript_for(committed, tmp_path / "committed.json")
        result = _run(
            RUNTIME_EVAL, ["--root", str(root), "--transcripts", str(path)]
        )
        assert result.returncode == 1
        assert f"case #{cases[0]['id']}" in result.stderr
        assert "no transcript supplied" in result.stderr


class TestTheLoopEndToEnd:
    def test_verdict_to_synthesised_case_to_capture_to_grade(self, tmp_path):
        root = _repo_with_evals(tmp_path)
        synth, cases = _synthesise(root)
        assert synth.returncode == 0, synth.stderr
        assert cases

        # The branch's eval set is the synthesised one; the gate grades the new
        # ids only, which is what the workflow does after the capture step.
        source = _transcript_for(cases, tmp_path / "source.json")
        args = ["--root", str(root), "--runner", "replay", "--from", str(source)]
        for case in cases:
            args += ["--only", str(case["id"])]
        out = tmp_path / "new-transcripts.json"
        args += ["--out", str(out)]
        capture = _run(CAPTURE, args)
        assert capture.returncode == 0, capture.stderr
        assert out.is_file(), "a complete capture must write its file"

        # --allow-partial: the file covers the new ids only, and the strict
        # default would fail the gate on every other case.
        graded = _run(
            RUNTIME_EVAL,
            ["--root", str(root), "--transcripts", str(out), "--allow-partial"],
        )
        assert graded.returncode == 0, graded.stderr
        assert "canonical text matched" in graded.stdout
        # The narrowing is reported, not hidden.
        assert "not in this file, so not graded" in graded.stdout

    def test_one_missing_needle_makes_the_new_case_red(self, tmp_path):
        """A gate that cannot fail is not a gate. This is the same chain with the
        new case's output truncated after its first needle."""
        root = _repo_with_evals(tmp_path)
        _, cases = _synthesise(root)
        broken = [
            {**case, "expected_output": case["expected_output"].split(",")[0]}
            for case in cases
        ]
        source = _transcript_for(broken, tmp_path / "source.json")
        out = tmp_path / "new-transcripts.json"
        args = ["--root", str(root), "--runner", "replay", "--from", str(source)]
        for case in cases:
            args += ["--only", str(case["id"])]
        args += ["--out", str(out)]
        assert _run(CAPTURE, args).returncode == 0

        graded = _run(
            RUNTIME_EVAL,
            ["--root", str(root), "--transcripts", str(out), "--allow-partial"],
        )
        assert graded.returncode == 1
        assert "missing needle" in graded.stderr

    def test_without_allow_partial_a_scoped_capture_fails_loudly(self, tmp_path):
        """The strict default must stay strict: dropping the flag cannot quietly
        turn a 3-case capture into a 36-case claim."""
        root = _repo_with_evals(tmp_path)
        _, cases = _synthesise(root)
        source = _transcript_for(cases, tmp_path / "source.json")
        out = tmp_path / "new-transcripts.json"
        args = ["--root", str(root), "--runner", "replay", "--from", str(source)]
        for case in cases:
            args += ["--only", str(case["id"])]
        args += ["--out", str(out)]
        assert _run(CAPTURE, args).returncode == 0

        graded = _run(RUNTIME_EVAL, ["--root", str(root), "--transcripts", str(out)])
        assert graded.returncode == 1
        assert "no transcript supplied" in graded.stderr

    def test_a_failed_capture_leaves_no_file_for_the_capture_flag(self, tmp_path):
        """The workflow treats the file existing as 'captured'. A partial capture
        must therefore not create one, or the gate would grade a subset and call
        it complete."""
        root = _repo_with_evals(tmp_path)
        _, cases = _synthesise(root)
        source = _transcript_for(cases[:1], tmp_path / "source.json")
        out = tmp_path / "new-transcripts.json"
        args = ["--root", str(root), "--runner", "replay", "--from", str(source)]
        for case in cases:
            args += ["--only", str(case["id"])]
        args += ["--out", str(out)]
        result = _run(CAPTURE, args)
        assert result.returncode == 1
        assert not out.exists()


class TestTheWorkflowWiring:
    """Structural pins, so the order above survives an edit to the YAML."""

    WORKFLOW = (REPO_ROOT / ".github" / "workflows" / "self-improve.yml").read_text(
        encoding="utf-8"
    )

    def test_capture_precedes_the_gate(self):
        assert self.WORKFLOW.index("Capture transcripts for the new case(s)") < (
            self.WORKFLOW.index("Gate — the repo's own graders")
        )

    def test_synthesis_writes_the_machine_readable_id_file(self):
        assert "--json > .tmp/new-cases.json" in self.WORKFLOW

    def test_the_gate_grades_the_new_cases_transcript(self):
        assert "--transcripts .tmp/new-transcripts.json" in self.WORKFLOW

    def test_the_scoped_grading_says_so_at_the_call_site(self):
        """`--allow-partial` is the concession that lets a 3-case capture be
        graded against a 36-case eval set. It must stay visible in the workflow."""
        assert (
            "--transcripts .tmp/new-transcripts.json --allow-partial"
            in self.WORKFLOW
        )

    def test_the_new_case_grading_is_inside_the_gate_step(self):
        """It used to be a sibling step, whose outcome the PR step never
        checked — so a failed grade could not stop a PR."""
        gate = self.WORKFLOW.split("Gate — the repo's own graders")[1]
        gate_body = gate.split("Open the pull request")[0]
        assert "--transcripts .tmp/new-transcripts.json" in gate_body

    def test_the_committed_capture_is_not_used_for_new_cases(self):
        assert (
            "--transcripts tests/fixtures/runtime_transcripts.json"
            not in self.WORKFLOW
        )
