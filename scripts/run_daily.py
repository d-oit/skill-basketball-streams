#!/usr/bin/env python3
"""run_daily.py — Phase 0 daily shell: search ladder → candidate ledger.

Phase 0 of `live-stream-runtime-spec.md` §16. This shell deliberately has **no
calendar access at all**: its only job is to populate the recall denominator so
that later phases can be judged, and so a bad run cannot embarrass a subscriber.

It walks the search ladder (Exa → TinyFish), records every candidate any backend
surfaced into the ledger, and prints recall. Live-signal detection here is a
cheap heuristic, not the 7-check pipeline — Phase 0 states are all
`unverifiable` by construction.

Usage:
    python3 scripts/run_daily.py --dest .tmp/telemetry --dry-run
    python3 scripts/run_daily.py --dest .tmp/telemetry --run-id 2026-09-14T08:30Z
    python3 scripts/run_daily.py --queries queries.txt --backend tinyfish
    python3 scripts/run_daily.py --check-backends   # offline CI preflight

Streams:
    stdout  payload only — with `--json`, the JSON document and nothing else
    stderr  every human-readable progress and OK line

Exit codes:
    0  PASS — at least one candidate recorded (or a dry run completed)
    1  FAIL — every backend unavailable, or zero candidates surfaced
    2  USAGE — bad arguments
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # direct CLI execution: `python3 scripts/run_daily.py`
    from candidates import (  # noqa: E402
        DEFAULT_LEDGER,
        append_rows,
        normalise_row,
        recall_metrics,
        read_ledger,
    )
except ImportError:  # imported as a package module, e.g. scripts.run_daily
    from scripts.candidates import (  # type: ignore[no-redef]
        DEFAULT_LEDGER,
        append_rows,
        normalise_row,
        recall_metrics,
        read_ledger,
    )

DEFAULT_QUERIES = (
    "Basketball Bundesliga live stream heute kostenlos",
    "EuroLeague live stream free today",
    "Basketball Champions League live kostenlos",
    "MagentaSport kostenlos Basketball live",
    "FIBA Basketball live stream",
    "Dyn Sport Mix Basketball live",
    "ALBA Berlin live stream frei",
    "FC Bayern Basketball live kostenlos",
)

LIVE_URL_HINTS = ("/live", "live-", "/watch?v=", "/tv/live")
LIVE_TEXT_HINTS = ("live", "livestream", "live stream", "kostenlos live")
TIMEOUT_SECONDS = 20
USER_AGENT = "basketball-streams-runtime/1.0"


class Backend:
    name = ""
    kind = "hosted"
    env_var = ""
    # Why a search produced nothing, when the reason was the transport rather
    # than the query. A configured-but-rejected key and a query with no results
    # both reach the caller as an empty list, so without this the loud failure at
    # the end of a run can only say "surfaced no candidates" — leaving the one
    # cause an operator can act on to be guessed. A set key is not a valid one;
    # `capture_transcripts.py --list-models` exists for the same split on the
    # model ladder. Presence stays the preflight's question (`backend_status`);
    # this is the run's, and it never changes which exit code is reached.
    last_error = ""

    def available(self) -> bool:
        return bool(os.environ.get(self.env_var)) if self.env_var else True

    def status_detail(self, configured: bool) -> str:
        """The preflight line for this rung — `backend_status` prints it verbatim."""
        return f"{self.env_var} is set" if configured else f"{self.env_var} is not set"

    def search(self, query: str, limit: int) -> list[dict]:
        raise NotImplementedError

    def _fail(self, reason: str) -> None:
        """Record the FIRST transport failure, so a message is deterministic."""
        self.last_error = self.last_error or reason

    def _result(self, query: str, items: list[dict]) -> list[dict]:
        return [item for item in items if isinstance(item, dict)]


class ExaBackend(Backend):
    name = "exa-mcp"
    env_var = "EXA_API_KEY"
    endpoint = "https://api.exa.ai/search"

    def search(self, query: str, limit: int) -> list[dict]:
        key = os.environ.get(self.env_var)
        if not key:
            return []
        payload = json.dumps(
            {"query": query, "numResults": limit, "useAutoprompt": True}
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            self._fail(f"HTTP {exc.code}")
            return []
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._fail(f"{type(exc).__name__}: {exc}")
            return []
        return self._result(
            query,
            [
                {"url": item.get("url", ""), "title": item.get("title", "")}
                for item in body.get("results", [])
            ],
        )


class ExaMcpKeylessBackend(Backend):
    """The hosted Exa MCP server in its free keyless mode.

    https://exa.ai/docs/get-started/exa-mcp: `https://mcp.exa.ai/mcp` serves
    rate-limited search with NO API key (their "Keyless" auth mode). This rung
    exists so Phase 0 can populate the recall ledger with zero secrets — the
    alternative was a preflight that red every day until a human bought a key.

    Position in the ladder: AFTER the paid `exa-mcp` rung and BEFORE `tinyfish`.
    Same provider as rung 1, so the rung steps aside entirely when EXA_API_KEY
    is set — a second rung re-searching every query would only duplicate rows
    (dedup hides it) and double the rate-limit spend. TinyFish stays last: it
    is the independent index, i.e. the rung that catches Exa-wide outages.

    Transport notes, from the live probe this class was written against: the
    endpoint speaks Streamable HTTP — POST JSON-RPC, `Accept: application/json,
    text/event-stream`, take `mcp-session-id` from the initialize response and
    send it back on every later call. The tool answer comes as an SSE `data:`
    line whose JSON `result.content[0].text` holds records separated by `---`
    with `Title:` / `URL:` line prefixes. A free-tier rate limit surfaces as
    HTTP 429, which `_fail` records and the ladder answers by moving on.
    """

    name = "exa-mcp-keyless"
    env_var = ""  # free: nothing to configure
    endpoint = "https://mcp.exa.ai/mcp"
    tool = "web_search_exa"

    def available(self) -> bool:
        # Step aside when the paid rung is configured (see class docstring).
        return not os.environ.get("EXA_API_KEY")

    def status_detail(self, configured: bool) -> str:
        if configured:
            return "free keyless tier of https://mcp.exa.ai/mcp (no key needed)"
        return "stepping aside: EXA_API_KEY is set, the paid rung serves"

    def _rpc(self, session: str | None, payload: dict) -> tuple[str | None, str]:
        """One JSON-RPC POST. Returns `(session_id_to_keep, raw_body)`."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
        }
        if session:
            headers["mcp-session-id"] = session
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            kept = resp.headers.get("mcp-session-id") or session
            return kept, resp.read().decode("utf-8", "replace")

    def _result_text(self, body: str) -> str:
        """The tool's text payload from an SSE `data:` line (or a bare JSON body)."""
        candidates = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        candidates.append(body)
        for raw in candidates:
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if isinstance(message, dict) and isinstance(message.get("result"), dict):
                content = message["result"].get("content") or []
                chunks = [
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                text = "\n".join(chunks)
                if text.strip():
                    return text
        return ""

    @staticmethod
    def _records(text: str) -> list[dict]:
        """`Title:`/`URL:` blocks separated by `---` into candidate dicts."""
        records: list[dict] = []
        for block in text.split("\n---"):
            url = title = ""
            for line in block.splitlines():
                if line.startswith("URL:") and not url:
                    url = line[len("URL:"):].strip()
                elif line.startswith("Title:") and not title:
                    title = line[len("Title:"):].strip()
            if url:
                records.append({"url": url, "title": title})
        return records

    def search(self, query: str, limit: int) -> list[dict]:
        session: str | None = None
        try:
            session, _ = self._rpc(
                None,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "basketball-streams-runtime", "version": "1.0"},
                    },
                },
            )
            try:
                self._rpc(session, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                pass  # a rejected notification does not gate the search itself
            _, body = self._rpc(
                session,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": self.tool,
                        "arguments": {"query": query, "numResults": max(1, limit)},
                    },
                },
            )
        except urllib.error.HTTPError as exc:
            self._fail(f"HTTP {exc.code}")
            return []
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._fail(f"{type(exc).__name__}: {exc}")
            return []
        text = self._result_text(body)
        if not text:
            self._fail("MCP response carried no result content")
            return []
        return self._result(query, self._records(text)[:limit])


