#!/usr/bin/env python3
"""link_inventory.py — the producer `link_check.py --input` never had.

`references/self-learning.md` lists five learning loops. Four of them have a
writer. The fifth did not:

    | Link revalidation | `scripts/link_check.py --input links.json` | report JSON |

`links.json` is named in that table, in `SKILL.md`, in `docs/do-harness.md` and in
`link_check.py`'s own docstring, and **nothing produced it**. The loop could not
run even in principle — the same shape as `telemetry/events.jsonl` and
`evidence.json` before `event_ledger.py` was written: a documented artifact with
a reader and no writer.

For links there was a second reason, one layer further up: until
`calendar_io.description_for` rendered the `FREE STREAM LINKS:` block, the runtime
recorded a link **nowhere**. `upsert_events._event_fields` did not carry it, so a
candidate's `directLink` was dropped at the planner boundary and never reached the
calendar. There was nothing to inventory. This script reads what is now written.

Two sources, because the links this skill depends on live in two places:

  * **the calendar** — one entry per stored stream link, read back out of the
    event description. These are the links that *rot*: a YouTube `/live` URL 404s
    the moment the broadcast ends, which is why `references/self-learning.md` §4
    quarantines the link instead of deleting the event.
  * **the approved registry** (`config/sources.json`) — one entry per approved
    domain, channel and validation account. This is the half no check covered: of
    the registry's 22 domains only 4 are reachable from the recorded page corpus,
    so a league that rebrands its site is found out by a failed run rather than by
    a check.

`kind` on each entry is what tells them apart, because the right reaction differs:
a dead `calendar-event` link is quarantined, a dead `approved-domain` is a change
to the tier table, and `validation-account` entries (`x.com/…`, `facebook.com/…`)
answer a bot with 401/403 by design — a `BLOCKED` there is the expected result,
not a finding.

Deliberately **not** a gate. It makes no network request, so it runs anywhere;
whether a URL answers is `link_check.py`'s question and its answer is not
deterministic. A free provider having a bad day must not red a build, which is why
nothing in `.github/workflows/` calls the probing half. The pair *is* the loop:

    python3 scripts/calendar_io.py list --time-min … --time-max … > .tmp/events.json
    python3 scripts/link_inventory.py --events .tmp/events.json --out .tmp/links.json
    python3 scripts/link_check.py --input .tmp/links.json --out .tmp/link-report.json

Usage:
    # The registry half alone — no calendar access needed, still a real inventory
    python3 scripts/link_inventory.py --root . --json

    python3 scripts/link_inventory.py --events .tmp/events.json --out logs/links.json

Exit codes:
    0  PASS — an inventory was produced (including an empty calendar half)
    1  FAIL — there is nothing to check: no registry entries and no stored links
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:  # direct CLI execution: `python3 scripts/link_inventory.py`
    from calendar_io import parse_event
    from source_learning import SOURCES_RELATIVE_PATH, load_sources
except ImportError:  # imported as a package module, e.g. scripts.link_inventory
    from scripts.calendar_io import parse_event  # type: ignore[no-redef]
    from scripts.source_learning import (  # type: ignore[no-redef]
        SOURCES_RELATIVE_PATH,
        load_sources,
    )

KIND_EVENT = "calendar-event"
KIND_DOMAIN = "approved-domain"
KIND_CHANNEL = "approved-channel"
KIND_ACCOUNT = "validation-account"
KINDS = (KIND_EVENT, KIND_DOMAIN, KIND_CHANNEL, KIND_ACCOUNT)

# link_check.py rejects `/channel/`, `/c/` and every `/user/` but TheDBBTV, so a
# bare `youtube.com` domain is inventoried as the home page rather than as
# `https://youtube.com/`… which is the same URL. The handles below it are the ones
# that carry the live-only rule.
EVENT_KEYS = ("items", "events", "rows")


def utc_iso(moment: datetime | None = None) -> str:
    """Second-resolution UTC, so two runs a millisecond apart compare equal."""
    return (moment or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def parse_moment(value: str | None) -> datetime:
    """`--now`, or the wall clock. Exits 2 on a value that is not ISO-8601."""
    if not value:
        return datetime.now(timezone.utc)
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        print(
            f"FAIL: link_inventory: {value!r} is not a valid ISO-8601 timestamp",
            file=sys.stderr,
        )
        sys.exit(2)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_events(path: Path) -> list[dict]:
    """The rows of an events export, whichever shape it was written in.

    `calendar_io list` writes a bare JSON array of normalised events; a dump taken
    straight from the Google API wraps them in `{"items": [...]}`. Both are
    accepted, because which one a caller happens to have decides nothing about the
    inventory. Unreadable input is exit 2 rather than an empty inventory: an
    inventory built from a file that was not there would report "no links" and
    look exactly like a calendar with no links on it.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: link_inventory: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: link_inventory: {path}: unreadable ({exc})", file=sys.stderr)
        sys.exit(2)

    if isinstance(payload, dict):
        for key in EVENT_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                payload = value
                break
        else:
            print(
                f'FAIL: link_inventory: {path}: expected a list, or an object '
                f'with one of {", ".join(EVENT_KEYS)}',
                file=sys.stderr,
            )
            sys.exit(2)
    if not isinstance(payload, list):
        print(
            f"FAIL: link_inventory: {path}: expected a list of events",
            file=sys.stderr,
        )
        sys.exit(2)
    return [row for row in payload if isinstance(row, dict)]


