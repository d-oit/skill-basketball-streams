#!/usr/bin/env python3
"""gh_issue.py — the one place that opens or comments on a dedupe-keyed issue.

Two scheduled workflows now report a finding as a GitHub issue: the weekly
`corpus-refresh` run (a recorded page's verdict flipped) and the daily
`runtime-daily` run (a render rung parked for N consecutive days). Both need the
same three behaviours, and they are subtle enough that two copies would drift:

* **One issue per finding, not one per run.** The title is the dedupe key, so it
  must carry no date and no page/rung name — either would make every report look
  like a new finding. A repeat **comments** on the open issue instead.
* **A finding that cannot be delivered is loud.** `gh` missing or failing must
  raise, so the caller can exit non-zero, because the alternative is a green
  workflow that reported nothing at all.
* **`gh` output is parsed defensively.** An unparseable issue list returns
  `None` (comment on nothing) rather than guessing, so a `gh` format change can
  not open a duplicate issue every week.

Read-only `gh issue list` stays allowed everywhere; only `create`/`comment` touch
GitHub, and both are on `replay_ci.py`'s denylist when reached through a
`--file` flag.

Usage (library only — no CLI):

    from gh_issue import file_or_comment
    file_or_comment(title, body, marker="[x] something")
"""
from __future__ import annotations

import json
import os
import subprocess


def run_gh(args: list[str]) -> subprocess.CompletedProcess:
    """Run `gh` and return the completed process (raises on a non-zero exit)."""
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    )


def existing_open_issue(payload: str, marker: str) -> int | None:
    """The number of the open issue whose title starts with `marker`, if any.

    `marker` is the stable half of the title and the whole dedupe mechanism, so
    it is a required argument rather than a module global: two callers must not
    be able to answer this question with each other's key.
    """
    try:
        issues = json.loads(payload or "[]")
    except json.JSONDecodeError:
        return None
    for issue in issues:
        if str(issue.get("title", "")).startswith(marker):
            return int(issue["number"])
    return None


def run_url() -> str:
    """A link back to this Actions run, or `(local run)`."""
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else "(local run)"


def file_or_comment(
    title: str, body: str, *, marker: str, runner=run_gh
) -> str:
    """Create the issue, or comment on the open one carrying `marker`.

    `runner` is injectable so the create-vs-comment decision can be tested
    against a stub `gh` without touching GitHub.
    """
    listed = runner(
        ["issue", "list", "--state", "open", "--limit", "100",
         "--json", "number,title"]
    )
    number = existing_open_issue(listed.stdout, marker)
    if number is None:
        runner(["issue", "create", "--title", title, "--body", body])
        return "created"
    runner(["issue", "comment", str(number), "--body", body])
    return f"commented on #{number}"
