#!/usr/bin/env python3
"""corpus_flip_issue.py — one issue when a recorded page's verdict flips.

The weekly `corpus-refresh` workflow re-fetches every page in
`tests/fixtures/pages/` and lets `record_pages.py --refresh` compare the
stream-evidence gate's verdict with the recorded one. This script turns the
result into an issue — or, far more often, into nothing at all.

**Only a flip files anything.** That is the whole noise policy, and it is not
squeamishness: the YouTube fixtures are extracts of a 1.3 MB document whose hash
changes whenever a sidebar suggestion is edited, so a byte-level diff would file
an issue every single week and the signal would drown. A persistent flip reuses
the open issue with a comment rather than opening a new one, so one stuck page
does not become a weekly pile.

Both directions are actionable and neither is "expected":

    lost-evidence    a page playing a real stream is no longer recognised
                     -> missed live streams in the calendar
    gained-evidence  a page playing nothing is now accepted
                     -> false positives, the failure this corpus exists for

The two fixes are different, and the issue says so: the source may have changed
(update `PAGES`/`expect` and re-record) or the gate may have broken (fix the
gate and keep the corpus). Nothing here decides which — a human does. This
script holds no calendar credential and never writes to the repository.

Usage:
    python3 scripts/corpus_flip_issue.py --report .tmp/refresh.json
    python3 scripts/corpus_flip_issue.py --report .tmp/refresh.json --dry-run
    python3 scripts/corpus_flip_issue.py --report .tmp/refresh.json --markdown
    python3 scripts/corpus_flip_issue.py --report .tmp/refresh.json --file

Exit codes:
    0  PASS — nothing flipped, or the flip was reported/filed
    1  FAIL — a flip exists and could not be filed (gh missing or failed)
    2  USAGE — bad arguments, or a report that could not be read
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from gh_issue import (
    existing_open_issue,
    file_or_comment,
    run_gh,
    run_url,
)

# The stable half of the issue title, and the dedupe key. It must not carry a
# date or a page name: either would make every week's flip look like a new
# issue, which is the noise this script exists to avoid.
TITLE_MARKER = "[corpus] page verdict flip"


# `gh_issue.py` owns the filer; these two wrappers bind it to this script's
# marker so a caller cannot dedupe the corpus report against the rung report.
# The names are kept (and tested) here rather than re-exported, so the marker is
# applied in exactly one place per caller.
def _existing_open_issue(payload: str) -> int | None:
    """The number of the open issue already carrying TITLE_MARKER, if any."""
    return existing_open_issue(payload, TITLE_MARKER)


def _run_url() -> str:
    return run_url()


def build_title() -> str:
    return TITLE_MARKER


def build_markdown(
    flips: list[dict], *, pages: int = 0, unusable: list[dict] | None = None
) -> str:
    """The job-summary view. One renderer for the artifact, not two.

    A summary written by hand next to a payload read by a script drifts, and the
    misleading one is the one people read.

    `unusable` is the part that must not be dropped: a refresh that could not
    reach a page still compares the rest, and a summary that reports "no verdict
    changed" without saying so turns an unasked question into a clean bill of
    health. An unreached page is not a page whose verdict held.
    """
    unreachable = list(unusable or [])
    lines = [
        "| | |",
        "|---|---|",
        f"| Pages re-recorded | {pages} |",
    ]
    if unreachable:
        lines.append(f"| Pages **not reachable** | {len(unreachable)} |")
    lines += [
        f"| Verdict flips | {len(flips)} |",
        "",
    ]
    if unreachable:
        lines += [
            f"**This comparison is partial: {len(unreachable)} page(s) could not be "
            "reduced, so it says nothing about them.** Nothing was written — the",
            "corpus on disk is unchanged — and no issue is filed off a partial run.",
            "",
        ]
        for item in unreachable:
            reason = " ".join(str(item.get("reason", "")).split())[:160]
            lines.append(f"- `{item.get('name', '?')}` — {reason}")
        lines.append("")
    if not flips:
        lines += [
            "No verdict changed"
            + (" among the pages compared" if unreachable else "")
            + ". A byte diff is not reported by design: the stored",
            "YouTube extracts change hash whenever the live page does, and that is",
            "not a finding.",
        ]
        return "\n".join(lines)
    lines += ["| Page | Was | Now | Direction |", "|---|---|---|---|"]
    for flip in flips:
        lines.append(
            f"| `{flip['name']}` | {flip['was']} | **{flip['now']}** "
            f"| {flip['direction']} |"
        )
    lines += [
        "",
        "`lost-evidence` means real broadcasts stop being recognised (missed",
        "games); `gained-evidence` means the false positives this corpus exists to",
        "prevent have come back. Both are filed on the issue; neither is fixed by",
        "editing a fixture.",
    ]
    return "\n".join(lines)


def build_body(flips: list[dict], *, refreshed_at: str = "") -> str:
    """The issue body: what flipped, why each direction matters, what to do."""
    rows = []
    for flip in flips:
        # `no` is not a claim about which side is wrong — only that they no longer
        # agree. Deciding which moved is the whole point of the issue.
        agrees = "yes" if flip.get("agrees_with_expect") else "**no**"
        rows.append(
            f"| `{flip['name']}` | {flip['was']} | **{flip['now']}** | "
            f"{flip['direction']} | {agrees} | {flip['url']} |"
        )
    notes = "\n".join(
        f"* `{flip['name']}` — {flip.get('note', '').strip()}"
        for flip in flips
        if flip.get("note")
    )
    return f"""The weekly `corpus-refresh` run re-recorded every page in
