#!/usr/bin/env python3
"""runtime_eval.py — scaffold runtime skill-evaluator for skill-basketball-streams.

Walks the 8 cases in evals/evals.json and either:

(a) Reports structural coverage per case (when --stubs is NOT provided).
(b) Treats the contents of --stubs JSON as the actual skill output for each
    case id and asserts each case's expected_output AND every assertion[]
    string holds against the corresponding stub.

This is a SCAFFOLD. Real runtime eval requires an LLM agent + sandboxed tools
(webSearch, openUrl, googleCalendarListEvents, googleCalendarCreateEvent).
This script operates on canned string outputs only — it does NOT invoke any
real skill. To use this in a CI pipeline that wants actual runtime eval,
replace the --stubs JSON with a transcript captured from a real
skill-evaluator run.

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


def runtime_pass(cases: list[dict], stubs: dict[int, str]) -> int:
    """Assert each case's expected_output equals its stub AND every assertion
    string holds against the stub. Both sides are .strip()-normalized so
    real-LLM transcripts with trailing whitespace still pass. Exits 0 only
    if every check passes."""
    failed = 0
    total_assertions = 0
    for case in cases:
        cid = case.get("id")
        expected_output = case.get("expected_output")
        assertions = case.get("assertions", [])
        if cid not in stubs:
            print(
                f"FAIL: case #{cid}: stub missing in --stubs JSON",
                file=sys.stderr,
            )
            failed += 1
            continue
        stub = stubs[cid].strip()
        expected_strip = expected_output.strip()
        if stub != expected_strip:
            print(
                f"FAIL: case #{cid}: stub != expected_output (after .strip()):\n"
                f"  expected: {expected_strip!r}\n"
                f"  stub    : {stub!r}",
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
        print(
            f"OK: runtime: Case {cid} passed "
            f"({len(assertions)} assertions evaluated)"
        )
    if failed:
        return 1
    print(
        f"OK: runtime_eval: {len(cases)} cases passed against --stubs "
        f"({total_assertions} assertions evaluated)"
    )
    return 0


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
    args = p.parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"FAIL: --root {root}: not a directory", file=sys.stderr)
        sys.exit(2)
    cases = load_evals(root)
    if args.stubs is None:
        rc = structural_pass(cases)
        sys.exit(rc)
    stubs = load_stubs(Path(args.stubs).resolve())
    rc = runtime_pass(cases, stubs)
    sys.exit(rc)


if __name__ == "__main__":
    main()
