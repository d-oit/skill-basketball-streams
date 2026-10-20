#!/usr/bin/env python3
"""approve_source.py — the runtime may promote a source, but only on evidence.

Until now every unapproved host was quarantined for a human PR. The owner
chose automatic approval, so this module is the one place that widens the
allow-list. It is deliberately narrow:

  * it only accepts candidates that were APPLIED to the calendar (the caller
    passes the applied set, not the agent's raw proposals);
  * the candidate must prove free access and a live broadcast (both booleans
    true), a validationTimestamp, an https directLink and an https sourceReference;
  * it never approves an excluded host, a plumbing/social host, an evidence-only
    host, or a host `config/retired-sources.json` proves dead;
  * a YouTube stream is approved by channel handle (`@handle`), never by the
    `youtube.com` host, which would approve every channel on YouTube;
  * it is idempotent: a host or handle already present is reported, not added.

New entries are inserted as TEXT before the `sources` array closes. The file is
not re-serialised, so its inline arrays and CRLF endings are preserved.

Usage:
    python3 scripts/approve_source.py --root . --candidates .tmp/applied.json
    python3 scripts/approve_source.py --root . --candidates ... --dry-run

Exit codes: 0 = ran (zero approvals is a valid outcome), 2 = unreadable input.
Status lines go to stderr.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_learning import NON_SOURCE_DOMAINS  # noqa: E402

SOURCES = "config/sources.json"
RETIRED = "config/retired-sources.json"
YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "youtu.be"}
# Hosts whose pages can confirm a game but can never be the stream itself.
EVIDENCE_ONLY = {"dyn.sport", "sportbild.de", "bild.de"}
HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{1,100}$")
INSERT_MARKER = "\n  ],\n  \"excluded\""


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _retired_hosts(root: Path) -> set[str]:
    path = root / RETIRED
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(e["domain"]).lower()
        for e in data.get("retired", [])
        if isinstance(e, dict) and e.get("domain")
    }


def _known(sources: dict) -> tuple[set[str], set[str], set[str]]:
    """(approved hosts, approved handles, excluded names) already on file."""
    hosts: set[str] = set()
    handles: set[str] = set()
    for source in sources.get("sources", []):
        if not isinstance(source, dict):
            continue
        for key in ("domains", "social"):
            hosts.update(str(v).lower() for v in source.get(key, []) if isinstance(v, str))
        handles.update(str(h).lower() for h in source.get("handles", []))
    excluded = {str(e.get("name", "")).lower() for e in sources.get("excluded", [])}
    return hosts, handles, excluded


def _validate(candidate: dict) -> str | None:
    """Return a refusal reason, or None when the candidate may be approved."""
    direct = str(candidate.get("directLink") or "")
    if urlparse(direct).scheme != "https" or not _host(direct):
        return "directLink is not an https URL"
    evidence = candidate.get("evidence") or {}
    if evidence.get("free_confirmed") is not True:
        return "free access is not confirmed"
    if evidence.get("live_confirmed") is not True:
        return "live broadcast is not confirmed"
    if not candidate.get("validationTimestamp"):
        return "no validationTimestamp"
    if not str(candidate.get("sourceReference") or "").startswith("https://"):
        return "no https sourceReference"
    return None


def _entry(value: str, *, is_handle: bool, candidate: dict, now: str) -> dict:
    entry: dict = {
        "name": f"{value} (runtime-approved)",
        "type": "runtime_approved",
        "tier": 7,
        "free_access": "selected",
        "notes": (
            f"Approved {now} by the daily runtime after a verified calendar event. "
            f"Evidence: {candidate.get('sourceReference')} (game: "
            f"{candidate.get('teams') or candidate.get('title') or 'n/a'}). "
            "Review in the next PR; remove the entry to revoke."
        ),
        "approved_by": "runtime",
        "approved_at": now,
    }
    if is_handle:
        entry["handles"] = [value]
        entry["domains"] = []
    else:
        entry["domains"] = [value]
    return entry


def _write_entries(path: Path, entries: list[dict]) -> None:
    """Insert entries as text before the sources array closes; keep CRLF/LF."""
    raw = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")
    if text.count(INSERT_MARKER) != 1:
        raise ValueError(f"{SOURCES}: cannot locate the end of the 'sources' array")
    block = ",\n".join(
        "    " + json.dumps(e, indent=2, ensure_ascii=False).replace("\n", "\n    ")
        for e in entries
    )
    text = text.replace(INSERT_MARKER, ",\n" + block + INSERT_MARKER, 1)
    json.loads(text)  # refuse to write anything that is not valid JSON
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))


def approve(root: Path, candidates: list[dict], *, dry_run: bool) -> list[str]:
    sources_path = root / SOURCES
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    hosts, handles, excluded = _known(sources)
    retired = _retired_hosts(root)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_entries: list[dict] = []
    added: list[str] = []

    for candidate in candidates:
        direct = str(candidate.get("directLink") or "")
        host = _host(direct)
        reason = _validate(candidate)
        if reason:
            print(f"SKIP: approve_source: {host or direct}: {reason}", file=sys.stderr)
            continue
        if host in YOUTUBE_HOSTS:
            parts = [p for p in urlparse(direct).path.split("/") if p]
            handle = parts[0] if parts and parts[0].startswith("@") else ""
            if not HANDLE_RE.match(handle):
                print(f"SKIP: approve_source: {direct}: no channel handle", file=sys.stderr)
                continue
            if handle.lower() in handles:
                print(f"OK: approve_source: {handle} already approved", file=sys.stderr)
                continue
            new_entries.append(_entry(handle, is_handle=True, candidate=candidate, now=now))
            handles.add(handle.lower())
            added.append(handle)
            continue
        if host in EVIDENCE_ONLY or host in NON_SOURCE_DOMAINS or host in retired:
            print(f"SKIP: approve_source: {host}: not promotable", file=sys.stderr)
            continue
        if host in excluded or host in hosts:
            print(f"OK: approve_source: {host} already approved", file=sys.stderr)
            continue
        new_entries.append(_entry(host, is_handle=False, candidate=candidate, now=now))
        hosts.add(host)
        added.append(host)

    if new_entries and not dry_run:
        _write_entries(sources_path, new_entries)
    verb = "would approve" if dry_run else "approved"
    for item in added:
        print(f"OK: approve_source: {verb} {item}", file=sys.stderr)
    if not added:
        print("OK: approve_source: no new sources to approve", file=sys.stderr)
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: approve_source: {args.candidates}: {exc}", file=sys.stderr)
        return 2
    candidates = payload.get("candidates", []) if isinstance(payload, dict) else payload
    if not isinstance(candidates, list):
        print("FAIL: approve_source: candidates must be a list", file=sys.stderr)
        return 2
    approve(Path(args.root).resolve(), [c for c in candidates if isinstance(c, dict)],
            dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
