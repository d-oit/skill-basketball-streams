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


def _identities(row: dict) -> list[str]:
    """Every identity this row answers to, in preference order.

    A row can name a game (`game_key`) and a URL, and the two must be treated as
    the **same** game. They did not have to be: Phase 0 writes only the URL (it
    has surfaced a search hit, not a parsed fixture), while Phase 1/2 writes the
    `game_key` the agent decided on. Before this joined them, appending an
    outcome row for a game Phase 0 had already recorded opened a *second* bucket
    for it — so the fix for "recall is always 0" silently inflated the
    denominator instead, and both numbers moved in the direction that hides the
    bug. Measured on the 1322-row telemetry ledger: two outcome rows took
    `unique_games` 399 -> 401 and `eligible` 270 -> 272.
    """
    return [i for i in (row.get("game_key"), row.get("url")) if i]


def game_buckets(rows: list[dict]) -> dict[str, dict]:
    """Group rows into one bucket per game.

    A row names a game in up to two ways (`game_key` and `url`), and a Phase 0
    row names only the URL while a Phase 1/2 row usually names only the
    `game_key`. Bucketing on either alone therefore splits one game in two, and
    an outcome row for a game Phase 0 had already recorded opens a *second*
    bucket for it — so the fix for "recall is always 0" silently inflates the
    denominator instead. Measured on the 1322-row telemetry ledger: two outcome
    rows took `unique_games` 399 -> 401 and `eligible` 270 -> 272.

    A URL is only a *hint*, never proof. The Dyn free-games case in the run log
    is the counter-example that decides the rule: one pluto.tv channel page backs
    two different games, so unioning on it would merge them and let one game's
    `created` mark the other captured. A URL is therefore only unioned into a
    game when it is unambiguous — it never points at two different `game_key`s.
    """
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[left_root] = right_root

    rows = [row for row in rows if isinstance(row, dict)]
    # Which games does each URL claim to be? A URL naming two games is ambiguous
    # and joins nothing.
    games_per_url: dict[str, set[str]] = {}
    for row in rows:
        url = str(row.get("url") or "")
        game_key = str(row.get("game_key") or "")
        if url and game_key:
            games_per_url.setdefault(url, set()).add(game_key)

    tagged: list[tuple[str, dict]] = []
    for row in rows:
        game_key = str(row.get("game_key") or "")
        url = str(row.get("url") or "")
        primary = game_key
        if not primary:
            if not url:
                continue
            primary = url
        elif url and len(games_per_url.get(url, ())) == 1:
            # Only a URL that has never named a second game may join the two.
            union(f"i:{primary}", f"i:{url}")
        tagged.append((primary, row))

    buckets: dict[str, dict] = {}
    for primary, row in tagged:
        root = find(f"i:{primary}")
        bucket = buckets.setdefault(
            root,
            {
                "game_key": "",
                "url": "",
                "live_signal": False,
                "created": False,
                "dispositions": set(),
                "backends": set(),
            },
        )
        key = str(row.get("game_key") or "")
        row_url = str(row.get("url") or "")
        if key and not bucket["game_key"]:
            bucket["game_key"] = key
        if row_url and not bucket["url"]:
            bucket["url"] = row_url
        if row.get("live_signal"):
            bucket["live_signal"] = True
        if row.get("disposition") == "created":
            bucket["created"] = True
        bucket["dispositions"].add(str(row.get("disposition") or ""))
        if row.get("backend"):
            bucket["backends"].add(str(row["backend"]))
    for bucket in buckets.values():
        if not bucket["game_key"]:
            bucket["game_key"] = bucket["url"]
    # Keyed by the game's own name, not by the union-find root: the root is an
    # internal `i:…` tag, and returning it made `retry` list games by a
    # synthetic identifier instead of the `game_key` the rest of the toolchain
    # joins on. Re-keyed last, since the name a bucket ends up with is only known
    # once all its rows have been read.
    return {bucket["game_key"]: bucket for bucket in buckets.values()}


