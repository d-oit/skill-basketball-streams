#!/usr/bin/env python3
"""youtube_tab.py — keyless YouTube channel-tab discovery (stdlib only).

Why this exists: the Live-filter `results?search_query=` URLs the runtime
fetches return boilerplate to a non-JS client, so scheduled games on approved
league channels were invisible. Measured 2026-11-11: all 12 Live-filter URLs
returned boilerplate while 8 in-window ENBL games were scheduled on
`@ENBLeague` and none reached the calendar.

The channel's own surfaces ARE server-rendered, verified the same day:

* `@handle/streams` tab HTML carries every listed `"videoId":"..."` (30
  unique IDs for @ENBLeague, plain `urllib` GET, no JS).
* `feeds/videos.xml?channel_id=...` (RSS) carries videoId + title + published
  for the 15 newest uploads — static XML any client can read.
* `watch?v=...` pages carry `liveBroadcastDetails` (`isLiveNow` plus an ISO
  `startTimestamp` for scheduled broadcasts) and the player title.

All three are plain GETs: no API key, no browser, stdlib only. The offline
slate's `scheduledStartTime` is a Unix epoch; the parser prefers the ISO
`startTimestamp` from `liveBroadcastDetails` and only falls back to the epoch.

What this module does NOT do: prove a game free. A discovered video is a
lead — the 7-check pipeline (and `classify_stream` in `youtube_live.py`)
still decides. Phase 0 rows built from this are `unverifiable` by
construction, like every other Phase 0 row.

Usage:
    python3 scripts/youtube_tab.py --handle enbleague --json
    python3 scripts/youtube_tab.py --all --no-watch --json
    python3 scripts/youtube_tab.py --handle legabasket --max-watch 4

Exit codes:
    0  at least one video discovered
    1  no video discovered (or every handle failed)
    2  USAGE — bad arguments
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

try:  # package-relative when imported as `scripts.youtube_tab`
    from .youtube_live import DEFAULT_ALLOWED_HANDLES, load_allowed_handles
except ImportError:  # direct execution
    from youtube_live import DEFAULT_ALLOWED_HANDLES, load_allowed_handles

USER_AGENT = "basketball-streams-runtime/1.0"
TIMEOUT_SECONDS = 20

STREAMS_URL = "https://www.youtube.com/@{handle}/streams"
RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"

# Channel IDs resolved live 2026-11-11 from each `@handle` page (`browseId`).
# A handle missing here is resolved the same way at runtime (one extra GET);
# the map only saves that request for the handles this skill approves.
CHANNEL_IDS = {
    "fiba": "UCtInrnU3QbWqFGsdKT1GZtg",
    "basketballbundesliga": "UCg4YgrMEO4Y5hg9FJce6Jag",
    "euroleague": "UCGr3nR_XH9r6E5b09ZJAT9w",
    "basketballcl": "UCZUkv6tSC5OfX54gLgjV9aw",
    "tampa2films": "UCDH9pTsSYS2zJDd_Rr9xbWg",
    "enbleague": "UC4nW9T_6d6e4DKa4A9_3Ouw",
    "legabasket": "UCKuhExlRMWgfcaD__HtN1yw",
}

BROWSE_ID_RE = re.compile(r'"browseId":"(UC[\w-]{22})"')
VIDEO_ID_RE = re.compile(r'"videoId":"([\w-]{11})"')
# The player title. Deliberately escape-aware: a title containing an escaped
# quote truncates under a plain `[^"]*` capture instead of failing loudly
# (the same lesson `render_ladder.py` documents for its own title pattern).
VIDEO_DETAILS_TITLE_RE = re.compile(
    r'"videoDetails"\s*:\s*\{[^}]*?"title"\s*:\s*"((?:[^"\\]|\\.)*)"'
)
LIVE_DETAILS_RE = re.compile(
    r'"liveBroadcastDetails"\s*:\s*\{\s*"isLiveNow"\s*:\s*(true|false)'
    r'(?:\s*,\s*"startTimestamp"\s*:\s*"([^"]*)")?'
)
SLATE_EPOCH_RE = re.compile(r'"scheduledStartTime"\s*:\s*"(\d{9,11})"')


def _unescape(value: str) -> str:
    return (
        value.replace('\\\\"', '"')
        .replace("\\\\/", "/")
        .replace("\\\\n", " ")
        .replace("\\\\\\\\", "\\\\")
        .strip()
    )


def fetch_text(url: str, *, timeout: float = TIMEOUT_SECONDS) -> str:
    """One plain GET. Raises — the caller names the failure, not this."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def resolve_channel_id(handle: str, *, fetch=None) -> str | None:
    """A handle's channel ID: the verified map first, the channel page after."""
    fetch = fetch_text if fetch is None else fetch
    cleaned = (handle or "").strip().lstrip("@").lower()
    if not cleaned:
        return None
    known = CHANNEL_IDS.get(cleaned)
    if known:
        return known
    try:
        page = fetch(f"https://www.youtube.com/@{cleaned}")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    match = BROWSE_ID_RE.search(page)
    return match.group(1) if match else None


