#!/usr/bin/env python3
"""metrics.py — derived telemetry snapshot for the runtime.

Reads the append-only JSONL streams from the telemetry branch and writes a
single `metrics.json`. It is **regenerable by construction**: deleting
`metrics.json` loses nothing, because every number in it is derived from
`candidates.jsonl`, `fixtures.jsonl` and `audit.jsonl`. That asymmetry is the
whole point of the split — a derived snapshot may be rewritten, the JSONL
streams may only be appended to.

The three metrics it reports measure three different failures, and they are
deliberately not collapsed into one score:

* **recall** — of the games a backend saw, how many did we capture?
* **fixture recall** — of the games that actually happened (official fixtures),
  how many did any backend even *see*? This is strictly harder than recall, and
  it is the only metric that can detect a systematically invisible source. It
  needs `fixtures.jsonl`, which is why the runtime persists it.
* **precision** — of the events the post-hoc audit could actually judge, how
  many were right? `INCONCLUSIVE` is excluded from the denominator: it is not
  evidence either way, and folding it in would let a quiet day look accurate.

Usage:
    python3 scripts/metrics.py --dest . --json
    python3 scripts/metrics.py --dest .tmp/telemetry --run-id 2026-09-14T08:30Z
    python3 scripts/metrics.py --candidates tests/fixtures/candidates_ledger.jsonl \\
        --fixtures tests/fixtures/league_fixtures.jsonl --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # direct CLI execution: `python3 scripts/metrics.py`
    from candidates import (  # noqa: E402
        fixture_recall,
        read_ledger,
        recall_metrics,
    )
except ImportError:  # imported as a package module, e.g. scripts.metrics
    from scripts.candidates import (  # type: ignore[no-redef]
        fixture_recall,
        read_ledger,
        recall_metrics,
    )

VERDICT_VERIFIED = "VERIFIED"
VERDICT_WRONG = "WRONG"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"

SNAPSHOT_NAME = "metrics.json"

# The telemetry artefacts this module reads. Kept beside the names it writes so
# a caller cannot rename one without seeing the other.
CANDIDATES_NAME = "candidates.jsonl"
FIXTURES_NAME = "fixtures.jsonl"
AUDIT_NAME = "audit.jsonl"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def audit_metrics(rows: list[dict]) -> dict:
    """Precision over the verdicts the audit could actually reach.

    The denominator is `verified + wrong`, **not** all rows. An audit that
    reached no verdict is silence, and counting silence as correctness is how a
    precision number starts flattering the thing it measures.
    """
    counts = {
        VERDICT_VERIFIED: 0,
        VERDICT_WRONG: 0,
        VERDICT_INCONCLUSIVE: 0,
    }
    for row in rows:
        verdict = str(row.get("verdict") or "").strip().upper()
        if verdict in counts:
            counts[verdict] += 1
    judged = counts[VERDICT_VERIFIED] + counts[VERDICT_WRONG]
    return {
        "rows": len(rows),
        "verified": counts[VERDICT_VERIFIED],
        "wrong": counts[VERDICT_WRONG],
        "inconclusive": counts[VERDICT_INCONCLUSIVE],
        "judged": judged,
        "precision": (
            round(counts[VERDICT_VERIFIED] / judged, 3) if judged else None
        ),
    }


def run_ids(candidates: list[dict], audit: list[dict]) -> list[str]:
    """Every `run_id` seen in either stream, ascending.

    `run_id` is an ISO-8601 UTC stamp produced by the workflow, so lexical order
    is chronological order. No timestamps are parsed and no clock is read, which
    is what keeps the trend reproducible from the log alone.
    """
    seen = {
        str(row.get("run_id") or "")
        for row in [*candidates, *audit]
        if isinstance(row, dict)
    }
    seen.discard("")
    return sorted(seen)


def trend(
    candidates: list[dict], audit: list[dict], *, limit: int = 14
) -> list[dict]:
    """Per-run progress, oldest to newest, capped at `limit` runs.

    Per-run recall answers "of the games this run saw, how many did this run
    capture?" — a different question from the cumulative figure, and the one
    that actually moves when a gate is tightened or loosened. A game is "seen"
    in a run when a row for it was appended with that `run_id`, and rows are
    appended per observation, so a game re-checked on a later day is seen (and
    counted) on that day too.

    Read it alongside the cumulative number, never instead of it: a run that
    captures 1 of its own 1 candidate scores 1.000 while failing to exist for
    the other nine games that were played.
    """
    rows: list[dict] = []
    for rid in run_ids(candidates, audit)[-limit:]:
        run_candidates = [
            row for row in candidates
            if isinstance(row, dict) and str(row.get("run_id") or "") == rid
        ]
        run_audit = [
            row for row in audit
            if isinstance(row, dict) and str(row.get("run_id") or "") == rid
        ]
        recall = recall_metrics(run_candidates)
        rows.append(
            {
                "run_id": rid,
                "rows": len(run_candidates),
                "unique_games": recall["unique_games"],
                "eligible": recall["eligible"],
                "captured": recall["captured"],
                "missed": recall["missed"],
                "recall": recall["recall"],
                "audit": audit_metrics(run_audit),
            }
        )
    return rows


# Block glyphs, low to high. Used for the trend sparklines.
BLOCKS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def sparkline(values: list, *, lo: float = 0.0, hi: float = 1.0) -> str:
    """A fixed-scale sparkline over a bounded ratio.

    The scale is deliberately absolute (0..1 by default) rather than min/max:
    normalising to the observed range would render `0.980` and `0.985` as a
    dramatic climb, which is exactly the misreading a trend chart is supposed to
    prevent. `None` becomes `·` so a gap is visible instead of interpolated.
    """
    out: list[str] = []
    for value in values:
        if value is None:
            out.append("\u00b7")
            continue
        if hi <= lo:
            out.append(BLOCKS[0])
            continue
        clamped = min(max(float(value), lo), hi)
        index = int(round((clamped - lo) / (hi - lo) * (len(BLOCKS) - 1)))
        out.append(BLOCKS[index])
    return "".join(out)


def render_trend(rows: list[dict]) -> str:
    """Aligned table plus two sparklines."""
    if not rows:
        return "trend: no runs recorded yet"

    header = (
        f"{'run':<22}{'rows':>5}{'games':>7}{'elig':>6}{'cap':>5}"
        f"{'recall':>8}{'judged':>7}{'wrong':>7}{'precis':>8}"
    )
    lines = [f"trend ({len(rows)} run(s), oldest first):", header]
    for row in rows:
        audit = row["audit"]
        lines.append(
            f"{row['run_id']:<22}{row['rows']:>5}{row['unique_games']:>7}"
            f"{row['eligible']:>6}{row['captured']:>5}"
            f"{fmt(row['recall']):>8}"
            f"{audit['judged']:>7}{audit['wrong']:>7}"
            f"{fmt(audit['precision']):>8}"
        )
    lines.append(f"{'recall':<10}{sparkline([r['recall'] for r in rows])}")
    lines.append(
        f"{'precision':<10}{sparkline([r['audit']['precision'] for r in rows])}"
    )
    lines.append(
        "scale is absolute 0..1; '\u00b7' means no judgement that run, not zero"
    )
    return "\n".join(lines)


def build_snapshot(
    candidates: list[dict],
    fixtures: list[dict],
    audit: list[dict],
    *,
    run_id: str = "",
    now: datetime | None = None,
    trend_limit: int = 14,
) -> dict:
    """Assemble the derived snapshot.

    Any of the three inputs may be empty; the corresponding metric is then
    `None` rather than `0.0`. A zero would read as "we measured this and it was
    bad", which is a different claim from "we have no data".
    """
    stamp = (now or utc_now()).astimezone(timezone.utc).isoformat()
    recall = recall_metrics(candidates)
    fixture = fixture_recall(candidates, fixtures) if fixtures else None
    return {
        "ts": stamp,
        "run_id": run_id,
        "derived": True,
        "source": {
            "candidates_rows": len(candidates),
            "fixtures_rows": len(fixtures),
            "audit_rows": len(audit),
        },
        "recall": recall,
        "fixture_recall": fixture,
        "audit": audit_metrics(audit),
        # Bounded history, so the snapshot is self-describing: an operator can
        # read the direction of travel without replaying the JSONL streams.
        "trend": trend(candidates, audit, limit=trend_limit),
    }


def write_snapshot(snapshot: dict, dest: Path) -> Path:
    """Write `metrics.json` atomically enough for a single-writer CI job."""
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / SNAPSHOT_NAME
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def fmt(value: object) -> str:
    """Fixed-width metric cell. `None` prints `n/a`, never `0`."""
    return "n/a" if value is None else f"{value:.3f}"


def render(snapshot: dict) -> str:
    """One-line summary. Missing metrics print `n/a`, never `0`."""
    pct = fmt

    recall = snapshot["recall"]
    fixture = snapshot["fixture_recall"]
    audit = snapshot["audit"]
    parts = [
        f"run={snapshot['run_id'] or 'unknown'}",
        f"recall={pct(recall['recall'])} ({recall['captured']}/{recall['eligible']})",
    ]
    if fixture is None:
        parts.append("fixture_recall=n/a (no fixtures persisted)")
    else:
        parts.append(
            f"fixture_recall={pct(fixture['fixture_recall'])} "
            f"({fixture['surfaced']}/{fixture['fixtures']})"
        )
    parts.append(
        f"precision={pct(audit['precision'])} "
        f"({audit['verified']}/{audit['judged']} judged, "
        f"{audit['inconclusive']} inconclusive)"
    )
    return "metrics: " + " ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Derive the runtime metrics snapshot from telemetry ledgers."
    )
    parser.add_argument(
        "--dest",
        default=".",
        help="directory holding the JSONL streams; metrics.json is written here",
    )
    parser.add_argument("--candidates", help="override the candidates JSONL path")
    parser.add_argument("--fixtures", help="override the fixtures JSONL path")
    parser.add_argument("--audit", help="override the audit JSONL path")
    parser.add_argument("--run-id", default="", help="run identifier to record")
    parser.add_argument("--now", help="ISO-8601 override (for tests)")
    parser.add_argument(
        "--trend",
        action="store_true",
        help="print the per-run trend instead of the one-line summary",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=14,
        help="how many runs the trend covers (default 14)",
    )
    parser.add_argument("--json", action="store_true", help="print the snapshot")
    parser.add_argument(
        "--dry-run", action="store_true", help="compute and print, write nothing"
    )
    args = parser.parse_args()

    dest = Path(args.dest)

    if args.now:
        try:
            now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        except ValueError:
            print(
                f"FAIL: metrics: --now {args.now!r} is not valid ISO-8601",
                file=sys.stderr,
            )
            raise SystemExit(2)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    else:
        now = None

    # `read_ledger` skips blank and malformed lines, so one bad row cannot take
    # the whole metric down — it is dropped, and `source.*_rows` shows the
    # surviving count rather than the file's line count.
    candidates = read_ledger(Path(args.candidates or dest / CANDIDATES_NAME))
    fixtures = read_ledger(Path(args.fixtures or dest / FIXTURES_NAME))
    audit = read_ledger(Path(args.audit or dest / AUDIT_NAME))

    if args.limit < 1:
        print(f"FAIL: metrics: --limit must be >= 1, got {args.limit}", file=sys.stderr)
        raise SystemExit(2)

    snapshot = build_snapshot(
        candidates,
        fixtures,
        audit,
        run_id=args.run_id,
        now=now,
        trend_limit=args.limit,
    )

    if args.dry_run:
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
        elif args.trend:
            print(render_trend(snapshot["trend"]))
            print("OK: metrics: dry-run, nothing written")
        else:
            print(f"OK: {render(snapshot)} (dry-run, nothing written)")
        return

    path = write_snapshot(snapshot, dest)
    if args.json:
        # Payload alone: the summary line goes to stderr so `--json` output stays
        # parseable by `json.load`. (Same rule as upsert_events/audit_events.)
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"OK: metrics: wrote {path}", file=sys.stderr)
    elif args.trend:
        print(render_trend(snapshot["trend"]))
        print(f"OK: metrics: wrote {path}")
    else:
        print(f"OK: {render(snapshot)}")
        print(f"OK: metrics: wrote {path}")


if __name__ == "__main__":
    main()
