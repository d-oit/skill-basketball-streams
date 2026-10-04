"""The transcript → planner channel is a contract, and it had no test.

The first successful skill run (2026-09-29) did the whole job correctly: it ran
Steps 1–7, validated two real BBL games against the Dyn free-games list, and
produced the Step 7 table. The run still failed, at the next step:

```
FAIL: extract_candidates: no usable candidates (0 rejected)
       — the agent must emit a fenced ```json block of candidate objects
```

Nothing had ever asked for that block. `SKILL.md` Step 7 specified the *table*,
the workflow prompt said "Emit the Step 7 table", and
`extract_candidates.FENCED_BLOCK` required a fenced JSON object that no
instruction mentioned. So the agent produced exactly what it was asked for and
the step reported "no usable candidates" — the same shape as a day with no free
streams, which is the failure mode this repository records as the worst it has.

The two ends are pinned together here on purpose:

* `SKILL.md` is the contract every run is given (`--file SKILL.md`);
* the workflow prompt is what the runtime actually asks;
* `extract_candidates.py` is what reads the answer.

A change to any one of them that the other two do not know about is the same
defect, so the tests assert the *agreement*, not each document's wording.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "SKILL.md"
RUNTIME_DAILY = REPO_ROOT / ".github" / "workflows" / "runtime-daily.yml"
EXTRACT = REPO_ROOT / "scripts" / "extract_candidates.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import runtime_agent  # noqa: E402

STEP_START = re.compile(r"^      - (?:name|uses|id):", re.M)


def _runtime_steps() -> list[str]:
    text = RUNTIME_DAILY.read_text(encoding="utf-8")
    job = text[text.index("\n  runtime:") : text.index("\n  audit:")]
    starts = [m.start() for m in STEP_START.finditer(job)]
    return [job[s:e] for s, e in zip(starts, starts[1:] + [len(job)])]


def _agent_step() -> str:
    matches = [s for s in _runtime_steps() if "- name: Run the skill\n" in s]
    assert len(matches) == 1, f"expected one `Run the skill` step, got {len(matches)}"
    return matches[0]


def _agent_prompt() -> str:
    """The prompt string, unescaped — what the model actually receives.

    It is a double-quoted YAML scalar spanning many lines, so the shell gets one
    argument. Asserting on the raw file would be asserting on YAML quoting, and a
    contract nobody receives is exactly the defect this file exists to prevent.
    """
    code = "\n".join(
        line for line in _agent_step().splitlines() if not line.lstrip().startswith("#")
    )
    start = code.index('"Execute Steps 1-7')
    tail = code[start + 1 :]
    # The scalar ends at the first *unescaped* `"`. The example inside it is
    # full of `\"`, so a plain `.*?"` stops one word into the JSON and reads as a
    # prompt that never mentions the block — a false negative on a correct
    # workflow, which is the kind of test that gets deleted.
    end = len(tail)
    for index, char in enumerate(tail):
        if char == '"' and (index == 0 or tail[index - 1] != "\\"):
            end = index
            break
    return (
        tail[:end]
        .replace('\\`', "`")
        .replace('\\"', '"')
        .replace("\\n", "\n")
    )


class TestTheContractIsStatedWhereItIsConsumed:
    def test_skill_md_step_7_requires_the_block(self):
        """The contract is `SKILL.md`, and every run is given `SKILL.md`.

        Not the workflow prompt: a prompt is a caller, and a caller that has to
        restate the callee's contract is how the two drift.
        """
        text = SKILL.read_text(encoding="utf-8")
        step7 = text[text.index("### Step 7") : text.index("## Important Constraints")]
        assert "```json" in step7, (
            "Step 7 must show the fenced json block the planner reads"
        )
        assert '"candidates"' in step7, "the block's shape must be shown, not described"
        assert "WRONG" in step7, (
            "Step 7 must say WRONG is not proposable — it is refused silently "
            "otherwise, and the agent has no way to learn that from a rejection"
        )

    def test_the_runtime_prompt_asks_for_it(self):
        """What the step asks for is what the step then parses.

        This is the exact omission that failed the first real run: the prompt
        said "Emit the Step 7 table" and nothing else.
        """
        prompt = _agent_prompt()
        assert "```json" in prompt, "the prompt must ask for the fenced block"
        assert '"candidates"' in prompt
        assert "candidates\": []" in prompt or "candidates\\\": []" in prompt, (
            "the prompt must say what to emit when nothing was found — silence "
            "is what a real 'no streams today' looks like"
        )

    def test_the_extractor_accepts_exactly_what_the_contract_shows(self):
        """The block in `SKILL.md` must survive the parser that reads it.

        A worked example in the contract that the parser would refuse is a
        contract that teaches the agent to fail.
        """
        text = SKILL.read_text(encoding="utf-8")
        example = re.search(r"```json\n(\{.*?\})\n```", text, re.DOTALL)
        assert example, "Step 7 must contain a worked example"
        payload = example.group(1)
        from scripts.extract_candidates import extract

        accepted, rejected = extract(payload)
        assert rejected == [], f"the worked example is refused: {rejected}"
        assert len(accepted) == 1, accepted
        candidate = accepted[0]
        for field in ("league", "teams", "start", "state", "url"):
            assert candidate.get(field), f"the example must carry {field}"
        assert len(candidate["teams"]) == 2
        assert candidate["state"] in ("VERIFIED", "UNVERIFIED")

    def test_the_prompt_example_is_also_accepted(self):
        """Same, for the prompt's inline example."""
        from scripts.extract_candidates import extract

        prompt = _agent_prompt()
        # The example sits inside a quoted YAML scalar, so every line is
        # indented; the parser's own fence pattern is anchored to a newline.
        example = re.search(r"```json\s*\n\s*(\{.*?\})\s*\n\s*```", prompt, re.DOTALL)
        assert example, "the prompt must show the block, not just name it"
        accepted, rejected = extract(example.group(1))
        assert rejected == [], f"the prompt's example is refused: {rejected}"
        assert len(accepted) == 1