def tab_video_ids(html: str) -> list[str]:
    """Every video ID the server-rendered `/streams` tab lists, in order."""
    return list(dict.fromkeys(VIDEO_ID_RE.findall(html or "")))


def rss_entries(xml_text: str) -> list[dict]:
    """`(video_id, title, published)` for each RSS entry, newest first."""
    try:
        root = ET.fromstring(xml_text or "<feed/>")
    except ET.ParseError:
        return []
    namespaces = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }
    entries: list[dict] = []
    for entry in root.findall("atom:entry", namespaces):
        video_id = (entry.findtext("yt:videoId", namespaces=namespaces) or "").strip()
        if not video_id:
            continue
        entries.append(
            {
                "video_id": video_id,
                "title": (entry.findtext("atom:title", namespaces=namespaces) or "").strip(),
                "published": (
                    entry.findtext("atom:published", namespaces=namespaces) or ""
                ).strip(),
            }
        )
    return entries


def watch_info(html: str) -> dict:
    """Title, scheduled start and live flag from a server-rendered watch page.

    `scheduled_start` is the ISO `startTimestamp` when present, else the
    offline-slate epoch converted to UTC, else `None`. Aired VODs carry
    `isLiveNow: false` with no start — they are dated, not datable.
    """
    page = html or ""
    title = ""
    match = VIDEO_DETAILS_TITLE_RE.search(page)
    if match:
        title = _unescape(match.group(1))
    is_live_now = False
    scheduled: str | None = None
    details = LIVE_DETAILS_RE.search(page)
    if details:
        is_live_now = details.group(1) == "true"
        scheduled = details.group(2) or None
    if scheduled is None:
        epoch = SLATE_EPOCH_RE.search(page)
        if epoch:
            try:
                scheduled = datetime.fromtimestamp(
                    int(epoch.group(1)), tz=timezone.utc
                ).isoformat()
            except (ValueError, OverflowError, OSError):
                scheduled = None
    return {
        "title": title,
        "scheduled_start": scheduled,
        "is_live_now": is_live_now,
    }


def parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def discover(
    handle: str,
    *,
    channel_id: str | None = None,
    fetch=None,
    max_watch: int = 8,
    now: datetime | None = None,
) -> dict:
    """One channel's upcoming/live surface: RSS union `/streams` tab.

    RSS-first ordering: placeholders are published days before tip-off, so the
    newest RSS entries are the closest games. Tab-only IDs (older placeholders
    that fell out of the 15-item RSS window) follow. Watch-page enrichment is
    capped — each costs a 1.3 MB fetch — and a failed enrichment keeps the
    ID with its RSS title rather than dropping the lead.
    """
    fetch = fetch_text if fetch is None else fetch
    cleaned = (handle or "").strip().lstrip("@").lower()
    resolved = channel_id or resolve_channel_id(cleaned, fetch=fetch)
    videos: list[dict] = []
    if not cleaned or not resolved:
        return {"handle": cleaned, "channel_id": resolved, "videos": videos}
    seen: set[str] = set()

    def _add(video_id: str, title: str = "", published: str = "") -> None:
        if not video_id or video_id in seen:
            return
        seen.add(video_id)
        videos.append(
            {
                "url": WATCH_URL.format(video_id=video_id),
                "video_id": video_id,
                "title": title,
                "published": published,
                "scheduled_start": None,
                "is_live_now": False,
            }
        )

    try:
        for entry in rss_entries(fetch(RSS_URL.format(channel_id=resolved))):
            _add(entry["video_id"], entry["title"], entry["published"])
    except (urllib.error.URLError, OSError, ValueError):
        pass
    try:
        for video_id in tab_video_ids(fetch(STREAMS_URL.format(handle=cleaned))):
            _add(video_id)
    except (urllib.error.URLError, OSError, ValueError):
        pass

    enriched = 0
    for video in videos:
        if enriched >= max(0, max_watch):
            break
        try:
            info = watch_info(fetch(video["url"]))
        except (urllib.error.URLError, OSError, ValueError):
            continue
        enriched += 1
        if info["title"]:
            video["title"] = info["title"]
        video["scheduled_start"] = info["scheduled_start"]
        video["is_live_now"] = info["is_live_now"]
    return {"handle": cleaned, "channel_id": resolved, "videos": videos}


def discover_handles(
    handles: list[str] | None = None,
    *,
    root: Path | str | None = None,
    fetch=None,
    max_watch: int = 0,
) -> list[dict]:
    """Every approved handle's video leads. `max_watch=0` is the Phase 0
    posture: IDs plus RSS titles, no 1.3 MB watch fetches — Phase 0 rows are
    `unverifiable` by construction, so scheduled-start proof buys nothing
    there and only spends the run's request budget."""
    fetch = fetch_text if fetch is None else fetch
    if handles is None:
        handles = list(load_allowed_handles(root) or DEFAULT_ALLOWED_HANDLES)
    found: list[dict] = []
    for handle in handles:
        result = discover(handle, fetch=fetch, max_watch=max_watch)
        if result["videos"]:
            found.append(result)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover upcoming/live videos on approved YouTube "
        "channels via their server-rendered tabs, RSS and watch pages.",
    )
    parser.add_argument("--handle", help="channel handle without @ (e.g. enbleague)")
    parser.add_argument("--all", action="store_true", help="all approved handles")
    parser.add_argument(
        "--max-watch",
        type=int,
        default=8,
        help="watch-page enrichments per handle (0 = IDs and RSS titles only)",
    )
    parser.add_argument("--now", help="ISO-8601 override (for tests)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if bool(args.handle) == bool(args.all):
        parser.error("exactly one of --handle or --all is required")

    now = parse_iso(args.now) if args.now else datetime.now(timezone.utc)
    if args.now is not None and now is None:
        print(
            f"FAIL: youtube_tab: --now {args.now!r} is not a valid ISO-8601 timestamp",
            file=sys.stderr,
        )
        sys.exit(2)

    handles = (
        list(load_allowed_handles() or DEFAULT_ALLOWED_HANDLES)
        if args.all
        else [args.handle]
    )
    results = [
        discover(handle, max_watch=args.max_watch) for handle in handles
    ]
    videos = [video for result in results for video in result["videos"]]
    promotable = [
        video
        for video in videos
        if video["is_live_now"]
        or (video["scheduled_start"] and parse_iso(video["scheduled_start"])
            and parse_iso(video["scheduled_start"]) > now)
    ]

    if args.json:
        print(json.dumps({"now": now.isoformat(), "channels": results}, indent=2))
    else:
        for result in results:
            print(
                f"OK: youtube_tab: @{result['handle']}: "
                f"{len(result['videos'])} videos "
                f"({result['channel_id'] or 'unresolved'})"
            )
        for video in promotable:
            state = "live now" if video["is_live_now"] else f"starts {video['scheduled_start']}"
            print(f"OK: youtube_tab: {video['url']}: {state} — {video['title'][:100]}")
    if not videos:
        print("FAIL: youtube_tab: no videos discovered", file=sys.stderr)
    sys.exit(0 if videos else 1)


if __name__ == "__main__":
    main()
