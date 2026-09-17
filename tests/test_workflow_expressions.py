#!/usr/bin/env python3
"""test_workflow_expressions.py — no workflow carries an empty expression.

GitHub parses `run:` blocks (comments included) for workflow expressions, and
an empty ``${{ }}`` is a parse error: "An expression was expected" (HTTP 422
on dispatch). Because the file fails to parse, EVERY trigger is dead — the
daily cron fails silently forever, and the only observable symptom is a
workflow dispatch that refuses to start.

Found in v1.2.0: five prose mentions of the literal token inside comments —
comments warning about that very failure mode — in `runtime-daily.yml`. The
`workflow-refs` sensor cannot see it (it checks paths), `replay_ci.py` cannot
see it (PyYAML parses the file fine; the expression is GitHub-side), and the
suite passed because nothing asserted what dispatch actually requires. So the
check lives here, where pytest is the gate.

Deliberately narrower than "no expressions in `run:` blocks": non-empty
expressions in run blocks are a real (bad-substitution) hazard, but the repo
also uses `if:` expressions legitimately and this suite pins step contracts
by reading them. An EMPTY expression is never legitimate — it is always a
prose artifact or a typo — so the invariant is exact, and a check that cries
wolf gets deleted.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# An expression with nothing between the braces. `\s*` on both sides because
# `${{  }}` is rejected by GitHub exactly like `${{ }}`.
EMPTY_EXPRESSION = re.compile(r"\$\{\{\s*\}\}")


def workflow_files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def test_workflows_directory_is_not_empty() -> None:
    """A move or rename that strands the glob would silently disarm every
    check below — the vacuous pass this file exists to prevent."""
    files = workflow_files()
    assert files, f"no workflow files found under {WORKFLOWS}"
    assert "runtime-daily.yml" in [p.name for p in files]


def test_no_workflow_carries_an_empty_expression() -> None:
    """Every workflow must be parseable by GitHub Actions itself.

    A file that fails GitHub's parser is dead for ALL triggers: `on: schedule`
    included. Nothing in this repository's offline gate can otherwise notice —
    the defect only surfaces as HTTP 422 from the dispatch API.
    """
    problems: list[str] = []
    for path in workflow_files():
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if EMPTY_EXPRESSION.search(line):
                problems.append(f"{path.name}:{number}: empty expression: {line.strip()}")
    assert not problems, (
        "An empty ${{ }} is a GitHub-side parse error that kills every trigger "
        "of the workflow, including the cron — say 'a workflow expression' in "
        "prose instead of writing the token:\n" + "\n".join(problems)
    )
