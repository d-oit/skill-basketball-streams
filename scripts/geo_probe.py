#!/usr/bin/env python3
"""Probe whether a stream page is reachable from a German exit, via Tor.

Free, no account: Tor (apt package on the CI runner) is restricted to German
exit relays (`ExitNodes {DE}` + `StrictNodes 1`). The probe confirms the exit
really is in Germany, then fetches the stream page through it.

The verdict is evidence, not a gate: a Tor exit is often blocked by CDNs, so a
block is recorded as `inconclusive`, never as `geo_blocked` on its own.

Usage:
    python3 scripts/geo_probe.py --url URL --out FILE [--socks 127.0.0.1:9050]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EXIT_COUNTRY_URL = "https://ipinfo.io/country"
TIMEOUT_SECONDS = 30


def torrc(data_dir: str, socks_port: int) -> str:
    """Torrc that only uses German exits; StrictNodes refuses any other exit."""
    return "\n".join(
        [
            f"SocksPort 127.0.0.1:{socks_port}",
            f"DataDirectory {data_dir}",
            "ExitNodes {DE}",
            "StrictNodes 1",
            "",
        ]
    )


def verdict(exit_country: str | None, status: int | None, error: str | None) -> str:
    """Map one probe into a verdict. Only a German exit can prove anything."""
    if exit_country != "DE":
        return "no_german_exit"
    if status is not None and 200 <= status < 300:
        return "reachable_from_de"
    if error is not None or status in (401, 403, 451):
        # Tor exits are widely blocked, so a failure here cannot prove a geo block.
        return "inconclusive"
    return "unexpected_status"


def _curl(url: str, socks: str) -> tuple[int | None, str, str | None]:
    """GET through SOCKS5 with remote DNS. Returns (http_status, body, error).

    curl is used rather than urllib because urllib has no SOCKS support.
    """
    cmd = [
        "curl", "-sS", "--max-time", str(TIMEOUT_SECONDS),
        "--socks5-hostname", socks,
        "-o", "-", "-w", "\n%{http_code}", url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS + 10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "", str(exc)
    if proc.returncode != 0:
        return None, "", proc.stderr.strip() or f"curl exit {proc.returncode}"
    body, _, code = proc.stdout.rpartition("\n")
    return int(code) if code.isdigit() else None, body, None


def probe(url: str, socks: str) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    exit_status, exit_body, exit_error = _curl(EXIT_COUNTRY_URL, socks)
    exit_country = exit_body.strip() if exit_status == 200 else None
    status: int | None = None
    error: str | None = None
    if exit_error or exit_status != 200:
        error = f"exit check failed: {exit_error or exit_status}"
    else:
        status, _, fetch_error = _curl(url, socks)
        if fetch_error:
            error = f"stream fetch failed: {fetch_error}"
    return {
        "checked_at": now,
        "url": url,
        "exit_country": exit_country,
        "status": status,
        "error": error,
        "verdict": verdict(exit_country, status, error),
    }


def latest_stream_url(calendar_log: Path) -> str | None:
    """Source reference of the newest `create` row in the calendar log, if any."""
    if not calendar_log.is_file():
        return None
    url: str | None = None
    for line in calendar_log.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("action") == "create" and not row.get("dry_run"):
            ref = (row.get("links") or {}).get("source_reference")
            if isinstance(ref, str) and ref.startswith("https://"):
                url = ref
    return url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--socks", default="127.0.0.1:9050")
    parser.add_argument("--write-torrc", type=Path, help="write the German-exit torrc and exit")
    parser.add_argument("--data-dir", default="/tmp/tor-geo")
    parser.add_argument("--from-calendar", type=Path, help="take --url from the newest create row")
    args = parser.parse_args()
    if args.from_calendar and not args.url:
        args.url = latest_stream_url(args.from_calendar)
        if args.url is None:
            print("OK: geo_probe: no create row in calendar log; nothing to probe", file=sys.stderr)
            return 0
    if args.write_torrc:
        args.write_torrc.parent.mkdir(parents=True, exist_ok=True)
        args.write_torrc.write_text(torrc(args.data_dir, 9050), encoding="utf-8")
        return 0
    if not args.url or not args.out:
        parser.error("--url and --out are required to probe")
    result = probe(args.url, args.socks)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"OK: geo_probe: {result['verdict']} (exit={result['exit_country']}, status={result['status']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
