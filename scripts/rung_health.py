#!/usr/bin/env python3
"""rung_health.py — per-rung render health across runs (spec §10 `rungs.json`).

`RungHealth` in `scripts/render_ladder.py` parks a rung after three consecutive
failures so a dead backend does not burn the whole ladder on every candidate —
but it is in memory, so the parking dies with the process. That made two
promises untrue:

    live-stream-runtime-spec.md §10     telemetry/rungs.json — per-rung health
    live-stream-runtime-spec.md §18.4   "a rung parked for N consecutive days
                                        raises an issue (the GitHub Models
                                        lesson: backends die permanently)"

Nothing persisted rung outcomes, so a backend could be retired — as GitHub
Models was on 2026-07-30 — and the only trace was one `failed` line in one
morning's log.

This script is the missing writer, and it keeps the two halves apart exactly as
§10.3 requires:

    rung-attempts.jsonl     append-only ledger, one row per rung per URL per run
    rungs.json              DERIVED snapshot, rewritten each run, safe to delete

Two properties are worth more than the feature:

* **A page body never reaches the ledger.** `render_ladder --probe --json` dumps
  every attempt's `text` and `html`, which on a YouTube page is >1 MB. Rows here
  are built from named fields only, so a run costs a few hundred bytes instead
  of megabytes and the ledger can be committed forever.
* **Availability is not failure.** A rung with no key or no package is `skipped`,
  which breaks a strike streak rather than extending it — otherwise every run in
  a keyless repository would "park" the hosted rungs, and installing a key later
  would start from a fake history.
* **The target's WAF is not our bug.** Only `fileable` failures accrue a
  filable streak: a hosted rung failing (any kind of failure — a provider
  rejecting the key is the retired-backend case), or a local rung failing to
  connect at all. A local rung that was *refused* is recorded in the snapshot
  and never filed, because the ladder treats a 403 as "climb", not "broken".

Usage:
    python3 scripts/rung_health.py probe --dest .tmp/telemetry \\
        --run-id 2026-09-15T08:30Z --url https://www.magentasport.de/
    python3 scripts/rung_health.py snapshot --dest .tmp/telemetry
    python3 scripts/rung_health.py parked --dest .tmp/telemetry --days 3
    python3 scripts/rung_health.py parked --dest .tmp/telemetry --days 3 --file

Exit codes:
    0  PASS — recorded, printed, or the finding was reported/filed
    1  FAIL — a rung is parked for N days and the issue could not be filed
    2  USAGE — bad arguments, no URL, or an unreadable ledger
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render_ladder  # noqa: E402
from gh_issue import file_or_comment, run_gh, run_url  # noqa: E402
# `attempt_state` comes from render_ladder rather than being reimplemented
# here: the report a human reads and the ledger the telemetry branch keeps must
# not be able to disagree about what an attempt was.
from render_ladder import (  # noqa: E402
    MAX_STRIKES,
    RungHealth,
    attempt_state,
    build_ladder,
    fetch_with_ladder,
    host_of,
)

LEDGER_NAME = "rung-attempts.jsonl"
SNAPSHOT_NAME = "rungs.json"

# The §18.4 default: three consecutive parked days is a dead backend, not a bad
# afternoon. Deliberately the same number as MAX_STRIKES, so an operator has one
# number to remember and the two cannot drift apart in a doc somewhere.
PARKED_DAYS = 3

# The stable half of the issue title, and the dedupe key (§10 noise policy): no
# date and no rung name. One issue per finding, commented on while it persists.
TITLE_MARKER = "[rungs] render rung parked"

# `blocked` counts as a failure because a 403 from a hosted API means the key
# was rejected — the rung is dead *for us* even when the page is fine. This
# matches `RungHealth.record` exactly, so the ledger and the in-run tracker
# cannot disagree about what a strike is.
FAILURE_STATES = ("blocked", "failed")

# Every rung's kind, so a row is self-describing without importing the registry
# in the reader. A row written before this field existed has no kind and is
# judged by its state alone.
KIND_OF = {name: rung.kind for name, rung in render_ladder.ALL_RUNGS.items()}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(moment: datetime | None = None) -> str:
    return (moment or utc_now()).isoformat(timespec="seconds")


def default_run_id() -> str:
    """The same shape the workflows pass: `2026-09-15T08:30Z`."""
    return utc_now().strftime("%Y-%m-%dT%H:%MZ")


def fileable(row: dict) -> bool:
    """Is this failure a finding about *our* stack rather than the target's WAF?

    A hosted rung failing is a finding either way: `blocked` means the provider
    rejected the credential, which is the retired-backend case §18.4 exists for.

    A **local** rung being `blocked` is a statement about the target — the same
    403 the ladder is explicitly designed to climb past — and filing an issue
    every time a CDN tightens its rules is how a tracker becomes noise nobody
    reads. A local rung whose state is `failed` *is* about us: it could not
    connect at all, so it is broken rather than refused.
    """
    if row.get("kind") == "hosted":
        return True
    return row.get("state") == "failed"


def attempt_rows(
    *, run_id: str, url: str, attempts, tracker: RungHealth, ts: str
) -> list[dict]:
    """One ledger row per attempt. Named fields only — never `result.__dict__`.

    `strikes` and `parked` are the tracker's verdict *after* this attempt, so
    the ledger records why a rung stopped being tried instead of leaving that to
    be re-derived from a later reading of the same events.
    """
    host = host_of(url)
    rows = []
    for result in attempts:
        rows.append(
            {
                "ts": ts,
                "run_id": run_id,
                "url": url,
                "host": host,
                "rung": result.rung,
                "kind": KIND_OF.get(result.rung, ""),
                "state": attempt_state(result),
                "status": result.status,
                "elapsed_ms": result.elapsed_ms,
                "error": (result.error or "")[:300],
                "strikes": tracker.strikes.get(result.rung, 0),
                "parked": tracker.is_parked(result.rung),
            }
        )
    return rows


def read_ledger(dest: Path) -> list[dict]:
    """Every row, in file order. A malformed line is skipped, not fatal."""
    path = Path(dest) / LEDGER_NAME
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _key(row: dict) -> tuple:
    return (row.get("run_id"), row.get("rung"), row.get("url"))


def append_rows(dest: Path, rows: list[dict]) -> int:
    """Append the rows not already in the ledger. Returns the number added.

    Idempotent by `(run_id, rung, url)`: a retried workflow run must not inflate
    the history it is recording, and a duplicate would read as a second
    observation on the same day.
    """
    path = Path(dest) / LEDGER_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = {_key(row) for row in read_ledger(dest)}
    fresh = [row for row in rows if _key(row) not in seen]
    if fresh:
        with path.open("a", encoding="utf-8") as handle:
            for row in fresh:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(fresh)


def _consecutive_failures(run_rows: list[dict], rung: str) -> int:
    """Strikes for one rung within one run, mirroring `RungHealth.record`."""
    strikes = 0
    for row in run_rows:
        if row.get("rung") != rung:
            continue
        if row.get("state") in FAILURE_STATES:
            strikes += 1
        else:
            strikes = 0
    return strikes


def build_snapshot(
    rows: list[dict], *, run_id: str, ts: str, max_strikes: int = MAX_STRIKES
) -> dict:
    """The derived `rungs.json`: current state, not history (§10.3)."""
    latest = [row for row in rows if row.get("run_id") == run_id]
    rungs: dict[str, dict] = {}
    for row in rows:
        name = row.get("rung") or ""
        entry = rungs.setdefault(
            name,
            {
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "skipped": 0,
                "ok_no_evidence": 0,
                "consecutive_failures": 0,
                "parked": False,
                "last_state": None,
                "last_error": "",
                "last_seen": None,
                "last_host": "",
            },
        )
        state = row.get("state")
        entry["attempts"] += 1
        if state == "evidence":
            entry["successes"] += 1
        elif state in FAILURE_STATES:
            entry["failures"] += 1
        elif state == "skipped":
            entry["skipped"] += 1
        elif state == "ok-no-evidence":
            entry["ok_no_evidence"] += 1
        if (row.get("ts") or "") >= (entry["last_seen"] or ""):
            entry["last_state"] = state
            entry["last_error"] = row.get("error") or ""
            entry["last_seen"] = row.get("ts")
            entry["last_host"] = row.get("host") or ""
    for name, entry in rungs.items():
        entry["consecutive_failures"] = _consecutive_failures(latest, name)
        # The recorded verdict is the truth here, not a re-derivation: an
        # attempt made while the rung was already parked carries `parked`, and
        # that is what stopped the ladder trying it.
        last = [row for row in latest if row.get("rung") == name]
        entry["parked"] = bool(last and last[-1].get("parked"))
        entry["max_strikes"] = max_strikes
    return {
        "ts": ts,
        "run_id": run_id,
        "derived": True,
        "max_strikes": max_strikes,
        "ledger_rows": len(rows),
        "parked": sorted(name for name, entry in rungs.items() if entry["parked"]),
        "rungs": rungs,
    }


def write_snapshot(dest: Path, snapshot: dict) -> Path:
    path = Path(dest) / SNAPSHOT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def day_of(ts: str | None) -> str:
    """The UTC calendar day of a timestamp (`2026-09-15`), or `''`."""
    return (ts or "")[:10]


def last_run_per_day(rows: list[dict]) -> dict[str, list[dict]]:
    """Per day, the rows of that day's LAST run, in order.

    Last-wins is the honest reading for a cross-day signal: a rung parked at
    08:00 and working at 20:00 is fixed, and treating the day as parked would
    keep claiming a dead backend that has already been replaced.
    """
    runs: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        day = day_of(row.get("ts"))
        if not day:
            continue
        runs.setdefault(day, {}).setdefault(row.get("run_id") or "", []).append(row)
    result = {}
    for day, by_run in runs.items():
        # Run ids are named after their timestamp (`2026-09-15T08:30Z`), so the
        # lexicographic maximum is the latest run.
        latest = max(by_run)
        result[day] = sorted(by_run[latest], key=lambda r: r.get("ts") or "")
    return result


def _missing_days(before: str, after: str) -> list[str]:
    """Calendar days strictly between two days, newest first.

    Used only to *explain* a broken streak: the operator needs to tell "the
    backend is dead" from "the workflow did not run", and those two look
    identical in a table of parked days alone.
    """
    cursor = datetime.strptime(after, "%Y-%m-%d")
    gaps = []
    while True:
        cursor = cursor.fromordinal(cursor.toordinal() - 1)
        day = cursor.strftime("%Y-%m-%d")
        if day <= before:
            break
        gaps.append(day)
    return sorted(gaps, reverse=True)


def parked_streaks(rows: list[dict]) -> dict[str, dict]:
    """Consecutive parked days per rung, ending at that rung's LATEST day.

    Two rules make this trustworthy, and each fixes a way of being wrong:

    * The streak ends at the most recent day the rung was **observed**, not at
      the most recent day it was parked. Otherwise a rung that was fixed today
      keeps reporting the streak it had last week, forever, and the issue never
      closes.
    * `consecutive` is claimed only where the ladder was observed: a calendar
      day with no rows for that rung **breaks** the streak, because a gap is not
      evidence of anything.

    Only `fileable` failures count (see there): a local rung refused by a WAF is
    recorded in `rungs.json` and deliberately never filed.
    """
    per_day = last_run_per_day(rows)
    parked_days: dict[str, dict[str, str]] = {}
    observed: dict[str, set[str]] = {}
    for day, day_rows in per_day.items():
        last_by_rung: dict[str, dict] = {}
        for row in day_rows:
            last_by_rung[row.get("rung") or ""] = row
        for rung, row in last_by_rung.items():
            observed.setdefault(rung, set()).add(day)
            if row.get("parked") and fileable(row):
                parked_days.setdefault(rung, {})[day] = row.get("error") or ""

    streaks: dict[str, dict] = {}
    for rung, days in parked_days.items():
        seen = sorted(observed.get(rung, set()), reverse=True)
        if not seen or seen[0] not in days:
            # Working now, or nothing to say: a streak that ended days ago is not
            # a current finding.
            continue
        streak = [seen[0]]
        cursor = datetime.strptime(seen[0], "%Y-%m-%d")
        for day in seen[1:]:
            previous = cursor.fromordinal(cursor.toordinal() - 1).strftime("%Y-%m-%d")
            if day != previous or day not in days:
                break
            streak.append(day)
            cursor = datetime.strptime(day, "%Y-%m-%d")
        earlier = [day for day in sorted(observed[rung]) if day < streak[-1]]
        latest = [row for row in rows if row.get("rung") == rung and row.get("parked")]
        streaks[rung] = {
            "streak": len(streak),
            "as_of": streak[0],
            "parked_days": sorted(streak, reverse=True),
            "last_error": days[streak[0]],
            "host": (latest[-1].get("host") if latest else ""),
            "gaps": _missing_days(max(earlier), streak[-1]) if earlier else [],
        }
    return streaks


def probe(
    urls: list[str],
    *,
    dest: Path,
    run_id: str,
    timeout: float = render_ladder.DEFAULT_TIMEOUT,
    tracker: RungHealth | None = None,
    ladder_factory=None,
    dry_run: bool = False,
    ts: str | None = None,
) -> list[dict]:
    """Climb the ladder for every URL with ONE tracker, then persist.

    One tracker for the whole run is the point: `render_ladder --probe` builds a
    fresh `RungHealth` per invocation, so three hosts each failing once would
    never reach the three strikes that park a dead backend.
    """
    stamp = ts or utc_iso()
    state = tracker or RungHealth()
    factory = ladder_factory or (lambda url: build_ladder(url, allow_opt_in=False))
    rows: list[dict] = []
    for url in urls:
        _, attempts = fetch_with_ladder(
            url, rungs=factory(url), timeout=timeout, health=state
        )
        rows.extend(
            attempt_rows(
                run_id=run_id, url=url, attempts=attempts, tracker=state, ts=stamp
            )
        )
    if not dry_run:
        append_rows(dest, rows)
        write_snapshot(
            dest, build_snapshot(read_ledger(dest), run_id=run_id, ts=stamp)
        )
    return rows


def render_probe(rows: list[dict]) -> str:
    """One line per attempt, plus the parked finding when there is one."""
    lines = [
        f"OK: rung_health: {row['rung']} on {row['host']}: {row['state']} "
        f"(status={row['status']}, {row['elapsed_ms']}ms, strikes={row['strikes']})"
        f" {row['error']}"
        for row in rows
    ]
    parked = sorted({row["rung"] for row in rows if row["parked"]})
    for name in parked:
        lines.append(
            f"PARKED: rung_health: {name} reached {MAX_STRIKES} consecutive "
            "failures — not tried again for the rest of this run"
        )
    if not parked:
        lines.append("OK: rung_health: no rung was parked in this run")
    return "\n".join(lines)


def observed_rungs(rows: list[dict]) -> int:
    """How many rungs were tried on the latest day with data.

    Deliberately not `len(streaks)`: that is the number of rungs with a
    *current* parked streak, so an all-clear summary would report "0 rungs
    observed" — a number that reads as "nothing was tried" when four backends
    answered. A count is only worth printing if the reader can act on it.
    """
    per_day = last_run_per_day(rows)
    if not per_day:
        return 0
    return len({row.get("rung") for row in per_day[max(per_day)]})


def build_markdown(
    streaks: dict[str, dict], *, days: int, as_of: str = "", observed: int = 0
) -> str:
    """The job-summary view. One renderer for the artifact, not two."""
    parked = {rung: item for rung, item in streaks.items() if item["streak"] >= days}
    lines = [
        "| | |",
        "|---|---|",
        f"| Rungs tried on the latest day | {observed} |",
        f"| Parked {days}+ consecutive days | {len(parked)} |",
        f"| Latest day with data | {as_of or 'none'} |",
        "",
    ]
    if not parked:
        lines += [
            f"No rung has been parked for {days} consecutive observed days. A day",
            "with no rows is not a parked day and does not extend a streak — a gap",
            "is not evidence.",
        ]
        return "\n".join(lines)
    lines += [
        "| Rung | Consecutive parked days | Last error |",
        "|---|---|---|",
    ]
    for rung, item in sorted(parked.items()):
        lines.append(
            f"| `{rung}` | {item['streak']} | {item['last_error'] or '(none)'} |"
        )
    lines += [
        "",
        "A backend can be retired without notice — GitHub Models was, on",
        "2026-07-30. Replace the rung or restore its credential; never edit the",
        "ledger, which is the evidence.",
    ]
    return "\n".join(lines)


def build_title() -> str:
    return TITLE_MARKER


def build_body(streaks: dict[str, dict], *, days: int, as_of: str = "") -> str:
    """The issue body: which rungs, why each state matters, what to do."""
    parked = sorted(
        ((rung, item) for rung, item in streaks.items() if item["streak"] >= days),
        key=lambda pair: pair[1]["streak"],
        reverse=True,
    )
    rows = "\n".join(
        f"| `{rung}` | {item['streak']} | {item['host'] or '(unknown)'} | "
        f"{item['as_of']} | `{'; '.join(item['parked_days'])}` | "
        f"{item['last_error'] or '(none)'} |"
        for rung, item in parked
    )
    gaps = "\n".join(
        f"* `{rung}` — observed days before this streak that are not in it: "
        f"`{'; '.join(item['gaps'])}`"
        for rung, item in parked
        if item.get("gaps")
    )
    return f"""The daily `runtime-daily` run probes the render ladder and