class TestTheChannelIsTheOnlyOneThereIs:
    def test_the_agent_cannot_write_the_file_itself(self):
        """Why the transcript has to carry the candidates at all.

        Without this, the contract above looks like belt-and-braces. With it, the
        requirement is a consequence of the permission model.

        The permissions moved from an `opencode agent create --permissions`
        flag to `scripts/runtime_agent.py` (the agent is written
        deterministically, then selected with `--agent`). The deny list is still
        the reason the transcript is the only channel.
        """
        step = _agent_step()
        assert "--agent" in step, (
            "the run must select the restricted agent, or the built-in `build` "
            "agent runs with every tool and the transcript is not the only channel"
        )
        body = runtime_agent.render_agent()
        for permission in ("edit", "bash"):
            assert f"{permission}: deny" in body, (
                f"{permission} must stay denied; the transcript is then the only "
                "channel"
            )

    def test_the_extractor_is_the_only_reader_of_that_block(self):
        """One reader, one shape — so the contract has exactly one consumer."""
        source = EXTRACT.read_text(encoding="utf-8")
        assert "```(?:json)?" in source, (
            "the parser's fence pattern is the other half of this contract; if it "
            "changes, SKILL.md's example must change with it"
        )

    def test_ci_exercises_both_transcript_shapes(self):
        """`validate.yml` is the only gate that runs the extractor end to end.

        It ran the *document* shape alone, which is how the stream shape reached
        production untested. Both are named now, so a future step cannot quietly
        drop one — and the runtime uses the stream, so the gate must match the
        consumer rather than the easier shape.
        """
        ci = (REPO_ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )
        for fixture in (
            "agent_transcript_sample.json",
            "agent_transcript_jsonl.txt",
        ):
            assert fixture in ci, f"{fixture} is not exercised by validate.yml"
        assert "transcript.json" in RUNTIME_DAILY.read_text(encoding="utf-8")
