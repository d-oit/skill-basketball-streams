#!/usr/bin/env python3
"""source_learning.py — Append-only run log, source scoring, new-source discovery.

The skill learns conservatively: it **never** promotes a new source into the
approved allow-list by itself. Instead it

1. `record` — appends one JSONL row per candidate stream/decision to
   `logs/run-log.jsonl`, giving every run an auditable history.
2. `score` — aggregates the log into a per-source hit rate, so search tiers can
   be reordered (a Tier 5 club site that hits 40% outranks a Tier 3 regional
   broadcaster that hits 0%).
3. `candidates` — extracts domains that produced search hits but are **not** in
   `config/sources.json` and writes them to `logs/source-candidates.json` with
   `"status": "quarantined"`. A human (or a PR) must approve a candidate before
   it is added to the allow-list.

Usage:
    python3 scripts/source_learning.py record --source magenta.tv \\
        --url https://www.magenta.tv/tv/live-basketball-12345 \\
        --outcome reject --reason "no matching free announcement"

    python3 scripts/source_learning.py score --root . --json
    python3 scripts/source_learning.py candidates --root . --dry-run

Exit codes:
    0  PASS — the requested mode completed
    1  FAIL — nothing to report / an entry could not be processed
    2  USAGE — bad arguments or unreadable files
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

LOG_RELATIVE_PATH = "logs/run-log.jsonl"
CANDIDATES_RELATIVE_PATH = "logs/source-candidates.json"
SOURCES_RELATIVE_PATH = "config/sources.json"

OUTCOMES = ("create", "skip", "reject", "blocked", "error")

# Domains that are search/aggregation plumbing, never promotable sources.
NON_SOURCE_DOMAINS = {
    "google.com", "www.google.com", "bing.com", "duckduckgo.com",
    "twitter.com", "x.com", "facebook.com", "instagram.com", "tiktok.com",
    "reddit.com", "wikipedia.org", "youtube.com", "www.youtube.com",
    "youtu.be", "googleusercontent.com", "github.com", "medium.com",
}


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        print(
            f"FAIL: source_learning: {value!r} is not a valid ISO-8601 timestamp",
            file=sys.stderr,
        )
        sys.exit(2)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def load_sources(root: Path) -> dict:
    """Load the approved-source registry (config/sources.json)."""
    path = root / SOURCES_RELATIVE_PATH
    if not path.is_file():
        return {"sources": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"FAIL: source_learning: {path}: invalid JSON ({exc})", file=sys.stderr
        )
        sys.exit(2)


def approved_domains(sources: dict) -> set[str]:
    """Every domain claimed by an approved source (including its aliases)."""
    domains: set[str] = set()
    for source in sources.get("sources", []):
        if not isinstance(source, dict):
            continue
        for key in ("domain", "domains", "social"):
            value = source.get(key)
            if isinstance(value, str):
                domains.add(value.lower())
            elif isinstance(value, list):
                domains.update(str(item).lower() for item in value)
    return domains


def read_log(log_path: Path) -> list[dict]:
    if not log_path.is_file():
        return []
    entries: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def resolve_log_path(args) -> Path:
    """`--dest DIR` writes run-log.jsonl inside DIR; otherwise use --log.

    `--dest` exists so the daily runtime can target a telemetry worktree
    (spec §10.4) without a second writer implementation.
    """
    dest = getattr(args, "dest", None)
    if dest:
        return Path(dest) / "run-log.jsonl"
    return Path(args.log)


def mode_record(args) -> int:
    log_path = resolve_log_path(args)
    if log_path.parent and str(log_path.parent) not in ("", "."):
        log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _parse_time(args.now).isoformat(),
        "run_id": args.run_id or "unassigned",
        "source": args.source,
        "tier": args.tier,
        "url": args.url or "",
        "outcome": args.outcome,
        "reason": args.reason or "",
    }
    # Optional runtime keys (spec §10.1). Only emitted when supplied, so rows
    # written before this change keep their exact shape.
    for key, value in (
        ("game_key", getattr(args, "game_key", None)),
        ("backend", getattr(args, "backend", None)),
        ("rung", getattr(args, "rung", None)),
        ("llm", getattr(args, "llm", None)),
        ("state", getattr(args, "state", None)),
        ("color_id", getattr(args, "color_id", None)),
        ("event_id", getattr(args, "event_id", None)),
    ):
        if value:
            entry[key] = value
    if args.dry_run:
        print(f"OK: source_learning: dry-run, would append: {json.dumps(entry)}")
        return 0
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"OK: source_learning: recorded {args.source} {args.outcome} -> {log_path}")
    return 0


def score_entries(entries: list[dict]) -> dict:
    """Aggregate a run log into per-source statistics."""
    stats: dict[str, dict] = {}
    for entry in entries:
        source = str(entry.get("source") or "unknown")
        bucket = stats.setdefault(
            source,
            {
                "source": source,
                "attempts": 0,
                "creates": 0,
                "rejects": 0,
                "skips": 0,
                "blocked": 0,
            },
        )
        bucket["attempts"] += 1
        outcome = str(entry.get("outcome") or "").lower()
        if outcome == "create":
            bucket["creates"] += 1
        elif outcome == "reject":
            bucket["rejects"] += 1
        elif outcome == "blocked":
            bucket["blocked"] += 1
        elif outcome == "skip":
            bucket["skips"] += 1
    for bucket in stats.values():
        attempts = bucket["attempts"] or 1
        bucket["hit_rate"] = round(bucket["creates"] / attempts, 3)
    return dict(
        sorted(stats.items(), key=lambda item: (-item[1]["hit_rate"], item[0]))
    )


def mode_score(args) -> int:
    root = Path(args.root).resolve()
    log_path = resolve_log_path(args) if (args.log or getattr(args, "dest", None)) else root / LOG_RELATIVE_PATH
    entries = read_log(log_path)
    stats = score_entries(entries)
    if not stats:
        print(
            f"FAIL: source_learning: no run log entries found at {log_path}",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(json.dumps(stats, indent=2))
        return 0
    for bucket in stats.values():
        print(
            "OK: source_learning: {source}: attempts={attempts} creates={creates} "
            "hit_rate={hit_rate}".format(**bucket)
        )
    return 0


def discover_candidates(
    entries: list[dict], approved: set[str]
) -> list[dict]:
    """Domains seen in the run log that are not approved and not plumbing."""
    seen: dict[str, dict] = {}
    for entry in entries:
        url = str(entry.get("url") or "")
        domain = _domain(url)
        if not domain or domain in approved or domain in NON_SOURCE_DOMAINS:
            continue
        if any(domain.endswith("." + ok) or domain == ok for ok in approved):
            continue
        bucket = seen.setdefault(
            domain,
            {"domain": domain, "status": "quarantined", "hits": 0, "examples": []},
        )
        bucket["hits"] += 1
        if url and url not in bucket["examples"] and len(bucket["examples"]) < 3:
            bucket["examples"].append(url)
    return sorted(seen.values(), key=lambda item: (-item["hits"], item["domain"]))


def mode_candidates(args) -> int:
    root = Path(args.root).resolve()
    log_path = (
        resolve_log_path(args)
        if (args.log or getattr(args, "dest", None))
        else root / LOG_RELATIVE_PATH
    )
    entries = read_log(log_path)
    approved = approved_domains(load_sources(root))
    candidates = discover_candidates(entries, approved)
    if not candidates:
        print("OK: source_learning: no new source candidates found")
        return 0
    out_path = (
        Path(args.out) if args.out else root / CANDIDATES_RELATIVE_PATH
    )
    for candidate in candidates:
        print(
            "OK: source_learning: candidate {domain}: hits={hits} "
            "(quarantined, requires manual approval)".format(**candidate)
        )
    if args.dry_run:
        print(f"OK: source_learning: dry-run, would write {out_path}")
        return 0
    if out_path.parent and str(out_path.parent) not in ("", "."):
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"candidates": candidates}, indent=2) + "\n", encoding="utf-8"
    )
    print(f"OK: source_learning: wrote {len(candidates)} candidates to {out_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run log, source scoring and new-source discovery.",
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    record = sub.add_parser("record", help="append one run-log entry")
    record.add_argument("--log", default=LOG_RELATIVE_PATH)
    record.add_argument(
        "--dest",
        help="directory to write run-log.jsonl into (overrides --log)",
    )
    record.add_argument("--game-key", dest="game_key")
    record.add_argument("--backend")
    record.add_argument("--rung")
    record.add_argument("--llm")
    record.add_argument("--state")
    record.add_argument("--color-id", dest="color_id")
    record.add_argument("--event-id", dest="event_id")
    record.add_argument("--run-id")
    record.add_argument("--source", required=True)
    record.add_argument("--tier", type=int, default=0)
    record.add_argument("--url", default="")
    record.add_argument("--outcome", choices=OUTCOMES, required=True)
    record.add_argument("--reason", default="")
    record.add_argument("--now", help="ISO-8601 timestamp (default: now)")
    record.add_argument("--dry-run", action="store_true")
    record.set_defaults(handler=mode_record)

    score = sub.add_parser("score", help="per-source hit rates")
    score.add_argument("--root", default=".")
    score.add_argument("--log")
    score.add_argument("--dest", help="directory containing run-log.jsonl")
    score.add_argument("--json", action="store_true")
    score.set_defaults(handler=mode_score)

    candidates = sub.add_parser(
        "candidates", help="discover unapproved source domains"
    )
    candidates.add_argument("--root", default=".")
    candidates.add_argument("--log")
    candidates.add_argument("--dest", help="directory containing run-log.jsonl")
    candidates.add_argument("--out")
    candidates.add_argument("--dry-run", action="store_true")
    candidates.set_defaults(handler=mode_candidates)

    args = parser.parse_args()
    sys.exit(args.handler(args))


if __name__ == "__main__":
    main()