def host_of(url: str) -> str:
    """The bare host of a URL, for labelling a report row. Never a claim."""
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def event_entries(events: list[dict]) -> tuple[list[dict], int]:
    """`(entries, events carrying no link)`.

    The second number is reported rather than dropped. An inventory that found no
    event links because every description predates the writer looks identical to
    one where every event was checked and found healthy — and the count is what
    tells those apart, which is the difference between a quiet week and a check
    that silently did nothing.

    The rows go through `calendar_io.parse_event` rather than reading
    `description` here: one normaliser for both input shapes, and the link's round
    trip through the description is then exercised by the same call the rest of
    the runtime uses.
    """
    entries: list[dict] = []
    without_links = 0
    for raw in events:
        try:
            event = parse_event(raw)
        except ValueError:
            continue
        links = event.get("links") or []
        if not links:
            without_links += 1
            continue
        for link in links:
            url = str(link.get("url") or "").strip()
            if not url:
                continue
            entries.append(
                {
                    "url": url,
                    "source": str(link.get("source") or "").strip()
                    or host_of(url),
                    "event_id": event["event_id"],
                    "kind": KIND_EVENT,
                    "label": event.get("summary", ""),
                }
            )
    return entries, without_links


def registry_entries(sources: dict) -> list[dict]:
    """One entry per approved domain, channel and validation account."""
    entries: list[dict] = []

    def add(url: str, source: str, kind: str, label: str) -> None:
        entries.append(
            {"url": url, "source": source, "event_id": "", "kind": kind, "label": label}
        )

    for source in sources.get("sources", []):
        if not isinstance(source, dict):
            continue
        name = str(source.get("name") or "")
        domains = [source.get("domain"), *(source.get("domains") or [])]
        for domain in domains:
            domain = str(domain or "").strip().strip("/")
            if domain:
                add(f"https://{domain}/", host_of(f"https://{domain}/"), KIND_DOMAIN, name)
        for handle in source.get("handles") or []:
            handle = str(handle or "").strip().strip("/")
            if handle:
                add(f"https://{handle}", host_of(f"https://{handle}"), KIND_CHANNEL, name)
        for handle in source.get("social") or []:
            handle = str(handle or "").strip().strip("/")
            if handle:
                add(f"https://{handle}", host_of(f"https://{handle}"), KIND_ACCOUNT, name)
    return entries


def build_inventory(
    events: list[dict] | None,
    sources: dict,
    *,
    now: datetime | None = None,
) -> tuple[dict, int]:
    """`(payload, events carrying no link)`. Deterministic for a fixed `--now`."""
    calendar, without_links = event_entries(events or [])
    registry = registry_entries(sources)

    # Deduped on the *role* a URL plays: the same URL stored on two events is two
    # findings, because quarantining it is per event, while the same domain claimed
    # by two registry entries is one.
    seen: set[tuple[str, str, str]] = set()
    links: list[dict] = []
    for entry in calendar + registry:
        key = (entry["kind"], entry["url"], entry["event_id"])
        if key not in seen:
            seen.add(key)
            links.append(entry)
    # Sorted rather than left in discovery order, so two runs over the same inputs
    # produce the same file and a diff shows a real change rather than a reordering.
    links.sort(key=lambda item: (item["kind"], item["url"], item["event_id"]))

    counts = {kind: 0 for kind in KINDS}
    for entry in links:
        counts[entry["kind"]] += 1
    payload = {
        "generated_at": utc_iso(now),
        "events_scanned": len(events or []),
        "events_without_links": without_links,
        "counts": {kind: count for kind, count in counts.items() if count},
        "total": len(links),
        "links": links,
    }
    return payload, without_links


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the links.json that link_check.py --input reads."
    )
    parser.add_argument(
        "--events", type=Path, help="events export to read stored links from"
    )
    parser.add_argument("--root", default=".", help="directory holding config/")
    parser.add_argument("--out", type=Path, help="write the inventory here")
    parser.add_argument("--now", help="ISO-8601 timestamp override (for tests)")
    parser.add_argument("--dry-run", action="store_true", help="do not write --out")
    parser.add_argument("--json", action="store_true", help="print the inventory as JSON")
    args = parser.parse_args()

    root = Path(args.root)
    registry_path = root / SOURCES_RELATIVE_PATH
    sources = load_sources(root)

    events: list[dict] = []
    if args.events:
        events = load_events(args.events)

    payload, without_links = build_inventory(events, sources, now=parse_moment(args.now))

    if args.out and not args.dry_run:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        # stderr, not stdout: with `--json` stdout is the payload alone. Writing
        # this confirmation to stdout is the mistake `link_check.py` still makes,
        # and it is the one that has been re-learned four times in this repo.
        print(f"OK: link_inventory: wrote {args.out}", file=sys.stderr)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for entry in payload["links"]:
            where = f" [{entry['event_id']}]" if entry["event_id"] else ""
            print(
                f"OK: link_inventory: {entry['kind']:18s} {entry['url']}{where}"
                f"  ({entry['label'] or entry['source']})"
            )
        summary = ", ".join(
            f"{kind}={count}" for kind, count in payload["counts"].items()
        )
        print(
            f"OK: link_inventory: {payload['total']} link(s) to check: "
            f"{summary or 'none'}"
        )
        if not registry_path.is_file():
            print(
                f"OK: link_inventory: {registry_path} not found — the inventory "
                "covers the calendar only"
            )
        if without_links:
            print(
                f"OK: link_inventory: {without_links} of {payload['events_scanned']} "
                "event(s) carry no link (no `FREE STREAM LINKS:` block), so their "
                "links are not in this inventory"
            )

    if not payload["links"]:
        print(
            "FAIL: link_inventory: nothing to check — no approved registry entries "
            "and no stored event links",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
