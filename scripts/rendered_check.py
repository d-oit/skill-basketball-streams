"""rendered_check.py — refuse to create a calendar event for a dead stream page.

`link_check.py` asks only whether a URL answers with a good HTTP status. That is
the check that let a dead link through: the server returned the same app shell
for every path, so the status was 200 while the page said "Page not found".
This module renders the page in a real browser (patchright, already installed
for `render_ladder`) and reads the visible text.

Fail-closed: if the page cannot be rendered, or patchright is missing, the link
is refused. An unverifiable link must not be written to a public calendar.

Used by `calendar_io apply --live` as the `link_gate` for create rows. Dry runs
perform no HTTP, so the gate is not consulted there.
"""

from __future__ import annotations

import re

try:  # direct CLI execution
    from stream_links import normalise_links
except ImportError:  # imported as a package module
    from scripts.stream_links import normalise_links  # type: ignore[no-redef]

TIMEOUT_SECONDS = 30

# Phrases a rendered page shows when the stream is gone. Matched
# case-insensitively against the visible body text only, not the raw HTML, so
# a script or a meta tag cannot trip them.
DEAD_PAGE_PHRASES = (
    "page not found",
    "page cannot be found",
    "page can't be found",
    "page doesn't exist",
    "page does not exist",
    "this page could not be found",
    "404 not found",
)

_WHITESPACE_RE = re.compile(r"\s+")


def dead_page_reason(visible_text: str) -> str | None:
    """Why the rendered text is a dead page, or None if it does not look dead."""
    haystack = _WHITESPACE_RE.sub(" ", visible_text or "").strip().lower()
    if not haystack:
        return "rendered page has no visible text"
    for phrase in DEAD_PAGE_PHRASES:
        if phrase in haystack:
            return f"rendered page says {phrase!r}"
    return None


def render_reason(url: str, *, timeout: float = TIMEOUT_SECONDS) -> str | None:
    """Render `url` in a browser; return why it must be refused, or None if it is fine."""
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        return "patchright is not installed, so the link cannot be rendered and verified"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                page.wait_for_timeout(3000)
                text = page.inner_text("body")
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 — any failure to render refuses the link
        return f"could not render the page: {exc}"
    return dead_page_reason(text)


def reject_reason(row: dict, *, render=render_reason) -> str | None:
    """The first stream link on a plan row that must not be written, or None.

    `render` is injectable so the gate can be tested without a browser.
    """
    for entry in normalise_links(row).get("direct_links") or []:
        url = str(entry.get("url") or "")
        if not url:
            continue
        reason = render(url)
        if reason:
            return f"{url}: {reason}"
    return None
