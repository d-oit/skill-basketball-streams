#!/usr/bin/env python3
"""query_experiments.py — bounded missed-fixture query experiment cycle.

Issue #98: `scripts/queries.py --suggest` prints queries for fixtures the
ledger misses, but nothing persists them, nothing runs them as a capped
canary, and query identity is dropped by every downstream transform — so the
system can never tell which wording found a game the baseline missed.

This module closes that loop in four small, offline, stdlib-only steps.
Every step is a pure function over JSONL-able dicts plus a thin CLI; the
daily wiring (self-improve persists, runtime-daily selects + tags, scoring
reads the tags) stays in the existing scripts so this file has no calendar,
no network, and no credentials.

1. **persist** — turn `{game_key, queries}` suggestions + fixture context
   into experiment rows with a stable `query_id`, normalized query text,
   `game_key`, competition, query family + language, timestamp, miss reason,
   and `status: proposed`. Never touches the canonical base query list.
2. **select (canary)** — deterministic, capped (default 6), deduped against
   the base + fixture-expanded queries. Only `proposed`/`scheduled` rows
   are eligible. A canary row only ever adds candidates to the ledger.
3. **score** — carry query identity through outcomes: per `query_id`
   attempts, unique games, verified outcomes, blocked/error rate, and
   latency where recorded. `classify_outcome` maps every raw outcome row
   into exactly one of `blocked | empty | rejected | duplicate | verified
   | attempted` so blocked is never confused with "no result".
4. **propose (promotion)** — advisory only. Compares canary vs baseline on
   fixture recall + verified precision with a minimum-attempts floor.
   Writes proposal JSON; never modifies `config/sources.json` or calendar
   state — promotion stays a human/PR decision behind the existing
   approval and seven-check gates.

Telemetry stays append-only and bounded: callers append rows to
`query-experiments.jsonl` / `query-outcomes.jsonl` on the telemetry branch
and trim to the newest N rows. The self-improvement job holds no calendar
credentials.

Usage:
    python3 scripts/query_experiments.py persist --suggestions sugg.json \\
        --fixtures fixtures.jsonl --run-id 2026-11-17T08:30Z --out experiments.jsonl
    python3 scripts/query_experiments.py select --experiments experiments.jsonl \\
        --base queries.txt --expanded expanded.txt --cap 6 --out canary.json
    python3 scripts/query_experiments.py score --outcomes outcomes.jsonl --json
    python3 scripts/query_experiments.py propose --experiments experiments.jsonl \\
        --outcomes outcomes.jsonl --baseline-recall 0.60 --out proposals.json

Exit codes:
    0  PASS — rows written / selection made / score computed
    1  FAIL — nothing eligible (no proposed rows, no outcomes)
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("proposed", "scheduled", "attempted", "retained", "rejected")
ELIGIBLE = ("proposed", "scheduled")
CLASSIFICATIONS = ("blocked", "empty", "rejected", "duplicate", "verified", "attempted")

DEFAULT_CANARY_CAP = 6
MIN_PROMOTION_ATTEMPTS = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_query(query: str) -> str:
    return " ".join(str(query or "").split())


def detect_family(query: str) -> str:
    lowered = normalize_query(query).casefold()
    if "livestream" in lowered or "live stream" in lowered:
        return "livestream"
    if "live" in lowered:
        return "live"
    if "übertragung" in lowered or "transmisja" in lowered:
        return "broadcast-local"
    return "generic"


def detect_language(query: str) -> str:
    lowered = normalize_query(query).casefold()
    if "kostenlos" in lowered or "übertragung" in lowered or "heute" in lowered:
        return "de"
    if "transmisja" in lowered or "na żywo" in lowered:
        return "pl"
    return "en"


def stable_query_id(query: str, game_key: str, family: str) -> str:
    seed = f"{normalize_query(query).casefold()}\x00{game_key}\x00{family}"
    return "q_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def persist_suggestions(
    suggestions: list[dict],
    fixtures_by_key: dict[str, dict],
    *,
    run_id: str,
    ts: str | None = None,
    reason: str = "fixture not covered by candidate ledger",
) -> list[dict]:
    """One experiment row per suggested query, with stable identity.

    Duplicate (game_key, normalized query) pairs collapse to one row: the
    same suggestion re-emitted by the next run must not open a second
    experiment.
    """
    stamp = ts or utc_now()
    rows: list[dict] = []
    seen: set[str] = set()
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        game_key = str(item.get("game_key") or "").strip()
        if not game_key:
            continue
        queries = item.get("queries") or []
        if not isinstance(queries, list):
            continue
        fixture = fixtures_by_key.get(game_key, {})
        league = str(fixture.get("league") or item.get("league") or "").strip()
        for query in queries:
            if not isinstance(query, str) or not query.strip():
                continue
            normalized = normalize_query(query)
            family = detect_family(normalized)
            query_id = stable_query_id(normalized, game_key, family)
            if query_id in seen:
                continue
            seen.add(query_id)
            rows.append(
                {
                    "query_id": query_id,
                    "query": normalized,
                    "query_family": family,
                    "language": detect_language(normalized),
                    "game_key": game_key,
                    "league": league,
                    "teams": fixture.get("teams") or [],
                    "reason": reason,
                    "status": "proposed",
                    "run_id": run_id,
                    "ts": stamp,
                }
            )
    rows.sort(key=lambda r: (r["game_key"], r["query_id"]))
    return rows


def select_canary(
    experiments: list[dict],
    base_queries: list[str],
    expanded_queries: list[str],
    *,
    cap: int = DEFAULT_CANARY_CAP,
) -> list[dict]:
    """Capped, deterministic canary selection after the baseline pass.

    Dedup is casefolded against base + expanded + within the selection, so
    a canary query never re-asks what the baseline already asked. Only
    `proposed`/`scheduled` rows are eligible; ordering is by
    (game_key, query_id) so the same input always yields the same canary.
    """
    limit = max(0, cap)
    covered = {normalize_query(q).casefold() for q in list(base_queries) + list(expanded_queries)}
    eligible = [
        row
        for row in experiments
        if isinstance(row, dict)
        and row.get("status") in ELIGIBLE
        and isinstance(row.get("query"), str)
        and row["query"].strip()
    ]
    eligible.sort(key=lambda r: (str(r.get("game_key") or ""), str(r.get("query_id") or "")))
    selected: list[dict] = []
    for row in eligible:
        if len(selected) >= limit:
            break
        key = normalize_query(str(row["query"])).casefold()
        if not key or key in covered:
            continue
        covered.add(key)
        selected.append(row)
    return selected


def classify_outcome(row: dict) -> str:
    """Exactly one bucket per outcome row; blocked is never 'empty'.

    Precedence is deliberate: a transport failure means the search never
    ran, so it must not read as "no result". A verified game outranks a
    duplicate tag (the game was found), and a rejection outranks a mere
    attempt.
    """
    if not isinstance(row, dict):
        return "attempted"
    transport = str(row.get("transport") or row.get("outcome") or "").strip().casefold()
    if transport in ("blocked", "rate-limited", "rate_limited", "error", "timeout", "provider-failure"):
        return "blocked"
    if str(row.get("outcome") or "").strip().casefold() in ("blocked", "error"):
        return "blocked"
    if bool(row.get("verified")) or str(row.get("disposition") or "") == "created":
        return "verified"
    disposition = str(row.get("disposition") or "")
    if disposition.startswith("rejected"):
        return "rejected"
    if str(row.get("outcome") or "").strip().casefold() == "reject":
        return "rejected"
    if disposition == "skipped_duplicate" or bool(row.get("duplicate")):
        return "duplicate"
    try:
        result_count = int(row.get("result_count", 0) or 0)
    except (TypeError, ValueError):
        result_count = 0
    if result_count == 0 and not row.get("url") and not row.get("game_key"):
        return "empty"
    if result_count == 0:
        return "empty"
    return "attempted"


def score_queries(outcomes: list[dict]) -> dict[str, dict]:
    """Per-query_id aggregates: attempts, games, verified, blocked share."""
    scores: dict[str, dict] = {}
    for row in outcomes:
        if not isinstance(row, dict):
            continue
        query_id = str(row.get("query_id") or "").strip()
        if not query_id:
            continue
        bucket = classify_outcome(row)
        entry = scores.setdefault(
            query_id,
            {
                "query_id": query_id,
                "query": str(row.get("query") or ""),
                "attempts": 0,
                "unique_games": set(),
                "verified": 0,
                "blocked": 0,
                "empty": 0,
                "rejected": 0,
                "duplicate": 0,
                "latencies_ms": [],
            },
        )
        entry["attempts"] += 1
        game_key = str(row.get("game_key") or "").strip()
        if game_key:
            entry["unique_games"].add(game_key)
        if bucket in ("verified", "blocked", "empty", "rejected", "duplicate"):
            entry[bucket] += 1
        latency = row.get("latency_ms")
        if isinstance(latency, (int, float)) and latency >= 0:
            entry["latencies_ms"].append(float(latency))
    report: dict[str, dict] = {}
    for query_id, entry in scores.items():
        latencies = entry.pop("latencies_ms")
        attempts = entry["attempts"]
        report[query_id] = {
            **entry,
            "unique_games": len(entry["unique_games"]),
            "verified_precision": (entry["verified"] / attempts) if attempts else 0.0,
            "blocked_rate": (entry["blocked"] / attempts) if attempts else 0.0,
            "avg_latency_ms": (sum(latencies) / len(latencies)) if latencies else None,
        }
    return report


def propose_promotions(
    report: dict[str, dict],
    *,
    baseline_recall: float,
    baseline_precision: float = 0.0,
    min_attempts: int = MIN_PROMOTION_ATTEMPTS,
) -> list[dict]:
    """Advisory promotion proposals — data, never a write.

    A query is proposed only with enough independent attempts, at least one
    verified game, and precision at or above the baseline. The proposal
    names the query for a human/PR to adopt into the base list; it never
    modifies sources, queries, or calendar state itself.
    """
    proposals: list[dict] = []
    for query_id, stats in report.items():
        if not isinstance(stats, dict):
            continue
        attempts = int(stats.get("attempts") or 0)
        verified = int(stats.get("verified") or 0)
        precision = float(stats.get("verified_precision") or 0.0)
        if attempts < max(1, min_attempts) or verified < 1:
            continue
        if precision < baseline_precision:
            continue
        proposals.append(
            {
                "query_id": query_id,
                "query": stats.get("query") or "",
                "attempts": attempts,
                "unique_games": int(stats.get("unique_games") or 0),
                "verified": verified,
                "verified_precision": precision,
                "baseline_recall": baseline_recall,
                "action": "propose for base-list adoption via PR; no automatic change made",
            }
        )
    proposals.sort(key=lambda p: (-p["verified_precision"], -p["verified"], p["query_id"]))
    return proposals


def _read_jsonl(path: Path) -> list[dict]:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded missed-fixture query experiments.")
    sub = parser.add_subparsers(dest="command", required=True)

    persist = sub.add_parser("persist", help="persist suggestions as experiments")
    persist.add_argument("--suggestions", required=True, help="JSON {suggestions: [...]} or JSONL")
    persist.add_argument("--fixtures", required=True, help="fixture JSONL")
    persist.add_argument("--run-id", required=True)
    persist.add_argument("--ts", default=None)
    persist.add_argument("--reason", default="fixture not covered by candidate ledger")
    persist.add_argument("--out", required=True)

    select = sub.add_parser("select", help="select the capped canary set")
    select.add_argument("--experiments", required=True, help="experiments JSONL")
    select.add_argument("--base", default=None, help="base queries, one per line")
    select.add_argument("--expanded", default=None, help="expanded queries, one per line")
    select.add_argument("--cap", type=int, default=DEFAULT_CANARY_CAP)
    select.add_argument("--out", required=True)

    score = sub.add_parser("score", help="score per-query outcomes")
    score.add_argument("--outcomes", required=True, help="query outcome JSONL")
    score.add_argument("--out", default=None)
    score.add_argument("--json", action="store_true")

    propose = sub.add_parser("propose", help="advisory promotion proposals")
    propose.add_argument("--experiments", required=True)
    propose.add_argument("--outcomes", required=True)
    propose.add_argument("--baseline-recall", type=float, required=True)
    propose.add_argument("--baseline-precision", type=float, default=0.0)
    propose.add_argument("--min-attempts", type=int, default=MIN_PROMOTION_ATTEMPTS)
    propose.add_argument("--out", required=True)

    args = parser.parse_args()

    if args.command == "persist":
        sugg_path = Path(args.suggestions)
        try:
            raw = sugg_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"FAIL: query_experiments: {sugg_path}: {exc}", file=sys.stderr)
            sys.exit(2)
        suggestions: list[dict] = []
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict) and isinstance(payload.get("suggestions"), list):
                suggestions = [r for r in payload["suggestions"] if isinstance(r, dict)]
            else:
                raise ValueError("not a suggestions payload")
        except (ValueError, json.JSONDecodeError):
            suggestions = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    suggestions.append(row)
        try:
            fixture_rows = _read_jsonl(Path(args.fixtures))
        except OSError as exc:
            print(f"FAIL: query_experiments: {args.fixtures}: {exc}", file=sys.stderr)
            sys.exit(2)
        if not fixture_rows:
            print(f"FAIL: query_experiments: no fixtures in {args.fixtures}", file=sys.stderr)
            sys.exit(1)
        by_key = {str(f.get("game_key") or ""): f for f in fixture_rows if isinstance(f, dict)}
        rows = persist_suggestions(suggestions, by_key, run_id=args.run_id, ts=args.ts, reason=args.reason)
        if not rows:
            print("FAIL: query_experiments: nothing to persist", file=sys.stderr)
            sys.exit(1)
        Path(args.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        print(f"OK: query_experiments: persisted {len(rows)} experiment(s)", file=sys.stderr)
        return

    if args.command == "select":
        try:
            experiments = _read_jsonl(Path(args.experiments))
        except OSError as exc:
            print(f"FAIL: query_experiments: {args.experiments}: {exc}", file=sys.stderr)
            sys.exit(2)

        def _lines(path: str | None) -> list[str]:
            if not path:
                return []
            try:
                return [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
            except OSError as exc:
                print(f"FAIL: query_experiments: {path}: {exc}", file=sys.stderr)
                sys.exit(2)

        selected = select_canary(experiments, _lines(args.base), _lines(args.expanded), cap=args.cap)
        if not selected:
            print("FAIL: query_experiments: no eligible canary queries", file=sys.stderr)
            sys.exit(1)
        Path(args.out).write_text(json.dumps({"canary": selected}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"OK: query_experiments: selected {len(selected)} canary querie(s)", file=sys.stderr)
        return

    if args.command == "score":
        try:
            outcomes = _read_jsonl(Path(args.outcomes))
        except OSError as exc:
            print(f"FAIL: query_experiments: {args.outcomes}: {exc}", file=sys.stderr)
            sys.exit(2)
        if not outcomes:
            print("FAIL: query_experiments: no outcomes to score", file=sys.stderr)
            sys.exit(1)
        report = score_queries(outcomes)
        payload = json.dumps(report, ensure_ascii=False, indent=1)
        if args.out:
            Path(args.out).write_text(payload + "\n", encoding="utf-8")
        elif args.json:
            print(payload)
        else:
            for query_id, stats in sorted(report.items()):
                print(f"{query_id}: attempts={stats['attempts']} verified={stats['verified']} games={stats['unique_games']}")
        print(f"OK: query_experiments: scored {len(report)} querie(s)", file=sys.stderr)
        return

    if args.command == "propose":
        try:
            outcomes = _read_jsonl(Path(args.outcomes))
            experiments = _read_jsonl(Path(args.experiments))
        except OSError as exc:
            print(f"FAIL: query_experiments: {exc}", file=sys.stderr)
            sys.exit(2)
        report = score_queries(outcomes)
        known = {str(r.get("query_id") or "") for r in experiments if isinstance(r, dict)}
        report = {qid: stats for qid, stats in report.items() if qid in known}
        if not report:
            print("FAIL: query_experiments: no scored experiments to propose from", file=sys.stderr)
            sys.exit(1)
        proposals = propose_promotions(
            report,
            baseline_recall=args.baseline_recall,
            baseline_precision=args.baseline_precision,
            min_attempts=args.min_attempts,
        )
        Path(args.out).write_text(json.dumps({"proposals": proposals}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"OK: query_experiments: {len(proposals)} proposal(s) (advisory only)", file=sys.stderr)
        return


if __name__ == "__main__":
    main()
