#!/usr/bin/env python3
"""replay_ci.py — run a workflow's `run:` steps against a clean copy of the repo.

This encodes the technique that has caught every CI-only defect in this
repository: replay the workflow against a **fresh checkout** instead of trusting
a green local run. Both `do-harness` issues documented in `docs/do-harness.md`
(a segfault and a spurious exit 1) reproduce only on a clean tree, and neither
was visible in a long-lived working copy whose state had already been recorded.

Why a script rather than a documented recipe: the replay has been done by hand
three times, and each time it was re-derived. Two details are easy to get wrong
and both fail *silently or misleadingly*:

* **A clean copy is not the working copy.** `.git`, `.do-harness`, `.tmp` and
  caches must be excluded, or the replay inherits exactly the state that hides
  the bug.
* **GitHub provides environment variables that a local shell does not.**
  `GITHUB_STEP_SUMMARY` and `GITHUB_OUTPUT` are always set in Actions; without
  them a step doing `>> "$GITHUB_STEP_SUMMARY"` dies with
  `: No such file or directory`, which reads as a broken workflow when it is a
  broken *replay*.
* **A step's own `env:` block is NOT injected**, and `${{ }}` is not expanded
  anywhere. Only the `run:` script is taken from the YAML. So a step that reads
  `EVENT: ${{ github.event_name }}` runs with `$EVENT` **empty**, and one that
  interpolates `${{ }}` straight into the script hits a bash "bad substitution".
  Both are replay artifacts rather than workflow bugs, which is why the rule is:
  pass context through `env`, and **fail closed on an empty value** instead of
  treating it as a usage error. Getting this wrong once made a correct step
  report `--event, --input and --enabled were all empty` and read like a defect
  in the workflow. Injecting the env block is deliberately *not* done: a literal
  `${{ secrets.X }}` would look configured to a backend and could start real
  network calls with a bogus key, which is worse than an obviously empty value.

Safety properties, deliberate:

* A workflow must be named explicitly. There is no "run everything" mode, so a
  replay can never wander into a calendar-writing job by accident.
* Steps that could write outside the replay directory — `--live`, `git push`,
  Google auth, the runtime agent, and anything that opens an issue or a pull
  request (`gh issue create`, `corpus_flip_issue.py --file`) — are **never
  executed**, whatever the flags say. They are reported as SKIPPED with the
  reason. A replay cannot delete what it created on GitHub, so the denial is on
  the capability (the flag), not on the step name.
* Every `*_API_KEY`-shaped variable in the parent environment is dropped before
  a step runs, so a replay cannot spend quota or touch a real service. The
  workflow's own secrets are never injected.

Usage:
    python3 scripts/replay_ci.py --workflow .github/workflows/validate.yml
    python3 scripts/replay_ci.py --workflow .github/workflows/verify.yml \\
        --job product-graders
    python3 scripts/replay_ci.py --workflow .github/workflows/validate.yml --json

Exit codes:
    0  PASS — every executed step succeeded
    1  FAIL — at least one executed step failed
    2  USAGE — bad arguments, or a workflow that could not be parsed
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Directories that carry machine-local state. A replay that keeps any of these
# inherits the state that hides the bug it is looking for.
EXCLUDED_NAMES = (
    ".git",
    ".do-harness",
    ".tmp",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
)

# Never executed, regardless of flags. Each of these can write somewhere the
# replay cannot undo.
DENYLIST = (
    (re.compile(r"--live\b"), "passes --live (would write to the real calendar)"),
    (re.compile(r"\bgit\s+push\b"), "pushes to a remote"),
    (re.compile(r"google-github-actions/auth"), "authenticates to Google"),
    # Any opencode subcommand, not just `run`/`serve`: `agent create` mutates
    # agent definitions and every one of them is a live model call.
    (re.compile(r"(?:^|[\s/])opencode\s+\w"), "invokes the opencode runtime"),
    (re.compile(r"\bnpm\s+install\s+-g\b"), "installs a global package"),
    # `gh` *writes* only. A replay of `self-improve.yml` would otherwise open a
    # real PR and a real issue against the public repository, which cannot be
    # undone by deleting a directory. Read-only subcommands (`issue list`) stay
    # allowed, because those are how a step decides what to do.
    (
        re.compile(
            r"\bgh\s+(?:issue|pr|release|repo)\s+"
            r"(?:create|comment|edit|delete|close|reopen|merge|lock|unlock|transfer)\b"
        ),
        "writes to GitHub (an issue, PR or release) via gh",
    ),
    # The issue-filing half of the scheduled reporting loops (corpus verdict
    # flips, parked render rungs). `--file` is the whole capability, exactly
    # like `--live`: without it the scripts only print. The report half stays
    # replayable so the summary renderers are still exercised, and DOTALL is
    # required because the flag usually sits on the next line of a continued
    # command — the same continuation a naive line-anchored regex steps over.
    (
        re.compile(
            r"(?:corpus_flip_issue|rung_health)\.py.*?--file\b", re.DOTALL
        ),
        "files a public GitHub issue (--file)",
    ),
)

# Steps that prepare a runner rather than test the code. Skipped by default
# because a replay already has its dependencies and no network guarantee.
SKIP_BY_NAME = ("checkout", "setup python", "install")

SECRETISH = re.compile(r"_API_KEY$|_TOKEN$|^GITHUB_TOKEN$|_SECRET$")


class Step:
    def __init__(self, name: str, script: str, workflow: str, job: str):
        self.name = name
        self.script = script
        self.workflow = workflow
        self.job = job

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Step {self.workflow}:{self.job}:{self.name}>"


def load_steps(workflow: Path, job_filter: str | None = None) -> list[Step]:
    """Parse a workflow into its shell steps. `uses:` steps are not reproducible."""
    try:
        import yaml  # noqa: PLC0415
    except ImportError:  # pragma: no cover - CI installs only pytest
        raise SystemExit(
            "FAIL: replay_ci: PyYAML is required to replay a workflow "
            "(pip install pyyaml) — this script is a local/CI-debugging tool, "
            "not part of the offline test suite"
        )
    try:
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SystemExit(f"FAIL: replay_ci: {workflow}: unparseable YAML ({exc})")
    jobs = (document or {}).get("jobs") or {}
    if not jobs:
        raise SystemExit(f"FAIL: replay_ci: {workflow}: no jobs found")
    steps: list[Step] = []
    for job_name, spec in jobs.items():
        if job_filter and job_name != job_filter:
            continue
        for entry in (spec or {}).get("steps") or []:
            if "run" not in entry:
                continue  # `uses:` steps are actions, not shell
            name = entry.get("name") or entry.get("id") or "unnamed step"
            steps.append(Step(name, entry["run"], workflow.name, job_name))
    return steps


def denylist_reason(script: str) -> str | None:
    for pattern, reason in DENYLIST:
        if pattern.search(script):
            return reason
    return None


def clean_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The parent environment minus anything credential-shaped, plus CI's own vars.

    Stripping secrets is not a convenience: a replay that inherited
    `EXA_API_KEY` could spend a live quota, and one that inherited a Google token
    could edit the calendar it is meant to be testing the *plan* for.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not SECRETISH.search(key)
    }
    env["CI"] = "true"
    if extra:
        env.update(extra)
    return env


def should_skip(name: str) -> str | None:
    lowered = name.lower()
    for needle in SKIP_BY_NAME:
        if needle in lowered:
            return f"runner preparation ({needle!r}) — the replay's environment is already set up"
    return None


def copy_tree(root: Path, dest: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in EXCLUDED_NAMES or name.endswith(".pyc")}

    shutil.copytree(root, dest, ignore=ignore, symlinks=True, dirs_exist_ok=True)


def replay(
    steps: list[Step], workdir: Path, *, timeout: int, shell_env: dict[str, str]
) -> list[dict]:
    results: list[dict] = []
    for step in steps:
        entry = {"job": step.job, "name": step.name, "status": "", "detail": ""}
        reason = denylist_reason(step.script)
        if reason:
            entry["status"] = "SKIPPED"
            entry["detail"] = f"denied: {reason}"
            results.append(entry)
            continue
        skip = should_skip(step.name)
        if skip:
            entry["status"] = "SKIPPED"
            entry["detail"] = skip
            results.append(entry)
            continue
        try:
            completed = subprocess.run(
                ["bash", "-e", "-c", step.script],
                cwd=workdir,
                env=shell_env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            entry["status"] = "FAIL"
            entry["detail"] = f"timed out after {timeout}s"
            results.append(entry)
            continue
        if completed.returncode == 0:
            entry["status"] = "ok"
        else:
            entry["status"] = "FAIL"
            entry["detail"] = (
                f"exit {completed.returncode}\n"
                f"--- stdout (tail) ---\n{completed.stdout[-1500:]}"
                f"--- stderr (tail) ---\n{completed.stderr[-1500:]}"
            )
        results.append(entry)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay a workflow's shell steps against a clean copy of the repo.",
    )
    parser.add_argument("--workflow", required=True, action="append",
                        help="workflow path (repeatable); no implicit 'all'")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--job", help="replay only this job")
    parser.add_argument("--work-dir", help="replay directory (default: a temp dir)")
    parser.add_argument("--keep", action="store_true",
                        help="keep the replay directory and print its path")
    parser.add_argument("--no-git", action="store_true",
                        help="do not `git init` the replay directory (faster, but "
                             "every `git` step then fails as it would in a bare dir)")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    workflows = [Path(path) for path in args.workflow]
    for workflow in workflows:
        if not workflow.is_file():
            print(f"FAIL: replay_ci: {workflow}: workflow not found", file=sys.stderr)
            sys.exit(2)

    steps: list[Step] = []
    for workflow in workflows:
        found = load_steps(workflow, args.job)
        if not found:
            print(
                f"FAIL: replay_ci: {workflow}: no shell steps"
                + (f" in job {args.job!r}" if args.job else ""),
                file=sys.stderr,
            )
            sys.exit(2)
        steps.extend(found)

    own_temp = args.work_dir is None
    workdir = Path(
        args.work_dir or tempfile.mkdtemp(prefix="ci-replay-")
    ).resolve()
    if own_temp or not workdir.exists():
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True)

    copy_tree(root, workdir)

    # `actions/checkout` always provides a repository, and excluding `.git` (which
    # the clean copy must do) otherwise makes every `git config` step fail with
    # `fatal: not in a git directory` — an artefact of the replay that reads as a
    # broken workflow. Initialise a bare one; nothing is committed and `git push`
    # is denied, so this cannot reach a remote.
    if not args.no_git:
        subprocess.run(
            ["git", "init", "-q"], cwd=workdir, capture_output=True, text=True
        )

    # The variables Actions always sets and a local shell never does. Without
    # them, any step appending to the job summary fails with
    # `: No such file or directory` and the replay reports a broken workflow.
    summary = workdir / ".ci-replay-step-summary.md"
    output = workdir / ".ci-replay-step-output.txt"
    summary.write_text("", encoding="utf-8")
    output.write_text("", encoding="utf-8")
    shell_env = clean_environment(
        {
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_WORKSPACE": str(workdir),
            "GITHUB_ACTIONS": "true",
        }
    )

    results = replay(steps, workdir, timeout=args.timeout, shell_env=shell_env)
    failed = [entry for entry in results if entry["status"] == "FAIL"]
    skipped = [entry for entry in results if entry["status"] == "SKIPPED"]

    if args.json:
        print(
            json.dumps(
                {
                    "workdir": str(workdir),
                    "steps": results,
                    "passed": len(results) - len(failed) - len(skipped),
                    "failed": len(failed),
                    "skipped": len(skipped),
                },
                indent=2,
            )
        )
    else:
        for entry in results:
            print(f"{entry['status']:8s} {entry['job']} / {entry['name']}")
            if entry["status"] != "ok":
                print(f"         {entry['detail']}")
        print()
        print(
            f"replay_ci: {len(results) - len(failed) - len(skipped)} passed, "
            f"{len(failed)} failed, {len(skipped)} skipped in {workdir}"
        )

    if args.keep:
        print(f"replay directory kept: {workdir}")
    elif own_temp:
        shutil.rmtree(workdir, ignore_errors=True)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
