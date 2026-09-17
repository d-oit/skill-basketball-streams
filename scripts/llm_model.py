#!/usr/bin/env python3
"""llm_model.py — decide, once, which model the `opencode run` steps use.

Why this exists. Two workflow steps pinned the model with

    LLM_MODEL: ${{ vars.LLM_MODEL || 'opencode/big-pickle' }}

`opencode/big-pickle` is an **OpenCode Zen** model id: `opencode/` is the provider
prefix and `big-pickle` is rung 2 of the LLM ladder. But the ladder is
credential-driven, and `capture_transcripts.RUNG_CREDENTIALS` already says so — a
repository holding only `GEMINI_API_KEY`, or only `OPENROUTER_API_KEY`, is a
*correctly configured* repository, and `capture_transcripts.py --check-rungs`
passes for it. The step nevertheless asked the CLI for a Zen model the repo had no
credential for, so the run produced no transcript: the same visible outcome as a
quiet day, which is the failure mode the preflight exists to prevent.

Two consequences follow, and both are why this is not a `${{ }}` expression:

- Which rung serves is a fact about the *credentials that are set*, not about the
  workflow file, so it cannot be written as a workflow literal.
- A decision written as an Actions expression cannot be exercised by the test
  suite, so it has no way to be caught before it ships (AGENTS.md; the `DRY_RUN`
  defect `scripts/write_mode.py` was written to undo). The steps therefore
  **read** the resolved value — `steps.llm.outputs.model` — rather than
  re-deriving it.

Precedence:

1. `LLM_MODEL` — the documented pin (`docs/runtime.md`), used verbatim.
2. `GEMINI_API_KEY` / `GOOGLE_API_KEY` — rung 1, as `google/<id>`.
3. `OPENCODE_ZEN_API_KEY` — rung 2, as `opencode/big-pickle`.
4. `OPENROUTER_API_KEY` — rung 3, as `openrouter/openrouter/free`.
5. Nothing at all — rung 3's model, with the reason saying so.

That is `capture_transcripts.LADDER`'s order, applied to model *selection* rather
than to failover. The ladder is not restated here: `RUNG_CREDENTIALS`,
`GEMINI_DEFAULT_MODEL` and `OPENROUTER_DEFAULT_MODEL` are imported, so the model
this step runs and the model the capture asks for cannot drift apart.

Two deliberate non-features:

- **This is a selector, not a gate.** `--check-rungs` is the gate on "at least one
  rung is configured" and it runs in the same job, upstream. Re-deriving that here
  would give the repo two answers to one question, and would also make the step
  unusable in a `replay_ci.py` replay, where the parent environment's
  `*_API_KEY`/`*_TOKEN` variables are stripped by design: a step that refuses to
  run for a reason that has nothing to do with the workflow is exactly the trap
  `write_mode.py` documents at length. A fallback is a resolution, not a failure.
- **A runner authenticated only through the CLI's own auth store is not
  detected.** `RUNG_CREDENTIALS["opencode"]` also accepts `ANTHROPIC_API_KEY` and
  friends so the *preflight* does not red such a runner, but a Zen model id is
  meaningless without a Zen key and this module reads the environment only — it
  will not probe `~/.local/share/opencode/auth.json`. That case is what the
  `LLM_MODEL` pin is for, and it is precedence 1.

Streams:
    stdout  the `GITHUB_OUTPUT` payload and nothing else (`key=value` lines).
            This is redirected with `>> "$GITHUB_OUTPUT"`, whose parser rejects a
            line without `=`, so a stray human line here would not merely be
            untidy — it would fail the step.
    stderr  the human-readable choice and its reason

Usage:
    python3 scripts/llm_model.py >> "$GITHUB_OUTPUT"

Exit codes:
    0  a model was resolved
    2  USAGE — bad arguments
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_transcripts import (  # noqa: E402
    GEMINI_DEFAULT_MODEL,
    OPENROUTER_DEFAULT_MODEL,
    RUNG_CREDENTIALS,
)

PIN = "LLM_MODEL"

# `opencode` composes a model id as `provider_id/model_id` (OpenRouter's own
# OpenCode integration doc), so a model whose *id* already contains a slash gets
# the provider prefix in front of it: provider `openrouter`, id
# `openrouter/free`. The double prefix is correct, not a typo — it is what makes
# the free router, rather than one arbitrarily chosen free model, the target.
RUNG_PROVIDERS = {
    "gemini": "google",
    "opencode": "opencode",
    "openrouter": "openrouter",
}

# Rung 2's $0 id, from `references/search-backends.md` (the Zen catalogue).
ZEN_DEFAULT_MODEL = "big-pickle"


class Resolution(NamedTuple):
    """What one run context resolves to. `reason` is written for a human log."""

    model: str
    rung: str
    reason: str


def _first_set(names: tuple[str, ...], environ: Mapping[str, str]) -> str:
    """The first of `names` holding a non-blank value, else `""`.

    Presence, not validity — a set key can still be rejected, which is why
    `--list-models` probes it. Red-ing here on a bad key would duplicate that
    check and break the replay for a network reason.
    """
    for name in names:
        if (environ.get(name) or "").strip():
            return name
    return ""


def resolve_model(pin: str, environ: Mapping[str, str]) -> Resolution:
    """`(model, rung, reason)` for one run context. Pure, so the matrix is tested.

    `environ` is passed in rather than read from `os.environ` so the matrix can be
    exercised without mutating the process environment — the same shape
    `write_mode.resolve_dry_run` takes, for the same reason.
    """
    if (pin or "").strip():
        # First, and unconditional: the pin is the documented override, and the
        # escape hatch for the undetectable cases in the module docstring.
        return Resolution(pin.strip(), "pinned", f"{PIN} is set, and the pin wins")

    gemini = _first_set(RUNG_CREDENTIALS["gemini"], environ)
    if gemini:
        return Resolution(
            f"{RUNG_PROVIDERS['gemini']}/{GEMINI_DEFAULT_MODEL}",
            "gemini",
            f"{gemini} is set, so rung 1 serves the run",
        )

    zen = _first_set(("OPENCODE_ZEN_API_KEY",), environ)
    if zen:
        return Resolution(
            f"{RUNG_PROVIDERS['opencode']}/{ZEN_DEFAULT_MODEL}",
            "opencode",
            f"{zen} is set, so rung 2 serves the run",
        )

    openrouter = _first_set(RUNG_CREDENTIALS["openrouter"], environ)
    if openrouter:
        return Resolution(
            f"{RUNG_PROVIDERS['openrouter']}/{OPENROUTER_DEFAULT_MODEL}",
            "openrouter",
            f"{openrouter} is set and neither earlier rung has a credential, "
            "so rung 3 serves the run",
        )

    return Resolution(
        f"{RUNG_PROVIDERS['openrouter']}/{OPENROUTER_DEFAULT_MODEL}",
        "openrouter",
        "no ladder credential was found, so the last rung's model is used; "
        "`capture_transcripts.py --check-rungs` is the gate on that, not this step",
    )


# The same character set `write_mode.py` drops, for the same two reasons: a
# newline would truncate the `>> "$GITHUB_OUTPUT"` file and fail the parser, and
# `"`/`$`/backtick/`\`/`|` re-open shell quoting or split a markdown table cell in
# a job summary. Only the Gemini id in a reason could carry them, but the value
# reaches the file either way.
UNSAFE_IN_REASON = str.maketrans({c: " " for c in "\n\r\t`\"'$\\|<>"})


def _output_safe(text: str) -> str:
    """A value that survives `>> $GITHUB_OUTPUT` and a markdown table cell."""
    return " ".join(text.translate(UNSAFE_IN_REASON).split())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve the model the opencode run steps should use.",
    )
    # `None` (not `""`) so a pin that was *omitted* is distinguishable from one
    # that was passed empty — GitHub renders an unset variable as the empty
    # string, and those two mean different things.
    parser.add_argument(
        "--pin",
        default=None,
        help=f"override the {PIN} environment variable (used by a local preview)",
    )
    args = parser.parse_args()

    pin = args.pin if args.pin is not None else os.environ.get(PIN, "")
    resolution = resolve_model(pin, os.environ)

    # stdout is the payload: exactly the lines `>> $GITHUB_OUTPUT` will consume.
    print(f"model={_output_safe(resolution.model)}")
    print(f"rung={_output_safe(resolution.rung)}")
    print(f"reason={_output_safe(resolution.reason)}")

    print(
        f"OK: llm_model: rung {resolution.rung} -> {resolution.model} "
        f"— {resolution.reason}",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
