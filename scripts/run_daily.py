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
import re
import sys
import time
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
    from youtube_tab import (  # noqa: E402
        discover_handles as _discover_youtube_tabs,
    )
    from youtube_tab import (  # noqa: E402
        discover_handles_from_urls as _discover_youtube_expanded,
    )
    from queries import expand_queries as _expand_queries  # noqa: E402
except ImportError:  # imported as a package module, e.g. scripts.run_daily
    from scripts.candidates import (  # type: ignore[no-redef]
        DEFAULT_LEDGER,
        append_rows,
        normalise_row,
        recall_metrics,
        read_ledger,
    )
    from scripts.youtube_tab import (  # type: ignore[no-redef]
        discover_handles as _discover_youtube_tabs,
    )
    from scripts.youtube_tab import (  # type: ignore[no-redef]
        discover_handles_from_urls as _discover_youtube_expanded,
    )
    from scripts.queries import (  # type: ignore[no-redef]
        expand_queries as _expand_queries,
    )

DEFAULT_QUERIES = (
    "Basketball Bundesliga live stream heute kostenlos",
    "NBBL Basketball Livestream",
    "JBBL Basketball Livestream",
    "Basketball Regionalliga Livestream kostenlos",
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

# A search is a **retryable GET**, unlike a calendar create. 429 and the
# gateway-family 5xx mean the request did not reach the index, so repeating it
# cannot duplicate a result; a connection reset has the same property. 4xx
# (except 429) is a request or credential fault and repeating it only spends the
# remaining quota on the same refusal.
RETRYABLE_HTTP_STATUS = (429, 500, 502, 503, 504)
MAX_RETRIES = 2
RETRY_BASE_SECONDS = 0.25


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    """A server's `Retry-After` hint, when it is a plain number of seconds."""
    try:
        value = str((exc.headers or {}).get("Retry-After") or "").strip()
        if value:
            return max(0.0, float(value))
    except (AttributeError, TypeError, ValueError):
        pass
    return None


def _open_with_retry(request: urllib.request.Request, *, backend: "Backend"):
    """`urlopen` with bounded retries for transport faults.

    Every search rung used to make exactly one attempt: a 429 from the free Exa
    tier, a 502 from a gateway, or a dropped connection ended that backend for
    the whole run, and the ledger lost every query it would have answered. The
    retry is deliberately limited to failures that prove the request was not
    served — see `RETRYABLE_HTTP_STATUS` — so this can never turn one query into
    duplicate rows. `Retry-After` is honoured when present, because a fixed
    exponential backoff that ignores the server's own hint keeps hammering a
    rate limit.
    """
    last_exc: BaseException | None = None
    for attempt in range(MAX_RETRIES + 1):
        backend.requests += 1
        try:
            return urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in RETRYABLE_HTTP_STATUS or attempt >= MAX_RETRIES:
                raise
            delay = _retry_after_seconds(exc)
        except (urllib.error.URLError, OSError) as exc:
            last_exc = exc
            if attempt >= MAX_RETRIES:
                raise
            delay = None
        backend.retries += 1
        time.sleep(delay if delay is not None else RETRY_BASE_SECONDS * (2**attempt))
    raise last_exc if last_exc is not None else RuntimeError("urlopen exhausted")


def unique_queries(queries: list[str]) -> list[str]:
    """Queries with case/whitespace duplicates removed, first spelling kept.

    The ladder is charged per query per backend. A query file that repeats a
    line — or a generated list that spells one query two ways — used to buy the
    same results twice and push a free tier towards its rate limit for nothing.
    """
    seen: set[str] = set()
    out: list[str] = []
    for query in queries:
        cleaned = " ".join(str(query).split())
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


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
    # Per-run counters. Class-level defaults are zero so a third-party backend
    # that does not call `super().__init__` still reports something sane; the
    # ladder resets them at the start of every `collect_candidates`.
    queries = 0
    requests = 0
    retries = 0
    failures = 0
    elapsed_seconds = 0.0

    def reset_stats(self) -> None:
        self.last_error = ""
        self.queries = 0
        self.requests = 0
        self.retries = 0
        self.failures = 0
        self.elapsed_seconds = 0.0

    def stats(self) -> dict:
        return {
            "queries": self.queries,
            "requests": self.requests,
            "retries": self.retries,
            "failures": self.failures,
            "elapsed_ms": round(self.elapsed_seconds * 1000),
            "last_error": self.last_error,
        }

    def available(self) -> bool:
        return bool(os.environ.get(self.env_var)) if self.env_var else True

    def status_detail(self, configured: bool) -> str:
        """The preflight line for this rung — `backend_status` prints it verbatim."""
        return f"{self.env_var} is set" if configured else f"{self.env_var} is not set"

    def search(self, query: str, limit: int) -> list[dict]:
        raise NotImplementedError

    def _fail(self, reason: str) -> None:
        """Record the FIRST transport failure, so a message is deterministic."""
        self.failures += 1
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
            with _open_with_retry(request, backend=self) as resp:
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
        with _open_with_retry(request, backend=self) as resp:
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
            with _open_with_retry(request, backend=self) as resp:
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


DIRECT_LISTS_CONFIG = Path(__file__).resolve().parent.parent / "config" / "direct-lists.json"


def load_direct_lists(root: Path | str | None = None) -> list[dict]:
    """Free listing pages from `config/direct-lists.json`.

    Same authority rule as the YouTube allow-list: a config that names lists
    is authoritative, and a missing/unreadable config means no lists rather
    than a hard-coded fallback — a fallback would make a removed page
    impossible to revoke.
    """
    # A caller that names a root gets that root's config; otherwise the module
    # constant decides — which is what makes the config replaceable in tests
    # and the absence of one a clean empty rather than a hard-coded fallback.
    config = (
        DIRECT_LISTS_CONFIG
        if root is None
        else Path(root) / "config" / "direct-lists.json"
    )
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    lists = payload.get("lists")
    if not isinstance(lists, list):
        return []
    return [
        {"name": str(item.get("name") or ""), "url": str(item.get("url") or "")}
        for item in lists
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]


LIST_LINK_RE = re.compile(
    r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def extract_list_links(html: str, base: str) -> list[dict]:
    """Stream-leaning links from a free listing page.

    A listing page (MDR livestreams, sportschau basketball) links hundreds of
    pages, nearly all articles. Only links with a live/basketball signal in
    the URL or the anchor text become candidates — the same cheap signal Phase
    0 uses everywhere else, so an article about last night's game stays a
    non-row the same way it does from a search backend.
    """
    import html as _html

    links: list[dict] = []
    seen: set[str] = set()
    for match in LIST_LINK_RE.finditer(html or ""):
        href = match.group(1).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        url = urllib.parse.urljoin(base, href)
        if not url.startswith("http") or url in seen:
            continue
        text = " ".join(_html.unescape(_TAG_RE.sub(" ", match.group(2))).split())
        if not looks_live(url, text):
            continue
        seen.add(url)
        links.append({"url": url, "title": text or url})
    return links


class DirectListBackend(Backend):
    """Keyless fetch of known free listing pages — no index between us.

    Search backends ask an index what it remembers; these pages (MDR
    livestreams, sportschau basketball) ARE the broadcaster's own listing, and
    their markup is server-rendered (verified 2026-11-13 via plain GET).
    One fetch per configured page per run, cached like the YouTube tab rung,
    stdlib only, always available. A page that fails to fetch degrades to no
    rows — the search rungs still cover the run.
    """

    name = "direct-list"
    env_var = ""  # free: nothing to configure

    def available(self) -> bool:
        return True

    def status_detail(self, configured: bool) -> str:
        return "keyless fetch of config/direct-lists.json free listing pages"

    def reset_stats(self) -> None:
        super().reset_stats()
        self._rows_cache: list[dict] | None = None

    def search(self, query: str, limit: int) -> list[dict]:
        cached = getattr(self, "_rows_cache", None)
        if cached is None:
            cached = []
            for entry in load_direct_lists():
                url = entry["url"]
                try:
                    request = urllib.request.Request(
                        url, headers={"User-Agent": USER_AGENT}
                    )
                    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
                        html = resp.read().decode("utf-8", "replace")
                except (urllib.error.URLError, OSError, ValueError) as exc:
                    self._fail(f"{entry.get('name', url)}: {type(exc).__name__}")
                    continue
                for link in extract_list_links(html, url):
                    cached.append(link)
                    if len(cached) >= max(1, limit):
                        break
            self._rows_cache = cached
        return cached


ARENA_GAME_LINK_RE = re.compile(
    r'<a\b[^>]*\bhref="(/tv/[^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def extract_arena_links(
    html: str, base: str = "https://www.magenta.tv"
) -> list[dict]:
    """Per-game program links from a rendered magenta.tv arena page.

    The arena text alone proves *something* is free but names no watchable
    game: the 2026-11-11 live run omitted Deutschland–Ungarn and
    Chemnitz–Napoli for lack of a per-game stream URL while both were free on
    the rendered `/sport` page. The links (`/tv/live-<slug>/<id>`) only exist
    after rendering — the server shell carries none — so this parses the
    rendered DOM, deduped by path, first (longest-titled) spelling kept.
    Only `/tv/` program links: nav (`/sport`, `/magenta-sport`) is not a game.
    """
    import html as _html

    links: list[dict] = []
    seen: set[str] = set()
    for match in ARENA_GAME_LINK_RE.finditer(html or ""):
        path = match.group(1).strip()
        if not path or path in seen:
            continue
        seen.add(path)
        text = " ".join(_html.unescape(_TAG_RE.sub(" ", match.group(2))).split())
        links.append({"url": base.rstrip("/") + path, "title": text})
    return links


class RenderArenaBackend(Backend):
    """Fetch a KNOWN arena URL by rendering it, not by searching for it.

    Every other rung in this ladder is a *search*, and that is why the free
    arena stayed invisible: `magenta.tv/sport` is a dynamic app shell that no
    index has ever seen, so a search for its games returns nothing and the run
    reports "no free streams found today" — the one failure that is invisible
    from the outside. Measured 2026-09-29: a plain GET returns 858 bytes of
    shell, and Firecrawl with a valid key returns `SCRAPE_ALL_ENGINES_FAILED`,
    so the hosted rungs cannot read it either. `patchright` renders it
    (10,085 chars; `KOSTENLOS` x11, `OHNE LOGIN` x10).

    This rung therefore takes a URL, not a query, and returns that one page as a
    single candidate. It is **last** in the ladder because it costs a browser
    launch and cannot answer an open-ended question; the search rungs stay
    ahead of it and it is the rung that catches a game no index carries.

    The arena is a constant, not configuration: it is the only host the skill
    may read this way, and an agent cannot steer this rung to an arbitrary URL.
    """

    name = "render-arena"
    env_var = "FIRECRAWL_API_KEY"  # presence-gated; patchright needs no key
    arena = "https://www.magenta.tv/sport"

    #: The marker the page is read for. Absent from the 858-byte shell, present
    #: only after rendering, so its absence is the signal to try the next rung.
    marker = "KOSTENLOS"

    def available(self) -> bool:
        # Gated on a credential so a runner with no browser still skips it
        # cleanly. The key itself is NOT what makes this work — see the class
        # docstring — it is the flag that says a rendering backend is licensed.
        return bool(os.environ.get(self.env_var)) and _patchright_importable()

    def status_detail(self, configured: bool) -> str:
        if not configured:
            return "no rendering credential; the arena cannot be read"
        return f"renders {self.arena} with patchright (marker: {self.marker!r})"

    def search(self, query: str, limit: int) -> list[dict]:
        text, html, error = _render_page(self.arena)
        if error:
            # Name the render failure rather than reporting only the missing
            # marker: a browser that would not start and a page that rendered
            # without the free-arena section need different fixes, and both used
            # to arrive as the same empty string.
            self._fail(f"render failed: {error}")
            return []
        if self.marker not in text:
            # Recorded as a transport failure, not an empty result: an empty
            # result would read as "nothing free today", which is precisely the
            # conclusion this rung exists to make unprovable.
            self._fail(f"rendered page lacks {self.marker!r} ({len(text)} chars)")
            return []
        rows = [{"url": self.arena, "title": "MagentaSport free arena (rendered)"}]
        for link in extract_arena_links(html):
            rows.append(
                {
                    "url": link["url"],
                    "title": link["title"] or "MagentaSport free arena game",
                }
            )
        return rows


def _patchright_importable() -> bool:
    """True when the local renderer can be imported. Offline and cheap."""
    try:
        import patchright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def _render_page(url: str, wait_ms: int = 6000) -> tuple[str, str, str]:
    """Render `url` headless. Returns `(visible_text, html, error_message)`.

    The wait is deliberate: the page is a client-rendered app shell, so its
    text does not exist at `domcontentloaded`. A shorter wait returns the shell
    again and the caller mistakes "unreadable" for "nothing free".

    The error is returned rather than swallowed. Returning `""` for both a
    missing browser and a page that loaded without the expected section made
    "the renderer is broken" indistinguishable from "nothing free today", which
    is the failure this rung exists to rule out.

    The HTML is carried alongside the text because per-game program links live
    in the rendered DOM (`extract_arena_links`), not in the visible text.
    """
    try:
        from patchright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - import guarded by caller
        return "", "", f"patchright unavailable: {type(exc).__name__}: {exc}"
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(wait_ms)
                return page.inner_text("body"), page.content(), ""
            finally:
                browser.close()
    except Exception as exc:
        return "", "", f"{type(exc).__name__}: {exc}"


def _render_once(url: str, wait_ms: int = 6000) -> tuple[str, str]:
    """Render `url` headless. Returns `(visible_text, error_message)`."""
    text, _, error = _render_page(url, wait_ms=wait_ms)
    return text, error


EVIDENCE_WATCH_PER_HANDLE = 6
ARENA_TEXT_CAP = 20000


def build_phase0_evidence(
    *,
    watch_per_handle: int = EVIDENCE_WATCH_PER_HANDLE,
    fetch=None,
    render=None,
    now: datetime | None = None,
) -> dict:
    """Bot-blocked-surface evidence for the planner agent, recorded on the runner.

    The daily agent is restricted to `webfetch` + `websearch`, and both surfaces
    below are unreadable to it: YouTube serves boilerplate to a non-JS client
    (Live-filter pages, `/streams` tabs, and even RSS return nothing usable
    through its fetcher — measured 2026-11-12, all 7 channel RSS feeds 404'd
    and every tab came back boilerplate), while `magenta.tv/sport` is a
    client-rendered SPA shell. The runner CAN read both (`urllib` for YouTube's
    server-rendered tab/RSS/watch pages, `patchright` for the arena), so this
    records them minutes before the agent runs and the workflow attaches the
    file to its prompt. The agent still validates Checks 1-5 itself against
    readable sources; this file's verbatim, same-run evidence is its Check 6/7
    proof for exactly the URLs named here — the render its runtime cannot do.
    A URL absent from this file has no such proof and is validated as before.

    `fetch`/`render` are seams for offline tests. `render` replaces the
    `patchright` page render and receives the arena URL.
    """
    moment = now or datetime.now(timezone.utc)
    recorded_at = moment.isoformat()
    evidence: dict = {
        "recorded_at": recorded_at,
        "youtube": {"channels": []},
        "magenta_arena": {
            "url": RenderArenaBackend.arena,
            "rendered_at": None,
            "markers": {},
            "text": "",
            "games": [],
        },
    }
    try:
        channels = _discover_youtube_tabs(
            max_watch=max(0, watch_per_handle), fetch=fetch
        )
    except Exception as exc:  # noqa: BLE001 - degraded, never fatal
        evidence["youtube"] = {
            "channels": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    else:
        for channel in channels:
            evidence["youtube"]["channels"].append(
                {
                    "handle": channel.get("handle", ""),
                    "channel_id": channel.get("channel_id", ""),
                    "videos": [
                        {
                            "url": video.get("url", ""),
                            "video_id": video.get("video_id", ""),
                            "title": video.get("title", ""),
                            "published": video.get("published", ""),
                            "scheduled_start": video.get("scheduled_start"),
                            "is_live_now": bool(video.get("is_live_now", False)),
                        }
                        for video in channel.get("videos", [])
                    ],
                }
            )
    try:
        if render is None:
            text, html, error = (
                _render_page(RenderArenaBackend.arena)
                if _patchright_importable()
                else ("", "", "patchright unavailable: no local renderer")
            )
        else:
            text, html, error = render(RenderArenaBackend.arena)
    except Exception as exc:  # noqa: BLE001 - degraded, never fatal
        text, html, error = "", "", f"{type(exc).__name__}: {exc}"
    arena = evidence["magenta_arena"]
    if error:
        arena["error"] = error
    elif RenderArenaBackend.marker not in text:
        arena["error"] = (
            f"rendered page lacks {RenderArenaBackend.marker!r} ({len(text)} chars)"
        )
    else:
        arena["rendered_at"] = recorded_at
        arena["markers"] = {
            "KOSTENLOS": text.count("KOSTENLOS"),
            "OHNE LOGIN": text.count("OHNE LOGIN"),
        }
        arena["text"] = text[:ARENA_TEXT_CAP]
        arena["games"] = [
            {"url": link["url"], "title": link["title"]}
            for link in extract_arena_links(html)
        ]
    return evidence


class YouTubeTabBackend(Backend):
    """The approved channels' own surfaces: `/streams` tabs plus RSS.

    Every other rung in this ladder is an open-ended *search*, and that is why
    scheduled YouTube games stayed invisible: the Live-filter `results` pages
    return boilerplate to a non-JS client, so a search for the games returned
    nothing and the run reported a quiet day. The tab HTML and the RSS feed
    are server-rendered (`scripts/youtube_tab.py`), so this rung fetches them
    with stdlib `urllib` — no key, no browser, always available.

    Fetch-once per run: `search` is called per query but the tab/RSS fetch
    happens a single time, cached until `reset_stats` clears it at the next
    `collect_candidates`. Phase 0 posture is IDs plus RSS titles only
    (`max_watch=0`): rows here are `unverifiable` by construction, so
    scheduled-start proof would only spend the run's request budget.
    """

    name = "youtube-tab"
    env_var = ""  # free: nothing to configure

    def available(self) -> bool:
        return True

    def status_detail(self, configured: bool) -> str:
        return "keyless stdlib fetch of approved channel tabs + RSS (no key needed)"

    def reset_stats(self) -> None:
        super().reset_stats()
        self._rows_cache: list[dict] | None = None

    def search(self, query: str, limit: int) -> list[dict]:
        cached = getattr(self, "_rows_cache", None)
        if cached is None:
            try:
                channels = _discover_youtube_tabs(max_watch=0)
            except Exception as exc:  # noqa: BLE001 - degraded, never fatal
                self._fail(f"{type(exc).__name__}: {exc}")
                return []
            cached = [
                {"url": video["url"], "title": video["title"]}
                for channel in channels
                for video in channel["videos"]
            ]
            self._rows_cache = cached
        return cached


ALL_BACKENDS: dict[str, Backend] = {
    backend.name: backend
    for backend in (
        ExaBackend(),
        ExaMcpKeylessBackend(),
        TinyFishSearchBackend(),
        DirectListBackend(),
        YouTubeTabBackend(),
        RenderArenaBackend(),
    )
}
LADDER = (
    "exa-mcp",
    "exa-mcp-keyless",
    "tinyfish",
    "direct-list",
    "youtube-tab",
    "render-arena",
)


def expand_youtube_leads(
    rows: list[dict],
    *,
    run_id: str,
    ts: str,
    max_new: int = 8,
    fetch=None,
) -> list[dict]:
    """Tab+RSS discovery for YouTube channels the ladder merely mentioned.

    Search hits like `youtube.com/@new-club/live` name a channel the
    approved-handle rung never fetches. This resolves up to `max_new` such
    channels (keyless, IDs + RSS titles only) and appends the videos as
    `youtube-expand` rows — still `unverifiable` Phase 0 leads, still
    quarantined for calendar use until Check 3 / `approve_source.py` says
    otherwise. Degraded, never fatal: discovery must not red a run that
    already has rows.
    """
    try:
        from youtube_tab import load_allowed_handles  # noqa: E402
    except ImportError:
        from scripts.youtube_tab import load_allowed_handles  # type: ignore[no-redef]
    try:
        known = list(load_allowed_handles() or ())
    except Exception:  # noqa: BLE001 - degraded, never fatal
        known = []
    try:
        channels = _discover_youtube_expanded(
            [str(row.get("url") or "") for row in rows],
            known_handles=known,
            fetch=fetch,
            max_watch=0,
            max_new=max_new,
        )
    except Exception:  # noqa: BLE001 - degraded, never fatal
        return []
    extra: list[dict] = []
    seen = {str(row.get("url") or "") for row in rows}
    for channel in channels:
        for video in channel.get("videos", []):
            url = str(video.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            extra.append(
                normalise_row(
                    {
                        "run_id": run_id,
                        "url": url,
                        "backend": "youtube-expand",
                        "query": "",
                        "live_signal": looks_live(url, video.get("title", "")),
                        "disposition": "unverifiable",
                        "first_seen_run": run_id,
                        "ts": ts,
                    },
                    ts=ts,
                    run_id=run_id,
                )
            )
    return extra

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
    queries = unique_queries(queries)
    for backend in backends:
        backend.reset_stats()
    for query in queries:
        for backend in backends:
            if not backend.available():
                continue
            if backend.name not in attempted:
                attempted.append(backend.name)
            before = len(rows)
            backend.queries += 1
            started = time.perf_counter()
            try:
                items = backend.search(query, limit)
            finally:
                backend.elapsed_seconds += time.perf_counter() - started
            for item in items:
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
                            "query": query,
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
    parser.add_argument(
        "--fixtures",
        help="fixture JSONL (scripts/fixtures.py output): per-game queries "
             "are merged into the run so the ladder asks for games that exist",
    )
    parser.add_argument(
        "--fixture-cap", type=int, default=12,
        help="max fixture-derived queries merged into the run (default: 12)",
    )
    parser.add_argument("--backend", action="append", default=[],
                        help="restrict to a backend (repeatable)")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--run-id")
    parser.add_argument("--now", help="ISO-8601 override (for tests)")
    parser.add_argument("--dry-run", action="store_true", help="do not write")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--evidence-out",
        help="write bot-blocked-surface evidence for the planner agent to PATH "
             "(YouTube channel videos with watch-page starts + rendered magenta "
             "arena) instead of running the search ladder",
    )
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
    if args.evidence_out:
        evidence = build_phase0_evidence()
        out = Path(args.evidence_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        sections = [
            bool(evidence["youtube"].get("channels")),
            bool(evidence["magenta_arena"].get("games")),
        ]
        if not any(sections):
            causes = [
                evidence["youtube"].get("error", "no channel videos"),
                evidence["magenta_arena"].get("error", "no arena games"),
            ]
            print(
                f"FAIL: run_daily: evidence empty (youtube: {causes[0]}; "
                f"arena: {causes[1]})",
                file=sys.stderr,
            )
            sys.exit(1)
        payload = json.dumps(evidence, ensure_ascii=False, indent=1) + "\n"
        if args.dry_run:
            print(payload, end="")
        else:
            out.write_text(payload, encoding="utf-8")
        n_videos = sum(
            len(channel["videos"])
            for channel in evidence["youtube"].get("channels", [])
        )
        n_games = len(evidence["magenta_arena"].get("games", []))
        print(
            f"OK: run_daily: evidence {n_videos} videos + {n_games} arena games "
            f"-> {out}",
            file=sys.stderr,
        )
        if args.json and not args.dry_run:
            print(payload, end="")
        return
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
    if args.fixtures:
        try:
            fixture_rows: list[dict] = []
            for line in Path(args.fixtures).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("fixtures"), list):
                    fixture_rows.extend(
                        item for item in payload["fixtures"] if isinstance(item, dict)
                    )
                elif isinstance(payload, dict):
                    fixture_rows.append(payload)
        except OSError as exc:
            print(f"FAIL: run_daily: {args.fixtures}: {exc}", file=sys.stderr)
            sys.exit(2)
        before = len(queries)
        queries = _expand_queries(
            queries, fixture_rows, cap=max(0, args.fixture_cap)
        )
        print(
            f"OK: run_daily: merged {len(queries) - before} fixture-derived "
            f"queries from {len(fixture_rows)} fixture(s)",
            file=sys.stderr,
        )
    unique = unique_queries(queries)
    if len(unique) != len(queries):
        print(
            f"note: run_daily: dropped {len(queries) - len(unique)} duplicate "
            f"quer{'y' if len(queries) - len(unique) == 1 else 'ies'} before the ladder",
            file=sys.stderr,
        )
    queries = unique

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
    expanded = expand_youtube_leads(rows, run_id=run_id, ts=ts, fetch=None)
    if expanded:
        rows.extend(expanded)
        if "youtube-expand" not in contributed:
            contributed.append("youtube-expand")
        print(
            f"OK: run_daily: youtube-expand: {len(expanded)} video(s) from "
            f"channels the ladder merely mentioned",
            file=sys.stderr,
        )

    # One line per rung, including the ones that answered nothing: the aggregate
    # "surfaced no candidates" cannot say which backend was rate-limited, how
    # many retries it needed, or how long it took. `WARN` marks a transport
    # failure (the only kind `last_error` records); a clean rung gets `OK`.
    for backend in backends:
        if backend.queries == 0 and not backend.available():
            continue
        marker = "WARN" if backend.last_error else "OK  "
        detail = f": {backend.last_error}" if backend.last_error else ""
        stats = backend.stats()
        print(
            f"{marker} run_daily: {backend.name}: queries={stats['queries']} "
            f"requests={stats['requests']} retries={stats['retries']} "
            f"failures={stats['failures']} elapsed_ms={stats['elapsed_ms']}{detail}",
            file=sys.stderr,
        )

    ledger = Path(args.ledger) if args.ledger else (
        Path(args.dest) / "candidates.jsonl" if args.dest else Path(DEFAULT_LEDGER)
    )

    if args.fixtures and ledger.is_file() and rows:
        # The fixture pass appends to a ledger the base pass already wrote.
        # Query-independent rungs are excluded from it by the caller, but a
        # search backend can still return a URL it already surfaced for a
        # generic query — a blind append would record that game twice and the
        # denominator would grow without covering anything new.
        seen_pairs: set[tuple[str, str]] = set()
        for line in ledger.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                stored = json.loads(line)
            except ValueError:
                continue
            if isinstance(stored, dict):
                seen_pairs.add(
                    (str(stored.get("backend") or ""), str(stored.get("url") or ""))
                )
        before_dedupe = len(rows)
        rows = [
            row
            for row in rows
            if (str(row.get("backend") or ""), str(row.get("url") or ""))
            not in seen_pairs
        ]
        if len(rows) != before_dedupe:
            print(
                f"OK: run_daily: dropped {before_dedupe - len(rows)} row(s) "
                f"already in {ledger}",
                file=sys.stderr,
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
                {
                    "metrics": metrics,
                    "backends": contributed,
                    "attempted": attempted,
                    "backend_stats": {
                        backend.name: backend.stats() for backend in backends
                    },
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