`scripts/rung_health.py` recorded **{len(parked)} rung(s) parked for {days} or
more consecutive observed days**.

A parked rung is not tried again for the rest of the run, so while it stays
parked every candidate that needs it is fetched by a weaker rung or by none at
all — which surfaces as *missing games*, not as an error message.

| Rung | Consecutive parked days | Host | Latest day | Days | Last error |
|---|---|---|---|---|---|
{rows}

Gaps in the evidence, if any. A day with no rows **breaks** a streak rather than
extending it, because a gap is not evidence — this is the line to read when a
dead backend looks exactly like a workflow that stopped running:

{gaps or '*(no gaps: every day in the streak was observed)*'}

Latest day with data: `{as_of or 'unknown'}`

## What to do

1. **A retired backend is the common case.** GitHub Models was withdrawn on
   2026-07-30 and the only trace was one `failed` line in one morning's log.
   Replace the rung in `scripts/render_ladder.py`, or restore its credential.
2. **A rejected key** shows up as `blocked` (HTTP 401/403 from the provider).
   Rotate it; the rung is dead for this repository until you do.
3. **Not installed is never a failure.** A rung with no key or no package is
   `skipped`, which breaks a streak instead of extending it. If this issue
   appeared right after a run in an unconfigured environment, read the ledger
   rather than the rung.

