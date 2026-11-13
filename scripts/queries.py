#!/usr/bin/env python3
"""queries.py — fixture-driven search queries and missed-game suggestions.

Generic queries (``Basketball live stream kostenlos``) surface whatever the
index happens to rank. A fixture names the actual pairing and date, so a query
built from it (``ALBA Berlin Bayern live kostenlos``) asks the index for a game
that exists — which is how broadcaster pages (sportschau, MDR, Dyn) and league
announcements are found instead of hoped for.

Two modes, both offline and stdlib-only:

* **expand** (Phase 0 input): merge per-fixture queries into a base list,
  deduped, capped — so a 60-game window cannot buy 120 searches on a free tier.
* **suggest** (self-improvement input): for fixtures no ledger row covers,
  propose the queries that would have asked for them.

Usage:
    python3 scripts/queries.py --fixtures .tmp/fixtures.jsonl --base queries.txt
    python3 scripts/queries.py --suggest --fixtures fixtures.jsonl \\
        --ledger candidates.jsonl --json

Streams:
    stdout  payload only with `--json`, the query list otherwise
    stderr  human-readable progress

Exit codes:
    0  PASS — queries produced
    1  FAIL — no fixtures, or nothing uncovered to suggest
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_PER_GAME = 2
DEFAULT_CAP = 12


def _patterns(teams: list[str], league: str) -> list[str]:
    """Two query spellings per game: with and without the league name."""
    home, away = teams[0].strip(), teams[1].strip()
    queries = [f"{home} {away} basketball livestream kostenlos"]
    if league and league.strip():
        queries.append(f"{league.strip()} {home} {away} live stream")
    else:
        queries.append(f"{home} {away} live stream basketball")
    return queries


def expand_queries(
    base: list[str],
    fixtures: list[dict],
    *,
    per_game: int = DEFAULT_PER_GAME,
    cap: int = DEFAULT_CAP,
) -> list[str]:
    """Base queries first, then per-fixture queries, deduped and capped.

    The cap counts fixture-derived queries only: the base list is the
    operator's explicit intent and is never truncated.
    """
    seen = set()
    out: list[str] = []
    for query in base:
        cleaned = " ".join(str(query).split())
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    added = 0
    for fixture in fixtures:
        if added >= max(0, cap):
            break
        if not isinstance(fixture, dict):
            continue
        teams = fixture.get("teams") or []
        if not isinstance(teams, list) or len(teams) != 2:
            continue
        if not all(isinstance(t, str) and t.strip() for t in teams):
            continue
        for query in _patterns(teams, str(fixture.get("league") or ""))[
            : max(0, per_game)
        ]:
            if added >= max(0, cap):
                break
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(query)
            added += 1
    return out


def suggest_for_unseen(
    fixtures: list[dict],
    known_keys: set[str],
    *,
    per_game: int = 1,
    cap: int = DEFAULT_CAP,
) -> list[dict]:
    """One suggestion per fixture no ledger row covers: `{game_key, queries}`.

    `known_keys` are ledger `game_key`s. A fixture without a usable key is
    skipped — a suggestion that cannot be joined to the ledger is noise.
    """
    suggestions: list[dict] = []
    for fixture in fixtures:
        if len(suggestions) >= max(0, cap):
            break
        if not isinstance(fixture, dict):
            continue
        key = str(fixture.get("game_key") or "").strip()
        if not key or key in known_keys:
            continue
        teams = fixture.get("teams") or []
        if not isinstance(teams, list) or len(teams) != 2:
            continue
        if not all(isinstance(t, str) and t.strip() for t in teams):
            continue
        suggestions.append(
            {
                "game_key": key,
                "queries": _patterns(teams, str(fixture.get("league") or ""))[
                    : max(1, per_game)
                ],
            }
        )
    return suggestions


def load_fixture_rows(path: Path) -> list[dict]:
    """Fixture JSONL, tolerating a `{\"fixtures\": [...]}` wrapper per line."""
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"FAIL: queries: {path}: {exc}", file=sys.stderr)
        sys.exit(2)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(
            payload.get("fixtures"), list
        ):
            rows.extend(
                item for item in payload["fixtures"] if isinstance(item, dict)
            )
        elif isinstance(payload, dict):
            rows.append(payload)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fixture-driven search queries and missed-game suggestions."
    )
    parser.add_argument("--fixtures", help="fixture JSONL (scripts/fixtures.py output)")
    parser.add_argument("--base", help="base queries, one per line")
    parser.add_argument("--ledger", help="candidate ledger for --suggest")
    parser.add_argument("--suggest", action="store_true",
                        help="suggest queries for fixtures the ledger misses")
    parser.add_argument("--per-game", type=int, default=DEFAULT_PER_GAME)
    parser.add_argument("--cap", type=int, default=DEFAULT_CAP)
    parser.add_argument("--out", help="write queries/suggestions here (default: stdout)")
    parser.add_argument("--json", action="store_true",
                        help="payload on stdout as JSON")
    args = parser.parse_args()

    if not args.fixtures:
        print("FAIL: queries: --fixtures is required", file=sys.stderr)
        sys.exit(2)
    fixtures = load_fixture_rows(Path(args.fixtures))
    if not fixtures:
        print(
            f"FAIL: queries: no fixture rows in {args.fixtures}", file=sys.stderr
        )
        sys.exit(1)

    if args.suggest:
        if not args.ledger:
            print("FAIL: queries: --suggest needs --ledger", file=sys.stderr)
            sys.exit(2)
        known: set[str] = set()
        try:
            for line in Path(args.ledger).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and str(row.get("game_key") or "").strip():
                    known.add(str(row["game_key"]).strip())
        except OSError as exc:
            print(f"FAIL: queries: {args.ledger}: {exc}", file=sys.stderr)
            sys.exit(2)
        suggestions = suggest_for_unseen(
            fixtures, known, per_game=args.per_game, cap=args.cap
        )
        if not suggestions:
            print(
                "FAIL: queries: every fixture is already covered by the ledger",
                file=sys.stderr,
            )
            sys.exit(1)
        payload = json.dumps({"suggestions": suggestions}, ensure_ascii=False, indent=1)
        if args.out:
            Path(args.out).write_text(payload + "\n", encoding="utf-8")
        elif args.json:
            print(payload)
        else:
            for item in suggestions:
                print(f"{item['game_key']}: {'; '.join(item['queries'])}")
        print(
            f"OK: queries: {len(suggestions)} suggestion(s) for "
            f"{len(fixtures)} fixture(s)",
            file=sys.stderr,
        )
        return

    base: list[str] = []
    if args.base:
        try:
            base = [
                line.strip()
                for line in Path(args.base).read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        except OSError as exc:
            print(f"FAIL: queries: {args.base}: {exc}", file=sys.stderr)
            sys.exit(2)
    queries = expand_queries(
        base, fixtures, per_game=args.per_game, cap=args.cap
    )
    if args.out:
        Path(args.out).write_text(
            "\n".join(queries) + "\n", encoding="utf-8"
        )
    elif args.json:
        print(json.dumps({"queries": queries}, ensure_ascii=False, indent=1))
    else:
        print("\n".join(queries))
    base_keys = {" ".join(str(q).split()).casefold() for q in base}
    derived = sum(1 for q in queries if q.casefold() not in base_keys)
    print(
        f"OK: queries: {len(queries)} querie(s) "
        f"({derived} fixture-derived) from {len(fixtures)} fixture(s)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
