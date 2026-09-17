#!/usr/bin/env python3
"""write_mode.py — decide, once, whether a run is allowed to write to the calendar.

Why this exists. `runtime-daily.yml` decided dry-run with

    DRY_RUN="${{ inputs.dry_run || 'true' }}"

in both the agent step and the apply step. On a `schedule` event `inputs` is an
empty object, so that expression is *always* `'true'`, and `--live` is never
reached. The consequence was invisible by construction: with
`ENABLE_CALENDAR_WRITES=true` the daily job ran green every morning, planned
every event, wrote nothing, and said so only in a `::notice::` line. The
documented switch (`docs/runtime.md`: "runs in dry-run unless the repo variable
`ENABLE_CALENDAR_WRITES` is `true`") was dead on the one path that matters.

The deeper problem is *where* the logic lived: a GitHub Actions expression cannot
be exercised by the test suite, so the defect had no way to be caught before a
subscriber noticed no events. This module moves the decision into ordinary Python
with the matrix written down and tested.

Precedence, fail-closed:

1. `ENABLE_CALENDAR_WRITES` must be exactly `true`. Anything else — unset,
   `false`, `1`, `yes`, a typo — means dry-run. The variable is the master
   switch and it is never inferred.
2. On `workflow_dispatch`, the `dry_run` input can only ever *add* dry-run. It
   can never grant a write the variable did not already allow, so a manual
   dispatch cannot widen the blast radius of a repository that has writes off.
3. Every other event (`schedule`, `repository_dispatch`) follows the variable.
4. An event name that is empty or unrecognised fails closed to dry-run.

Streams:
    stdout  the `GITHUB_OUTPUT` payload and nothing else (`key=value` lines).
            This is redirected with `>> "$GITHUB_OUTPUT"`, and GitHub's parser
            rejects a line without `=`, so a stray human line here would not
            merely be untidy — it would fail the step.
    stderr  the human-readable decision and its reason

Omitted versus empty. Exit 2 is reserved for the caller passing **no** arguments
at all — a step that was never wired up. Being *passed* an empty value is not a
usage error: that is what GitHub does when an expression cannot be resolved, and
what any local replay of the step produces, because `replay_ci.py` runs the
`run:` block without the step's `env:` block. The two cases are told apart by a
sentinel default, and the empty case fails closed to dry-run with a reason rather
than refusing to run — otherwise a real workflow would be unreplayable for a
reason that has nothing to do with it.

Usage:
    python3 scripts/write_mode.py --event "${{ github.event_name }}" \\
        --input "${{ inputs.dry_run }}" \\
        --enabled "${{ vars.ENABLE_CALENDAR_WRITES }}" >> "$GITHUB_OUTPUT"

Exit codes:
    0  a decision was produced (dry-run is a decision, not a failure)
    2  USAGE — bad arguments
"""
from __future__ import annotations

import argparse
import sys

# The only values that mean "writes are enabled" for the repository variable.
TRUTHY = frozenset({"true"})
# The `workflow_dispatch` input is a boolean, so GitHub renders it as `true` or
# `false`. Anything else is a typo and is treated as "not a decision".
DISPATCH_TRUE = frozenset({"true"})
DISPATCH_FALSE = frozenset({"false"})

DISPATCH_EVENT = "workflow_dispatch"


def resolve_dry_run(event: str, dispatch_input: str, enabled: str) -> tuple[bool, str]:
    """`(dry_run, reason)` for one run context. Pure, so the matrix is testable.

    The reason is written for the human reading a job summary: it names the input
    that decided the outcome, so "why did nothing get written?" has an answer
    that does not require re-deriving a four-branch precedence in your head.
    """
    event = (event or "").strip()
    given = (dispatch_input or "").strip().lower()
    switch = (enabled or "").strip().lower()

    if switch not in TRUTHY:
        return True, (
            "dry-run: ENABLE_CALENDAR_WRITES is "
            + (enabled.strip() if enabled.strip() else "not set")
            + ", so writes are off regardless of the event"
        )

    if not event:
        return True, "dry-run: no event name was passed, so the mode cannot be decided"

    if event != DISPATCH_EVENT:
        # schedule / repository_dispatch: there is no input to consult, so the
        # variable is the whole decision. This is the branch that was broken.
        return False, (
            f"live: ENABLE_CALENDAR_WRITES=true on a {event} run, and no "
            "dry_run input exists to override it"
        )

    if not given:
        # Defensive: a dispatch always carries the input (it has a default), so
        # an empty value means the caller wired it up wrong. Follow the switch
        # rather than guessing, and say so.
        return False, (
            "live: ENABLE_CALENDAR_WRITES=true and no dry_run input was passed "
            "on the dispatch, so the variable decides"
        )
    if given in DISPATCH_TRUE:
        return True, "dry-run: the workflow_dispatch dry_run input is true"
    if given in DISPATCH_FALSE:
        return False, (
            "live: ENABLE_CALENDAR_WRITES=true and the dispatch asked for "
            "dry_run=false"
        )
    return True, (
        f"dry-run: unrecognised dry_run input {dispatch_input.strip()} "
        "(expected the word true or false), failing closed"
    )


# Characters that would corrupt one of the two places the reason is used: the
# `>> "$GITHUB_OUTPUT"` line (newline would truncate the file and fail the
# parser) and the job-summary `echo "..."` / markdown table it lands in (`"`,
# `$`, backtick and `\\` re-open shell quoting; `|` splits a table cell).
# The reason echoes a repository variable and a dispatch input, so it is not
# fully under our control — dropping the characters rather than trusting them.
UNSAFE_IN_REASON = str.maketrans({c: " " for c in "\n\r\t`\"'$\\|<>"})


def _output_safe(text: str) -> str:
    """A reason that survives `>> $GITHUB_OUTPUT` and a markdown table cell."""
    return " ".join(text.translate(UNSAFE_IN_REASON).split())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve whether this run may write to the calendar.",
    )
    # `None` (not `""`) so an argument that was *omitted* is distinguishable
    # from one that was passed an empty value.
    parser.add_argument(
        "--event",
        default=None,
        help="github.event_name (e.g. schedule, workflow_dispatch)",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="the workflow_dispatch dry_run input; empty on schedule events",
    )
    parser.add_argument(
        "--enabled",
        default=None,
        help="the ENABLE_CALENDAR_WRITES repository variable",
    )
    args = parser.parse_args()

    if args.event is None and args.input is None and args.enabled is None:
        print(
            "FAIL: write_mode: --event, --input and --enabled were all omitted; "
            "there is nothing to decide a write mode from",
            file=sys.stderr,
        )
        sys.exit(2)

    # Keyed off the raw arguments, not the resulting reason: with the switch off
    # the reason is about the switch, and the empty case would then be invisible
    # exactly when it is most likely to be confusing.
    if not (args.event or args.input or args.enabled):
        print(
            "note: write_mode: every value was empty (an unresolved expression, "
            "or a replay without the step's env block); failing closed to dry-run",
            file=sys.stderr,
        )

    dry_run, reason = resolve_dry_run(
        args.event or "", args.input or "", args.enabled or ""
    )

    # stdout is the payload: exactly the lines `>> $GITHUB_OUTPUT` will consume.
    print(f"dry_run={'true' if dry_run else 'false'}")
    print(f"reason={_output_safe(reason)}")

    banner = "OK: write_mode"
    print(
        f"{banner}: {'DRY-RUN' if dry_run else 'LIVE WRITES PERMITTED'} — {reason}",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
