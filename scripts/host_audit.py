#!/usr/bin/env python3
"""host_audit.py — pin every approved host to a committed measurement.

`references/approved-sources.md` has claimed a Tier 5 since it was written, and
on 2026-11-27 eight of its eighteen rows turned out to be hosts that could never
have produced a stream: six names did not resolve at all, one was a supporters'
club behind a failing TLS handshake, and one failed the handshake while
redirecting to its real site. Nothing caught them because nothing compared the
approved list to the network, and `retired-sources` can only police a host after
someone has noticed it is dead.

This is that missing comparison, split the way this repository splits every
recorded corpus:

    record   network, run by a human, writes `config/host-audit.json`
    check    offline, every run of the gate, asserts the snapshot still decides

`check` is not a liveness test — CI must never depend on 61 hosts answering. It
asserts three things the snapshot can decide without a socket:

1. every host `config/sources.json` approves has a recorded measurement, so a
   new source cannot be added without saying what it answered;
2. no recorded host with an empty DNS answer (`dns: []`, a name that does not
   exist) is still approved — the exact state eight Tier 5 rows were in;
3. the snapshot is non-empty and dated, because a snapshot that parses to
   nothing would pass everything.

A read timeout or a 403 is recorded and tolerated: that is traffic shaping, not
evidence about a broadcast. Only a name that does not resolve is a fact about
the name.

    python3 scripts/host_audit.py record --root .
    python3 scripts/host_audit.py check --root .

Exit codes:
    0  PASS — every approved host is recorded and none recorded dead is approved
    1  FAIL — an unrecorded or dead host is approved, or the snapshot is unusable
    2  USAGE — bad arguments or a missing registry
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

SNAPSHOT_RELATIVE_PATH = "config/host-audit.json"
SOURCES_RELATIVE_PATH = "config/sources.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
TIMEOUT = 10
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def host_of(value: str) -> str:
    """The bare, lowercase, `www.`-less host of a URL or a registry value.

    A registry value can be a host with a path (`x.com/FIBA`), a full URL, or a
    plain host. One normaliser must accept all three, or a recorded host would
    never match the entry it is supposed to cover.
    """
    text = str(value).split("://", 1)[-1].strip().lower()
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].rstrip(".")
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if ":" in text:
        text = text.rsplit(":", 1)[0]
    return text[4:] if text.startswith("www.") else text


def approved_hosts(root: Path) -> list[str]:
    path = root / SOURCES_RELATIVE_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    hosts: list[str] = []
    for entry in data.get("sources", []):
        for key in ("domain", "domains", "social", "handles"):
            value = entry.get(key)
            values = [value] if isinstance(value, str) else list(value or [])
            hosts.extend(host_of(item) for item in values if item)
    return sorted(set(hosts))


def resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, 443)})
    except OSError:
        return []


def fetch(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Language": "de,en"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read(200_000).decode("utf-8", "ignore")
            title = _TITLE.search(body)
            return {
                "tls_ok": True,
                "status": int(response.status),
                "final": response.geturl(),
                "title": re.sub(r"\s+", " ", title.group(1)).strip()[:120] if title else "",
            }
    except ssl.SSLError as exc:
        return {"tls_ok": False, "status": None, "final": None, "error": f"TLS: {exc.reason or exc}"[:120]}
    except urllib.error.HTTPError as exc:
        # A 401/403/429 is a server deciding not to answer this client. The
        # host exists, which is the only fact this file records.
        return {"tls_ok": True, "status": int(exc.code), "final": exc.geturl(), "error": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"tls_ok": False, "status": None, "final": None, "error": f"{type(exc).__name__}: {exc}"[:120]}


def measure(host: str) -> dict:
    """Probe one host the way a browser would: bare first, then `www.`."""
    record: dict = {"dns": resolve(host)}
    if not record["dns"]:
        record.update(tls_ok=False, status=None, final=None, error="does not resolve")
        return record
    result = fetch(f"https://{host}/")
    if result["status"] is None and not host.startswith("www."):
        # A bare host with no A record for HTTPS is often served only on `www.`;
        # the answer belongs to the same site, so record the winning name.
        alt = fetch(f"https://www.{host}/")
        if alt["status"] is not None:
            alt["via"] = f"www.{host}"
            result = alt
    record.update(result)
    return record


def load_snapshot(root: Path) -> dict:
    path = root / SNAPSHOT_RELATIVE_PATH
    if not path.is_file():
        raise ValueError(
            f"{SNAPSHOT_RELATIVE_PATH} is missing — without it this gate cannot "
            "tell a measured host from an assumed one"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    hosts = data.get("hosts")
    if not isinstance(hosts, dict) or not hosts:
        raise ValueError(
            f"{SNAPSHOT_RELATIVE_PATH} records no hosts; a snapshot that is "
            "empty passes every check while proving nothing"
        )
    if not str(data.get("measured_on") or "").strip():
        raise ValueError(f"{SNAPSHOT_RELATIVE_PATH} has no 'measured_on' date")
    return data


def check(root: Path) -> int:
    try:
        approved = approved_hosts(root)
    except (OSError, ValueError) as exc:
        print(f"FAIL: host_audit: {SOURCES_RELATIVE_PATH}: {exc}", file=sys.stderr)
        return 2
    if not approved:
        print(
            f"FAIL: host_audit: {SOURCES_RELATIVE_PATH} approves no host, so this "
            "gate would assert nothing",
            file=sys.stderr,
        )
        return 1
    try:
        snapshot = load_snapshot(root)
    except (OSError, ValueError) as exc:
        print(f"FAIL: host_audit: {exc}", file=sys.stderr)
        return 1

    recorded = {host_of(host): record for host, record in snapshot["hosts"].items()}
    problems: list[str] = []
    unrecorded = [host for host in approved if host not in recorded]
    if unrecorded:
        problems.append(
            f"{len(unrecorded)} approved host(s) have no recorded measurement "
            f"({', '.join(unrecorded[:6])}"
            f"{', …' if len(unrecorded) > 6 else ''}) — run "
            f"`python3 scripts/host_audit.py record` and commit the snapshot"
        )
    dead = [
        host
        for host in approved
        if host in recorded and not recorded[host].get("dns")
    ]
    if dead:
        problems.append(
            f"{len(dead)} approved host(s) do not resolve at all and are still "
            f"declared sources ({', '.join(dead)}) — a name that does not exist "
            "cannot serve a stream; correct the domain and retire the old one in "
            f"{'config/retired-sources.json'}"
        )

    for problem in problems:
        print(f"FAIL: host_audit: {problem}", file=sys.stderr)
    if problems:
        return 1

    responding = sum(1 for host in approved if recorded[host].get("status"))
    shaped = sum(
        1
        for host in approved
        if recorded[host].get("dns") and not recorded[host].get("status")
    )
    print(
        f"OK: host_audit: {len(approved)} approved host(s) recorded on "
        f"{snapshot['measured_on']}; {responding} answered a request, {shaped} "
        "answered none (shaping, not evidence), 0 approved host without a DNS answer"
    )
    return 0


def record(root: Path) -> int:
    try:
        approved = approved_hosts(root)
    except (OSError, ValueError) as exc:
        print(f"FAIL: host_audit: {SOURCES_RELATIVE_PATH}: {exc}", file=sys.stderr)
        return 2
    hosts: dict[str, dict] = {}
    for host in approved:
        record_row = measure(host)
        hosts[host] = record_row
        state = record_row.get("status") or record_row.get("error")
        print(f"  {host:34} dns={'yes' if record_row['dns'] else 'NONE':4} {state}", file=sys.stderr)
    payload = {
        "$comment": (
            "Measured on the date below by `python3 scripts/host_audit.py record`, "
            "one row per host `config/sources.json` approves. `dns: []` means the "
            "name does not exist; `status: null` means the host exists but "
            "answered no request (blocked, timed out or failed the handshake), "
            "which is traffic shaping or site configuration, not evidence about a "
            "broadcast. `python3 scripts/host_audit.py check` is the offline gate: "
            "it fails when an approved host is unrecorded or recorded dead. Do not "
            "hand-edit a row to silence it."
        ),
        "version": "1.0.0",
        "measured_on": date.today().isoformat(),
        "probe": "TLS + DNS, bare then www, browser User-Agent; see scripts/host_audit.py",
        "hosts": hosts,
    }
    path = root / SNAPSHOT_RELATIVE_PATH
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dead = [host for host, row in hosts.items() if not row["dns"]]
    print(
        f"OK: host_audit: recorded {len(hosts)} host(s) to {SNAPSHOT_RELATIVE_PATH} "
        f"on {payload['measured_on']}"
        + (f"; {len(dead)} do not resolve and must be retired ({', '.join(dead)})" if dead else "")
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("record", "check"))
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    return record(root) if args.mode == "record" else check(root)


if __name__ == "__main__":
    sys.exit(main())
