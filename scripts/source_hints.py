"""Build advisory search hints for the runtime agent.

Reads the telemetry branch's ``calendar-log.jsonl`` (what the calendar already
verified) and ``run-log.jsonl`` (what the runtime skipped), and writes one JSON
file the agent receives as an attachment:

* ``preferred_hosts`` — hosts a VERIFIED calendar event streams from. The agent
  should search these first; a host that has delivered a verified game is the
  best lead for the next one.
* ``deprioritized_hosts`` — hosts the runtime skipped as not free / not
  verifiable. The agent should not spend its budget on them.

The hints are advisory only. They never add a host to ``config/sources.json``
and never bypass validation: Check 3 and ``approve_source.py`` decide what is
stored, exactly as before. A missing input yields empty lists, not a failure,
because a first run has no history yet.

Usage: ``source_hints.py --dest DIR --out FILE`` (DIR holds the two logs).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

VERIFIED_ACTIONS = {"create", "update"}
SKIP_OUTCOME = "skip"


def _host(url: str) -> str:
    host = urlparse(url or "").hostname or ""
    return host.lower().removeprefix("www.")


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def preferred_hosts(calendar_rows: list[dict]) -> list[str]:
    hosts: set[str] = set()
    for row in calendar_rows:
        if row.get("action") not in VERIFIED_ACTIONS or row.get("state") != "VERIFIED":
            continue
        links = row.get("links") or {}
        for link in links.get("direct_links") or []:
            host = _host(link.get("url", "") if isinstance(link, dict) else "")
            if host:
                hosts.add(host)
        ref_host = _host(links.get("source_reference", ""))
        if ref_host:
            hosts.add(ref_host)
    return sorted(hosts)


def deprioritized_hosts(run_rows: list[dict], preferred: list[str]) -> list[str]:
    # A host that also delivered a verified game is not deprioritized, even if
    # one of its other pages was skipped: the skip was per game, not per host.
    keep = set(preferred)
    hosts = {
        str(r.get("source", "")).lower()
        for r in run_rows
        if r.get("outcome") == SKIP_OUTCOME and r.get("source")
    }
    return sorted(h for h in hosts if h and h not in keep)


def build(dest: Path) -> dict:
    preferred = preferred_hosts(_rows(dest / "calendar-log.jsonl"))
    deprioritized = deprioritized_hosts(_rows(dest / "run-log.jsonl"), preferred)
    return {
        "preferred_hosts": preferred,
        "deprioritized_hosts": deprioritized,
        "note": "Advisory only. Validation and approve_source.py decide what is stored.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    hints = build(args.dest)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(hints, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"OK: source_hints: {len(hints['preferred_hosts'])} preferred, "
        f"{len(hints['deprioritized_hosts'])} deprioritized -> {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
