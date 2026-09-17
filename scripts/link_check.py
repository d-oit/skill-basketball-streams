#!/usr/bin/env python3
"""link_check.py — Revalidate stored calendar stream links.

Every calendar event carries a `sourceReference` and one or more direct stream
links. Links rot: YouTube live URLs expire the moment a broadcast ends, and
magenta.tv dynamic URLs are dropped after the matchday. This script re-tests
stored links and classifies each failure so the skill can decide whether to
**quarantine** (remove the link) or merely **retry** (transient).

Usage:
    # Offline structural check — no network, safe for CI
    python3 scripts/link_check.py --input links.json --dry-run

    # Live HTTP probe (default)
    python3 scripts/link_check.py --input links.json --checked-at 2026-09-14T08:00:00Z

    # Ad-hoc URLs
    python3 scripts/link_check.py --url https://www.youtube.com/@fiba/live --dry-run

Input schema (either shape is accepted):
    [{"url": "...", "source": "magenta.tv", "event_id": "abc123"}]
    {"links": [ ... same ... ]}

Exit codes:
    0  every link classified OK
    1  at least one link is not OK (BROKEN / BLOCKED / ERROR / UNREACHABLE)
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BasketballStreamsValidator/1.0"
)
TIMEOUT_SECONDS = 15

# Anti-bot statuses: the URL may be perfectly valid, we just cannot see it
# without a browser-rendering backend (Firecrawl / TinyFish Fetch / Playwright).
BLOCKED_STATUSES = frozenset({401, 403, 429, 451})
BROKEN_STATUSES = frozenset({404, 405, 410, 418, 422})

OK = "OK"
BROKEN = "BROKEN"
BLOCKED = "BLOCKED"
ERROR = "ERROR"
UNREACHABLE = "UNREACHABLE"
INVALID = "INVALID"

REJECTED_YOUTUBE_RE = re.compile(
    r"^https?://(?:www\.|m\.)?youtube\.com/(?:channel/|user/(?!TheDBBTV)|c/)",
    re.IGNORECASE,
)


def structural_check(url: str) -> tuple[str, str] | None:
    """Cheap, offline checks. Returns (status, reason) or None when usable."""
    if not url or not isinstance(url, str):
        return INVALID, "empty or non-string URL"
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return INVALID, "URL must be absolute http(s)"
    if re.match(r"^https?://localhost", url, re.IGNORECASE):
        return INVALID, "localhost URLs are never promotable stream links"
    if REJECTED_YOUTUBE_RE.match(url):
        return INVALID, (
            "rejected YouTube shape (/channel/, /user/ other than TheDBBTV, /c/)"
        )
    return None


def probe(url: str, timeout: int = TIMEOUT_SECONDS) -> tuple[str, str, int | None]:
    """HTTP-probe a URL. Returns (status, reason, http_status)."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT}, method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            code = response.status
            if code in BLOCKED_STATUSES:
                return BLOCKED, f"anti-bot status {code}", code
            if 200 <= code < 300:
                return OK, f"HTTP {code}", code
            return ERROR, f"unexpected HTTP {code}", code
    except urllib.error.HTTPError as exc:
        code = exc.code
        if code in BLOCKED_STATUSES:
            return (
                BLOCKED,
                f"anti-bot status {code} — retry via a browser backend "
                "(Firecrawl / TinyFish Fetch)",
                code,
            )
        if code in BROKEN_STATUSES:
            return BROKEN, f"HTTP {code} — link is dead, quarantine the event", code
        if 500 <= code < 600:
            return ERROR, f"HTTP {code} — transient server error, retry later", code
        return BROKEN, f"HTTP {code}", code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return UNREACHABLE, f"network failure: {exc}", None


def check_link(
    entry: dict, dry_run: bool = False, timeout: int = TIMEOUT_SECONDS
) -> dict:
    """Classify a single link entry, returning an annotated result dict."""
    url = entry.get("url") if isinstance(entry, dict) else str(entry)
    url = (url or "").strip()
    result = {
        "url": url,
        "source": (entry or {}).get("source", "") if isinstance(entry, dict) else "",
        "event_id": (
            (entry or {}).get("event_id", "") if isinstance(entry, dict) else ""
        ),
        "mode": "dry-run" if dry_run else "http",
    }
    structural = structural_check(url)
    if structural is not None:
        result["status"], result["reason"] = structural
        result["http_status"] = None
        return result
    if dry_run:
        result["status"] = OK
        result["reason"] = "structural checks passed (no network in --dry-run)"
        result["http_status"] = None
        return result
    status, reason, code = probe(url, timeout=timeout)
    result["status"], result["reason"], result["http_status"] = status, reason, code
    return result


def _load_entries(path: Path) -> list:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: link_check: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"FAIL: link_check: {path}: invalid JSON ({exc})", file=sys.stderr)
        sys.exit(2)
    if isinstance(payload, dict):
        payload = payload.get("links", [])
    if not isinstance(payload, list):
        print(
            'FAIL: link_check: input must be a JSON list or {"links": [...]}',
            file=sys.stderr,
        )
        sys.exit(2)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Revalidate stored basketball stream links.",
    )
    parser.add_argument("--input", help="JSON file with link entries")
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="ad-hoc URL to check (repeatable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="structural checks only — makes no network requests",
    )
    parser.add_argument(
        "--timeout", type=int, default=TIMEOUT_SECONDS, help="per-request timeout"
    )
    parser.add_argument(
        "--checked-at",
        help="ISO-8601 timestamp stamped onto the report (default: now)",
    )
    parser.add_argument("--out", help="write the JSON report to this path")
    parser.add_argument(
        "--json", action="store_true", help="print the report as JSON"
    )
    args = parser.parse_args()

    if not args.input and not args.url:
        parser.error("provide --input and/or at least one --url")

    entries: list = []
    if args.input:
        entries.extend(_load_entries(Path(args.input)))
    entries.extend({"url": url, "source": "cli"} for url in args.url)

    results = [
        check_link(entry, dry_run=args.dry_run, timeout=args.timeout)
        for entry in entries
    ]

    checked_at = args.checked_at or datetime.now(timezone.utc).isoformat()
    report = {
        "checked_at": checked_at,
        "mode": "dry-run" if args.dry_run else "http",
        "results": results,
        "summary": {},
    }
    for status in (OK, BROKEN, BLOCKED, ERROR, UNREACHABLE, INVALID):
        report["summary"][status] = sum(
            1 for item in results if item["status"] == status
        )

    if args.out:
        Path(args.out).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        # `--json` promises stdout is the payload alone, so under that flag this
        # confirmation goes to stderr. It used to go to stdout in both modes, which
        # made `--json --out` emit a JSON document with a human line in front of
        # it — unparseable to every reader, and the same trap `upsert_events`,
        # `calendar_io`, `synthesise_eval_case` and `run_daily` each had to learn.
        print(
            f"OK: link_check: wrote report to {args.out}",
            file=sys.stderr if args.json else sys.stdout,
        )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for item in results:
            line = f"{item['url']}: {item['status']} — {item['reason']}"
            if item["status"] == OK:
                print(f"OK: link_check: {line}")
            else:
                print(f"FAIL: link_check: {line}", file=sys.stderr)
        summary = ", ".join(
            f"{key}={value}" for key, value in report["summary"].items() if value
        )
        print(f"OK: link_check: summary: {summary or 'no links checked'}")

    all_ok = bool(results) and report["summary"][OK] == len(results)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
