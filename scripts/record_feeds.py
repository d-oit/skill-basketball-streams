#!/usr/bin/env python3
"""record_feeds.py — record and re-check the iCalendar corpus.

A feed's `SUMMARY` format is a **provider's output**: the official BBL calendar
writes the competition onto the first club name (`easyCredit BBL Spiel ALBA
BERLIN vs NINERS Chemnitz`) and leaves every `DTSTART` *floating*, declaring the
zone once at calendar level. Both are facts about a league's server, so a
hand-written fixture would encode this repository's guess at them and prove the
guess. This tool keeps that corpus honest, and is the **writer** named by
`tests/fixtures/feeds/README.md`.

    # Re-fetch the feed and rewrite the corpus + manifest (network)
    python3 scripts/record_feeds.py --refresh

    # Gate: are the stored bytes and the manifest still what they claim?
    python3 scripts/record_feeds.py --check

    # Human report of what the corpus covers
    python3 scripts/record_feeds.py --list

`--check` is the gate and makes **no** network request, so it is what CI runs. It
refuses a stored file whose bytes are not the recorded bytes: without that, a
fixture could be quietly edited into agreeing with a weakened reader — it would
still parse, so nothing else would notice.

Why a trim and no verdict FLIPs, unlike `record_pages.py`. The page corpus has
seven URLs whose *verdicts* must stay comparable week to week, which is what a
FLIP report is for. This has one URL, and its payload is trimmed against a
declared selection rule (every competition prefix × folded-`SUMMARY` × non-ASCII
combination the response contains), so there is no verdict to compare: a
re-record that changed the event count fails `--check`'s manifest assertion and
is read by a human instead.

The write is all-or-nothing. The payload and the manifest are computed first and
written together at the end, so an aborted refresh leaves the corpus
byte-identical rather than with a manifest describing bytes that were never
stored.

Exit codes:
    0  PASS — stored bytes and manifest agree (or --refresh / --list)
    1  FAIL — a stored file is missing, edited, or the manifest is inconsistent
    2  USAGE — bad arguments
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import DEFAULT_SOURCES, parse_ics, unfold_ics  # noqa: E402
from render_ladder import USER_AGENT  # noqa: E402

TIMEOUT_SECONDS = 30
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "feeds"
MANIFEST_NAME = "manifest.json"

# The stored name per feed, so adding a second feed is a one-line change rather
# than a branch in the writer.
STORED_NAMES = {"bbl": "bbl_all_games.ics"}

FEED_SOURCES = {
    name: cfg["url"]
    for name, cfg in DEFAULT_SOURCES.items()
    if cfg.get("kind") == "ics"
}

# The selection rule, stated once and enforced by `_select`. A trim whose rule is
# not written down is indistinguishable from a trim chosen to make the tests
# pass, which is the thing this corpus exists to prevent.
COMPETITION_PREFIXES = ("easyCredit BBL Spiel", "BBL Pokal Spiel")
NON_ASCII = "äöüßÖÄÜéÉèÈ"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event_blocks(lines: list[str]) -> list[list[str]]:
    """Every VEVENT as a standalone block, **including** its BEGIN/END lines.

    The envelope is part of the block, not decoration around it: the first
    version of this stripped `BEGIN:VEVENT` and `END:VEVENT` on the way to
    selecting, and the payload it wrote was a calendar whose events had no
    envelope at all — 7 kept, 0 parsable, and the corpus `--check` happily
    passed, because the bytes it had recorded were exactly the wrong bytes. The
    reader is right to reject that; the recorder was wrong to write it.
    """
    events: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = []
        if current is not None:
            current.append(line)
        if line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
    return events


def _summary_of(block: list[str]) -> str:
    """The `SUMMARY` of one VEVENT, unfolded.

    A summary read with a fold left in place names a *different* club, so this
    is what the manifest records and what the fixture's meaning rests on.
    """
    for line in unfold_ics("\r\n".join(block)):
        if line.upper().startswith("SUMMARY:"):
            return line.split(":", 1)[1]
    return ""


def _usable(block: list[str], header: list[str] | None = None) -> bool:
    """Does this block yield a fixture **according to the reader**?

    Asked of the reader rather than re-guessed here, because a guess is a second
    opinion that drifts: a first version checked `DTSTART` was present and called
    an all-day `DTSTART;VALUE=DATE:` event usable, which the reader refuses on
    purpose. Selection would then keep an event that parses to nothing, and the
    suite would pass while pinning nothing.

    A kept event the reader refuses would sit in the corpus asserting a format
    the reader cannot read. Selection prefers a usable event per cell and falls
    back to an unusable one only for a cell nothing readable ever fills.
    """
    envelope = list(header or []) + list(block) + ["END:VCALENDAR", ""]
    return bool(
        parse_ics("\r\n".join(envelope), league="BBL", source="bbl")
    )


def _combination(block: list[str]) -> str:
    """Which (competition x folded x non-ASCII) cell this event fills."""
    raw = next((x for x in block if x.startswith("SUMMARY")), "")
    prefix = next(
        (p for p in COMPETITION_PREFIXES if raw.startswith(f"SUMMARY:{p}")), "unknown"
    )
    folded = any(x[:1] in (" ", "\t") for x in block)
    non_ascii = any(c in x for x in block for c in NON_ASCII)
    return f"{prefix} x folded={folded} x non-ascii={non_ascii}"


def select(text: str) -> tuple[list[str], list[list[str]], list[str]]:
    """(header, kept blocks, combinations) — first event wins each cell.

    A function of the response, not of the order a human picked things in.
    """
    lines = text.split("\r\n")
    if "BEGIN:VEVENT" not in lines:
        raise ValueError("no VEVENT in the response — is the URL still the feed?")
    header = lines[: lines.index("BEGIN:VEVENT")]
    kept: list[list[str]] = []
    combos: list[str] = []
    seen: set[str] = set()
    fallback: dict[str, list[str]] = {}
    for block in _event_blocks(lines):
        cell = _combination(block)
        if cell in seen:
            continue
        if not _usable(block, header):
            # Remembered, not kept: a later event in the same cell may well be
            # readable, and the fallback is only appended for cells nothing
            # readable ever fills.
            fallback.setdefault(cell, block)
            continue
        seen.add(cell)
        kept.append(block)
        combos.append(cell)
    for cell, block in fallback.items():
        if cell in seen:
            continue
        kept.append(block)
        combos.append(cell)
    return header, kept, combos


def fetch(url: str) -> tuple[str, int]:
    """(body, status). An HTTP error status is a failure, not a body."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", "replace"), response.status
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}") from error


