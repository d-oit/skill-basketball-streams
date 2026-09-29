#!/usr/bin/env python3
"""llm_model.py — decide, once, which model the `opencode run` steps use.

Why this exists. Two workflow steps pinned the model with

    LLM_MODEL: ${{ vars.LLM_MODEL || 'opencode/big-pickle' }}

`opencode/big-pickle` is an **OpenCode Zen** model id: `opencode/` is the provider
prefix and `big-pickle` is a Zen id of the LLM ladder. But the ladder is
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
2. `opencode/big-pickle` — always, and unconditionally.

The second entry replaced a credential ladder. It was wrong for the consumer:
this module chooses the model for the **agent**, and the agent is invoked as
`opencode run --model <id>`, so the id must be in the *CLI's* catalogue. The
OpenRouter rung was valid for `capture_transcripts.py` — which posts to
OpenRouter's HTTPS API directly and is rung 1 of the capture ladder — but the
CLI has no route for it, and answered every such id with
`provider.auth 401` or `provider.no-route`. Reordering could not have fixed
that, because no ordering of an unusable id is usable.

`big-pickle` is served by the CLI **without any credential**, so the agent runs
on a repository with no secrets at all. The `RUNG_CREDENTIALS` and default-model
imports below are kept because `--check-rungs` and the preflight still gate the
repository's overall configuration; they no longer select this model.

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


# The CLI's free model this run uses. **Changed 2026-09-29** from `big-pickle`.
#
# `big-pickle` is still *free* — it did not become paid — but it is the **oldest**
# model in the free catalogue (released 2025-10-17 against a current leader of
# 2026-09-25), and on a real dispatch it **degenerated mid-generation**: 39,289
# characters of near-random tokens, no Step 7 table, no candidate block, and an
# extraction step that reported the fault as a missing output contract. The
# contract was fine. The model was not.
#
# `longcat-2.5-preview-free` is the newest free id as of 2026-09-29 with a 1M
# context, and it answers the candidate-block contract correctly (measured).
# `scripts/free_models.py --pinned` reports the current free catalogue and
# whether this pin is still free, so ageing is visible without anyone
# remembering to look.
#
# A **preview** model is a deliberate choice, and the trade is worth stating: the
# newest free ids are previews, so they are the ones most likely to change or
# disappear. That is the same reason this stays a *pinned constant on a branch*
# rather than a selection that follows the live catalogue — if this id stops
# answering, the failure is a red step naming a model, not a silent change of
# model under a pinned expectation.
ZEN_DEFAULT_MODEL = "longcat-2.5-preview-free"


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

    **The model must be one the `opencode` CLI can actually serve.** This module
    selects for the *agent*, which is invoked as
    `opencode run --model <id>`, and the CLI resolves that id against **its own**
    provider registry — not against OpenRouter's. Measured on this machine with
    the CLI (v2.0.16, 2026-09-28):

        opencode/big-pickle                 -> answered "PONG"
        openrouter/openrouter/free          -> provider.auth, 401 User not found
        openrouter/free, openrouter/auto    -> provider.no-route, Model unavailable
        opencode/mimo-v2.5-free             -> provider.no-route, Model unavailable

    So an OpenRouter key — valid, and accepted by the live probe — cannot serve
    the agent, because the id it implies is not in the CLI's catalogue. That is
    why the OpenRouter rung was removed from this list rather than reordered: it
    is a real ladder rung for `capture_transcripts.py`, which talks to
    OpenRouter's HTTPS API directly, and it is not one for the CLI.
    """
    if (pin or "").strip():
        # First, and unconditional: the pin is the documented override, and the
        # escape hatch for the undetectable cases in the module docstring.
        return Resolution(pin.strip(), "pinned", f"{PIN} is set, and the pin wins")

    # `big-pickle` is the CLI's own free model and answered with **no credential
    # of any kind** — `OPENROUTER_API_KEY`, `OPENCODE_ZEN_API_KEY`,
    # `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` were all stripped for that test.
    # So it is both the default and the floor: a repository with no configured
    # rung still runs the agent.
    return Resolution(
        f"{RUNG_PROVIDERS['opencode']}/{ZEN_DEFAULT_MODEL}",
        "opencode",
        "the agent runs through the opencode CLI, whose catalogue is its own; "
        "big-pickle is free and served without a credential, and an "
 "OpenRouter id is rejected by the CLI as a provider 401",
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
