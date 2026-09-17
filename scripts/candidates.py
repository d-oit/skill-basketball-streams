#!/usr/bin/env python3
"""candidates.py — candidate ledger and the recall denominator.

Phase 0 of the runtime plan (see `live-stream-runtime-spec.md` §8 and §10.1).

Every candidate stream that **any** search/render backend surfaces is appended to
`candidates.jsonl`. That ledger is the recall denominator: without it there is no
way to tell whether a change made the pipeline better or merely quieter.

Recall is computed over unique games, not unique rows, because the same game is
routinely surfaced by several backends:

    eligible  = unique game_keys with live_signal == true
    captured  = unique game_keys with disposition == "created"
    recall    = captured / eligible

Rows whose `disposition` is `unverifiable` (every render rung failed) are the
cheapest available recall win: the URL is already known, so `retry` lists them
for a later run.

`recall` can also be measured against **official league fixtures** (Phase 5,
`scripts/fixtures.py`). That is the metric search-derived candidates structurally
cannot produce: a game that *no* backend surfaced is invisible to the ledger, and
it is the worst failure this system has.

    eligible  = unique game_keys with live_signal == true
    captured  = unique game_keys with disposition == "created"
    recall    = captured / eligible                      (search-derived)
    unseen    = fixture game_keys in no ledger row at all (fixture-derived)

Usage:
    python3 scripts/candidates.py append --rows rows.json --ledger candidates.jsonl
    python3 scripts/candidates.py recall --ledger candidates.jsonl
    python3 scripts/candidates.py recall --ledger candidates.jsonl --json
    python3 scripts/candidates.py recall --ledger candidates.jsonl --fixtures fixtures.jsonl
    python3 scripts/candidates.py unseen --ledger candidates.jsonl --fixtures fixtures.jsonl
    python3 scripts/candidates.py retry  --ledger candidates.jsonl

Exit codes:
    0  PASS — rows appended, or metrics computed over at least one eligible game
    1  FAIL — no ledger, no eligible games, no fixtures, or nothing to retry
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DISPOSITIONS = (
    "created",
    "skipped_duplicate",
    "unverifiable",
)
# Any disposition not in DISPOSITIONS must start with this prefix.
REJECTED_PREFIX = "rejected_"

DEFAULT_LEDGER = "candidates.jsonl"
WRITE_CHUNK = 500


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def valid_disposition(value: str) -> bool:
    """`created`, `skipped_duplicate`, `unverifiable`, or `rejected_<check>`."""
    if not isinstance(value, str):
        return False
    return value in DISPOSITIONS or value.startswith(REJECTED_PREFIX)


def normalise_row(raw: dict, *, ts: str | None = None, run_id: str = "") -> dict:
    """Coerce a candidate into a ledger row, filling required fields.

    Raises ValueError on unrecoverable input (no url and no game_key, or an
    invalid disposition).
    """
    if not isinstance(raw, dict):
        raise ValueError(f"row must be an object, got {type(raw).__name__}")
    url = str(raw.get("url") or "").strip()
    game_key = str(raw.get("game_key") or "").strip()
    if not url and not game_key:
        raise ValueError("row needs at least one of 'url' or 'game_key'")
    disposition = str(raw.get("disposition") or "").strip()
    if not valid_disposition(disposition):
        raise ValueError(
            f"invalid disposition {disposition!r} "
            f"(expected one of {DISPOSITIONS} or 'rejected_<check>')"
        )
    return {
        "ts": ts or str(raw.get("ts") or utc_now().isoformat()),
        "run_id": str(raw.get("run_id") or run_id),
        "candidate_id": str(raw.get("candidate_id") or ""),
        "url": url,
        "backend": str(raw.get("backend") or ""),
        "game_key": game_key,
        "live_signal": bool(raw.get("live_signal", False)),
        "disposition": disposition,
        "state": str(raw.get("state") or ""),
        "first_seen_run": str(raw.get("first_seen_run") or run_id),
    }


def read_ledger(path: Path) -> list[dict]:
    """Read a JSONL ledger, skipping blank and malformed lines."""
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _key(row: dict) -> str:
    """Dedupe key: the game when known, else the URL."""
    return row.get("game_key") or row.get("url") or ""


def recall_metrics(rows: list[dict]) -> dict[str, Any]:
    """Compute recall over unique games.

    `eligible` is the denominator, `captured` the numerator. Rows without a
    `live_signal` are never eligible: a candidate that was not a live broadcast
    is not a miss.
    """
    by_key: dict[str, dict] = {}
    for row in rows:
        key = _key(row)
        if not key:
            continue
        bucket = by_key.setdefault(
            key,
            {
                "game_key": key,
                "live_signal": False,
                "created": False,
                "dispositions": set(),
                "backends": set(),
            },
        )
        if row.get("live_signal"):
            bucket["live_signal"] = True
        if row.get("disposition") == "created":
            bucket["created"] = True
        bucket["dispositions"].add(str(row.get("disposition") or ""))
        if row.get("backend"):
            bucket["backends"].add(str(row["backend"]))

    eligible = [b for b in by_key.values() if b["live_signal"]]
    captured = [b for b in eligible if b["created"]]
    unverifiable = [
        b for b in eligible if "unverifiable" in b["dispositions"] and not b["created"]
    ]
    missed = [b for b in eligible if not b["created"]]
    return {
        "rows": len(rows),
        "unique_games": len(by_key),
        "eligible": len(eligible),
        "captured": len(captured),
        "missed": len(missed),
        "unverifiable": len(unverifiable),
        "recall": round(len(captured) / len(eligible), 3) if eligible else None,
        "missed_keys": sorted(b["game_key"] for b in missed),
        "unverifiable_keys": sorted(b["game_key"] for b in unverifiable),
    }


def unseen_keys(ledger_rows: list[dict], fixtures: list[dict]) -> list[str]:
    """Fixture game_keys that appear in no ledger row — games nothing surfaced.

    Empty fixture `game_key`s are ignored rather than treated as unseen: a
    fixture that failed to normalise is a parsing problem, and counting it here
    would report a phantom missed game and quietly deflate recall.
    """
    known = {
        str(row.get("game_key") or "")
        for row in ledger_rows
        if isinstance(row, dict)
    }
    known.discard("")
    return sorted(
        key
        for key in (
            str(fixture.get("game_key") or "") for fixture in fixtures
            if isinstance(fixture, dict)
        )
        if key and key not in known
    )


def distinct_fixtures(fixtures: list[dict]) -> list[dict]:
    """One entry per non-empty `game_key`, first occurrence winning.

    A fixtures ledger is append-only and a daily job re-observes the same games,
    so the denominator has to be **distinct games**, not rows. Counting rows
    would make the metric depend on how long the file happens to be, and would
    let a duplicated parse inflate the numerator and denominator together.

    Entries with no `game_key` are dropped rather than kept: they failed to
    normalise, and a dropped entry must never be reported as a missed game.
    """
    seen: dict[str, dict] = {}
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        key = str(fixture.get("game_key") or "")
        if key:
            seen.setdefault(key, fixture)
    return list(seen.values())


def fixture_recall(ledger_rows: list[dict], fixtures: list[dict]) -> dict[str, Any]:
    """Recall measured against official fixtures instead of against ourselves.

    `surfaced` counts fixtures that at least one backend found, whether or not
    the game was eventually captured — this answers "did we even see it?", which
    is strictly harder than "did we capture what we saw?".

    The denominator is distinct games (see `distinct_fixtures`), and the two
    ways a row fails to become a game are reported separately because they mean
    opposite things:

    * `dropped` — no usable `game_key`: a parse failure. A rising number here is
      a parser regression, and it must never look like a smaller but healthier
      denominator.
    * `duplicates` — a `game_key` already seen: the same game observed again by
      a later run. Expected, and harmless.
    """
    usable = [
        fixture
        for fixture in fixtures
        if isinstance(fixture, dict) and str(fixture.get("game_key") or "")
    ]
    distinct = distinct_fixtures(fixtures)
    unseen = unseen_keys(ledger_rows, distinct)
    known = [
        fixture for fixture in distinct
        if str(fixture.get("game_key") or "") not in unseen
    ]
    return {
        "fixtures": len(distinct),
        "rows": len(fixtures),
        "dropped": len(fixtures) - len(usable),
        "duplicates": len(usable) - len(distinct),
        "surfaced": len(known),
        "unseen": len(unseen),
        "fixture_recall": (
            round(len(known) / len(distinct), 3) if distinct else None
        ),
        "unseen_keys": unseen,
    }


def select_retry(rows: list[dict], *, before_run: str | None = None) -> list[dict]:
    """Games that every render rung failed on and that were never captured.

    `before_run` limits the result to rows first seen earlier than that run, so a
    single run does not immediately re-try its own failures.
    """
    retryable: dict[str, dict] = {}
    captured: set[str] = set()
    for row in rows:
        key = _key(row)
        if not key:
            continue
        if row.get("disposition") == "created":
            captured.add(key)
    for row in rows:
        key = _key(row)
        if not key or key in captured:
            continue
        if row.get("disposition") != "unverifiable":
            continue
        if not row.get("live_signal"):
            continue
        first_seen = str(row.get("first_seen_run") or row.get("run_id") or "")
        if before_run and first_seen and first_seen >= before_run:
            continue
        retryable.setdefault(
            key,
            {
                "game_key": key,
                "url": str(row.get("url") or ""),
                "backend": str(row.get("backend") or ""),
                "first_seen_run": first_seen,
                "attempts": 0,
            },
        )
        retryable[key]["attempts"] += 1
    return sorted(retryable.values(), key=lambda item: item["game_key"])


def append_rows(rows: list[dict], ledger: Path) -> int:
    """Append normalised rows as JSONL. Returns the number written."""
    if not rows:
        return 0
    if ledger.parent and str(ledger.parent) not in ("", "."):
        ledger.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with ledger.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
    return written


def _load_rows(path: Path) -> list:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: candidates: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"FAIL: candidates: {path}: invalid JSON ({exc})", file=sys.stderr)
        sys.exit(2)
    if isinstance(payload, dict):
        payload = payload.get("candidates", payload.get("rows", []))
    if not isinstance(payload, list):
        print(
            'FAIL: candidates: input must be a list or {"candidates": [...]}',
            file=sys.stderr,
        )
        sys.exit(2)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Candidate ledger, recall metrics and re-try selection.",
    )
    parser.add_argument(
        "--ledger",
        default=DEFAULT_LEDGER,
        help=f"JSONL ledger path (default: {DEFAULT_LEDGER})",
    )

    def add_ledger(sub_parser):
        """Repeat --ledger on each subcommand with SUPPRESS, so the flag works
        both before and after the subcommand without the subparser's default
        clobbering the parent's value."""
        sub_parser.add_argument(
            "--ledger", default=argparse.SUPPRESS, metavar="LEDGER"
        )
        return sub_parser

    sub = parser.add_subparsers(dest="mode", required=True)

    append = add_ledger(
        sub.add_parser("append", help="append candidate rows to the ledger")
    )
    append.add_argument("--rows", required=True, help="JSON file of candidate rows")
    append.add_argument("--run-id", default="")
    append.add_argument("--ts", help="ISO-8601 timestamp override (for tests)")
    append.add_argument("--json", action="store_true")
    append.add_argument(
        "--dry-run", action="store_true", help="validate but write nothing"
    )
    append.add_argument(
        "--allow-invalid",
        action="store_true",
        help="skip rows that fail validation instead of exiting 2",
    )

    recall = add_ledger(sub.add_parser("recall", help="recall over unique games"))
    recall.add_argument("--json", action="store_true")
    recall.add_argument(
        "--fixtures",
        help="optional fixtures JSONL: also report recall against league fixtures",
    )

    unseen = add_ledger(
        sub.add_parser("unseen", help="fixture games no backend surfaced")
    )
    unseen.add_argument("--fixtures", required=True)
    unseen.add_argument("--json", action="store_true")

    retry = add_ledger(
        sub.add_parser("retry", help="list unverifiable games to re-try")
    )
    retry.add_argument("--before-run", default=None)
    retry.add_argument("--json", action="store_true")

    args = parser.parse_args()
    ledger = Path(args.ledger)

    if args.mode == "append":
        raw_rows = _load_rows(Path(args.rows))
        normalised: list[dict] = []
        skipped: list[str] = []
        for raw in raw_rows:
            try:
                normalised.append(
                    normalise_row(raw, ts=args.ts, run_id=args.run_id)
                )
            except ValueError as exc:
                if not args.allow_invalid:
                    print(
                        f"FAIL: candidates: {exc}", file=sys.stderr
                    )
                    sys.exit(2)
                skipped.append(str(exc))
        if args.dry_run:
            print(
                f"OK: candidates: dry-run, would append {len(normalised)} rows "
                f"to {ledger} (skipped {len(skipped)})"
            )
            return
        written = append_rows(normalised, ledger)
        print(
            f"OK: candidates: appended {written} rows to {ledger} "
            f"(skipped {len(skipped)})"
        )
        if args.json:
            print(json.dumps({"written": written, "skipped": skipped}, indent=2))
        return

    rows = read_ledger(ledger)

    if args.mode == "unseen":
        fixtures = read_ledger(Path(args.fixtures))
        if not fixtures:
            print(
                f"FAIL: candidates: no fixtures in {args.fixtures} — nothing to "
                "measure against (a fixture list is the only way to see a game "
                "no backend surfaced)",
                file=sys.stderr,
            )
            sys.exit(1)
        report = fixture_recall(rows, fixtures)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            for key in report["unseen_keys"]:
                print(f"UNSEEN {key}")
            print(
                "OK: candidates: fixtures={fixtures} surfaced={surfaced} "
                "unseen={unseen} fixture_recall={fixture_recall}".format(**report)
            )
        return

    if args.mode == "recall":
        metrics = recall_metrics(rows)
        fixtures = read_ledger(Path(args.fixtures)) if args.fixtures else []
        if args.json:
            if fixtures:
                metrics["fixture_recall"] = fixture_recall(rows, fixtures)
            print(json.dumps(metrics, indent=2))
        if metrics["eligible"] == 0:
            print(
                f"FAIL: candidates: no eligible games in {ledger} "
                f"({metrics['rows']} rows) — recall denominator is empty",
                file=sys.stderr,
            )
            sys.exit(1)
        if not args.json:
            print(
                "OK: candidates: recall={recall} captured={captured}/"
                "{eligible} missed={missed} unverifiable={unverifiable} "
                "unique_games={unique_games}".format(**metrics)
            )
            if fixtures:
                print(
                    "OK: candidates: fixtures={fixtures} surfaced={surfaced} "
                    "unseen={unseen} fixture_recall={fixture_recall}".format(
                        **fixture_recall(rows, fixtures)
                    )
                )
        return

    retryable = select_retry(rows, before_run=args.before_run)
    if args.json:
        print(json.dumps({"retry": retryable}, indent=2))
    for item in retryable:
        print(
            "OK: candidates: retry {game_key} (attempts={attempts}, "
            "first_seen={first_seen_run})".format(**item)
        )
    if not retryable:
        print(
            "FAIL: candidates: nothing to retry "
            f"({len(rows)} rows in {ledger})",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
