#!/usr/bin/env python3
"""render_ladder.py — render a page through an ordered ladder of backends.

Implements `live-stream-runtime-spec.md` §5.4 and §18.

Three designed-in properties matter more than the individual rungs:

1. **`blocked` is a distinct outcome from a real error.** `401/403/429/451` mean
   *this backend was refused*, so the ladder keeps climbing; a genuine failure
   stops it. This is what makes "403 means climb, not reject the game"
   enforceable in code rather than in prose.
2. **`skipped` is a third outcome: the rung was never available.** A missing
   optional package or an unset key is not a failure of the target and must not
   end the climb. It used to: with nothing installed the ladder gave up on its
   *first* rung (`curl_cffi not installed` read as a real error), so a stdlib-only
   environment could never produce evidence for any game and the install order
   was inverted — the ladder was only ever as good as its cheapest rung.
3. **AGPL tooling can never be a default rung.** `assert_shippable()` is called
   on every ladder the module builds, and a test asserts the default ladder
   contains zero AGPL entries, so `nodriver` cannot be quietly promoted.

Rungs, cheapest-and-most-permissive first:

| Rung | Kind | Licence | JS | Default |
|---|---|---|---|---|
| `urllib` | local | MIT (stdlib) | no | on |
| `firecrawl` | hosted | proprietary (free tier) | yes | on |
| `tinyfish` | hosted | proprietary (free tier) | yes | on |
| `curl_cffi` | local | MIT | no | on |
| `patchright` | local | Apache-2.0 | yes | on |
| `camoufox` | local | MPL-2.0 | yes | on |
| `nodriver` | local | **AGPL-3.0** | yes | **opt-in only** |

`urllib` exists so the ladder is **never empty**: this repo is stdlib-only by
intent (`requirements-dev.txt` installs pytest and nothing else), so without it
every rung is either a key we may not have or a package we deliberately do not
require. On a JS host it proves the gate posture (the app shell, or a 403) and
on a server-rendered host it can carry real evidence.

Every rung is invoked lazily, so this module imports with **stdlib only** and a
missing optional package degrades to a reported `not_installed` result rather
than an ImportError at import time.

Usage:
    python3 scripts/render_ladder.py --list
    python3 scripts/render_ladder.py --url https://www.magenta.tv/tv/live-x --plan
    python3 scripts/render_ladder.py --url https://www.magenta.tv/tv/live-x --probe

Exit codes:
    0  PASS — a rung returned usable evidence (or --list/--plan)
    1  FAIL — every rung was blocked, uninstalled, or errored
    2  USAGE — bad arguments
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:  # direct CLI execution
    from team_tokens import pairing_count, split_teams, team_matches
except ImportError:  # imported as a package module
    from scripts.team_tokens import (  # type: ignore
        pairing_count,
        split_teams,
        team_matches,
    )

# --- Evidence markers -------------------------------------------------------
#
# These two sets are a **conjunction**: a page carries stream evidence only if it
# shows a media-bearing token *and* a live-state token. Bare vocabulary
# ("player", "dash", "live", "läuft", "livestream") is deliberately NOT used:
# it is matched against the whole document, and every modern page embeds its JS
# runtime inline. Verified against the recorded corpus in
# `tests/fixtures/pages/` (2026-09-15) — the old vocabulary matched
# `youtube.com/watch?v=<a recorded VOD>`, the YouTube home page, the
# MagentaSport announce home page and the Basketball Champions League home page.
# Every marker here must be justified by a recorded page that proves it
# discriminates; see `tests/fixtures/pages/README.md`.
#
# The split below is the other half of the fix. Markers that are *markup* facts
# (`<video`, a manifest URL, an `og:video` meta, a visible "Jetzt live" badge)
# are matched against the document with its `<script>` blocks removed, because
# that is where the noise lives. Markers that are *structured data* (YouTube's
# own player fields) only ever exist inside a script block, so they are matched
# against the raw document — they are exact JSON keys, so noise cannot fake them.
SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)

MEDIA_MARKERS_MARKUP = (
    "<video",                      # real player element
    ".m3u8",                       # HLS manifest
    ".mpd",                        # DASH manifest
    "application/x-mpegurl",       # HLS content type
    "application/dash+xml",        # DASH content type
    "og:video",                    # page publishes a real video asset
)
MEDIA_MARKERS_DATA = (
    '"islivecontent":true',        # YouTube: this video *is* a live broadcast
)
LIVE_STATE_MARKERS_MARKUP = (
    "jetzt live",                  # DE live badge (MagentaSport/MagentaTV)
)
LIVE_STATE_MARKERS_DATA = (
    '"islivecontent":true',        # YouTube: live broadcast
    '"islivenow":true',            # YouTube: broadcasting right now
    '"livebroadcastcontent":"live"',  # YouTube renderer/API field
)
BLOCKED_STATUSES = frozenset({401, 403, 429, 451})
MAX_STRIKES = 3
DEFAULT_TIMEOUT = 20.0
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0 Safari/537.36"
)

LICENCE_MIT = "MIT"
LICENCE_APACHE = "Apache-2.0"
LICENCE_MPL = "MPL-2.0"
LICENCE_AGPL = "AGPL-3.0"
LICENCE_PROPRIETARY = "proprietary"

FORBIDDEN_IN_SHIPPED_CODE = frozenset({LICENCE_AGPL})


# --- Game anchoring ---------------------------------------------------------
#
# `has_stream_evidence` answers "is this page playing a live stream?". It cannot
# answer "is it playing *this* game?" — and a page that streams something else
# entirely passes it. The corpus README records that residual risk; this is how
# it is closed: match the expected clubs against the document's **own name**
# (its `<title>`, `og:title`, `twitter:title` and YouTube's `videoDetails.title`)
# rather than against the body.
#
# Anchoring on the title rather than the body is the same discipline as the
# marker split above: a body mentions every club it links to, so a body-wide
# match would be as meaningless as a body-wide "live" match. The broadcast's
# name is what names the broadcast.
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_TITLE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:title|twitter:title)["\'][^>]*>',
    re.IGNORECASE,
)
META_CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']', re.IGNORECASE)
# Deliberately a plain `[^"]*` capture and not an escape-aware group: the value
# is used for matching and reporting, and a title containing an escaped quote
# truncates rather than failing. Written this way because the escape-aware
# version of this pattern silently matched nothing (a missing quote after
# `videoDetails`) and only the recorded page caught it.
VIDEO_DETAILS_TITLE_RE = re.compile(
    r'"videoDetails"\s*:\s*\{[^}]*?"title"\s*:\s*"([^"]*)"', re.DOTALL
)
MAX_TITLE_CHARS = 400


def _unescape_title(value: str) -> str:
    """Undo the backslash escapes a title carries inside a JSON string."""
    return (
        value.replace("\\\"", '"')
        .replace("\\/", "/")
        .replace("\\n", " ")
        .replace("\\\\", "\\")
        .strip()[:MAX_TITLE_CHARS]
    )


def page_titles(body: str) -> tuple[str, ...]:
    """Every name the document gives itself, in priority order, deduped."""
    titles: list[str] = []
    for match in META_TITLE_RE.finditer(body):
        content = META_CONTENT_RE.search(match.group(0))
        if content and content.group(1).strip():
            titles.append(_unescape_title(content.group(1)))
    tag = TITLE_RE.search(body)
    if tag and tag.group(1).strip():
        titles.append(_unescape_title(re.sub(r"\s+", " ", tag.group(1))))
    player = VIDEO_DETAILS_TITLE_RE.search(body)
    if player and player.group(1).strip():
        titles.append(_unescape_title(player.group(1)))
    seen: list[str] = []
    for title in titles:
        if title and title not in seen:
            seen.append(title)
    return tuple(seen)


@dataclass
class GameAnchor:
    """Whether a page names a specific game, and how confident that is."""

    matched: bool = False
    title: str = ""
    missing: tuple[str, ...] = ()
    ambiguous: bool = False
    titles: tuple[str, ...] = ()

    def reason(self) -> str:
        teams = ", ".join(self.missing) if self.missing else ""
        if self.ambiguous:
            return (
                f"{self.title!r} lists more than one pairing, so it cannot be "
                f"attributed to this game"
            )
        if not self.titles:
            return "the page gives no title, so it cannot be attributed to this game"
        if self.matched:
            return f"named in {self.title!r}"
        return f"no title names all of [{teams}]"


def anchor_game(body: str, teams: object) -> GameAnchor:
    """Does this document name *this* game?

    Requires a single title that contains every expected club. A title that
    advertises several pairings (`"A vs B | C vs D"`, the signature of a
    keyword-stuffed live stream) satisfies that test for every pairing it lists,
    so it is reported as `ambiguous` and does **not** count as a match — silently
    picking one would put an arbitrary game in the calendar.
    """
    expected = [team for team in split_teams(teams) if team.strip()]
    titles = page_titles(body)
    if not expected:
        return GameAnchor(matched=False, titles=titles, missing=())
    for title in titles:
        for clause in _title_clauses(title):
            # The clause must *be* a pairing, not merely mention both clubs:
            # without this, a competition name (`Tauihi Basketball Aotearoa`) is a
            # subset of the same clause and matches as though it were a club.
            if pairing_count(clause) < 1:
                continue
            if not all(
                any(team_matches(team, part) for part in _teams_in_clause(clause))
                for team in expected
            ):
                continue
            if pairing_count(title) > 1:
                return GameAnchor(
                    matched=False, title=title, ambiguous=True, titles=titles
                )
            return GameAnchor(matched=True, title=title, titles=titles)
    return GameAnchor(matched=False, titles=titles, missing=tuple(expected))


def _teams_in_clause(clause: str) -> list[str]:
    """The club spellings a pairing clause advertises, as one name per side.

    `"... Tauranga Whai v Northern Kāhu"` → `["Tauranga Whai", "Northern Kāhu"]`.
    A clause with no separator yields the whole clause, so a caller that expects a
    single club can still be matched.
    """
    sides = split_teams(clause)
    return sides if len(sides) > 1 else [clause]


def _title_clauses(title: str) -> list[str]:
    """Candidate club spellings inside a title, so `team_matches` can apply.

    Splits on the pairing separators and on `|`/`/`, which is where a title puts
    "one of N games" rather than "this game".
    """
    return [part.strip() for part in re.split(r"\||/|\u2013|\u2014", title) if part.strip()]


def _strip_scripts(body: str) -> str:
    """Return `body` with inline `<script>…</script>` blocks removed.

    `has_stream_evidence` matches markup markers against this view so that a
    word which only occurs as a JavaScript identifier or bundle string cannot
    count as stream evidence. An unterminated `<script>` removes nothing, so a
    malformed document fails *open* to the raw view rather than silently losing
    its evidence.
    """
    return SCRIPT_RE.sub(" ", body)


class LicenceError(RuntimeError):
    """Raised when a non-opt-in rung carries a licence we cannot ship."""


@dataclass
class FetchResult:
    ok: bool = False
    status: int | None = None
    text: str | None = None
    html: str | None = None
    blocked: bool = False
    skipped: bool = False   # rung unavailable (no key / not installed)
    rung: str = ""
    error: str = ""
    elapsed_ms: int = 0

    def has_stream_evidence(self) -> bool:
        """Media-bearing marker AND live-state marker, per §6 of the magenta-tv
        reference.

        Why it is this narrow: a bare 200 on a JS app shell (or on any page with
        an inline bundle) is *not* a working stream, and the caller turns this
        verdict into a calendar write. Requiring a media token rules out the
        shell; requiring a live-state token rules out a recorded video that
        happens to publish `og:video` (verified on a real VOD watch page).

        `"isLiveContent":true` satisfies both halves on purpose: it is YouTube's
        own structured boolean meaning "this video is a live broadcast", so it is
        simultaneously the media and the state signal. It cannot be produced by
        bundle noise, and a VOD carries the same key with `false`.
        """
        raw = f"{self.text or ''}\n{self.html or ''}".lower()
        if not raw.strip():
            return False
        markup = _strip_scripts(raw)
        has_media = any(m in markup for m in MEDIA_MARKERS_MARKUP) or any(
            m in raw for m in MEDIA_MARKERS_DATA
        )
        has_live_state = any(m in markup for m in LIVE_STATE_MARKERS_MARKUP) or any(
            m in raw for m in LIVE_STATE_MARKERS_DATA
        )
        return has_media and has_live_state


def attempt_state(result: "FetchResult") -> str:
    """The one classification of an attempt, shared by the report and the ledger.

    `skipped` is deliberately distinct from `failed`. A rung with no key and a
    rung the target refused both climb the ladder, and neither is a broken
    rung — but a transcript that calls them all `failed` makes a missing
    credential look exactly like a dead backend, which is the reading
    `scripts/rung_health.py` then commits to the telemetry branch for months.
    """
    if result.skipped:
        return "skipped"
    if result.blocked:
        return "blocked"
    if result.ok and result.has_stream_evidence():
        return "evidence"
    if result.ok:
        return "ok-no-evidence"
    return "failed"


class Rung:
    """One fetch backend. Subclasses override `fetch`."""

    name: str = ""
    kind: str = "local"          # "hosted" | "local"
    license: str = LICENCE_MIT
    requires_js: bool = False
    opt_in: bool = False
    package: str = ""            # importable name for local rungs

    def installed(self) -> bool:
        """Hosted rungs check for a key; local rungs check the package."""
        return True

    def fetch(self, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> FetchResult:
        raise NotImplementedError

    def _skip(self, reason: str) -> FetchResult:
        """This rung is unavailable — the ladder must climb, not stop."""
        return FetchResult(ok=False, rung=self.name, error=reason, skipped=True)


def _http_post_json(
    endpoint: str, payload: dict, headers: dict, timeout: float
) -> tuple[int | None, dict]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(
                response.read().decode("utf-8", "replace") or "{}"
            )
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, {"error": str(exc)}


class HostedRung(Rung):
    kind = "hosted"
    license = LICENCE_PROPRIETARY
    requires_js = True
    env_var = ""
    endpoint = ""

    def installed(self) -> bool:
        import os

        return bool(os.environ.get(self.env_var))

    def fetch(self, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> FetchResult:
        import os

        started = time.monotonic()
        key = os.environ.get(self.env_var)
        if not key:
            return self._skip(f"{self.env_var} not set")
        status, payload = self._request(url, key, timeout)
        elapsed = int((time.monotonic() - started) * 1000)
        result = FetchResult(
            status=status, rung=self.name, elapsed_ms=elapsed,
            error=str(payload.get("error") or ""),
        )
        if status in BLOCKED_STATUSES:
            result.blocked = True
            return result
        if status is None or status >= 400:
            return result
        result.ok = True
        result.text = payload.get("text")
        result.html = payload.get("html")
        return result

    def _request(self, url: str, key: str, timeout: float) -> tuple[int | None, dict]:
        raise NotImplementedError


class FirecrawlRung(HostedRung):
    name = "firecrawl"
    env_var = "FIRECRAWL_API_KEY"
    endpoint = "https://api.firecrawl.dev/v1/scrape"

    def _request(self, url: str, key: str, timeout: float) -> tuple[int | None, dict]:
        status, payload = _http_post_json(
            self.endpoint,
            {
                "url": url,
                "formats": ["markdown", "html"],
                "waitFor": 6000,
                "onlyMainContent": False,
            },
            {"Authorization": f"Bearer {key}"},
            timeout,
        )
        data = payload.get("data") or {}
        return status, {
            "text": data.get("markdown"),
            "html": data.get("html"),
            "error": payload.get("error"),
        }


class TinyFishRung(HostedRung):
    name = "tinyfish"
    env_var = "TINYFISH_API_KEY"
    # [VERIFY] Fetch endpoint path — Search is api.search.tinyfish.ai; Fetch is
    # configured here so a correction is a one-line change.
    endpoint = "https://api.tinyfish.ai/fetch"

    def _request(self, url: str, key: str, timeout: float) -> tuple[int | None, dict]:
        status, payload = _http_post_json(
            self.endpoint, {"url": url}, {"X-API-Key": key}, timeout
        )
        return status, {
            "text": payload.get("text") or payload.get("content"),
            "html": payload.get("html"),
            "error": payload.get("error"),
        }


class LocalRung(Rung):
    kind = "local"

    def installed(self) -> bool:
        try:
            __import__(self.package)
        except ImportError:
            return False
        return True


class UrllibRung(Rung):
    """Stdlib `urllib.request`. No key, no package, always available.

    Not the strongest rung — a JS-rendered host returns its app shell, which is
    exactly why a shell is *not* evidence on its own. Its job is to make the
    ladder able to answer at all, and to record *why* a page yielded nothing (a
    shell, a 403) instead of reporting that no rung existed.
    """

    name = "urllib"
    kind = "local"
    license = LICENCE_MIT
    requires_js = False
    package = ""

    def installed(self) -> bool:
        return True

    def fetch(self, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> FetchResult:
        started = time.monotonic()
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - an unreadable body is not fatal
                pass
            return FetchResult(
                ok=False, status=exc.code, blocked=exc.code in BLOCKED_STATUSES,
                text=body or None, html=body or None, rung=self.name,
                error=f"HTTP {exc.code}",
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return FetchResult(
                ok=False, rung=self.name, error=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        return FetchResult(
            ok=200 <= status < 300, status=status, blocked=status in BLOCKED_STATUSES,
            text=body, html=body, rung=self.name,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


class CurlCffiRung(LocalRung):
    name = "curl_cffi"
    license = LICENCE_MIT
    requires_js = False
    package = "curl_cffi"

    def fetch(self, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> FetchResult:
        if not self.installed():
            return self._skip("curl_cffi not installed")
        from curl_cffi import requests  # type: ignore

        started = time.monotonic()
        try:
            response = requests.get(
                url, impersonate="chrome", timeout=timeout
            )
        except Exception as exc:  # noqa: BLE001 - backend-specific failures
            return FetchResult(
                ok=False, rung=self.name, error=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        return FetchResult(
            ok=200 <= response.status_code < 300,
            status=response.status_code,
            blocked=response.status_code in BLOCKED_STATUSES,
            text=response.text,
            html=response.text,
            rung=self.name,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


class _PlaywrightishRung(LocalRung):
    """Shared driver for the Playwright-family rungs (patchright / camoufox)."""

    requires_js = True
    driver = "sync_playwright"
    browser = "chromium"
    launch_kwargs: dict = {}

    def fetch(self, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> FetchResult:
        if not self.installed():
            return self._skip(f"{self.package} not installed")
        started = time.monotonic()
        module = __import__(self.package, fromlist=[self.driver])
        playwright = getattr(module, self.driver)()
        try:
            browser = getattr(playwright, self.browser).launch(**self.launch_kwargs)
            page = browser.new_page(user_agent=USER_AGENT)
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            page.wait_for_timeout(3000)
            html = page.content()
            text = page.inner_text("body")
            status = response.status if response else None
            return FetchResult(
                ok=status is not None and status < 400,
                status=status,
                blocked=status in BLOCKED_STATUSES,
                text=text,
                html=html,
                rung=self.name,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return FetchResult(
                ok=False, rung=self.name, error=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        finally:
            try:
                playwright.stop()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass


class PatchrightRung(_PlaywrightishRung):
    name = "patchright"
    license = LICENCE_APACHE
    package = "patchright"
    browser = "chromium"
    launch_kwargs = {"channel": "chrome"}


class CamoufoxRung(_PlaywrightishRung):
    name = "camoufox"
    license = LICENCE_MPL
    package = "camoufox"
    browser = "firefox"
    launch_kwargs = {}


class NodriverRung(LocalRung):
    """AGPL-3.0. The only rung with zero blocked targets in the 2026 benchmark,
    and the only one that may never become a default. Opt-in by design."""

    name = "nodriver"
    license = LICENCE_AGPL
    requires_js = True
    package = "nodriver"
    opt_in = True

    def fetch(self, url: str, *, timeout: float = DEFAULT_TIMEOUT) -> FetchResult:
        if not self.installed():
            return self._skip(
                "nodriver not installed — optional AGPL-3.0 rung; install it "
                "yourself if your use permits AGPL"
            )
        started = time.monotonic()
        try:
            import asyncio

            import nodriver  # type: ignore

            async def _get() -> tuple[str, str]:
                browser = await nodriver.start()
                try:
                    tab = await browser.get(url)
                    await tab.sleep(3)
                    html = await tab.get_content()
                    return html, html
                finally:
                    browser.stop()

            html, text = asyncio.run(_get())
            return FetchResult(
                ok=True, status=200, text=text, html=html, rung=self.name,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return FetchResult(
                ok=False, rung=self.name, error=str(exc),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )


ALL_RUNGS: dict[str, Rung] = {
    rung.name: rung
    for rung in (
        UrllibRung(),
        FirecrawlRung(),
        TinyFishRung(),
        CurlCffiRung(),
        PatchrightRung(),
        CamoufoxRung(),
        NodriverRung(),
    )
}

DEFAULT_LADDER = (
    "urllib", "firecrawl", "tinyfish", "curl_cffi", "patchright", "camoufox"
)

HOST_LADDERS: dict[str, tuple[str, ...]] = {
    # SPA: JS is required, so browserless rungs only measure the gate posture —
    # which is worth one free request if nothing else is installed. Kept last so
    # a real renderer is never pre-empted by a request that cannot succeed.
    "magenta.tv": ("firecrawl", "tinyfish", "curl_cffi", "patchright", "urllib"),
    # Server-rendered: no browser needed, so the keyless rung is genuinely first
    # — verified against the recorded pages (988 KB and 222 KB of real HTML).
    "championsleague.basketball": ("urllib", "curl_cffi", "firecrawl", "tinyfish"),
    "magentasport.de": ("urllib", "curl_cffi", "firecrawl", "tinyfish"),
    # YouTube is handled by the API/live filter, never by rendering.
    "youtube.com": (),
    "www.youtube.com": (),
}

JS_REQUIRED_HOSTS = frozenset({"magenta.tv", "www.magenta.tv"})


def host_of(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def assert_shippable(rungs: Iterable[Rung]) -> None:
    """A default (non-opt-in) rung may never be AGPL."""
    for rung in rungs:
        if rung.opt_in:
            continue
        if rung.license in FORBIDDEN_IN_SHIPPED_CODE:
            raise LicenceError(
                f"{rung.name} is {rung.license}; it may only ship as an opt-in rung"
            )


def js_hint(url: str) -> bool:
    """True when the host is known to require JS execution to render."""
    return host_of(url) in JS_REQUIRED_HOSTS


def build_ladder(
    url: str, *, enabled: Iterable[str] | None = None, allow_opt_in: bool = False
) -> list[Rung]:
    """Ordered rungs for a URL, with the licence guard applied."""
    host = host_of(url)
    names = HOST_LADDERS.get(host, DEFAULT_LADDER)
    if enabled is not None:
        allowed = set(enabled)
        names = tuple(name for name in names if name in allowed)
    rungs = [ALL_RUNGS[name] for name in names if name in ALL_RUNGS]
    if allow_opt_in:
        rungs = rungs + [r for r in ALL_RUNGS.values() if r.opt_in]
    assert_shippable(rungs)
    return rungs


@dataclass
class RungHealth:
    """Consecutive-failure tracking; parks a rung for the rest of the run."""

    max_strikes: int = MAX_STRIKES
    strikes: dict[str, int] = field(default_factory=dict)
    parked: dict[str, str] = field(default_factory=dict)

    def record(self, result: FetchResult) -> None:
        if result.ok or result.skipped:
            # A rung that is not installed did not fail, and parking it would
            # hide it from the run that installs it.
            self.strikes.pop(result.rung, None)
            return
        strikes = self.strikes.get(result.rung, 0) + 1
        self.strikes[result.rung] = strikes
        if strikes >= self.max_strikes:
            self.parked[result.rung] = result.error or result.status and (
                f"HTTP {result.status}"
            ) or "unknown"

    def is_parked(self, name: str) -> bool:
        return name in self.parked

    def reset(self) -> None:
        self.strikes.clear()
        self.parked.clear()


def fetch_with_ladder(
    url: str,
    *,
    rungs: list[Rung] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    health: RungHealth | None = None,
    allow_opt_in: bool = False,
) -> tuple[FetchResult, list[FetchResult]]:
    """Climb the ladder. Returns (final_result, every_attempt).

    Stops at the first rung that returns usable evidence. Climbs past `blocked`
    (the target refused us) and past `skipped` (the rung was never available).
    Stops on a real error — a stronger rung will not fix malformed input.
    """
    ladder = rungs if rungs is not None else build_ladder(
        url, allow_opt_in=allow_opt_in
    )
    assert_shippable(ladder)
    tracker = health or RungHealth()
    needs_js = js_hint(url)
    attempts: list[FetchResult] = []

    for rung in ladder:
        if tracker.is_parked(rung.name):
            attempts.append(
                FetchResult(
                    ok=False, rung=rung.name,
                    error=f"parked after {tracker.max_strikes} consecutive failures",
                )
            )
            continue
        # Skip browserless local rungs when the host provably needs JS, unless
        # a browser rung is unavailable — then the cheap rung is still worth a
        # try because it can reveal the gate posture.
        if needs_js and rung.kind == "local" and not rung.requires_js:
            if any(
                r.kind == "local" and r.requires_js and r.installed()
                for r in ladder
            ):
                attempts.append(
                    FetchResult(
                        ok=False,
                        rung=rung.name,
                        # A deliberate skip, so it must carry the flag: without
                        # it the report and the rung ledger both classify a rung
                        # that was never tried as `failed`, and three of those
                        # park a rung that is working perfectly.
                        skipped=True,
                        error=(
                            "skipped: host requires JS and a browser rung is "
                            "available"
                        ),
                    )
                )
                continue

        result = rung.fetch(url, timeout=timeout)
        attempts.append(result)
        tracker.record(result)
        if result.ok and result.has_stream_evidence():
            return result, attempts
        if result.blocked or result.skipped:
            continue
        if result.ok:
            # Reachable but no stream evidence: keep climbing, the page may be
            # a shell for this rung only.
            continue
        break
    return (attempts[-1] if attempts else FetchResult()), attempts


def _read_body(path: Path) -> str:
    """Read a saved page, transparently un-gzipping it."""
    raw = path.read_bytes()
    if path.suffix == ".gz":
        import gzip

        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def _judge_body(args: argparse.Namespace) -> None:
    """Verdict for a page we already have: evidence, then (optionally) the game."""
    try:
        body = _read_body(args.body)
    except OSError as exc:
        print(f"FAIL: render_ladder: cannot read {args.body}: {exc}", file=sys.stderr)
        sys.exit(2)
    result = FetchResult(ok=True, status=200, text=body, html=body, rung="body")
    expected = list(args.expect_team)
    if args.expect_teams:
        expected.extend(split_teams(args.expect_teams))
    anchor = anchor_game(body, expected) if expected else None
    evidence = result.has_stream_evidence()
    ok = evidence and (anchor is None or anchor.matched)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "evidence": evidence,
                    "source": str(args.body),
                    "titles": list(page_titles(body)),
                    "expected_teams": expected,
                    "game": None if anchor is None else {
                        "matched": anchor.matched,
                        "ambiguous": anchor.ambiguous,
                        "title": anchor.title,
                        "reason": anchor.reason(),
                    },
                },
                indent=2,
            )
        )
    else:
        print(
            f"OK: render_ladder: {args.body}: stream evidence "
            f"{'present' if evidence else 'ABSENT'}"
        )
        for title in page_titles(body):
            print(f"    title: {title[:120]}")
        if anchor is not None:
            state = "OK" if anchor.matched else "FAIL"
            print(f"{state}: render_ladder: game anchor: {anchor.reason()}")
    if not ok:
        if evidence and anchor is not None and not anchor.matched:
            print(
                "FAIL: render_ladder: the page carries stream evidence but not "
                f"for {expected} — refusing to attribute it",
                file=sys.stderr,
            )
        else:
            print(
                f"FAIL: render_ladder: no stream evidence in {args.body}",
                file=sys.stderr,
            )
    sys.exit(0 if ok else 1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a URL through the ladder and report per-rung evidence.",
    )
    parser.add_argument("--url", help="URL to render")
    parser.add_argument(
        "--body",
        type=Path,
        help=(
            "judge a saved page instead of fetching (a `.gz` file is decompressed). "
            "Needed for hosts the ladder never renders — YouTube goes through the "
            "API/live filter — and for replaying a recorded page"
        ),
    )
    parser.add_argument("--list", action="store_true", help="list rungs and licences")
    parser.add_argument(
        "--plan", action="store_true", help="print the planned ladder without fetching"
    )
    parser.add_argument(
        "--probe", action="store_true", help="actually fetch through the ladder"
    )
    parser.add_argument(
        "--allow-opt-in",
        action="store_true",
        help="include opt-in rungs such as the AGPL-3.0 nodriver",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--expect-teams",
        help='the game this page is being fetched for, e.g. "ALBA Berlin vs FC Bayern"',
    )
    parser.add_argument(
        "--expect-team",
        action="append",
        default=[],
        help="one expected club; repeatable (alternative to --expect-teams)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.list:
        payload = [
            {
                "name": r.name,
                "kind": r.kind,
                "license": r.license,
                "requires_js": r.requires_js,
                "opt_in": r.opt_in,
                "installed": r.installed(),
            }
            for r in ALL_RUNGS.values()
        ]
        assert_shippable(build_ladder("https://example.com"))
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            for item in payload:
                print(
                    "OK: render_ladder: {name:12s} {kind:6s} {license:16s} "
                    "js={requires_js!s:5s} opt_in={opt_in!s:5s} "
                    "installed={installed}".format(**item)
                )
        return

    if args.body is not None:
        return _judge_body(args)

    if not args.url:
        parser.error("--url is required unless --list or --body is given")

    ladder = build_ladder(args.url, allow_opt_in=args.allow_opt_in)
    if args.plan or not args.probe:
        for index, rung in enumerate(ladder):
            print(
                f"OK: render_ladder: rung {index} {rung.name} "
                f"({rung.kind}, {rung.license}, js={rung.requires_js})"
            )
        if not ladder:
            print(
                "OK: render_ladder: no rendering rungs for this host "
                "(handled by the API path instead)"
            )
        return

    result, attempts = fetch_with_ladder(
        args.url, rungs=ladder, timeout=args.timeout
    )
    expected = list(args.expect_team)
    if args.expect_teams:
        expected.extend(split_teams(args.expect_teams))
    anchor = (
        anchor_game(
            f"{result.text or ''}\n{result.html or ''}", expected
        )
        if expected
        else None
    )
    evidence = result.ok and result.has_stream_evidence()
    ok = evidence and (anchor is None or anchor.matched)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "evidence": evidence,
                    "rung": result.rung,
                    "expected_teams": expected,
                    "game": None if anchor is None else {
                        "matched": anchor.matched,
                        "ambiguous": anchor.ambiguous,
                        "title": anchor.title,
                        "titles": list(anchor.titles),
                        "reason": anchor.reason(),
                    },
                    # `state` is the shared classification, so the JSON payload
                    # and the prose below (and the rung ledger) cannot disagree
                    # about what an attempt was. The bodies are carried because
                    # a human may want to judge the page offline — they are why
                    # this output is for a person, not for the telemetry branch.
                    "attempts": [
                        {**a.__dict__, "state": attempt_state(a)} for a in attempts
                    ],
                },
                indent=2,
            )
        )
    else:
        for attempt in attempts:
            print(
                f"OK: render_ladder: {attempt.rung}: {attempt_state(attempt)} "
                f"(status={attempt.status}, {attempt.elapsed_ms}ms) {attempt.error}"
            )
    if anchor is not None:
        state = "OK" if anchor.matched else "FAIL"
        print(
            f"{state}: render_ladder: game anchor: {anchor.reason()}",
            file=sys.stdout if anchor.matched else sys.stderr,
        )
    if not ok:
        if evidence and anchor is not None and not anchor.matched:
            print(
                "FAIL: render_ladder: the page carries stream evidence but not "
                f"for {expected} — refusing to attribute it",
                file=sys.stderr,
            )
        else:
            print(
                "FAIL: render_ladder: no rung produced player+live evidence for "
                f"{args.url}",
                file=sys.stderr,
            )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