Never hand-edit `rung-attempts.jsonl`: it is the evidence. `rungs.json` is
derived from it and regenerates if deleted.

## What this run did

One probe per host with the rungs that are installed, recorded and committed to
the `telemetry` branch. This job holds no calendar credential and writes no
calendar event.

Run: {run_url()}
"""


def _parked_lines(parked: dict[str, dict]) -> list[str]:
    return [
        f"PARKED: rung_health: {rung} parked for {item['streak']} "
        f"consecutive day(s), latest {item['as_of']}"
        for rung, item in sorted(parked.items())
    ]


def _mode_probe(args: argparse.Namespace) -> int:
    run_id = args.run_id or default_run_id()
    rows = probe(
        args.url,
        dest=args.dest,
        run_id=run_id,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps({"run_id": run_id, "attempts": rows}, indent=2))
    else:
        print(render_probe(rows))
        if args.dry_run:
            print("OK: rung_health: dry-run — nothing written")
    return 0


def _mode_snapshot(args: argparse.Namespace) -> int:
    rows = read_ledger(args.dest)
    run_id = args.run_id or max((row.get("run_id") or "" for row in rows), default="")
    snapshot = build_snapshot(rows, run_id=run_id, ts=utc_iso())
    # `--json` is machine-readable stdout and nothing else: the prose goes to
    # stderr, because a caller that pipes this into a file must not have to
    # strip a friendly line off the end to parse it.
    if not args.dry_run:
        write_snapshot(args.dest, snapshot)
        summary = (
            f"OK: rung_health: snapshot written for run {run_id or 'unknown'} "
            f"({len(rows)} ledger row(s), {len(snapshot['parked'])} parked)"
        )
    else:
        summary = (
            f"OK: rung_health: {len(rows)} ledger row(s) (dry-run, nothing written)"
        )
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
        print(summary, file=sys.stderr)
    else:
        print(summary)
    return 0


def _mode_parked(args: argparse.Namespace) -> int:
    if args.file and args.dry_run:
        print(
            "FAIL: rung_health: --file and --dry-run are exclusive", file=sys.stderr
        )
        return 2
    rows = read_ledger(args.dest)
    streaks = parked_streaks(rows)
    # The latest day *observed at all*, not the latest parked day: the reader has
    # to be able to see that the data itself is old.
    as_of = max((day_of(row.get("ts")) for row in rows), default="")
    parked = {rung: item for rung, item in streaks.items() if item["streak"] >= args.days}

    observed = observed_rungs(rows)
    if args.markdown:
        print(
            build_markdown(
                streaks, days=args.days, as_of=as_of, observed=observed
            )
        )
        return 0

    if not parked:
        if args.json:
            print(
                json.dumps(
                    {
                        "days": args.days,
                        "as_of": as_of,
                        "observed": observed,
                        "parked": {},
                        "filed": None,
                    },
                    indent=2,
                )
            )
        else:
            print(
                f"OK: rung_health: no rung parked for {args.days} consecutive "
                f"observed day(s) ({len(streaks)} rung(s) observed, "
                f"latest day {as_of or 'none'})"
            )
        return 0

    title = build_title()
    body = build_body(streaks, days=args.days, as_of=as_of)

    if args.dry_run:
        print(title)
        print()
        print(body)
        return 0

    if not args.file:
        # A report, not a write. The PARKED lines are the finding; the workflow
        # is the thing that passes --file.
        findings = _parked_lines(parked)
        if args.json:
            for line in findings:
                print(line, file=sys.stderr)
            print(
                json.dumps(
                    {
                        "days": args.days,
                        "as_of": as_of,
                        "observed": observed,
                        "parked": parked,
                        "title": title,
                        "filed": None,
                    },
                    indent=2,
                )
            )
        else:
            for line in findings:
                print(line)
            print(
                f"OK: rung_health: {len(parked)} rung(s) parked {args.days}+ days — "
                "pass --file to open the issue"
            )
        return 0

    try:
        filed = file_or_comment(title, body, marker=TITLE_MARKER, runner=run_gh)
    except FileNotFoundError:
        # A finding that could not be delivered must be loud, or the workflow
        # goes green having reported nothing at all.
        print(
            "FAIL: rung_health: `gh` is not installed, so the parked rung(s) "
            "could not be filed",
            file=sys.stderr,
        )
        return 1
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        print(f"FAIL: rung_health: gh failed: {detail[:400]}", file=sys.stderr)
        return 1

    for line in _parked_lines(parked):
        print(line)
    print(f"OK: rung_health: {filed}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    modes = parser.add_subparsers(dest="mode", required=True)

    probe_parser = modes.add_parser(
        "probe", help="climb the ladder per URL with one tracker, then record it"
    )
    probe_parser.add_argument("--dest", type=Path, required=True)
    probe_parser.add_argument(
        "--url", action="append", default=[], required=True,
        help="a URL to probe; repeatable (one shared strike tracker per run)",
    )
    probe_parser.add_argument("--run-id", default=None)
    probe_parser.add_argument("--timeout", type=float, default=render_ladder.DEFAULT_TIMEOUT)
    probe_parser.add_argument("--json", action="store_true")
    probe_parser.add_argument(
        "--dry-run", action="store_true", help="probe, but write nothing"
    )
    probe_parser.set_defaults(handler=_mode_probe)

    snapshot_parser = modes.add_parser(
        "snapshot", help="derive rungs.json from the ledger"
    )
    snapshot_parser.add_argument("--dest", type=Path, required=True)
    snapshot_parser.add_argument("--run-id", default=None)
    snapshot_parser.add_argument("--json", action="store_true")
    snapshot_parser.add_argument("--dry-run", action="store_true")
    snapshot_parser.set_defaults(handler=_mode_snapshot)

    parked_parser = modes.add_parser(
        "parked", help="report (or file) rungs parked for N consecutive days"
    )
    parked_parser.add_argument("--dest", type=Path, required=True)
    parked_parser.add_argument("--days", type=int, default=PARKED_DAYS)
    parked_parser.add_argument(
        "--file", action="store_true",
        help="create or comment on the issue (touches GitHub)",
    )
    parked_parser.add_argument(
        "--dry-run", action="store_true", help="print the issue exactly as it would be sent"
    )
    parked_parser.add_argument(
        "--markdown", action="store_true", help="print the job-summary view (no network)"
    )
    parked_parser.add_argument("--json", action="store_true")
    parked_parser.set_defaults(handler=_mode_parked)

    args = parser.parse_args()
    if getattr(args, "days", PARKED_DAYS) < 1:
        print("FAIL: rung_health: --days must be at least 1", file=sys.stderr)
        return 2
    if not args.dest.exists() and args.mode == "parked":
        print(
            f"FAIL: rung_health: {args.dest}: no telemetry directory, so no ledger "
            "to read",
            file=sys.stderr,
        )
        return 2
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