class TinyFishSearchBackend(Backend):
    name = "tinyfish"
    env_var = "TINYFISH_API_KEY"
    endpoint = "https://api.search.tinyfish.ai"

    def search(self, query: str, limit: int) -> list[dict]:
        key = os.environ.get(self.env_var)
        if not key:
            return []
        params = urllib.parse.urlencode({"query": query, "page": 0})
        request = urllib.request.Request(
            f"{self.endpoint}?{params}",
            headers={"X-API-Key": key, "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
                body = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            self._fail(f"HTTP {exc.code}")
            return []
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._fail(f"{type(exc).__name__}: {exc}")
            return []
        return self._result(
            query,
            [
                {"url": item.get("url", ""), "title": item.get("title", "")}
                for item in body.get("results", [])[:limit]
            ],
        )


ALL_BACKENDS: dict[str, Backend] = {
    backend.name: backend
    for backend in (ExaBackend(), ExaMcpKeylessBackend(), TinyFishSearchBackend())
}
LADDER = ("exa-mcp", "exa-mcp-keyless", "tinyfish")

# Hosts that must never be recorded as candidate stream sources.
NEVER_SOURCE_HOSTS = (
    "google.com",
    "bing.com",
    "duckduckgo.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "reddit.com",
    "wikipedia.org",
)


def backend_status(names: list[str] | None = None) -> list[tuple[str, bool, str]]:
    """`[(backend, configured, detail)]`, decided by the environment alone.

    Deliberately **presence**, not validity, and therefore offline and
    deterministic: a CI preflight that made a network call would red a run for a
    reason that is not the run's fault. A key that is present but rejected is a
    different failure, reported by the run itself.
    """
    status: list[tuple[str, bool, str]] = []
    for name in names if names is not None else LADDER:
        backend = ALL_BACKENDS[name]
        configured = backend.available()
        status.append((name, configured, backend.status_detail(configured)))
    return status


def looks_live(url: str, title: str = "") -> bool:
    """Cheap Phase-0 live signal. NOT a substitute for the 7 checks."""
    lowered_url = (url or "").lower()
    if any(hint in lowered_url for hint in LIVE_URL_HINTS):
        return True
    lowered_title = (title or "").lower()
    return any(hint in lowered_title for hint in LIVE_TEXT_HINTS)


def host_of(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_plumbing(url: str) -> bool:
    host = host_of(url)
    return any(host == bad or host.endswith("." + bad) for bad in NEVER_SOURCE_HOSTS)


def collect_candidates(
    queries: list[str],
    *,
    backends: list[Backend],
    limit: int,
    run_id: str,
    ts: str,
) -> tuple[list[dict], list[str], list[str]]:
    """Run the ladder over every query.

    Returns `(ledger_rows, contributed, attempted)`. The two backend lists are
    kept apart on purpose: a backend that was configured but returned nothing is
    **attempted**, not a source of candidates, and reporting it as one would make
    "5 candidates from [exa-mcp, tinyfish]" indistinguishable from the case
    where exa contributed all five.
    """
    rows: list[dict] = []
    contributed: list[str] = []
    attempted: list[str] = []
    seen: set[tuple[str, str]] = set()
    for query in queries:
        for backend in backends:
            if not backend.available():
                continue
            if backend.name not in attempted:
                attempted.append(backend.name)
            before = len(rows)
            for item in backend.search(query, limit):
                url = str(item.get("url") or "").strip()
                if not url or is_plumbing(url):
                    continue
                key = (backend.name, url)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    normalise_row(
                        {
                            "run_id": run_id,
                            "url": url,
                            "backend": backend.name,
                            "live_signal": looks_live(url, item.get("title", "")),
                            # Phase 0 validates nothing: everything is unverifiable.
                            "disposition": "unverifiable",
                            "first_seen_run": run_id,
                            "ts": ts,
                        },
                        ts=ts,
                        run_id=run_id,
                    )
                )
            if len(rows) > before and backend.name not in contributed:
                contributed.append(backend.name)
    return rows, contributed, attempted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 0 daily shell: search ladder into the candidate ledger.",
    )
    parser.add_argument("--dest", help="telemetry directory (ledger lives here)")
    parser.add_argument("--ledger", help="explicit ledger path (overrides --dest)")
    parser.add_argument("--queries", help="file with one query per line")
    parser.add_argument("--backend", action="append", default=[],
                        help="restrict to a backend (repeatable)")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--run-id")
    parser.add_argument("--now", help="ISO-8601 override (for tests)")
    parser.add_argument("--dry-run", action="store_true", help="do not write")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--check-backends",
        action="store_true",
        help="fail when NO search backend is configured; offline and "
             "deterministic, so a CI preflight can gate on it",
    )
    args = parser.parse_args()

    # A scheduled run with no search backend configured at all is a configuration
    # fault, not a quiet day: it would look exactly like "no streams announced
    # today". This answers that offline, so a preflight can fail loudly on it.
    #
    # It matters here because Phase 0's whole purpose is to populate the recall
    # denominator — a green run that recorded nothing silently shrinks the only
    # evidence a later change can be judged against.
    if args.check_backends:
        unknown = [name for name in args.backend if name not in ALL_BACKENDS]
        if unknown:
            print(f"FAIL: run_daily: unknown backend(s) {unknown}", file=sys.stderr)
            sys.exit(2)
        status = backend_status(args.backend or None)
        for name, configured, detail in status:
            print(f"{'OK  ' if configured else 'NO  '} backend {name}: {detail}")
        if not any(configured for _, configured, _ in status):
            print(
                "FAIL: run_daily: no search backend is configured — set "
                "EXA_API_KEY or TINYFISH_API_KEY, or drop --backend so the "
                "keyless rung (exa-mcp-keyless) serves. Phase 0 writes nothing "
                "without one, so the run would be green and the ledger empty",
                file=sys.stderr,
            )
            sys.exit(1)
        print(
            "note: this reports configuration, not validity — a set key can "
            "still be rejected. The ladder run itself proves that."
        )
        sys.exit(0)

    if args.now:
        try:
            now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        except ValueError:
            print(f"FAIL: run_daily: --now {args.now!r} is not ISO-8601", file=sys.stderr)
            sys.exit(2)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)

    ts = now.isoformat()
    run_id = args.run_id or now.strftime("%Y-%m-%dT%H:%MZ")

    if args.queries:
        queries_path = Path(args.queries)
        if not queries_path.is_file():
            print(f"FAIL: run_daily: {queries_path}: file not found", file=sys.stderr)
            sys.exit(2)
        queries = [
            line.strip()
            for line in queries_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    else:
        queries = list(DEFAULT_QUERIES)
    if not queries:
        print("FAIL: run_daily: no queries to run", file=sys.stderr)
        sys.exit(2)

    names = args.backend or list(LADDER)
    backends = [ALL_BACKENDS[name] for name in names if name in ALL_BACKENDS]
    if not backends:
        print(f"FAIL: run_daily: unknown backend(s) {names}", file=sys.stderr)
        sys.exit(2)

    available = [b.name for b in backends if b.available()]
    if not available:
        missing = sorted({b.env_var for b in backends if b.env_var})
        print(
            "FAIL: run_daily: every backend is unavailable "
            f"(missing {', '.join(missing) or 'the selected rungs'}) — "
            "Phase 0 writes nothing so the ledger stays honest",
            file=sys.stderr,
        )
        sys.exit(1)

    rows, contributed, attempted = collect_candidates(
        queries, backends=backends, limit=args.limit, run_id=run_id, ts=ts
    )

    ledger = Path(args.ledger) if args.ledger else (
        Path(args.dest) / "candidates.jsonl" if args.dest else Path(DEFAULT_LEDGER)
    )

    if not rows:
        # Name the cause where there is one: a rejected key is actionable, an
        # empty query is not, and this is the only place either is reported.
        causes = [f"{b.name}: {b.last_error}" for b in backends if b.last_error]
        detail = f" — {'; '.join(causes)}" if causes else ""
        print(
            f"FAIL: run_daily: backends {attempted or available} surfaced no candidates "
            f"across {len(queries)} queries{detail}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Human-readable progress goes to stderr so that `--json` keeps stdout as
    # payload-only. A summary line printed before the JSON makes stdout
    # unparseable, which is a bug this repo has now fixed in four writers
    # (upsert_events, calendar_io, synthesise_eval_case, and here).
    if args.dry_run:
        print(
            f"OK: run_daily: dry-run, {len(rows)} candidates from {contributed} "
            f"of {attempted} attempted ({len(queries)} queries) -> {ledger}",
            file=sys.stderr,
        )
    else:
        written = append_rows(rows, ledger)
        print(
            f"OK: run_daily: appended {written} candidates from {contributed} "
            f"of {attempted} attempted -> {ledger}",
            file=sys.stderr,
        )

    metrics = recall_metrics(read_ledger(ledger) or rows)
    print(
        "OK: run_daily: candidates={unique_games} eligible={eligible} "
        "captured={captured} recall={recall}".format(**metrics),
        file=sys.stderr,
    )
    if args.json:
        print(
            json.dumps(
                {"metrics": metrics, "backends": contributed, "attempted": attempted},
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
