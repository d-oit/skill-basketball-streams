#!/usr/bin/env python3
"""runtime_agent.py — write the permission-restricted runtime agent.

Why this exists. `runtime-daily.yml` created its agent with

    opencode agent create --path .opencode/agent --mode primary \\
      --permissions read,glob,grep,webfetch,websearch,skill

and that step failed the whole `runtime` job on 2026-10-04 with

    LLM failed to generate agent: This request requires at least $1.00 in
    balance for image or video output

— a provider's balance, not this repository's. `agent create` is an **LLM
call**: it asks a model to invent a system prompt and an identifier. The agent
it produces is static — a fixed tool allow-list and a description — so a live
model is not needed to produce it and is the wrong dependency for a security
control that must hold on every scheduled run.

There is a second, worse defect behind the first. `opencode run` without
`--agent` selects the built-in `build` agent, which has **every** tool: the
2026-10-02 run shows the agent invoking `bash`, and the job then spent 21
minutes exploring the runner until `timeout 1200` killed it. The generated
agent was never selected, so "the runtime is denied `edit` and `bash`"
(`PRODUCT.md`) was a promise the workflow did not keep — the exact
"rule that reads a field nothing writes" shape `AGENTS.md` warns about.

So the fix is two halves, and they are one change:

1. This module writes `.opencode/agent/runtime.md` deterministically. No model
   call, no identifier to guess, and the file name *is* the agent name, so the
   workflow can select it by name.
2. `Run the skill` passes `--agent runtime`, which is what actually applies the
   restriction. The test suite pins both halves, because either one alone
   leaves the control decorative.

The permissions mirror `agent create --permissions
read,glob,grep,webfetch,websearch,skill`: everything in the CLI's vocabulary
that is not on that allow-list is denied. `read`/`glob`/`grep`/`webfetch`/
`websearch`/`skill` are permissive by default and left alone; `edit` (which
covers `write` and `apply_patch`), `bash`, `task` (a subagent is not bound by
this agent's permissions), `todowrite` and `lsp` are denied. `external_directory`
and `doom_loop` are deliberately **not** touched: their defaults are the
stricter `ask`, and a blanket deny would also block the tool-output directory
opencode reads its own large results back from.

Streams:
    stdout  the `GITHUB_OUTPUT` payload and nothing else (`key=value` lines).
            This is redirected with `>> "$GITHUB_OUTPUT"`; a stray human line
            would fail GitHub's parser.
    stderr  the human-readable confirmation.

Usage:
    python3 scripts/runtime_agent.py --path .opencode/agent >> "$GITHUB_OUTPUT"

Exit codes:
    0  the agent file was written
    2  USAGE — the path cannot be created
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The file name is the agent name (`runtime.md` -> `runtime`), so `--agent`
# and the workflow must agree with this constant. Pinned by the test suite.
AGENT_NAME = "runtime"

DESCRIPTION = "Basketball streams runtime: read-only research, calendar writer"

# Denied explicitly. The allow-list (`read`, `glob`, `grep`, `webfetch`,
# `websearch`, `skill`) is the CLI's permissive default, so only the denials
# need writing down. `edit` covers `write` and `apply_patch`.
DENIED_PERMISSIONS = ("edit", "bash", "task", "todowrite", "lsp")

PROMPT = (
    "You are the basketball streams runtime agent. Execute the root SKILL.md "
    "contract exactly: search the approved official sources for today's "
    "free-to-watch basketball streams, run every candidate through the 7-check "
    "pipeline, and report the result.\n"
    "\n"
    "You have read-only tools (read, glob, grep) plus web search and fetch. You "
    "cannot edit files and cannot run shell commands, so every result must be "
    "emitted in your final message — the fenced JSON block Step 7 requires is "
    "the only channel the planner reads.\n"
)


def render_agent() -> str:
    """The exact bytes of `.opencode/agent/runtime.md`, deterministic."""
    lines = [
        "---",
        f"description: {DESCRIPTION}",
        "mode: primary",
        "permission:",
    ]
    lines += [f"  {name}: deny" for name in DENIED_PERMISSIONS]
    lines += ["---", PROMPT]
    return "\n".join(lines)


def write_agent(directory: Path) -> Path:
    """Write the agent into `directory` and return the file written."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{AGENT_NAME}.md"
    path.write_text(render_agent(), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write the permission-restricted opencode runtime agent.",
    )
    parser.add_argument(
        "--path",
        default=".opencode/agent",
        help="directory the agent markdown is written into",
    )
    args = parser.parse_args()

    try:
        path = write_agent(Path(args.path))
    except OSError as exc:
        print(f"FAIL: runtime_agent: cannot write {args.path}: {exc}", file=sys.stderr)
        sys.exit(2)

    # stdout is the `$GITHUB_OUTPUT` payload: the name `--agent` must use.
    print(f"agent={AGENT_NAME}")
    print(
        f"OK: runtime_agent: wrote {path} — agent '{AGENT_NAME}', "
        f"denied: {', '.join(DENIED_PERMISSIONS)}",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
