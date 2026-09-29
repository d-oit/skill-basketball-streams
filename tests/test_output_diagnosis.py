"""The extraction failure has to say *which* fault it is.

The message this replaces — "the agent must emit a fenced ```json block" — was
printed for three unrelated faults, and the one that actually occurred was
blamed on the contract:

| what happened | what the message said | where the fault was |
|---|---|---|
| 39,289 characters of near-random tokens, no answer | "you must emit a block" | **the model** |
| a sensible answer with no block | "you must emit a block" | the contract |
| a run that stopped before answering | "you must emit a block" | neither |

The first row is the 2026-09-29 dispatch. The response to a message like that is
to edit `SKILL.md` Step 7 — which is what I did, twice, before reading the
transcript. Diagnosis costs one pass over the text and turns a mystery into a
decision.

**The signal is randomness, not repetition, and the first implementation was
inverted.** I assumed a collapsed model repeats itself and scored repeated
12-grams; measured on the real degenerate output that scored **0.6%**, because
the fragments alternate and no 12-gram repeats, while a healthy markdown answer
scored **83%** — tables genuinely are repetitive. Token *diversity* separates
them cleanly, and the thresholds here are set from those measurements rather than
from a guess:

| output | unique-token share |
|---|---|
| degenerate run (real) | **0.986** |
| contract miss | 0.093 |
| healthy answer | 0.787 (on a short sample) |

The fixtures are reduced from a real transcript, not invented: a synthetic
"gibberish" string would score whatever the generator happened to produce, and
the point of the measurement is that it did not.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.extract_candidates import (
    DEGENERATION_DIVERSITY,
    MIN_ANSWER_CHARS,
    _model_text,
    _token_diversity,
    diagnose,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "extract_candidates.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
DEGENERATE = FIXTURES / "agent_transcript_degenerate.jsonl"
HEALTHY = FIXTURES / "agent_transcript_jsonl.txt"


class TestTheDegenerateFixtureIsReal:
    """Reduced from a real transcript, because the measurement is the point."""

    def test_it_is_a_jsonl_event_stream(self):
        lines = [l for l in DEGENERATE.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) >= 3
        for line in lines:
            json.loads(line)

    def test_it_carries_a_tool_result_and_model_text(self):
        """The tool output is the trap: it is unique-token noise too.

        Scored on the whole envelope the degenerate fixture reads *ordinary* — the
        JSON tool result and the session ids drag the number down. That is the
        measurement that made this non-obvious.
        """
        events = [json.loads(l) for l in DEGENERATE.read_text(encoding="utf-8").splitlines() if l.strip()]
        types = [e.get("part", {}).get("type") for e in events]
        assert "tool" in types, "the fixture must contain tool output to be a real test"
        assert "text" in types

    def test_it_stays_small(self):
        assert DEGENERATE.stat().st_size < 16_384, (
            "the corpus is committed; a fixture that grows without bound makes "
            "every clone heavier for no extra coverage"
        )


class TestTheSignalIsRandomness:
    def test_a_healthy_answer_is_not_called_degenerate(self):
        report = diagnose(HEALTHY.read_text(encoding="utf-8"))
        assert report["verdict"] != "degenerate"

    def test_a_real_degenerate_answer_is(self):
        report = diagnose(DEGENERATE.read_text(encoding="utf-8"))
        assert report["verdict"] == "degenerate"
        assert report["diversity"] >= DEGENERATION_DIVERSITY

    def test_the_tool_result_would_mask_the_signal_if_it_were_scored(self):
        """The isolation earns its keep: the tool result pulls the number down.

        Pinned as a *pair* because either number alone proves nothing — it is the
        gap that shows the isolation is doing something. The tool output here is
        a JSON blob whose tokens are all unique, which is exactly what dragged the
        real 2026-09-29 transcript from 0.986 down to 0.646 and made a collapsed
        model look like an ordinary answer.
        """
        raw = DEGENERATE.read_text(encoding="utf-8")
        events = [json.loads(l) for l in raw.splitlines() if l.strip()]
        tool_output = next(
            e["part"]["state"]["output"]
            for e in events
            if e.get("part", {}).get("type") == "tool"
        )
        tool_diversity = _token_diversity(tool_output)
        model_diversity = _token_diversity(_model_text(raw))

        assert tool_diversity is not None, "the tool result needs a real sample"
        assert model_diversity >= DEGENERATION_DIVERSITY
        assert tool_diversity < DEGENERATION_DIVERSITY, (
            "if the tool output also scored high, this fixture would not be "
            "testing the isolation at all"
        )

    def test_too_short_output_is_not_judged(self):
        """No sample, no statistic — and a short answer is not a random one."""
        assert _token_diversity("ok") is None
        assert _token_diversity("word " * 10) is None

    def test_the_verdict_is_reported_with_its_evidence(self):
        """A classification a reader must take on trust is not a diagnosis."""
        report = diagnose(DEGENERATE.read_text(encoding="utf-8"))
        for key in ("verdict", "reason", "chars", "fence", "diversity", "head", "tail"):
            assert key in report, key
        assert report["chars"] > 0
        assert "MODEL fault" in report["reason"]


class TestTheThreeFaultsAreDistinct:
    def test_a_contract_miss(self):
        report = diagnose(_transcript("Here is the Step 7 table for today. " * 30))
        assert report["verdict"] == "no-block"
        assert "SKILL.md Step 7" in report["reason"]

    def test_a_run_that_stopped_early(self):
        report = diagnose(_transcript("done"))
        assert report["verdict"] == "empty"
        assert "stopped" in report["reason"]

    def test_a_malformed_block(self):
        """A fence is present, so the *shape* is wrong, not the contract."""
        report = diagnose(
            _transcript(
                "Here is the answer. " * 20
                + "\n```json\n[{\"league\": \"BBL\"}]\n```\n"
            )
        )
        assert report["verdict"] == "malformed-block"
        assert report["fence"] is True

    def test_a_healthy_answer_extracts_and_diagnoses_to_nothing(self):
        report = diagnose(HEALTHY.read_text(encoding="utf-8"))
        # It extracted, so `diagnose` is not consulted — but it must not claim
        # the model was silent if a caller asks.
        assert report["chars"] > 0


class TestTheCliReportsIt:
    def test_the_failure_names_the_verdict_not_only_the_contract(self):
        result = _run(DEGENERATE)
        assert result.returncode == 1
        assert "degenerate" in result.stderr
        assert "MODEL fault" in result.stderr
        assert "free_models.py" in result.stderr, (
            "a model fault must point at the thing that fixes it"
        )

    def test_a_contract_miss_still_says_the_contract(self):
        result = _run(_write("prose.jsonl", _transcript("Here is the table. " * 30)))
        assert result.returncode == 1
        assert "no-block" in result.stderr
        assert "SKILL.md Step 7" in result.stderr

    def test_the_excerpt_is_printed_so_the_artifact_need_not_be_opened(self):
        result = _run(DEGENERATE)
        assert "head:" in result.stderr and "tail:" in result.stderr

    def test_json_carries_the_diagnosis(self):
        result = _run(DEGENERATE, "--json")
        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert payload["diagnosis"]["verdict"] == "degenerate"
        assert payload["candidates"] == []

    def test_a_successful_run_is_unaffected(self):
        result = _run(HEALTHY)
        assert result.returncode == 0
        assert "OK: extract_candidates" in result.stdout


class TestTheCollectorFix:
    """`_collect` must descend into nested keys, or nothing is found at all.

    The real envelope nests the model's output at `part.text`. `only_text_keys`
    mode used an `elif`, so a key that was not an output key was never walked —
    the envelope yielded *nothing*, and extraction only ever worked because the
    caller then fell back to "every string leaf". That fallback is the 73,425
    characters of noise this diagnosis has to see past.
    """

    def test_a_nested_output_key_is_found(self):
        from scripts.extract_candidates import _collect

        chunks: list[str] = []
        _collect({"type": "text", "part": {"text": "hello"}}, chunks, only_text_keys=True)
        assert chunks == ["hello"]

    def test_a_deeply_nested_output_key_is_found(self):
        from scripts.extract_candidates import _collect

        chunks: list[str] = []
        _collect(
            {"a": {"b": {"c": {"output": "deep"}}}}, chunks, only_text_keys=True
        )
        assert chunks == ["deep"]


def _transcript(text: str) -> dict:
    return {"transcripts": [{"id": 1, "output": text}]}


def _write(name: str, payload) -> Path:
    target = REPO_ROOT / ".tmp" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        target.write_text(payload, encoding="utf-8")
    else:
        target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _run(transcript: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--transcript", str(transcript), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
