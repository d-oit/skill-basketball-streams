#!/usr/bin/env python3
"""prefix_red.py — demonstrate pre-fix-red against the run that made the misjudgement.

§9.3's gate asserts the pre-fix-red property: "a synthesised case must FAIL
against the current code and only pass once the gate is tightened". Until 1.7.0
that property was asserted, not demonstrated, because the transcript of the
misjudged run was never kept — `docs/runtime.md` said so. Now the audit files
every run's transcript on the telemetry branch under `transcripts/<run_id>.json`
and the event ledger names the same `run_id`, so the chain

    verdict -> event_id -> run_id -> transcripts/<run_id>.json

can finally be walked. This script walks it.

The demonstration is deliberately NOT `runtime_eval --transcripts` on the
transcript. A synthesised case carries needles no real transcript contains
(`eventCreated=FAIL`, `regression=PASS` — they live in `expected_output`, which
a model produces only when *asked the case*). Grading the run's own transcript
against the full assertion list would be red for reasons that have nothing to
do with the bug, and a red that cannot be wrong proves nothing. Instead, the
property checked here is the one that encodes the misjudgement:

* the case's first assertion names the check that should have failed
  (`freeAccess expected FAIL`);
* the misjudged run's own answer, recorded in its transcript, carries the
  opposite claim (`freeAccess=PASS`) for exactly that game;
* so the needle is ABSENT from reality — the case is red against the run's
  own answer, red for the right reason.

The game's lines are scoped out of the transcript by the event's `summary`
(exact line match first, then a token fuzzy match), so a different game's
correctly-rejected candidate cannot satisfy or spoil the needle. When no line
matches, the whole model text is used and the case is reported unscoped — an
honest weaker claim rather than a precise-looking false one.

Inputs:
    --cases        synthesise_eval_case.py --json output (.tmp/new-cases.json)
    --events       events.jsonl (event_id -> run_id, from the telemetry branch)
    --transcripts  directory of filed transcripts, one `<run_id>.json` per run
    --out          optional evidence record (JSON) for the run log / PR body
    --list-needed  print the run ids the cases join to (space-separated), for
                   the fetch step — offline, deterministic, no side effects.

Exit codes:
    0  PASS — at least one case was demonstrated red against its run
    1  FAIL — nothing demonstrated; every case's reason is reported (the
              pre-1.7.0 world, or the ledger predates the filing)
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:  # direct CLI execution: `python3 scripts/prefix_red.py`
    from extract_candidates import _decode_jsonl, transcript_text
    from synthesise_eval_case import assertion_needle
except ImportError:  # imported as a package module, e.g. scripts.prefix_red
    from scripts.extract_candidates import _decode_jsonl, transcript_text  # type: ignore
    from scripts.synthesise_eval_case import assertion_needle  # type: ignore

# The grader's needle grammar, re-used rather than re-implemented: if this
# drifted, the demonstration would prove a property the grader cannot check.
MIN_FUZZY_TOKENS = 3
TOKEN = re.compile(r"[\w'äöüßÄÖÜ]{4,}", re.UNICODE)


def load_cases(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: prefix_red: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"FAIL: prefix_red: {path}: invalid JSON ({exc})", file=sys.stderr)
        sys.exit(2)
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        print(f"FAIL: prefix_red: {path}: expected {{\"cases\": [...]}}", file=sys.stderr)
        sys.exit(2)
    return [case for case in cases if isinstance(case, dict)]


def load_events(path: Path) -> dict[str, dict]:
    """`{event_id: row}`, the LAST row per event — the same rule `audit_events`
    applies to the verdicts, so a re-planned event is judged by its newest
    recording rather than by a row a later run superseded."""
    if not path.is_file():
        return {}
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"FAIL: prefix_red: {path}: invalid JSONL row ({exc})", file=sys.stderr)
            sys.exit(2)
        if not isinstance(row, dict):
            continue
        event_id = str(row.get("event_id") or row.get("id") or "")
        if event_id:
            rows[event_id] = row
    return rows


def load_transcript(path: Path) -> str:
    """The model's text from a filed transcript, via the extraction seam's own
    reader — a second parser here is how the two would come to disagree about
    what the run actually said."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    payload = _decode_jsonl(text)
    return transcript_text(payload if payload is not None else text).strip()


def scope_lines(summary: str, text: str) -> tuple[str, bool]:
    """The game's lines out of the run's answer: exact summary match first,
    then a token fuzzy match, else the whole text (unscoped)."""
    if not text:
        return "", False
    if summary:
        lines = text.splitlines()
        exact = [ln for ln in lines if summary in ln]
        if exact:
            return "\n".join(exact), True
        tokens = {m.group(0) for m in TOKEN.finditer(summary)}
        if tokens:
            fuzzy = [
                ln
                for ln in lines
                if sum(token in ln for token in tokens)
                >= min(MIN_FUZZY_TOKENS, len(tokens))
            ]
            if fuzzy:
                return "\n".join(fuzzy), True
    return text, False