def recall_metrics(rows: list[dict]) -> dict[str, Any]:
    """Compute recall over unique games.

    `eligible` is the denominator, `captured` the numerator. Rows without a
    `live_signal` are never eligible: a candidate that was not a live broadcast
    is not a miss.

    Bucketing is by *game*, unioning rows that name the same game through
    different identities (see `_identities`) — so a Phase 1/2 outcome row joins
    the game Phase 0 already recorded instead of opening a second bucket for it,
    which would move the denominator as well as the numerator.
    """
    by_key = game_buckets(rows)

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
    buckets = game_buckets(rows)
    members: dict[str, list[dict]] = {}
    for row in rows:
        identities = _identities(row) if isinstance(row, dict) else []
        if not identities:
            continue
        game_key = str(row.get("game_key") or row.get("url") or "")
        for bucket in buckets.values():
            if game_key in (bucket["game_key"], bucket["url"]):
                members.setdefault(bucket["game_key"], []).append(row)

    retryable: dict[str, dict] = {}
    for game_key, bucket in buckets.items():
        if bucket["created"] or not bucket["live_signal"]:
            continue
        if "unverifiable" not in bucket["dispositions"]:
            continue
        # `before_run` is a decision about a *game*, not about one row: Phase 0
        # saw it earlier than the outcome row that would prove it captured, so
        # the earliest sighting is what "first seen" means here.
        seen = members.get(game_key, [])
        first_seen = min(
            (
                str(row.get("first_seen_run") or row.get("run_id") or "")
                for row in seen
            ),
            default="",
        )
        if before_run and first_seen and first_seen >= before_run:
            continue
        retryable[game_key] = {
            "game_key": game_key,
            "url": bucket["url"],
            "backend": sorted(bucket["backends"])[0] if bucket["backends"] else "",
            "first_seen_run": first_seen,
            "attempts": sum(
                1
                for row in seen
                if str(row.get("disposition") or "") == "unverifiable"
            ),
        }
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


def load_plan(path: Path) -> list[dict]:
    """The planner's rows, or `[]`. Raises `ValueError` on an unusable shape."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("plan") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError('plan must be a list or {"plan": [...]}')
    return [row for row in rows if isinstance(row, dict)]


def load_candidates(path: Path) -> list[dict]:
    """Phase 1/2's extracted candidates, or `[]`."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("candidates") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError('candidates must be a list or {"candidates": [...]}')
    return [row for row in rows if isinstance(row, dict)]


def _url_for(plan_row: dict, candidates: list[dict]) -> str:
    """The URL for this planned game, joined the way the planner joined it.

    A plan row deliberately carries no `url` — the planner is not a scraper, and
    an event body has no field for one — so the URL has to come from the
    candidates Phase 1/2 extracted.

    Matching on `game_key` alone is not enough, and the failure is silent: on the
    **update** path `upsert_events.py` deliberately replaces the candidate's key
    with the *existing event's* key, so the two spellings differ for exactly the
    games that already have an event. A `game_key`-only match then found nothing,
    the row was written with no URL, and it became a second game — recall read
    0.5 on a run that captured the only game there was. So fall back to the
    planner's own identity for the event, which is how it decided the match.
    """
    game_key = str(plan_row.get("game_key") or "")
    for candidate in candidates:
        if str(candidate.get("game_key") or "") == game_key and candidate.get("url"):
            return str(candidate["url"])
    teams = [str(t) for t in (plan_row.get("teams") or [])]
    start = str(plan_row.get("start") or "")
    for candidate in candidates:
        if not candidate.get("url"):
            continue
        if teams and [str(t) for t in (candidate.get("teams") or [])] != teams:
            continue
        if start and str(candidate.get("start") or "") != start:
            continue
        return str(candidate["url"])
    return ""


