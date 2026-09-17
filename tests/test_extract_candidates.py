"""Tests for scripts/extract_candidates.py.

This is the seam between the model-driven half of the runtime and the
deterministic half, and it is a security-relevant one: the runtime agent has no
`edit` and no `bash`, so the transcript is the only way it can put anything on
the calendar. The tests pin the two rules that keep that boundary honest:

1. **A candidate the model proposes is validated, never trusted.** Exactly two
   teams, a parseable start, and a proposable state.
2. **`WRONG` is not proposable.** It is an audit verdict. A run that could assert
   it would be overwriting the one label a human is meant to trust.

The envelope is a third-party format, so the extractor is also tested against
several shapes — an envelope change must not silently produce zero candidates,
because that looks exactly like "no games today".
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.extract_candidates import (
    candidate_objects,
    extract,
    json_blocks,
    normalise_candidate,
    transcript_text,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "extract_candidates.py"

CANDIDATE = {
    "league": "BBL",
    "teams": ["ALBA Berlin", "FC Bayern Muenchen"],
    "start": "2026-09-16T19:00:00+02:00",
    "state": "UNVERIFIED",
    "summary": "BBL: ALBA Berlin vs FC Bayern Muenchen",
}


def _transcript(text: str) -> dict:
    return {"transcripts": [{"id": 1, "output": text}]}


class TestTranscriptText:
    def test_rich_shape(self):
        assert "hello" in transcript_text(_transcript("hello"))

    def test_flat_shape(self):
        assert "hello" in transcript_text({"1": "hello"})

    def test_plain_string_payload(self):
        assert transcript_text("hello") == "hello"

    def test_falls_back_to_every_string_leaf(self):
        # An envelope change must not silently yield no candidates: that failure
        # is indistinguishable from "no games today".
        assert "hello" in transcript_text({"unknown": {"deep": ["hello"]}})

    def test_event_stream_with_parts(self):
        payload = {"events": [{"parts": [{"text": "block"}]}], "type": "message"}
        assert "block" in transcript_text(payload)

    def test_empty_payload(self):
        assert transcript_text({}) == ""


class TestJsonBlocks:
    def test_fenced_json_block(self):
        text = 'before\n```json\n[{"a": 1}]\n```\nafter'
        assert json_blocks(text) == [[{"a": 1}]]

    def test_fenced_without_a_language_tag(self):
        assert json_blocks("```\n[1]\n```") == [[1]]

    def test_malformed_block_is_skipped(self):
        assert json_blocks("```json\n{not json}\n```") == []

    def test_bare_json_document(self):
        assert json_blocks('[{"a": 1}]') == [[{"a": 1}]]

    def test_no_json_at_all(self):
        assert json_blocks("nothing here") == []

    def test_several_blocks(self):
        assert len(json_blocks("```json\n[1]\n```\n```json\n[2]\n```")) == 2


class TestCandidateObjects:
    def test_list_payload(self):
        assert candidate_objects([CANDIDATE]) == [CANDIDATE]

    @pytest.mark.parametrize("key", ["candidates", "streams", "games", "rows"])
    def test_envelope_keys(self, key):
        assert candidate_objects({key: [CANDIDATE]}) == [CANDIDATE]

    def test_single_bare_candidate(self):
        assert candidate_objects(CANDIDATE) == [CANDIDATE]

    def test_non_dict_items_are_dropped(self):
        assert candidate_objects(["junk", CANDIDATE]) == [CANDIDATE]

    def test_unrelated_dict_is_not_a_candidate(self):
        assert candidate_objects({"status": "ok"}) == []


class TestNormaliseCandidate:
    def test_full_candidate(self):
        candidate = normalise_candidate(CANDIDATE)
        assert candidate["league"] == "BBL"
        assert candidate["game_key"] == "BBL|ALBA Berlin|FC Bayern Muenchen|2026-09-16T17:00Z"

    def test_vs_string_teams(self):
        candidate = normalise_candidate(
            {"league": "BBL", "teams": "A vs B", "start": "2026-09-16T19:00:00+02:00"}
        )
        assert candidate["teams"] == ["A", "B"]

    def test_default_state_is_unverified(self):
        # Never VERIFIED by default: an unproven stream must not be presented as
        # confirmed merely because the model omitted the field.
        candidate = normalise_candidate(
            {"league": "BBL", "teams": ["A", "B"], "start": "2026-09-16T19:00:00+02:00"}
        )
        assert candidate["state"] == "UNVERIFIED"

    def test_summary_is_derived_when_absent(self):
        candidate = normalise_candidate(
            {"league": "BBL", "teams": ["A", "B"], "start": "2026-09-16T19:00:00+02:00"}
        )
        assert candidate["summary"] == "BBL: A vs B"

    @pytest.mark.parametrize(
        "raw,reason",
        [
            ({"teams": ["A", "B"], "start": "2026-09-16T19:00:00Z"}, "league"),
            ({"league": "BBL", "start": "2026-09-16T19:00:00Z"}, "two teams"),
            ({"league": "BBL", "teams": ["A"], "start": "2026-09-16T19:00:00Z"}, "two teams"),
            ({"league": "BBL", "teams": ["A", "B"], "start": "soon"}, "unparseable"),
            ({"league": "BBL", "teams": ["A", "B"], "start": ""}, "unparseable"),
        ],
    )
    def test_invalid_candidates_are_refused_with_a_reason(self, raw, reason):
        with pytest.raises(ValueError, match=reason):
            normalise_candidate(raw)

    def test_wrong_state_is_not_proposable(self):
        # An audit verdict, not something a run may assert about a game it is
        # proposing. Allowing it would overwrite the label a human trusts.
        with pytest.raises(ValueError, match="not proposable"):
            normalise_candidate({**CANDIDATE, "state": "WRONG"})

    def test_state_is_case_insensitive(self):
        assert normalise_candidate({**CANDIDATE, "state": "verified"})["state"] == (
            "VERIFIED"
        )

    def test_optional_fields_only_appear_when_supplied(self):
        candidate = normalise_candidate(CANDIDATE)
        assert "end" not in candidate
        assert "league_color_id" not in candidate

    def test_optional_fields_are_carried_when_supplied(self):
        candidate = normalise_candidate(
            {
                **CANDIDATE,
                "end": "2026-09-16T21:30:00+02:00",
                "league_color_id": "11",
                "url": "https://x.test/live",
                "backend": "exa-mcp",
            }
        )
        assert candidate["end"] == "2026-09-16T21:30:00+02:00"
        assert candidate["league_color_id"] == "11"
        assert candidate["backend"] == "exa-mcp"

    def test_evidence_flags_are_carried_through(self):
        """Phase 3 audits against these; this whitelist used to drop them.

        With them dropped the audit could never get past "no evidence recorded",
        so the whole Phase 3 loop was dead regardless of what the agent observed.
        """
        candidate = normalise_candidate(
            {
                **CANDIDATE,
                "evidence": {"live_confirmed": True, "free_confirmed": True},
            }
        )
        assert candidate["evidence"] == {
            "live_confirmed": True,
            "free_confirmed": True,
        }

    def test_a_candidate_with_no_evidence_gains_no_evidence_key(self):
        """An absent key is INCONCLUSIVE; a defaulted one would be a verdict."""
        assert "evidence" not in normalise_candidate(CANDIDATE)

    def test_an_unclear_evidence_value_is_dropped_not_defaulted(self):
        candidate = normalise_candidate(
            {**CANDIDATE, "evidence": {"live_confirmed": "unknown"}}
        )
        assert "evidence" not in candidate

    def test_a_flat_evidence_flag_is_read_too(self):
        candidate = normalise_candidate({**CANDIDATE, "paid": "yes"})
        assert candidate["evidence"] == {"paid": True}


class TestExtract:
    def _wrap(self, body: str) -> dict:
        return _transcript(f"Here you go:\n\n```json\n{body}\n```\n")

    def test_extracts_from_a_fenced_block(self):
        accepted, rejected = extract(self._wrap(json.dumps([CANDIDATE])))
        assert len(accepted) == 1
        assert rejected == []

    def test_dedupes_by_game_key(self):
        accepted, _ = extract(self._wrap(json.dumps([CANDIDATE, CANDIDATE])))
        assert len(accepted) == 1

    def test_rejections_are_reported_with_their_position(self):
        accepted, rejected = extract(
            self._wrap(json.dumps([CANDIDATE, {"league": "BBL"}]))
        )
        assert len(accepted) == 1
        assert len(rejected) == 1
        assert "block 0 item 1" in rejected[0]

    def test_a_transcript_with_no_json_yields_nothing(self):
        accepted, rejected = extract(_transcript("I found no games today."))
        assert accepted == []
        assert rejected == []

    def test_bare_json_transcript(self):
        accepted, _ = extract(json.dumps({"candidates": [CANDIDATE]}))
        assert len(accepted) == 1


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestCli:
    def _write(self, tmp_path, body: str) -> Path:
        path = tmp_path / "transcript.json"
        path.write_text(
            json.dumps(_transcript(f"```json\n{body}\n```")), encoding="utf-8"
        )
        return path

    def test_writes_the_candidates_file(self, tmp_path):
        transcript = self._write(tmp_path, json.dumps([CANDIDATE]))
        out = tmp_path / "candidates.json"
        result = _run(["--transcript", str(transcript), "--out", str(out)])
        assert result.returncode == 0, result.stderr
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert len(payload["candidates"]) == 1

    def test_json_mode_is_parseable(self, tmp_path):
        transcript = self._write(tmp_path, json.dumps([CANDIDATE]))
        result = _run(["--transcript", str(transcript), "--json"])
        assert result.returncode == 0
        assert json.loads(result.stdout.split("\n", 1)[1])["candidates"]

    def test_no_candidates_exits_one(self, tmp_path):
        transcript = tmp_path / "transcript.json"
        transcript.write_text(
            json.dumps(_transcript("nothing to report")), encoding="utf-8"
        )
        result = _run(["--transcript", str(transcript)])
        assert result.returncode == 1
        assert "no usable candidates" in result.stderr

    def test_rejections_warn_on_stderr_but_still_succeed(self, tmp_path):
        transcript = self._write(
            tmp_path, json.dumps([CANDIDATE, {"league": "BBL"}])
        )
        result = _run(["--transcript", str(transcript)])
        assert result.returncode == 0
        assert "rejected" in result.stderr

    def test_plain_text_transcript_is_accepted(self, tmp_path):
        transcript = tmp_path / "transcript.txt"
        transcript.write_text(
            f"Findings:\n```json\n{json.dumps([CANDIDATE])}\n```\n", encoding="utf-8"
        )
        assert _run(["--transcript", str(transcript)]).returncode == 0

    def test_missing_transcript_is_a_usage_error(self, tmp_path):
        result = _run(["--transcript", str(tmp_path / "nope.json")])
        assert result.returncode == 2

    def test_transcript_is_required(self):
        assert _run([]).returncode == 2