def demonstrate(case: dict, event_row: dict, text: str) -> dict:
    """Is the case red against this run's own answer — and for the right reason?

    `red` alone is not enough (a missing needle is also what an unrelated
    transcript yields); `claimed` is what makes it the misjudgement: the run
    really did record the opposite of what the case now demands.
    """
    assertions = case.get("assertions") or []
    needle = assertion_needle(str(assertions[0])) if assertions else None
    claimed_needle = needle.replace("=FAIL", "=PASS") if needle else None
    summary = str(event_row.get("summary") or "")
    slice_text, scoped = scope_lines(summary, text)
    red = bool(needle) and needle not in slice_text
    claimed = bool(claimed_needle) and claimed_needle in slice_text
    return {
        "case_id": case.get("id"),
        "event_id": str(case.get("synthesised_from") or event_row.get("event_id") or ""),
        "run_id": str(event_row.get("run_id") or ""),
        "needle": needle or "",
        "claimed_needle": claimed_needle or "",
        "scoped": scoped,
        "red": red,
        "claimed": claimed,
        "demonstrated": red and claimed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Demonstrate a synthesised case is red against the misjudged "
            "run's own transcript."
        ),
    )
    parser.add_argument("--cases", required=True, help="synthesise --json output")
    parser.add_argument(
        "--events", required=True, help="events.jsonl from the telemetry branch"
    )
    parser.add_argument(
        "--transcripts", required=True, help="directory of filed transcripts"
    )
    parser.add_argument("--out", help="write the evidence record (JSON) here")
    parser.add_argument(
        "--list-needed",
        action="store_true",
        help="print the run ids the cases join to (space-separated) and exit",
    )
    args = parser.parse_args()

    cases = load_cases(Path(args.cases))
    events = load_events(Path(args.events))
    transcripts_dir = Path(args.transcripts)

    # The join, walked for every case — reported per case, because a ledger that
    # predates the transcript filing is the expected state for old verdicts and
    # must read as "not demonstrable", not as an error.
    results: list[dict] = []
    for case in cases:
        event_id = str(case.get("synthesised_from") or "")
        if not event_id:
            results.append(
                {
                    "case_id": case.get("id"),
                    "demonstrated": False,
                    "reason": "the case carries no synthesised_from event id",
                }
            )
            continue
        event_row = events.get(event_id)
        if event_row is None:
            results.append(
                {
                    "case_id": case.get("id"),
                    "event_id": event_id,
                    "demonstrated": False,
                    "reason": (
                        f"no events.jsonl row for {event_id} — the ledger "
                        "predates the run or the event"
                    ),
                }
            )
            continue
        run_id = str(event_row.get("run_id") or "")
        if not run_id:
            results.append(
                {
                    "case_id": case.get("id"),
                    "event_id": event_id,
                    "demonstrated": False,
                    "reason": f"the events row for {event_id} carries no run_id",
                }
            )
            continue
        text = load_transcript(transcripts_dir / f"{run_id}.json")
        if not text:
            results.append(
                {
                    "case_id": case.get("id"),
                    "event_id": event_id,
                    "run_id": run_id,
                    "demonstrated": False,
                    "reason": (
                        f"transcripts/{run_id}.json is not on the telemetry "
                        "branch (the misjudged run predates the transcript filing)"
                    ),
                }
            )
            continue
        results.append(demonstrate(case, event_row, text))

    if args.list_needed:
        # The fetch step's shopping list, ids only: the caller shells it into a
        # loop, and anything richer here is a second format to keep in sync.
        needed = sorted({str(r.get("run_id")) for r in results if r.get("run_id")})
        print(" ".join(needed))
        return

    demonstrated = [r for r in results if r.get("demonstrated")]
    for r in results:
        if r.get("demonstrated"):
            scope = "scoped" if r.get("scoped") else "UNSCOPED (whole-text grade)"
            print(
                f"OK: prefix_red: case {r.get('case_id')} is red against run "
                f"{r.get('run_id')}: its own answer claims "
                f"`{r.get('claimed_needle')}`, the case demands "
                f"`{r.get('needle')}' ({scope})"
            )
        else:
            if r.get("red") is False and r.get("claimed"):
                reason = (
                    "the case is NOT red against the run's answer — the needle "
                    f"`{r.get('needle')}` is already there, so the case may not "
                    "encode the bug"
                )
            elif r.get("red") and not r.get("claimed"):
                reason = (
                    "the run's answer does not carry the misjudged claim "
                    f"(`{r.get('claimed_needle')}` absent) — nothing to "
                    "demonstrate against"
                )
            else:
                reason = str(r.get("reason") or "no join to a filed transcript")
            print(f"OK: prefix_red: case {r.get('case_id')} not demonstrated — {reason}")

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "generated_by": "prefix_red.py",
                    "demonstrated": len(demonstrated),
                    "cases": len(results),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"OK: prefix_red: evidence record written to {args.out} (verify-only)")

    if demonstrated:
        print(
            f"OK: prefix_red: {len(demonstrated)}/{len(results)} case(s) "
            "demonstrated red against the misjudged run's own transcript"
        )
        sys.exit(0)
    print(
        "FAIL: prefix_red: no case was demonstrated red — pre-fix-red stays "
        "asserted, not demonstrated (each case's reason is reported above)",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()