def outcome_rows(
    plan: list[dict],
    candidates: list[dict],
    *,
    applied: dict | None = None,
    run_id: str = "",
    ts: str | None = None,
) -> list[dict]:
    """One ledger row per planned game, carrying the outcome it reached.

    This is the writer `recall`'s numerator never had. Phase 0 labels every row
    `unverifiable` because it has only surfaced a search hit, and nothing in the
    runtime ever appended the row saying what the run decided — so `captured` was
    0 on every run for the life of the project, and the metric read as a measured
    total failure rather than a field nobody writes. Measured on the real
    telemetry ledger: 1322 rows, 1322 `unverifiable`, 0 `created`.

    Dispositions come from the planner's own action, never from the state:

    * `create`/`update` -> `created`: the game reached the calendar.
    * `skip` -> `skipped_duplicate`: it was already there, or an audit `WRONG`
      held it. A skip is **not** a miss, and it is not `unverifiable` either, so
      the game leaves the retry queue instead of being chased again.
    * `unchanged` -> `skipped_duplicate`: the game is on the calendar and the
      stored event already carries exactly what the planner would have sent, so
      no write was issued. That is the *same fact* as a duplicate — it is there
      and it is right — and mapping it to `unverifiable` (which the `else` used
      to do) would put an on-calendar game back in the retry queue and score it
      as unreached, which is the error this docstring's `rejected_auditWrong`
      rule exists to prevent in the other direction.
    * anything else -> `unverifiable`: unreached, and still retryable.

    A dry run records nothing: no event was written, so a `created` row would be
    a claim about a calendar that does not hold it.
    """
    if applied is not None and applied.get("dry_run"):
        return []
    rows: list[dict] = []
    for plan_row in plan:
        game_key = str(plan_row.get("game_key") or "")
        if not game_key:
            continue
        action = str(plan_row.get("action") or "")
        reason = str(plan_row.get("reason") or "")
        if action in ("create", "update"):
            disposition = "created"
        elif action == "skip":
            # The two skip reasons are not the same fact. "verified event
            # exists" is a duplicate — the game is on the calendar, and the
            # search pipeline did its job. "audit verdict WRONG stands" is the
            # audit *refusing* this game: it reached a calendar, was found paid
            # or never live, and a fresh guess must not put it back. Recording
            # both as `skipped_duplicate` would score a condemned game as a
            # success, which is the one thing §17's hard requirement forbids.
            disposition = (
                "rejected_auditWrong"
                if "WRONG" in reason
                else "skipped_duplicate"
            )
        elif action == "unchanged":
            # Already on the calendar, and the stored event already matches — the
            # same "it is there" fact as `skipped_duplicate`, and explicitly not
            # a fall-through to `unverifiable`, which is the retry queue.
            disposition = "skipped_duplicate"
        else:
            disposition = "unverifiable"
        rows.append(
            normalise_row(
                {
                    "run_id": run_id,
                    "url": _url_for(plan_row, candidates),
                    "backend": "agent",
                    "game_key": game_key,
                    # Deliberately NOT asserted here. `live_signal` is the
                    # eligibility flag, and the outcome row cannot know it: the
                    # agent planned a game, which is not the same claim as "this
                    # URL is a live broadcast", and Phase 0 is the job that
                    # observed the page. Setting it True would put a game into
                    # the denominator that no backend ever saw live — inflating
                    # eligible on a row that exists only to fill the numerator.
                    # The union means the Phase 0 row's own flag still counts.
                    "live_signal": False,
                    "disposition": disposition,
                    "state": str(plan_row.get("state") or ""),
                    "first_seen_run": run_id,
                    "ts": ts,
                },
                ts=ts,
                run_id=run_id,
            )
        )
    return rows


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

    record = add_ledger(
        sub.add_parser(
            "record",
            help="append the run's per-game outcome to the ledger (recall's "
            "numerator)",
        )
    )
    record.add_argument("--plan", required=True, help="planner output JSON")
    record.add_argument(
        "--candidates", help="Phase 1/2 candidates JSON, for the stored url"
    )
    record.add_argument(
        "--applied",
        help="apply result JSON; a dry run there records nothing",
    )
    record.add_argument("--run-id", default="")
    record.add_argument("--ts", help="ISO-8601 timestamp override (for tests)")
    record.add_argument("--json", action="store_true")
    record.add_argument(
        "--dry-run", action="store_true", help="validate but write nothing"
    )

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

    if args.mode == "record":
        try:
            plan = load_plan(Path(args.plan))
            candidates = (
                load_candidates(Path(args.candidates)) if args.candidates else []
            )
            applied = (
                json.loads(Path(args.applied).read_text(encoding="utf-8"))
                if args.applied
                else None
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"FAIL: candidates: {exc}", file=sys.stderr)
            sys.exit(2)
        rows_out = outcome_rows(
            plan, candidates, applied=applied, run_id=args.run_id, ts=args.ts
        )
        if args.dry_run:
            print(
                f"OK: candidates: dry-run, would append {len(rows_out)} outcome "
                f"row(s) to {ledger}"
            )
            return
        written = append_rows(rows_out, ledger)
        by_disposition: dict[str, int] = {}
        for row in rows_out:
            by_disposition[row["disposition"]] = (
                by_disposition.get(row["disposition"], 0) + 1
            )
        # A run that created nothing is a legitimate outcome and says so; a run
        # that *should* have created something and did not is the case this
        # writer exists to make visible, so the count is in the summary rather
        # than only in a file nobody opens.
        print(
            f"OK: candidates: recorded {written} outcome row(s) to {ledger} "
            f"{json.dumps(by_disposition, sort_keys=True)}"
        )
        if args.json:
            print(
                json.dumps(
                    {"written": written, "dispositions": by_disposition}, indent=2
                )
            )
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
