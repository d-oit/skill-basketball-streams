"""Tests for scripts/capture_transcripts.py and runtime_eval.py --transcripts.

The point of this pair is that a transcript must be *captured*, never invented.
These tests exercise both shapes of the transcript file and the grading path,
entirely offline (--runner replay needs no credentials).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.capture_transcripts import build_prompt, load_cases, load_transcripts

from scripts import capture_transcripts as ct

REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURE = REPO_ROOT / "scripts" / "capture_transcripts.py"
RUNTIME_EVAL = REPO_ROOT / "scripts" / "runtime_eval.py"


def _run(script: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True
    )


def _expected_transcripts() -> dict[int, str]:
    payload = json.loads(
        (REPO_ROOT / "evals" / "evals.json").read_text(encoding="utf-8")
    )
    return {case["id"]: case["expected_output"] for case in payload["evals"]}


class TestPayloadProvenance:
    """The payload must say which model answered, not just which rung did.

    `rungs` already records the rung per case. That is not enough: `openrouter/free`
    is a router, so "the openrouter rung served this" does not name the model that
    wrote the transcript, and a grading failure then has no model to look at. The
    writer is the response's own `model` field (`LAST_MODELS`), and these tests
    pin that it reaches the file rather than dying in the module.
    """

    def _capture(self, monkeypatch, tmp_path, body: dict):
        monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
        monkeypatch.setattr(ct, "LAST_MODELS", {})
        monkeypatch.setattr(
            ct,
            "_request",
            lambda method, url, *, payload=None, headers=None, timeout=60: (
                200,
                body,
                "",
            ),
        )
        out = tmp_path / "transcripts.json"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "capture_transcripts.py",
                "--root",
                str(REPO_ROOT),
                "--runner",
                "openrouter",
                "--only",
                "1",
                "--out",
                str(out),
            ],
        )
        with pytest.raises(SystemExit) as exc:
            ct.main()
        assert exc.value.code == 0
        return json.loads(out.read_text(encoding="utf-8"))

    def test_the_served_model_reaches_the_file(self, monkeypatch, tmp_path):
        payload = self._capture(
            monkeypatch,
            tmp_path,
            {
                "model": "upstage/solar-pro-3:free",
                "choices": [{"message": {"content": "Decision=CREATE; x"}}],
            },
        )
        assert payload["models"] == {"1": "upstage/solar-pro-3:free"}
        assert payload["transcripts"][0]["output"] == "Decision=CREATE; x"

    def test_a_response_without_a_model_omits_the_key(self, monkeypatch, tmp_path):
        # Absent, not empty-string: an empty entry would read as "the model is the
        # empty string", which is a claim. No key is the honest shape.
        payload = self._capture(
            monkeypatch,
            tmp_path,
            {"choices": [{"message": {"content": "Decision=CREATE; x"}}]},
        )
        assert "models" not in payload


class TestLoadTranscripts:
    def test_rich_shape(self, tmp_path):
        path = tmp_path / "t.json"
        path.write_text(
            json.dumps({"transcripts": [{"id": 7, "output": "Decision=CREATE; x"}]}),
            encoding="utf-8",
        )
        assert load_transcripts(path) == {7: "Decision=CREATE; x"}

    def test_flat_mapping_shape(self, tmp_path):
        path = tmp_path / "t.json"
        path.write_text(json.dumps({"7": "out"}), encoding="utf-8")
        assert load_transcripts(path) == {7: "out"}

    def test_missing_file_is_usage_error(self, tmp_path):
        result = _run(
            CAPTURE, ["--runner", "replay", "--from", str(tmp_path / "no.json"), "--check"]
        )
        assert result.returncode == 2


class TestBuildPrompt:
    def test_prompt_carries_case_and_decision_shape(self):
        prompt = build_prompt(
            {"id": 3, "prompt": "some input", "expected_output": "Decision=SKIP; checks: a=FAIL"}
        )
        assert "Case 3" in prompt
        assert "some input" in prompt
        assert "Decision=" in prompt

    def test_the_answer_is_not_in_the_prompt(self):
        """Grading collects `name=PASS|FAIL` needles by substring, so a prompt
        that quoted `expected_output` would let a model score 100% by echoing
        it back — a grade that measures nothing."""
        expected = "Decision=CREATE; reason=secret-reason; checks: freeAccess=PASS"
        prompt = build_prompt(
            {"id": 4, "prompt": "some input", "expected_output": expected}
        )
        assert expected not in prompt
        assert "secret-reason" not in prompt
        assert "freeAccess=PASS" not in prompt
        # The check *vocabulary* is fair to point at; the verdict is not.
        assert "references/validation-workflow.md" in prompt


class TestLoadCases:
    def test_loads_all_cases(self):
        cases = load_cases(REPO_ROOT, [])
        assert len(cases) >= 32

    def test_only_filter(self):
        cases = load_cases(REPO_ROOT, [21, 22])
        assert sorted(case["id"] for case in cases) == [21, 22]


class TestCaptureCliReplay:
    def test_replay_check_passes_on_full_transcripts(self, tmp_path):
        path = tmp_path / "t.json"
        path.write_text(
            json.dumps(
                {
                    "transcripts": [
                        {"id": case_id, "output": output}
                        for case_id, output in _expected_transcripts().items()
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = _run(
            CAPTURE, ["--runner", "replay", "--from", str(path), "--check"]
        )
        assert result.returncode == 0
        assert "have a usable transcript" in result.stdout

    def test_replay_check_fails_on_missing_case(self, tmp_path):
        path = tmp_path / "t.json"
        path.write_text(
            json.dumps({"transcripts": [{"id": 1, "output": "x"}]}), encoding="utf-8"
        )
        result = _run(
            CAPTURE,
            ["--runner", "replay", "--from", str(path), "--check", "--only", "1",
             "--only", "2"],
        )
        assert result.returncode == 1
        assert "must never be invented" in result.stderr

    def test_replay_writes_output_file(self, tmp_path):
        source = tmp_path / "src.json"
        source.write_text(
            json.dumps({"1": "Decision=CREATE; checks: a=PASS"}), encoding="utf-8"
        )
        out = tmp_path / "out.json"
        result = _run(
            CAPTURE,
            ["--runner", "replay", "--from", str(source), "--only", "1",
             "--out", str(out)],
        )
        assert result.returncode == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["captured"] == 1
        assert payload["transcripts"][0]["id"] == 1

    def test_runner_command_requires_command(self):
        result = _run(CAPTURE, ["--runner", "command"])
        assert result.returncode == 2

    def test_no_matching_cases_is_usage_error(self):
        result = _run(CAPTURE, ["--runner", "replay", "--from", "x.json", "--only", "9999"])
        assert result.returncode == 2


class TestRuntimeEvalGrading:
    def test_grading_full_real_transcripts_passes(self, tmp_path):
        path = tmp_path / "transcripts.json"
        path.write_text(
            json.dumps(
                {
                    "transcripts": [
                        {"id": case_id, "output": output}
                        for case_id, output in _expected_transcripts().items()
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = _run(RUNTIME_EVAL, ["--root", ".", "--transcripts", str(path)])
        assert result.returncode == 0, result.stderr
        assert "cases passed against --transcripts" in result.stdout

    def test_a_model_style_transcript_passes_without_equalling_expected(self, tmp_path):
        """The capability this was written for. A real model wraps the answer in
        prose, so equality with `expected_output` is unachievable and grading on
        it made this path unable to pass anything."""
        path = tmp_path / "transcripts.json"
        path.write_text(
            json.dumps(
                {
                    "transcripts": [
                        {
                            "id": case_id,
                            "output": "I checked the approved sources.\n" + output,
                        }
                        for case_id, output in _expected_transcripts().items()
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = _run(RUNTIME_EVAL, ["--root", ".", "--transcripts", str(path)])
        assert result.returncode == 0, result.stderr
        assert "canonical text differs" in result.stdout

    def test_the_same_prose_input_still_fails_in_stub_mode(self, tmp_path):
        """The two regimes must not collapse into one. We author the stubs, so a
        stub drifting from `expected_output` is a real regression."""
        path = tmp_path / "stubs.json"
        path.write_text(
            json.dumps(
                {
                    case_id: "I checked the approved sources.\n" + output
                    for case_id, output in _expected_transcripts().items()
                }
            ),
            encoding="utf-8",
        )
        result = _run(RUNTIME_EVAL, ["--root", ".", "--stubs", str(path)])
        assert result.returncode == 1
        assert "stub != expected_output" in result.stderr

    def test_a_transcript_with_no_decision_line_fails(self, tmp_path):
        """Needles alone are not a decision: an echoed prompt must not pass."""
        path = tmp_path / "transcripts.json"
        path.write_text(
            json.dumps(
                {
                    "transcripts": [
                        {"id": case_id, "output": "checks: freeAccess=PASS"}
                        for case_id in _expected_transcripts()
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = _run(RUNTIME_EVAL, ["--root", ".", "--transcripts", str(path)])
        assert result.returncode == 1
        assert "no 'Decision=' line" in result.stderr

    def test_a_partial_needle_set_fails(self, tmp_path):
        """Assertion grading still has to be able to say no."""
        expected = _expected_transcripts()
        # Case 1 carries 7 assertions; keep only the first needle.
        expected[1] = expected[1].split(",")[0]
        path = tmp_path / "transcripts.json"
        path.write_text(
            json.dumps(
                {"transcripts": [{"id": k, "output": v} for k, v in expected.items()]}
            ),
            encoding="utf-8",
        )
        result = _run(RUNTIME_EVAL, ["--root", ".", "--transcripts", str(path)])
        assert result.returncode == 1
        assert "missing needle" in result.stderr

    def test_grading_a_wrong_transcript_fails(self, tmp_path):
        transcripts = _expected_transcripts()
        transcripts[1] = "Decision=CREATE; reason=invented; checks: freeAccess=PASS"
        path = tmp_path / "transcripts.json"
        path.write_text(
            json.dumps(
                {"transcripts": [{"id": k, "output": v} for k, v in transcripts.items()]}
            ),
            encoding="utf-8",
        )
        result = _run(RUNTIME_EVAL, ["--root", ".", "--transcripts", str(path)])
        assert result.returncode == 1
        assert "case #1" in result.stderr

    def test_missing_transcript_for_a_case_fails(self, tmp_path):
        path = tmp_path / "transcripts.json"
        path.write_text(json.dumps({"transcripts": [{"id": 1, "output": "x"}]}),
                        encoding="utf-8")
        result = _run(RUNTIME_EVAL, ["--root", ".", "--transcripts", str(path)])
        assert result.returncode == 1

    def test_an_empty_transcript_file_fails_rather_than_passing_vacuously(self, tmp_path):
        path = tmp_path / "transcripts.json"
        path.write_text(json.dumps({"transcripts": []}), encoding="utf-8")
        result = _run(RUNTIME_EVAL, ["--root", ".", "--transcripts", str(path)])
        assert result.returncode == 1
        assert "no transcript supplied" in result.stderr

    def test_an_empty_file_fails_even_with_allow_partial(self, tmp_path):
        """--allow-partial narrows the scope; it must not permit grading nothing."""
        path = tmp_path / "transcripts.json"
        path.write_text(json.dumps({"transcripts": []}), encoding="utf-8")
        result = _run(
            RUNTIME_EVAL,
            ["--root", ".", "--transcripts", str(path), "--allow-partial"],
        )
        assert result.returncode == 1
        assert "nothing was graded" in result.stderr

    def test_a_stale_case_id_is_an_error_not_a_silent_extra(self, tmp_path):
        """A transcript for a case that no longer exists is rot, and ignoring it
        is how a captured file drifts away from the eval set it claims to cover."""
        transcripts = _expected_transcripts()
        transcripts[9999] = "Decision=CREATE; checks: freeAccess=PASS"
        path = tmp_path / "transcripts.json"
        path.write_text(
            json.dumps(
                {"transcripts": [{"id": k, "output": v} for k, v in transcripts.items()]}
            ),
            encoding="utf-8",
        )
        result = _run(RUNTIME_EVAL, ["--root", ".", "--transcripts", str(path)])
        assert result.returncode == 1
        assert "not in evals.json" in result.stderr

    def test_non_string_output_is_usage_error(self, tmp_path):
        path = tmp_path / "transcripts.json"
        path.write_text(json.dumps({"transcripts": [{"id": 1, "output": 42}]}),
                        encoding="utf-8")
        result = _run(RUNTIME_EVAL, ["--root", ".", "--transcripts", str(path)])
        assert result.returncode == 1
        assert "not a string" in result.stderr

    def test_flat_shape_is_accepted_by_runtime_eval(self, tmp_path):
        path = tmp_path / "transcripts.json"
        path.write_text(
            json.dumps({str(k): v for k, v in _expected_transcripts().items()}),
            encoding="utf-8",
        )
        result = _run(RUNTIME_EVAL, ["--root", ".", "--transcripts", str(path)])
        assert result.returncode == 0, result.stderr

    def test_structural_mode_still_works_without_transcripts(self):
        result = _run(RUNTIME_EVAL, ["--root", "."])
        assert result.returncode == 0
        assert "OK: structural:" in result.stdout
