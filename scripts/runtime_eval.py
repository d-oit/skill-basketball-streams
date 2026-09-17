#!/usr/bin/env python3
"""runtime_eval.py — scaffold runtime skill-evaluator for skill-basketball-streams.

Walks the 8 cases in evals/evals.json and either:

(a) Reports structural coverage per case (when --stubs is NOT provided).
(b) Treats the contents of --stubs JSON as the actual skill output for each
    case id and asserts each case's expected_output AND every assertion[]
    string holds against the corresponding stub.

This is a SCAFFOLD. Real runtime eval requires an LLM agent + sandboxed tools
(webSearch, openUrl, GOOGLECALENDAR_EVENTS_LIST, GOOGLECALENDAR_CREATE_EVENT).
This script operates on canned string outputs only — it does NOT invoke any
real skill. `--transcripts` grades output captured from a real run
(`capture_transcripts.py`) instead of a canned stub, and grades it on the
per-case `assertions[]` needles rather than on equality with `expected_output`:
see `runtime_pass` for why those two regimes have to differ.

Usage:
    python3 scripts/runtime_eval.py [--root PATH] [--stubs PATH]

If --stubs is omitted, runs in structural-pass mode (reports per-case
coverage, exits 0 only if every case has well-formed fields). If --stubs
PATH is provided, runs in runtime-pass mode (asserts each case's
expected_output + assertions[] against the matching stub; exits 0 only
when every assertion holds).

Exit codes:
    0  PASS — every requested check succeeded.
    1  FAIL — one or more cases reported an error (printed to stderr).
    2  USAGE — bad arguments or a required input file is missing.

Stubs JSON shape:
    {
      "1": "<canned skill output string for case id=1>",
      "2": "<canned skill output string for case id=2>",
      ...
    }
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def load_evals(root: Path) -> list[dict]:
    f = root / "evals" / "evals.json"
    if not f.is_file():
        _fail(f"{f.relative_to(root)}: file missing")
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _fail(f"evals/evals.json: invalid JSON ({e})")
    cases = d.get("evals")
    if not isinstance(cases, list):
        _fail("evals: expected list, got " + type(cases).__name__)
    return cases


def load_stubs(path: Path) -> dict[int, str]:
    if not path.is_file():
        _fail(f"{path}: file missing")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _fail(f"{path}: invalid JSON ({e})")
    out: dict[int, str] = {}
    for k, v in raw.items():
        try:
            int_k = int(k)
        except (TypeError, ValueError):
            _fail(f"{path}: stub key {k!r} is not coercible to int")
        if not isinstance(v, str):
            _fail(
                f"{path}: stub key {k!r} has non-string value {v!r} "
                f"(expected str, got {type(v).__name__})"
            )
        out[int_k] = v
    return out


def structural_pass(cases: list[dict]) -> int:
    """Report per-case coverage without invoking any skill. Exits 0 if every
    case has well-formed id / expected_output / assertions fields."""
    failed = 0
    for case in cases:
        cid = case.get("id")
        expected_output = case.get("expected_output")
        assertions = case.get("assertions")
        if not isinstance(cid, int):
            print(f"FAIL: case {case.get('id')!r}: id expected int", file=sys.stderr)
            failed += 1
            continue
        if not isinstance(expected_output, str):
            print(
                f"FAIL: case #{cid}.expected_output: expected str",
                file=sys.stderr,
            )
            failed += 1
            continue
        if not isinstance(assertions, list):
            print(
                f"FAIL: case #{cid}.assertions: expected list",
                file=sys.stderr,
            )
            failed += 1
            continue
        print(
            f"OK: structural: Case {cid}: "
            f"{len(assertions)} assertions, "
            f"expected_output length={len(expected_output)}"
        )
    return 1 if failed else 0


DECISION_MARKER = "Decision="


def evaluate_assertion(assertion: str, stub_output: str) -> tuple[bool, str]:
    """Translate an assertion of form '<name> expected <status>' into a check
    that the stub output contains '<name>=<status>'. Returns
    (passed, needle_explanation)."""
    m = re.match(r"^(\S+)\s+expected\s+(PASS|FAIL)$", assertion)
    if not m:
        return False, f"(malformed assertion: {assertion!r})"
    name, status = m.group(1), m.group(2)
    needle = f"{name}={status}"
    return needle in stub_output, needle


def runtime_pass(
    cases: list[dict],
    stubs: dict[int, str],
    *,
    require_exact: bool = True,
    allow_partial: bool = False,
) -> int:
    """Assert every case's `assertions[]` hold against its output.

    Two regimes, and the difference is deliberate rather than a flag for its own
    sake:

    * `--stubs` (`require_exact=True`) — we author the canned outputs, so
      byte-equality with `expected_output` is a legitimate regression lock: a
      stub drifting means the canonical answer changed.
    * `--transcripts` (`require_exact=False`) — a real model cannot reproduce
      `expected_output` character-for-character. Requiring equality made this
      grader unable to pass anything, which is why the workflow's
      real-transcript step could only ever print a warning. Here the
      **assertions are the gate** and the canonical text is reported, not
      required.

    A file that covers only SOME cases is a FAIL by default, because "the
    capture is complete" is the property grading rests on. `allow_partial=True`
    narrows grading to the cases the file actually covers and reports how many
    were left out — it exists for the self-improvement loop, which captures
    transcripts for the newly synthesised ids alone (a full re-capture per PR
    would be 36 model calls to grade 3 cases). The concession is a named flag at
    the call site rather than a silent behaviour, so a reader of the workflow can
    see the scope it is narrowing to.

    Three things must hold in every mode: a transcript id that is not in
    `evals.json` is stale and is an error; the output must carry a `Decision=`
    line; and at least one case must actually be graded, so an empty file can
    never pass. Outputs are `.strip()`-normalized so trailing whitespace is a
    no-op. Exits 0 only if every check passes.
    """
    failed = 0
    graded = 0
    total_assertions = 0
    skipped: list[int] = []
    label = "--stubs" if require_exact else "--transcripts"
    known = {case.get("id") for case in cases}
    stale = sorted(set(stubs) - known)
    if stale:
        print(
            f"FAIL: runtime_eval: {label} names case ids not in evals.json: "
            f"{stale} — the file is stale, not evidence",
            file=sys.stderr,
        )
        return 1
    for case in cases:
        cid = case.get("id")
        expected_output = case.get("expected_output")
        assertions = case.get("assertions", [])
        if cid not in stubs:
            if allow_partial:
                skipped.append(cid)
                continue
            print(
                f"FAIL: case #{cid}: "
                + ("stub missing in --stubs JSON" if require_exact else "no transcript supplied"),
                file=sys.stderr,
            )
            failed += 1
            continue
        graded += 1
        stub = stubs[cid].strip()
        expected_strip = expected_output.strip()
        canonical_match = stub == expected_strip
        if require_exact and not canonical_match:
            print(
                f"FAIL: case #{cid}: stub != expected_output (after .strip()):\n"
                f"  expected: {expected_strip!r}\n"
                f"  stub    : {stub!r}",
                file=sys.stderr,
            )
            failed += 1
            continue
        if not require_exact and DECISION_MARKER not in stub:
            print(
                f"FAIL: case #{cid}: transcript has no {DECISION_MARKER!r} line, "
                "so there is no decision to grade",
                file=sys.stderr,
            )
            failed += 1
            continue
        if not assertions:
            print(
                f"FAIL: case #{cid}: no assertions, so nothing is graded",
                file=sys.stderr,
            )
            failed += 1
            continue
        case_failed = False
        for assertion in assertions:
            passed, needle = evaluate_assertion(assertion, stub)
            if not passed:
                print(
                    f"FAIL: case #{cid}.assertions: missing needle {needle!r} "
                    f"for assertion {assertion!r}",
                    file=sys.stderr,
                )
                case_failed = True
        if case_failed:
            failed += 1
            continue
        total_assertions += len(assertions)
        verdict = "canonical text matched" if canonical_match else (
            "canonical text differs — expected for a live model"
        )
        suffix = f"; {verdict}" if not require_exact else ""
        print(
            f"OK: runtime: Case {cid} passed "
            f"({len(assertions)} assertions evaluated{suffix})"
        )
    if failed:
        return 1
    if graded == 0:
        print(
            f"FAIL: runtime_eval: nothing was graded — {label} supplied no "
            "transcript for any case",
            file=sys.stderr,
        )
        return 1
    partial = (
        f"; {len(skipped)} case(s) not in this file, so not graded"
        if skipped
        else ""
    )
    print(
        f"OK: runtime_eval: {graded} cases passed against {label} "
        f"({total_assertions} assertions evaluated{partial})"
    )
    return 0


def load_transcripts(path: Path) -> dict[int, str]:
    """Load REAL captured transcripts produced by capture_transcripts.py.

    Accepts the rich shape ({"transcripts": [{"id": int, "output": str}]})
    as well as a flat {id: output} mapping, so a file written by
    capture_transcripts.py can be graded directly here.
    """
    if not path.is_file():
        _fail(f"{path}: file missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _fail(f"{path}: invalid JSON ({e})")
    if isinstance(payload, dict) and "transcripts" in payload:
        payload = payload["transcripts"]
    out: dict[int, str] = {}
    if isinstance(payload, dict):
        return load_stubs_from_mapping(payload, path)
    if not isinstance(payload, list):
        _fail(f"{path}: expected an object or a list of transcripts")
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            _fail(f"{path}: transcripts[{index}] is not an object")
        try:
            case_id = int(item["id"])
        except (KeyError, TypeError, ValueError):
            _fail(f"{path}: transcripts[{index}].id is not an int")
        output = item.get("output", "")
        if not isinstance(output, str):
            _fail(
                f"{path}: transcripts[{index}].output is not a string "
                f"(got {type(output).__name__})"
            )
        out[case_id] = output
    return out


def load_stubs_from_mapping(raw: dict, path: Path) -> dict[int, str]:
    """Shared coercion for the flat {id: str} shape (--stubs and --transcripts)."""
    out: dict[int, str] = {}
    for k, v in raw.items():
        try:
            int_k = int(k)
        except (TypeError, ValueError):
            _fail(f"{path}: stub key {k!r} is not coercible to int")
        if not isinstance(v, str):
            _fail(
                f"{path}: stub key {k!r} has non-string value {v!r} "
                f"(expected str, got {type(v).__name__})"
            )
        out[int_k] = v
    return out


def main() -> None:
    p = argparse.ArgumentParser(
        description="Scaffold runtime skill-evaluator for skill-basketball-streams.",
    )
    p.add_argument(
        "--root",
        default=".",
        help="path to the skill root (default: current working directory)",
    )
    p.add_argument(
        "--stubs",
        default=None,
        help="optional path to a JSON file mapping case id (int) to "
             "canned skill output (str)",
    )
    p.add_argument(
        "--transcripts",
        default=None,
        help="optional path to REAL captured transcripts "
             "(capture_transcripts.py output); graded on assertions[] — a live "
             "model cannot reproduce expected_output byte-for-byte",
    )
    p.add_argument(
        "--allow-partial",
        action="store_true",
        help="grade only the cases the file covers, and say how many were "
             "skipped. Without it, a missing transcript is a FAIL; use it only "
             "when the capture is deliberately scoped (e.g. the new-case "
             "capture in self-improve.yml)",
    )
    args = p.parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"FAIL: --root {root}: not a directory", file=sys.stderr)
        sys.exit(2)
    cases = load_evals(root)
    if args.transcripts is not None:
        # Assertion-graded, not equality-graded: see `runtime_pass`.
        rc = runtime_pass(
            cases,
            load_transcripts(Path(args.transcripts).resolve()),
            require_exact=False,
            allow_partial=args.allow_partial,
        )
        sys.exit(rc)
    if args.stubs is None:
        rc = structural_pass(cases)
        sys.exit(rc)
    stubs = load_stubs(Path(args.stubs).resolve())
    rc = runtime_pass(cases, stubs, allow_partial=args.allow_partial)
    sys.exit(rc)


if __name__ == "__main__":
    main()