def build(name: str, body: str, status: int) -> tuple[bytes, dict]:
    """(payload bytes, manifest). Nothing is written by this function."""
    raw = body.encode("utf-8")
    header, kept, combos = select(body)
    payload = "\r\n".join(
        header + [line for block in kept for line in block] + ["END:VCALENDAR", ""]
    ).encode("utf-8")
    stored_name = STORED_NAMES[name]
    manifest = {
        "recorded": utc_now(),
        "source": name,
        "source_url": FEED_SOURCES[name],
        "http_status": status,
        "response_bytes": len(raw),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "events_in_response": len(_event_blocks(body.split("\r\n"))),
        "selection_rule": (
            "first event of every (competition prefix x folded SUMMARY x "
            "non-ASCII) combination the response contains"
        ),
        "stored_file": stored_name,
        "stored_bytes": len(payload),
        "stored_sha256": hashlib.sha256(payload).hexdigest(),
        "kept": [
            {
                "uid": next(
                    (x.split(":", 1)[1] for x in b if x.startswith("UID:")), ""
                ),
                "summary": _summary_of(b),
                "combination": cell,
            }
            for b, cell in zip(kept, combos)
        ],
    }
    return payload, manifest


def check(out: Path) -> int:
    """The offline gate. Names the problem; never touches the network."""
    manifest_path = out / MANIFEST_NAME
    if not manifest_path.exists():
        print(f"FAIL: record_feeds: no manifest at {manifest_path}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []

    stored_path = out / manifest.get("stored_file", "")
    if not stored_path.is_file():
        failures.append(f"{stored_path} is missing")
    else:
        data = stored_path.read_bytes()
        if len(data) != manifest.get("stored_bytes"):
            failures.append(
                f"{stored_path.name}: {len(data)} bytes, manifest says "
                f"{manifest.get('stored_bytes')}"
            )
        digest = hashlib.sha256(data).hexdigest()
        if digest != manifest.get("stored_sha256"):
            failures.append(
                f"{stored_path.name}: not the recorded bytes (sha256 {digest[:12]}, "
                f"manifest {str(manifest.get('stored_sha256'))[:12]})"
            )

    # The manifest must still name a source the fetcher uses, or the corpus has
    # silently become a statement about a URL nothing reads.
    if manifest.get("source_url") not in FEED_SOURCES.values():
        failures.append(
            f"manifest URL {manifest.get('source_url')!r} is not a declared ics "
            f"source in DEFAULT_SOURCES any more"
        )

    # Every kept event must still be in the response's event count, or the trim
    # rule and the manifest have drifted apart.
    kept = manifest.get("kept", [])
    if len(kept) >= manifest.get("events_in_response", 0):
        failures.append(
            f"{len(kept)} kept of {manifest.get('events_in_response')} events — "
            f"this is supposed to be a trim"
        )
    cells = [k.get("combination") for k in kept]
    if len(set(cells)) != len(cells):
        failures.append("two kept events claim the same selection combination")
    if "unknown" in cells:
        failures.append(
            "a kept event's SUMMARY carries none of the declared competition "
            f"prefixes {COMPETITION_PREFIXES} — the format may have changed"
        )

    # The stored payload must actually parse, and every kept event must be one
    # of the fixtures. `--check` verified only that the bytes were the recorded
    # bytes — which passed happily over a calendar whose events had no
    # `BEGIN:VEVENT`/`END:VEVENT` envelope at all: 7 kept, 0 parsable, corpus
    # green. A hash proves the bytes are unchanged; it cannot prove they are
    # readable, and only the reader can say.
    if stored_path.is_file():
        parsed = parse_ics(
            stored_path.read_text(encoding="utf-8"),
            league="BBL",
            source=manifest.get("source", "bbl"),
            source_url=manifest.get("source_url", ""),
        )
        if not parsed:
            failures.append(f"{stored_path.name}: the reader parses no fixture from it")
        elif len(parsed) != len(kept):
            failures.append(
                f"{stored_path.name}: {len(parsed)} fixture(s) from {len(kept)} kept "
                f"events — a kept event the reader refuses pins nothing"
            )
        else:
            bad = [
                team
                for row in parsed
                for team in row["teams"]
                if team.lower().startswith(tuple(p.lower() for p in COMPETITION_PREFIXES))
            ]
            if bad:
                failures.append(
                    f"{stored_path.name}: a club name still carries its competition "
                    f"prefix ({bad[0]!r}) — the prefix list has drifted from the feed"
                )

    for line in failures:
        print(f"FAIL: record_feeds: {line}", file=sys.stderr)
    if failures:
        print(
            "FAIL: record_feeds: re-record with --refresh or revert the edit — a "
            "captured fixture is a statement about the provider, not about us",
            file=sys.stderr,
        )
        return 1
    print(
        f"OK: record_feeds: {stored_path.name} is the recorded bytes of "
        f"{manifest.get('source_url')} ({manifest.get('events_in_response')} events "
        f"in the response, {len(kept)} kept)"
    )
    return 0


def show(out: Path) -> int:
    manifest_path = out / MANIFEST_NAME
    if not manifest_path.exists():
        print(f"FAIL: record_feeds: no manifest at {manifest_path}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"source : {manifest['source_url']}")
    print(
        f"taken  : {manifest['recorded']}  HTTP {manifest['http_status']}, "
        f"{manifest['response_bytes']} bytes, {manifest['events_in_response']} events"
    )
    print(
        f"stored : {manifest['stored_file']}  {manifest['stored_bytes']} bytes, "
        f"{len(manifest['kept'])} kept"
    )
    print(f"selection rule: {manifest['selection_rule']}")
    for row in manifest["kept"]:
        print(f"  - [{row['combination']}] {row['summary']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="record and re-check a feed corpus")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--refresh", action="store_true", help="re-fetch and rewrite (network)")
    mode.add_argument("--check", action="store_true", help="gate: bytes match the manifest")
    mode.add_argument("--list", dest="listing", action="store_true", help="what it covers")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="corpus directory")
    parser.add_argument(
        "--source", choices=sorted(FEED_SOURCES), help="which feed to refresh"
    )
    args = parser.parse_args(argv)

    if args.check:
        return check(args.out)
    if args.listing:
        return show(args.out)

    name = args.source or ("bbl" if "bbl" in FEED_SOURCES else sorted(FEED_SOURCES)[0])
    try:
        body, status = fetch(FEED_SOURCES[name])
        payload, manifest = build(name, body, status)
    except (RuntimeError, urllib.error.URLError, ValueError, OSError) as error:
        print(
            f"FAIL: record_feeds: could not record {FEED_SOURCES[name]}: {error}",
            file=sys.stderr,
        )
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    # Written together, at the end, after everything above succeeded.
    (args.out / manifest["stored_file"]).write_bytes(payload)
    (args.out / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        f"OK: record_feeds: recorded {manifest['events_in_response']} events from "
        f"{FEED_SOURCES[name]}, kept {len(manifest['kept'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())