`tests/fixtures/pages/` and the stream-evidence gate returned a **different
verdict** for {len(flips)} page(s) than the recorded one.

That gate is the last check before a rendered page may become a link in the
Google Calendar, so this is a finding about the product, not about the tests.

| Page | Was | Now | Direction | Matches the recorded expectation | URL |
|---|---|---|---|---|---|
{chr(10).join(rows)}

A `no` there is the more urgent of the two: the gate and the recorded expectation
now disagree, and which one moved is a question for a human, not for this report.

Recorded at: `{refreshed_at or 'unknown'}`

Why the fixtures are what they are:

{notes or '*(no notes recorded)*'}

## Which of the two causes is it?

1. **The source changed** — a re-branded player, a moved video field, a page
   that stopped embedding a stream. Update `PAGES`/`expect` in
   `scripts/record_pages.py` and re-record:
   `python3 scripts/record_pages.py --refresh`. The `FLIP:` lines it prints are
   the diff you are accepting.
2. **The gate broke** — the page is what it always was and the gate stopped
   reading it. Fix `scripts/render_ladder.py` and **leave the corpus alone**: a
   real page disagreeing with the gate is the evidence, not an obstacle.

Never hand-edit a fixture to silence this. `record_pages.py --check` refuses a
file whose hash is not the recorded bytes, precisely so the corpus cannot be
turned into an assertion about a file we wrote rather than about the web.

## What this run did

Nothing but read. The refreshed corpus is a build artifact, this workflow holds
no credential, and it never commits — whatever lands has to come through a PR,
where `validate.yml` re-runs `record_pages.py --check`.

Run: {_run_url()}
"""


def _file_or_comment(title: str, body: str, runner=run_gh) -> str:
    """Create the issue, or comment on the open one carrying the marker."""
    return file_or_comment(title, body, marker=TITLE_MARKER, runner=runner)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, required=True,
                        help="the payload from `record_pages.py --refresh --json`")
    parser.add_argument("--file", action="store_true",
                        help="create or comment on the issue (touches GitHub)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the issue exactly as it would be sent")
    parser.add_argument("--markdown", action="store_true",
                        help="print the job-summary view (no network, exit 0)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.file and args.dry_run:
        print("FAIL: corpus_flip_issue: --file and --dry-run are exclusive",
              file=sys.stderr)
        sys.exit(2)

    try:
        payload = json.loads(args.report.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: corpus_flip_issue: {args.report}: {exc}", file=sys.stderr)
        sys.exit(2)

    flips = payload.get("flips") or []
    title = build_title()
    body = build_body(flips, refreshed_at=payload.get("refreshed_at", ""))

    if args.markdown:
        print(
            build_markdown(
                flips,
                pages=len(payload.get("pages") or []),
                unusable=payload.get("unusable") or [],
            )
        )
        return

    if not flips:
        if args.json:
            print(json.dumps({"flips": [], "filed": None}, indent=2))
        else:
            print("OK: corpus_flip_issue: no verdict flipped "
                  f"({len(payload.get('pages') or [])} page(s) re-recorded)")
        return

    if args.dry_run:
        print(title)
        print()
        print(body)
        return

    if not args.file:
        # A report, not a write. The `FLIP:` lines are the finding; the workflow
        # is the thing that passes --file.
        for flip in flips:
            print(
                f"FLIP: corpus_flip_issue: {flip['name']} {flip['was']} -> "
                f"{flip['now']} ({flip['direction']})"
            )
        if args.json:
            print(json.dumps({"flips": flips, "title": title, "filed": None}, indent=2))
        else:
            print(f"OK: corpus_flip_issue: {len(flips)} flip(s) to report — "
                  "pass --file to open the issue")
        return

    try:
        result = _file_or_comment(title, body)
    except FileNotFoundError:
        # A finding that could not be delivered must be loud, or the workflow
        # goes green having reported nothing at all.
        print("FAIL: corpus_flip_issue: `gh` is not installed, so the flip could "
              "not be filed", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        print(f"FAIL: corpus_flip_issue: gh failed: {detail[:400]}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps({"flips": flips, "title": title, "filed": result}, indent=2))
    else:
        for flip in flips:
            print(
                f"FLIP: corpus_flip_issue: {flip['name']} {flip['was']} -> "
                f"{flip['now']} ({flip['direction']})"
            )
        print(f"OK: corpus_flip_issue: {result}")


if __name__ == "__main__":
    main()
