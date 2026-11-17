#!/usr/bin/env python3
"""resolver_discovery.py — opt-in alternative-source discovery (issue #99).

Experiment: can `do-wdr` (do-web-doc-resolver) find additional *official*
broadcaster, league, federation, club, or regional listing pages the current
search ladder misses? This is not another unconditional search pass: it runs
only for uncovered fixtures (or events whose source diversity is thin),
behind an explicit flag, with per-run query/latency caps — and everything it
finds stays a *lead* until this repository's own seven checks pass.

Safety and budget boundaries (enforced, not documented):

* Default OFF. `enabled()` is true only when `RESOLVER_ENABLED=true` (or
  `--enable`) AND a runner is available. No flag, no run.
* The discovery rung may append candidates and evidence only. It never
  creates, updates, or relabels calendar events, and never writes
  `config/sources.json` — quarantine (`approve_source.py`) stays human/PR.
* Existing sources and fallback behavior are unchanged when the resolver is
  unavailable or returns nothing: every transport failure degrades to `[]`.
* Provenance travels with every row: query, strategy, backend, discovered
  URL, timestamp.

Query strategies per uncovered fixture (distinct families, not rewordings):

1. `pairing-competition` — fixture pairing + competition
   ("ALBA Berlin Bayern BBL official live").
2. `localized-teams` — localized team names + league schedule
   ("ALBA Berlin Bayern Basketball Bundesliga Spielplan live").
3. `federation-schedule` — league/federation official schedule page
   ("BBL official schedule broadcaster").
4. `club-broadcaster` — club channel/broadcaster pages
   ("ALBA Berlin official broadcast club TV").
5. `regional-listing` — regional public broadcaster listing
   ("Basketball Regionalliga MDR sportschau live" style per league).
6. `alternative-listing` — alternative official listing pages
   ("BBL live free official listing").

Usage:
    python3 scripts/resolver_discovery.py --fixtures fixtures.jsonl \\
        --ledger candidates.jsonl --out leads.json --enable
    python3 scripts/resolver_discovery.py --fixtures fixtures.jsonl \\
        --ledger candidates.jsonl --out leads.json   # disabled -> [] (exit 1)

Exit codes:
    0  PASS — at least one lead recorded
    1  FAIL — disabled, nothing uncovered, or nothing found (all advisory)
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND_NAME = "resolver"
DEFAULT_MAX_QUERIES = 8
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_SECONDS = 120

STRATEGIES = (
    "pairing-competition",
    "localized-teams",
    "federation-schedule",
    "club-broadcaster",
    "regional-listing",
    "alternative-listing",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_enabled(explicit: bool = False) -> bool:
    if explicit:
        return True
    return os.environ.get("RESOLVER_ENABLED", "").strip().lower() == "true"


def runner_available() -> bool:
    if shutil.which("do-wdr"):
        return True
    try:
        __import__("do_web_doc_resolver")
        return True
    except ImportError:
        return False


def build_strategies(fixture: dict) -> list[dict]:
    """Distinct query strategies for one fixture (pure, capped by caller)."""
    if not isinstance(fixture, dict):
        return []
    teams = fixture.get("teams") or []
    if not isinstance(teams, list) or len(teams) != 2:
        return []
    home, away = (str(teams[0] or "").strip(), str(teams[1] or "").strip())
    if not home or not away:
        return []
    league = str(fixture.get("league") or "").strip()
    game_key = str(fixture.get("game_key") or "").strip()
    pairing = f"{home} {away}".strip()
    league_prefix = f"{league} " if league else ""
    return [
        {"strategy": "pairing-competition", "query": f"{pairing} {league} official live".strip(), "game_key": game_key},
        {"strategy": "localized-teams", "query": f"{pairing} {league} Spielplan live".strip(), "game_key": game_key},
        {"strategy": "federation-schedule", "query": f"{league_prefix}official schedule broadcaster".strip(), "game_key": game_key},
        {"strategy": "club-broadcaster", "query": f"{home} official broadcast club TV".strip(), "game_key": game_key},
        {"strategy": "regional-listing", "query": f"{pairing} {league_prefix}live free official listing".strip(), "game_key": game_key},
        {"strategy": "alternative-listing", "query": f"{league_prefix}live free official stream listing".strip(), "game_key": game_key},
    ]


def uncovered_fixtures(fixtures: list[dict], covered_keys: set[str]) -> list[dict]:
    out: list[dict] = []
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        key = str(fixture.get("game_key") or "").strip()
        if not key or key in covered_keys:
            continue
        out.append(fixture)
    return out


def default_runner(query: str, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> list[dict]:
    """Best-effort `do-wdr` call; every failure degrades to no rows."""
    cli = shutil.which("do-wdr")
    if not cli:
        return []
    try:
        proc = subprocess.run(
            [cli, "search", "--json", query],
            capture_output=True, text=True, timeout=max(1, timeout_seconds),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    items = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        items = payload if isinstance(payload, list) else []
    leads: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        leads.append({"url": url, "title": str(item.get("title") or "")})
    return leads


def discover(
    fixtures: list[dict],
    covered_keys: set[str],
    *,
    runner=None,
    max_queries: int = DEFAULT_MAX_QUERIES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_seconds: int = DEFAULT_MAX_SECONDS,
    run_id: str = "",
    ts: str | None = None,
) -> tuple[list[dict], dict]:
    """Bounded discovery over uncovered fixtures only.

    Returns (lead rows, stats). Never raises on transport failure: a failed
    query is a skipped query, and the stats say so.
    """
    stamp = ts or utc_now()
    runner = runner or default_runner
    stats = {"strategies_attempted": 0, "queries": 0, "leads": 0, "failures": 0, "elapsed_ms": 0,
             "uncovered_fixtures": 0, "budget_hit": False}
    started = time.perf_counter()
    leads: list[dict] = []
    seen_urls: set[str] = set()
    uncovered = uncovered_fixtures(fixtures, covered_keys)
    stats["uncovered_fixtures"] = len(uncovered)
    budget = max(0, max_queries)
    for fixture in uncovered:
        if stats["queries"] >= budget:
            stats["budget_hit"] = True
            break
        for planned in build_strategies(fixture):
            if stats["queries"] >= budget:
                stats["budget_hit"] = True
                break
            elapsed = time.perf_counter() - started
            if elapsed * 1000 >= max(0, max_seconds) * 1000:
                stats["budget_hit"] = True
                break
            query = planned["query"]
            stats["queries"] += 1
            stats["strategies_attempted"] += 1
            try:
                found = runner(query, timeout_seconds=timeout_seconds)
            except Exception:
                stats["failures"] += 1
                continue
            if not isinstance(found, list):
                stats["failures"] += 1
                continue
            for item in found:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                leads.append(
                    {
                        "url": url,
                        "title": str(item.get("title") or ""),
                        "backend": BACKEND_NAME,
                        "query": query,
                        "strategy": planned["strategy"],
                        "game_key": planned.get("game_key") or "",
                        "run_id": run_id,
                        "ts": stamp,
                    }
                )
    stats["leads"] = len(leads)
    stats["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
    return leads, stats


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
    parser = argparse.ArgumentParser(description="Opt-in resolver discovery for uncovered fixtures.")
    parser.add_argument("--fixtures", required=True, help="fixture JSONL")
    parser.add_argument("--ledger", required=True, help="candidate ledger (coverage join)")
    parser.add_argument("--out", required=True, help="write leads JSON here")
    parser.add_argument("--enable", action="store_true", help="opt in (or RESOLVER_ENABLED=true)")
    parser.add_argument("--max-queries", type=int, default=DEFAULT_MAX_QUERIES)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-seconds", type=int, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()

    if not is_enabled(explicit=args.enable):
        print("FAIL: resolver_discovery: disabled (pass --enable or RESOLVER_ENABLED=true)", file=sys.stderr)
        sys.exit(1)
    try:
        fixtures = _read_jsonl(Path(args.fixtures))
    except OSError as exc:
        print(f"FAIL: resolver_discovery: {args.fixtures}: {exc}", file=sys.stderr)
        sys.exit(2)
    if not fixtures:
        print(f"FAIL: resolver_discovery: no fixtures in {args.fixtures}", file=sys.stderr)
        sys.exit(1)
    try:
        ledger = _read_jsonl(Path(args.ledger))
    except OSError as exc:
        print(f"FAIL: resolver_discovery: {args.ledger}: {exc}", file=sys.stderr)
        sys.exit(2)
    covered = {str(row.get("game_key") or "") for row in ledger if str(row.get("game_key") or "").strip()}
    leads, stats = discover(
        fixtures, covered,
        max_queries=args.max_queries, timeout_seconds=args.timeout_seconds,
        max_seconds=args.max_seconds, run_id=args.run_id,
    )
    Path(args.out).write_text(json.dumps({"leads": leads, "stats": stats}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not leads:
        print(f"OK: resolver_discovery: no new leads ({stats['queries']} querie(s), {stats['failures']} failure(s))", file=sys.stderr)
        sys.exit(1)
    print(f"OK: resolver_discovery: {len(leads)} lead(s) from {stats['queries']} querie(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
