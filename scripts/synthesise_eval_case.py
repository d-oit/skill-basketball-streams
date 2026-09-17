#!/usr/bin/env python3
"""synthesise_eval_case.py — turn an audit misjudgement into a regression case.

Phase 4 of `live-stream-runtime-spec.md` (§9.2 "auto-grow evals", §9.3 the gate).

The audit (`scripts/audit_events.py`) is the only component that learns the
truth about a broadcast, because a false positive can only be confirmed after
the whistle. This script converts each confirmed misjudgement into a new case in
`evals/evals.json`, so the mistake is fenced off permanently.

**The case encodes the bug, not the fix.** It asserts the outcome the pipeline
*should* have produced, which means it must FAIL against the current code and
only pass once the gate is actually tightened. That is the whole point: a
synthesised case that passes immediately would let the loop "learn" by weakening
the gate that caught the misjudgement. `scripts/validate.py` and
`runtime_eval.py` are what prove the case is well-formed; running it against the
pre-fix transcript is what proves it is a real test.

Three misjudgement classes are recognised:

| Audit outcome | What it means | Synthesised assertion |
|---|---|---|
| `WRONG`, reason mentions paid access | Check 1 (Free Access) passed on evidence that never proved entitlement | `freeAccess` should have been `FAIL` |
| `WRONG`, reason mentions no live broadcast | Check 7 (Direct Stream) passed on a broadcast that never happened | `directStreamVerification` should have been `FAIL` |
| `INCONCLUSIVE` on an event that was `VERIFIED` | the pipeline claimed confirmation it never had | `freeAccess` should have been `FAIL` and `verifiedClaimed` `FAIL` |

That third class matters most: "a `WRONG` event is never shown as `VERIFIED`
before the audit" is a 100 % requirement, so over-claiming is a hard failure
even when the broadcast later turns out to have been fine.

Every synthesised case carries a `synthesised_from` key recording the audit
event id, and re-running is idempotent: a case whose prompt already exists is
skipped rather than duplicated.

Usage:
    python3 scripts/synthesise_eval_case.py --verdicts audit.jsonl --dry-run
    python3 scripts/synthesise_eval_case.py --verdicts audit.jsonl \\
        --evals evals/evals.json [--ledger candidates.jsonl] [--limit 5]
    python3 scripts/synthesise_eval_case.py --verify

Exit codes:
    0  PASS — at least one case was synthesised (or a dry run found one)
    1  FAIL — nothing actionable, or --verify found a broken needle
    2  USAGE — bad arguments or unreadable input

Re-running over the same audit ledger is a no-op, so the workflow can run this
on every daily run without the eval set growing daily.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_EVALS = "evals/evals.json"
DEFAULT_LIMIT = 5

# Mirrors `runtime_eval.evaluate_assertion`. Kept identical on purpose: if this
# grammar drifts, the synthesiser would emit cases the grader cannot check.
ASSERTION_PATTERN = re.compile(r"^(\S+)\s+expected\s+(PASS|FAIL)$")

CHECK_FREE_ACCESS = "freeAccess"
CHECK_DIRECT_STREAM = "directStreamVerification"
CHECK_EVENT_CREATED = "eventCreated"
CHECK_VERIFIED_CLAIMED = "verifiedClaimed"
CHECK_REGRESSION = "regression"

CLASS_PAID = "paid"
CLASS_NEVER_LIVE = "never_live"
CLASS_OVERCLAIM = "overclaim"

VALIDATION_FILES = ["references/validation-workflow.md", "references/self-learning.md"]
CALENDAR_FILES = ["references/calendar-setup.md", "references/self-learning.md"]


def assertion_needle(assertion: str) -> str | None:
    """`"<name> expected PASS"` → `"<name>=PASS"`, or None if malformed."""
    match = ASSERTION_PATTERN.match(assertion or "")
    return f"{match.group(1)}={match.group(2)}" if match else None


def build_case_assertions(failing_check: str, *, overclaim: bool = False) -> list[str]:
    """The assertion list for a synthesised case.

    Order is deliberate: the check that should have failed first (it names the
    gate to tighten), then the consequence, then the regression marker.
    """
    assertions = [
        f"{failing_check} expected FAIL",
        (
            f"{CHECK_VERIFIED_CLAIMED} expected FAIL"
            if overclaim
            else f"{CHECK_EVENT_CREATED} expected FAIL"
        ),
        f"{CHECK_REGRESSION} expected PASS",
    ]
    return assertions


def build_expected_output(
    failing_check: str, reason: str, *, overclaim: bool = False
) -> str:
    """The graded output line, carrying a needle for every assertion."""
    consequence = (
        f"{CHECK_VERIFIED_CLAIMED}=FAIL"
        if overclaim
        else f"{CHECK_EVENT_CREATED}=FAIL"
    )
    return (
        f"Decision=SKIP; reason={reason}; checks: "
        f"{failing_check}=FAIL, {consequence}, {CHECK_REGRESSION}=PASS"
    )


def classify(verdict: dict) -> str | None:
    """Map an audit verdict row to a misjudgement class, or None if it is clean.

    `None` is the common case and is not an error: most audits find nothing, and
    a synthesiser that invented a case for every row would drown the eval set.
    """
    if not isinstance(verdict, dict):
        return None
    kind = str(verdict.get("verdict") or "").strip().upper()
    reason = str(verdict.get("reason") or "").lower()
    prior = str(verdict.get("prior_state") or "").strip().upper()

    if kind == "WRONG":
        if "paid" in reason:
            return CLASS_PAID
        return CLASS_NEVER_LIVE
    if kind == "INCONCLUSIVE":
        # An event the pipeline presented as confirmed, which the audit could not
        # confirm. Over-claiming is the failure, regardless of what the truth
        # later turned out to be.
        if prior == "VERIFIED":
            return CLASS_OVERCLAIM
        return None
    return None


def _context_for(verdict: dict, ledger_rows: list[dict]) -> dict:
    """Best-effort lookup of the candidate row that produced the event."""
    game_key = str(verdict.get("game_key") or "")
    event_id = str(verdict.get("event_id") or "")
    for row in ledger_rows:
        if not isinstance(row, dict):
            continue
        if game_key and str(row.get("game_key") or "") == game_key:
            return row
        # The candidate ledger has no event ids; the run log does. Fall back to
        # a url match only when the verdict carries one, which it normally does.
        if event_id and str(row.get("event_id") or "") == event_id:
            return row
    return {}


def _input_clause(verdict: dict, context: dict) -> str:
    parts = []
    url = str(context.get("url") or verdict.get("url") or "")
    if url:
        parts.append(f"url={url}")
    backend = str(context.get("backend") or "")
    if backend:
        parts.append(f"backend={backend}")
    game_key = str(verdict.get("game_key") or context.get("game_key") or "")
    if game_key:
        parts.append(f"game_key={game_key}")
    prior = str(verdict.get("prior_state") or "")
    if prior:
        parts.append(f"prior_state={prior}")
    reason = str(verdict.get("reason") or "")
    if reason:
        parts.append(f"audit_reason={reason}")
    audited = str(verdict.get("ts") or "")
    if audited:
        parts.append(f"audited_at={audited}")
    return "; ".join(parts) + "." if parts else ""


def synthesise(
    verdict: dict, context: dict | None, *, case_id: int, audited_at: str = ""
) -> dict | None:
    """Build one eval case from one misjudgement. Returns None if nothing to say."""
    kind = classify(verdict)
    if kind is None:
        return None
    context = context or {}
    event_id = str(verdict.get("event_id") or "unknown")
    stamp = audited_at or str(verdict.get("ts") or "")

    if kind == CLASS_PAID:
        failing_check = CHECK_FREE_ACCESS
        headline = (
            "a created event turned out to be paid, so the free-access gate was "
            "passed on evidence that never proved entitlement"
        )
        reason = (
            "the audit proved the broadcast was paid, so Check 1 (Free Access) "
            "should have failed and no event should exist"
        )
        files = VALIDATION_FILES
        overclaim = False
    elif kind == CLASS_NEVER_LIVE:
        failing_check = CHECK_DIRECT_STREAM
        headline = (
            "a created event was never actually broadcast, so the direct-stream "
            "gate was passed on a page that did not prove a live stream"
        )
        reason = (
            "the broadcast never happened, so Check 7 (Direct Stream "
            "Verification) should have failed and no event should exist"
        )
        files = VALIDATION_FILES
        overclaim = False
    else:
        failing_check = CHECK_FREE_ACCESS
        headline = (
            "an event was presented as VERIFIED even though the audit could not "
            "confirm free access"
        )
        reason = (
            "free access was never confirmed, so the event must not have been "
            "claiming VERIFIED"
        )
        files = CALENDAR_FILES
        overclaim = True

    stamp_clause = f", audited {stamp}" if stamp else ""
    return {
        "id": case_id,
        "prompt": (
            f"Audit regression (synthesised from {event_id}{stamp_clause}): "
            f"{headline}. Input: {_input_clause(verdict, context)}"
        ),
        "expected_output": build_expected_output(
            failing_check, reason, overclaim=overclaim
        ),
        "assertions": build_case_assertions(failing_check, overclaim=overclaim),
        "files": list(files),
        "synthesised_from": event_id,
    }


def next_case_id(cases: list[dict]) -> int:
    """Highest existing id + 1, so ids stay monotonic across a hand-edited file."""
    ids = [c["id"] for c in cases if isinstance(c, dict) and isinstance(c.get("id"), int)]
    return (max(ids) + 1) if ids else 1


def dedupe_key(case: dict) -> tuple[str, str]:
    """Stable identity for a synthesised case.

    Deliberately **not** the prompt: the prompt carries the audit timestamp so a
    human can tell when the misjudgement was found, and that changes on every
    run. Keying on it would re-file the same misjudgement every single day,
    inflating the eval set until a green run meant nothing. The audit event id
    plus the first assertion is stable and distinguishes the three misjudgement
    classes from one another.
    """
    assertions = case.get("assertions") or [""]
    return (str(case.get("synthesised_from") or ""), str(assertions[0]))


def assert_gradeable(case: dict) -> None:
    """Raise if any assertion has no matching needle in expected_output.

    A case whose needle is missing grades *nothing* — it passes vacuously, which
    is worse than not having the case at all. Better to refuse to write it.
    """
    expected = case.get("expected_output") or ""
    for assertion in case.get("assertions") or []:
        needle = assertion_needle(assertion)
        if needle is None:
            raise ValueError(f"malformed assertion {assertion!r}")
        if needle not in expected:
            raise ValueError(
                f"assertion {assertion!r} has no needle {needle!r} in expected_output"
            )


def read_verdicts(path: Path) -> list[dict]:
    """Read an audit ledger as JSON (`{"verdicts": [...]}`) or JSONL."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"FAIL: synthesise_eval_case: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except OSError as exc:
        print(f"FAIL: synthesise_eval_case: {path}: unreadable ({exc})", file=sys.stderr)
        sys.exit(2)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        rows: list[dict] = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                print(
                    f"FAIL: synthesise_eval_case: {path}:{number}: invalid JSON ({exc})",
                    file=sys.stderr,
                )
                sys.exit(2)
            if isinstance(parsed, dict):
                rows.append(parsed)
        return rows

    if isinstance(payload, dict):
        for key in ("verdicts", "audit", "rows"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
        return [payload]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    print(
        f"FAIL: synthesise_eval_case: {path}: expected an object or a list",
        file=sys.stderr,
    )
    sys.exit(2)


def load_jsonl(path: Path) -> list[dict]:
    """Read an optional JSONL side file (the candidate ledger). Never exits."""
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def load_evals(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: synthesise_eval_case: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"FAIL: synthesise_eval_case: {path}: invalid JSON ({exc})", file=sys.stderr)
        sys.exit(2)


def verify(payload: dict) -> int:
    """Check every case in the file is gradeable. Exit 1 on the first breakage."""
    cases = payload.get("evals")
    if not isinstance(cases, list):
        print("FAIL: synthesise_eval_case: evals must be a list", file=sys.stderr)
        return 1
    broken = 0
    for case in cases:
        if not isinstance(case, dict):
            continue
        cid = case.get("id")
        try:
            assert_gradeable(case)
        except ValueError as exc:
            print(f"FAIL: synthesise_eval_case: case #{cid}: {exc}", file=sys.stderr)
            broken += 1
    if broken:
        return 1
    print(f"OK: synthesise_eval_case: all {len(cases)} cases are gradeable")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Turn audit misjudgements into regression eval cases.",
    )
    parser.add_argument("--verdicts", help="audit ledger (JSON or JSONL)")
    parser.add_argument("--evals", default=DEFAULT_EVALS, help="evals/evals.json")
    parser.add_argument("--ledger", help="optional candidates.jsonl for context")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--now", help="ISO-8601 override (for tests)")
    parser.add_argument("--dry-run", action="store_true", help="write nothing")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="check every existing case is gradeable, then exit",
    )
    args = parser.parse_args()

    evals_path = Path(args.evals)
    payload = load_evals(evals_path)

    if args.verify:
        sys.exit(verify(payload))

    if not args.verdicts:
        print(
            "FAIL: synthesise_eval_case: --verdicts is required (or use --verify)",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.limit < 1:
        print("FAIL: synthesise_eval_case: --limit must be >= 1", file=sys.stderr)
        sys.exit(2)

    cases = payload.get("evals")
    if not isinstance(cases, list):
        print("FAIL: synthesise_eval_case: evals must be a list", file=sys.stderr)
        sys.exit(2)

    verdicts = read_verdicts(Path(args.verdicts))
    ledger_rows = load_jsonl(Path(args.ledger)) if args.ledger else []
    audited_at = args.now or datetime.now(timezone.utc).isoformat()

    existing_prompts = {
        str(case.get("prompt")) for case in cases if isinstance(case, dict)
    }
    # Only already-synthesised cases carry `synthesised_from`; a hand-written
    # case must never be treated as a duplicate of a synthesised one.
    existing_keys = {
        dedupe_key(case)
        for case in cases
        if isinstance(case, dict) and case.get("synthesised_from")
    }
    next_id = next_case_id(cases)

    created: list[dict] = []
    skipped_dupes = 0
    for verdict in verdicts:
        if len(created) >= args.limit:
            break
        case = synthesise(
            verdict,
            _context_for(verdict, ledger_rows),
            case_id=next_id + len(created),
            audited_at=audited_at,
        )
        if case is None:
            continue
        if dedupe_key(case) in existing_keys or case["prompt"] in existing_prompts:
            # Idempotent: the same misjudgement must not be filed twice.
            skipped_dupes += 1
            continue
        try:
            assert_gradeable(case)
        except ValueError as exc:
            print(f"FAIL: synthesise_eval_case: refused to write: {exc}", file=sys.stderr)
            sys.exit(1)
        existing_keys.add(dedupe_key(case))
        existing_prompts.add(case["prompt"])
        created.append(case)

    if not created:
        print(
            "FAIL: synthesise_eval_case: no actionable misjudgements "
            f"({len(verdicts)} verdicts read, {skipped_dupes} already filed)",
            file=sys.stderr,
        )
        sys.exit(1)

    # With `--json` the payload is the ONLY thing on stdout, so a caller can
    # `json.load(stdout)` without stripping a summary line first. The human line
    # moves to stderr rather than disappearing: `self-improve.yml` needs the
    # created ids to capture transcripts for exactly those cases, and silently
    # changing what stdout means is how a machine-readable interface rots.
    human = sys.stderr if args.json else sys.stdout
    if args.dry_run:
        print(
            f"OK: synthesise_eval_case: dry-run, would append {len(created)} cases "
            f"(next id {created[0]['id']})",
            file=human,
        )
    else:
        payload["evals"] = [*cases, *created]
        evals_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(
            f"OK: synthesise_eval_case: appended {len(created)} cases to {evals_path} "
            f"(ids {created[0]['id']}-{created[-1]['id']})",
            file=human,
        )

    if args.json:
        print(
            json.dumps(
                {
                    "cases": created,
                    "skipped_duplicates": skipped_dupes,
                    "dry_run": bool(args.dry_run),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
