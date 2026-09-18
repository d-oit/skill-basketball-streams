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

    def test_the_case_references_are_inlined(self):
        """`evals[].files` is the reference a case is decided from, and it used
        to be **write-only** — `synthesise_eval_case.py` emitted it, its test
        asserted it, and nothing ever opened one. The prompt asks for the check
        names "exactly as `references/validation-workflow.md` names them", which
        the two HTTP rungs have no filesystem to read: the material has to travel
        inside the prompt every rung shares.
        """
        prompt = build_prompt(
            {
                "id": 3,
                "prompt": "some input",
                "files": ["references/validation-workflow.md"],
            },
            root=REPO_ROOT,
        )
        assert "--- references/validation-workflow.md ---" in prompt
        # The reference's own body, not just its name.
        assert "Check 3: Official Source" in prompt
        assert prompt.index("Check 3: Official Source") < prompt.index("Case 3:")

    def test_inlining_a_reference_does_not_inline_the_verdict(self):
        """The references are the skill's own content; the verdict is the
        grader's. Both halves are asserted against a real eval case, so the two
        cannot drift into each other."""
        case = load_cases(REPO_ROOT, [3])[0]
        prompt = build_prompt(case, root=REPO_ROOT)
        assert case["expected_output"] not in prompt
        assert "officialSource=FAIL" not in prompt
        assert "Decision=SKIP" not in prompt
        assert "Check 3: Official Source" in prompt

    def test_a_declared_reference_that_is_missing_is_refused(self, tmp_path):
        """A case whose reference is absent cannot be captured honestly: the
        prompt would carry a model guessing at material nobody gave it, and the
        capture would look like every other capture."""
        root = tmp_path / "skill"
        (root / "evals").mkdir(parents=True)
        (root / "evals" / "evals.json").write_text(
            json.dumps(
                {
                    "evals": [
                        {
                            "id": 1,
                            "prompt": "some input",
                            "expected_output": "Decision=CREATE",
                            "assertions": ["a expected PASS"],
                            "files": ["references/gone.md"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        source = tmp_path / "src.json"
        source.write_text(json.dumps({"1": "Decision=CREATE"}), encoding="utf-8")
        result = _run(
            CAPTURE,
            ["--root", str(root), "--runner", "replay", "--from", str(source)],
        )
        assert result.returncode == 2
        assert "references/gone.md" in result.stderr

    def test_the_payload_records_what_each_case_was_decided_from(self, tmp_path):
        root = tmp_path / "skill"
        (root / "evals").mkdir(parents=True)
        (root / "references").mkdir()
        (root / "references" / "validation-workflow.md").write_text(
            "# Checks\n", encoding="utf-8"
        )
        (root / "evals" / "evals.json").write_text(
            json.dumps(
                {
                    "evals": [
                        {
                            "id": 1,
                            "prompt": "some input",
                            "assertions": ["a expected PASS"],
                            "files": ["references/validation-workflow.md"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        source = tmp_path / "src.json"
        source.write_text(json.dumps({"1": "Decision=CREATE"}), encoding="utf-8")
        out = tmp_path / "out.json"
        result = _run(
            CAPTURE,
            ["--root", str(root), "--runner", "replay", "--from", str(source),
             "--out", str(out)],
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["prompt_files"] == {"1": ["references/validation-workflow.md"]}


class TestTheCheckVocabularyIsDelivered:
    """The grader's needles and the references' ids must be one vocabulary.

    The grader matches `<check-id>=PASS|FAIL` by substring; the model can only
    emit a token something it read contains. Naming the ids in the references
    makes the reference the writer and the inlined prompt the reader — and this
class is the pair: if an id is asserted by any case but is named in no
    reference that case declares, the capture could never produce a transcript
    that passes it. That state existed for the life of the eval set (the ids
    were grader-only), and the live captures failed every assertion because of
    it.
    """

    CHECK_IDS = (
        "freeAccess",
        "liveContent",
        "officialSource",
        "basketballSpecific",
        "dateTimeRange",
        "workingLink",
        "directStreamVerification",
    )

    def _reference(self) -> str:
        return (REPO_ROOT / "references" / "validation-workflow.md").read_text(
            encoding="utf-8"
        )

    def test_the_reference_names_every_check_id(self):
        body = self._reference()
        missing = [check for check in self.CHECK_IDS if check not in body]
        assert not missing, (
            f"check id(s) {missing} are asserted by the eval set but named "
            "nowhere in references/validation-workflow.md — a transcript could "
            "never contain them"
        )

    @pytest.mark.parametrize("check", CHECK_IDS)
    def test_each_id_is_stated_as_an_id_in_its_check_heading(self, check: str):
        """Present is not enough — it must be presented *as* the check's id, in
        a `check id:` note on the heading, so the association is readable rather
        than incidental (the word also appears in prose like `duplicateCheck`)."""
        heading = next(
            line for line in self._reference().splitlines()
            if line.startswith("## Check") and f"`{check}`" in line
        )
        assert "check id:" in heading, heading

    def test_every_asserted_token_is_named_in_a_reference_the_case_declares(
        self, tmp_path
    ):
        """The whole vocabulary, generalised over the real eval set.

        Every `<id> expected PASS|FAIL` assertion's id must be found in at least
        one *markdown* reference the case declares (the material `build_prompt`
        actually inlines). A case may declare several references, so the pin is
        existential — named somewhere it will receive — not "in every file it
        names". This is the test that found cases 22, 23, 26, 32, 33 asserting
        check ids without declaring the reference that names them.
        """
        import re

        needle = re.compile(r"^(\w+) expected (?:PASS|FAIL)$")
        gaps = []
        for case in load_cases(REPO_ROOT, []):
            tokens = {
                m.group(1)
                for assertion in case.get("assertions") or []
                if (m := needle.match(assertion))
            }
            for token in sorted(tokens):
                delivered = any(
                    token
                    in (REPO_ROOT / name).read_text(encoding="utf-8")
                    for name in case.get("files") or []
                    if name.endswith(".md")
                    and (REPO_ROOT / name).is_file()
                )
                if not delivered:
                    gaps.append((case["id"], token))
        assert not gaps, (
            f"assertion id(s) {gaps} are named in no reference their case "
            "declares — a transcript could never contain them"
        )

    def test_the_inlined_prompt_contains_every_id_a_case_asserts(self):
        """End to end over the real eval set: build the real prompt for one
        real case and require the ids it will be graded on to be inside it."""
        case = load_cases(REPO_ROOT, [1])[0]
        prompt = build_prompt(case, root=REPO_ROOT)
        for check in self.CHECK_IDS:
            assert f"`{check}`" in prompt


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
