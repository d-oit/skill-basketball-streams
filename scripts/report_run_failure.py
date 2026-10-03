#!/usr/bin/env python3
"""report_run_failure.py — file the red daily run as an issue, deduped.

The feedback gap this closes: **a red daily run left no trace.** The
2026-09-30 run failed after 24 minutes and nothing was filed — the only way
to notice was to read the Actions history by hand, which nobody schedules.
The rung health loop already reports its findings as issues (issue #46 was
filed by that mechanism); a red RUN is a bigger finding than a parked rung
and was the one thing the loop did not report. `if: failure()` in the
workflow means a green run files nothing and a skipped or cancelled job is
not a red run.

Dedupe (the same rule `gh_issue.py` serves the other loops with): ONE open
issue at a time, keyed by MARKER — "the daily run is red" is one ongoing
finding, the operator action is the same every time, and a repeat failure
comments on the open issue rather than opening another. The body carries the
failed jobs and the run link, so each comment says what failed THIS time.

Offline by construction: the run's job table is gathered by the workflow
step (`gh run view --json jobs`) and passed in as a file, so this script
never needs the network to be tested. Without `--file` it prints the report
and files nothing — that is the replay/verify mode, and the flag is on
`replay_ci.py`'s denylist for exactly that reason.

Usage:
    python3 scripts/report_run_failure.py --jobs jobs.json [--run-url URL] [--file]

Exit codes:
    0  PASS — a report was built (and filed, with --file)
    1  FAIL — nothing to report: no job of this run failed (the green path;
              also the workflow's own guard against filing on green)
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:  # direct CLI execution: `python3 scripts/report_run_failure.py`
    from gh_issue import file_or_comment, run_url
except ImportError:  # imported as a package module, e.g. scripts.report_run_failure
    from scripts.gh_issue import file_or_comment, run_url  # type: ignore

# The dedupe key AND the issue marker: one open "[runtime] daily run red"
# issue at a time. The failed-job list rides in the title for readability but
# never in the marker — a different failing job is the same ongoing finding,
# and listing it in the key would open a new issue per symptom.
MARKER = "[runtime] daily run red"

# Where to look when a run goes red. The pointers matter more than the list:
# a red run that tells the operator to read three specific places is worth
# three that just say "failed".
READ_THIS = (
    "1. The failed job's log (the link above names the jobs that failed).",
    "2. The job summaries of this run — write mode, transcript grade, funnel.",
    "3. `transcripts/` on the `telemetry` branch — the filed transcript of the\n"
    "   run, graded, kept under the run id.",
)


def load_failed_jobs(path: Path) -> list[str]:
    """The names of the jobs whose conclusion is `failure`, in run order.

    `skipped` and `cancelled` are deliberately not failures: a skipped gate
    is a configuration fact (and the workflow's `if: failure()` would not
    have run this script for it), and folding them in would let a
    misconfigured run read as a red one.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: report_run_failure: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"FAIL: report_run_failure: {path}: invalid JSON ({exc})", file=sys.stderr)
        sys.exit(2)
    jobs = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(jobs, list):
        print(
            f"FAIL: report_run_failure: {path}: expected {{\"jobs\": [...]}}",
            file=sys.stderr,
        )
        sys.exit(2)
    return [
        str(job.get("name") or "(unnamed job)")
        for job in jobs
        if isinstance(job, dict) and str(job.get("conclusion") or "") == "failure"
    ]


def build_title(failed: list[str]) -> str:
    return f"{MARKER}: {', '.join(failed)}"


def build_body(failed: list[str], url: str) -> str:
    lines = [
        "A job of the daily runtime run failed. This issue is filed by the",
        "`Report a red run` job (`if: failure()`), so a green run files nothing",
        "and a red run is never silent — the 2026-09-30 run failed for 24",
        "minutes and left no trace, which is the gap this closes.",
        "",
        "| Failed job |",
        "|---|",
    ]
    lines += [f"| {name} |" for name in failed]
    lines += ["", f"Run: {url}", "", "Read, in this order:", ""]
    lines += READ_THIS
    lines += [
        "",
        "Do not silence this report to get a green board: fix the cause, or",
        "close the issue deliberately and say why in the commit message.",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report a red daily run as a deduped GitHub issue.",
    )
    parser.add_argument(
        "--jobs",
        required=True,
        help="the run's job table, as written by `gh run view --json jobs`",
    )
    parser.add_argument(
        "--run-url",
        default="",
        help="link to the Actions run (defaults to this run, via gh_issue.run_url)",
    )
    parser.add_argument(
        "--file",
        action="store_true",
        help="file the issue (or comment on the open one); without it, print only",
    )
    args = parser.parse_args()

    failed = load_failed_jobs(Path(args.jobs))
    if not failed:
        print("OK: report_run_failure: no failed job — nothing to report (verify-only)")
        sys.exit(1)

    url = args.run_url or run_url()
    title = build_title(failed)
    body = build_body(failed, url)

    if not args.file:
        print(title)
        print(body)
        print("OK: report_run_failure: report built, not filed (verify-only)")
        sys.exit(0)

    result = file_or_comment(title, body, marker=MARKER)
    print(f"OK: report_run_failure: {result}")
    sys.exit(0)


if __name__ == "__main__":
    main()