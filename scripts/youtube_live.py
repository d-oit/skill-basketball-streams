#!/usr/bin/env python3
"""youtube_live.py — Live-only, future-only YouTube stream filter.

Implements the "YouTube real live stream" sub-skill: a search hit is only
promotable to the 7-check pipeline when it is a *real live broadcast* (not a
recorded video, not an archived live) AND its start datetime is **greater than
now** (or it is live right now with no end timestamp).

One search entry point is used: the **HTML live filter, which needs no API
key.** Append `sp=EgJAAQ%3D%3D` to a YouTube results URL — this is YouTube's
"Live" search filter, so the page only returns live/upcoming broadcasts. Build
it with `build_live_search_url()`.

The **YouTube Data API v3 path was removed.** `build_api_live_search_url()`
existed, and `YOUTUBE_API_KEY` was a rehearsal surface, but nothing ever
*fetched* the URL it built: its only callers were `--print-urls` and a test
asserting the string. So the runtime reached YouTube through this keyless
filter while the release rehearsal asked for a key that no code path
consumed — a `missing` surface that fixing it would never have changed, in a
report whose only value is being trustworthy. `references/youtube-live-search.md`
still documents the raw-payload field mapping, because the agent normalises
whatever the search returns, not only what this URL produces.

Raw YouTube payloads are NOT parsed here — the agent normalizes them into the
candidate schema below (see `references/youtube-live-search.md` for the field
mapping) and this module decides. That keeps the gate deterministic and
unit-testable.

Candidate schema (all keys optional except `url`):
    {
      "url": "https://www.youtube.com/@fiba/live",
      "video_id": "abc123",
      "title": "EuroLeague LIVE: Real Madrid vs ALBA Berlin",
      "channel": "@EuroLeague",
      "live_broadcast_content": "live" | "upcoming" | "none",
      "is_live_now": true,
      "is_live_content": true,          # ytInitialPlayerResponse videoDetails
      "scheduled_start": "2026-09-20T18:00:00+02:00",   # scheduledStartTime
      "actual_start": "2026-09-20T18:02:11+02:00",      # actualStartTime
      "actual_end": null,                                # actualEndTime
      "duration_seconds": null
    }

Usage:
    python3 scripts/youtube_live.py --input candidates.json
    python3 scripts/youtube_live.py --input candidates.json --now 2026-09-14T12:00:00Z --json
    python3 scripts/youtube_live.py --print-urls "EuroLeague live"

Exit codes:
    0  at least one candidate is LIVE or SCHEDULED
    1  every candidate was rejected
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

try:  # package-relative when imported as `scripts.youtube_live`
    from .relevance import relevance_reason
except ImportError:  # direct execution, where the repo root is on sys.path
    from relevance import relevance_reason

# YouTube's "Live" search filter (sp parameter). Decodes to a type=video +
# features=live filter, i.e. only live/upcoming broadcasts.
LIVE_SEARCH_SP = "EgJAAQ%3D%3D"
# Alternative encoding seen in some clients (double-encoded).
LIVE_SEARCH_SP_ALT = "EgJAAQ%253D%253D"

LIVE_BROADCAST_CONTENT = {"live", "upcoming"}

# Accepted YouTube live URL shapes. Anything else (channel root, /channel/UC…,
# legacy /user/, playlists, shorts) is rejected before the pipeline.
LIVE_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/"
    r"(?:@[\w.\-]+/live|live/[\w\-]+|watch\?(?:[^#]*&)?v=[\w\-]+|live\b)",
    re.IGNORECASE,
)
# De-advertised / non-live YouTube URL shapes that must always be rejected.
REJECTED_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.)?youtube\.com/(?:channel/|user/|c/|playlist\?|results\?)",
    re.IGNORECASE,
)
# `@handle` extraction, used for the approved-channel check.
HANDLE_RE = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?youtube\.com/@([\w.\-]+)", re.IGNORECASE
)

# Approved YouTube handles for this skill. `config/sources.json` carries the
# same list, and `tests/test_youtube_live.py` asserts the two agree, so they
# cannot drift apart.
#
# The scheme in `HANDLE_RE` is **optional** because `config/sources.json` stores
# its handles scheme-less (`youtube.com/@fiba`, not `https://youtube.com/@fiba`).
# It used to be required, which meant the registry parsed to nothing, every read
# took the `or DEFAULT_ALLOWED_HANDLES` fallback below, and the "the two agree"
# comment was true only in the sense that a constant was being compared with
# itself. The practical cost was that the documented promotion path — add a tier
# row and a `handles` entry, open a PR — changed nothing for YouTube, so a new
# official channel could only be admitted by editing this tuple. `HANDLE_RE` is
# the single place that knows the handle shape, and both the URL check and the
# registry read go through it so they cannot diverge again.
#
# This check exists because a gate that only validates the URL *shape* promotes
# `@FIBAWorld/live` — a channel-shaped URL for a handle that does not exist.
# That is the same class of failure as the original `/user/FIBA` incident, and
# eval case 2 plus `references/lessons-learned.md` had already documented the
# rule while no code enforced it.
DEFAULT_ALLOWED_HANDLES = (
    "fiba",
    "basketballbundesliga",
    "euroleague",
    "basketballcl",
)


def handle_of(url: str) -> str:
    """The `@handle` in a YouTube URL, lowercased. `""` for `/live/<id>` and
    `watch?v=<id>`, which carry no handle."""
    match = HANDLE_RE.match(url or "")
    return match.group(1).lower() if match else ""


def load_allowed_handles(root: Path | str | None = None) -> tuple[str, ...]:
    """Approved handles as declared in `config/sources.json`.

    Falls back to `DEFAULT_ALLOWED_HANDLES` only when the config is absent,
    unreadable, or declares no `@handle` at all — so the gate keeps working from
    a bare checkout. A config that *does* declare handles is authoritative: the
    fallback is not merged in, because merging would make a removed channel
    impossible to revoke.

    Registry entries that are not `@handle` URLs (for example
    `youtube.com/user/TheDBBTV`) carry no handle and are skipped here; that path
    is allowed separately, by exact URL shape, in the live-only gate.
    """
    config = Path(root or ".") / "config" / "sources.json"
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_ALLOWED_HANDLES

    handles: list[str] = []
    for source in payload.get("sources") or []:
        if not isinstance(source, dict):
            continue
        for entry in source.get("handles") or []:
            match = HANDLE_RE.match(str(entry))
            if match:
                handles.append(match.group(1).lower())
    return tuple(dict.fromkeys(handles)) or DEFAULT_ALLOWED_HANDLES


def is_allowed_handle(url: str, allowed: tuple[str, ...] | None = None) -> bool:
    """False when a `@handle` URL names a channel outside the allow-list.

    `/live/<id>` and `watch?v=<id>` URLs carry no handle and are therefore not
    judged here; they are covered by the live/ended/duration gates.
    """
    handle = handle_of(url)
    if not handle:
        return True
    approved = {item.lower() for item in (allowed or DEFAULT_ALLOWED_HANDLES)}
    return handle in approved

DECISION_LIVE = "LIVE"
DECISION_SCHEDULED = "SCHEDULED"
DECISION_REJECT = "REJECT"


def parse_iso(value: object) -> datetime | None:
    """Parse an ISO-8601 string into an aware datetime (naive input -> UTC)."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_youtube_live_url(url: str) -> bool:
    """True only for YouTube URLs that can point directly at a live broadcast."""
    if not url:
        return False
    if REJECTED_URL_RE.match(url):
        return False
    return bool(LIVE_URL_RE.match(url))


def build_live_search_url(query: str) -> str:
    """HTML search URL restricted to live broadcasts (no API key required)."""
    return (
        "https://www.youtube.com/results?search_query="
        f"{quote_plus(query)}&sp={LIVE_SEARCH_SP}"
    )



def classify_stream(
    candidate: dict,
    now: datetime | None = None,
    allowed_handles: tuple[str, ...] | None = None,
) -> tuple[str, str]:
    """Decide whether a normalized YouTube candidate is a promotable live stream.

    Returns `(decision, reason)` where decision is one of `LIVE`, `SCHEDULED`
    or `REJECT`. Only `LIVE` and `SCHEDULED` may enter the 7-check pipeline.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    url = str(candidate.get("url") or "").strip()
    if not url:
        return DECISION_REJECT, "no URL supplied"
    if not is_youtube_live_url(url):
        return (
            DECISION_REJECT,
            "not a YouTube live URL (accepted: /@handle/live, /live/<id>, "
            "/watch?v=<id>; rejected: /channel/, /user/, /c/, playlists)",
        )

    # 0. Approved channel. Checked before the payload gates because it is a
    #    *static* fact about the URL: a well-formed live-looking URL for a
    #    handle that does not exist is exactly how @FIBAWorld 404s reached the
    #    calendar, and no amount of payload evidence makes it real.
    if not is_allowed_handle(url, allowed_handles):
        approved = ", ".join("@" + item for item in (
            allowed_handles or DEFAULT_ALLOWED_HANDLES
        ))
        return DECISION_REJECT, (
            "channel @%s is not on the approved YouTube allow-list (%s)"
            % (handle_of(url), approved)
        )

    # 0b. Relevance, for the URLs the allow-list cannot judge. `/live/<id>` and
    #     `/watch?v=<id>` carry no handle, so step 0 waved them through and they
    #     reached the payload gates and the 7-check pipeline with nothing
    #     upstream asking whether they were basketball at all. One recorded run
    #     spent a full pass each on `Pop The Balloon` and `Acerting Art`.
    #     Fail-open by construction (`scripts/relevance.py`), and unreachable for
    #     an approved channel because step 0 already returned.
    _irrelevant = relevance_reason(
        candidate.get("title"),
        candidate.get("channel") or candidate.get("channelTitle"),
    )
    if _irrelevant:
        return DECISION_REJECT, _irrelevant

    lbc = str(
        candidate.get("live_broadcast_content")
        or candidate.get("liveBroadcastContent")
        or ""
    ).strip().lower()
    is_live_now = bool(candidate.get("is_live_now")) or lbc == "live"
    is_live_content = bool(
        candidate.get("is_live_content") or candidate.get("isLiveContent")
    )

    # 1. Real-live gate: a regular upload is never a live stream.
    if not (is_live_now or is_live_content or lbc in LIVE_BROADCAST_CONTENT):
        return DECISION_REJECT, (
            "not a live broadcast (liveBroadcastContent=%r) — regular video upload"
            % (lbc or "none")
        )

    # 2. Ended broadcast => archived VOD, even though isLiveContent is true.
    actual_end = parse_iso(
        candidate.get("actual_end") or candidate.get("actualEndTime")
    )
    if actual_end is not None:
        return DECISION_REJECT, (
            "broadcast already ended (%s) — archived live, not a live stream"
            % actual_end.isoformat()
        )

    # 3. Fixed runtime + not live now => recorded VOD.
    duration = candidate.get("duration_seconds")
    try:
        duration = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None
    if not is_live_now and duration not in (None, 0):
        return DECISION_REJECT, (
            "video has a fixed duration (%ss) — recorded video, not a real live "
            "stream" % duration
        )

    # 4. Currently live: accept, but reject inconsistent future start times.
    if is_live_now:
        actual_start = parse_iso(
            candidate.get("actual_start") or candidate.get("actualStartTime")
        )
        if actual_start is not None and actual_start > now:
            return DECISION_REJECT, (
                "inconsistent payload: isLiveNow=true but actualStartTime %s is in "
                "the future" % actual_start.isoformat()
            )
        return DECISION_LIVE, (
            "stream is live right now (real live broadcast, no end timestamp)"
        )

    # 5. Upcoming: the start datetime MUST be greater than now.
    scheduled = parse_iso(
        candidate.get("scheduled_start")
        or candidate.get("scheduledStartTime")
        or candidate.get("published_at")
        or candidate.get("publishedAt")
    )
    if scheduled is None:
        return DECISION_REJECT, (
            "no scheduled start time — cannot prove the broadcast starts in the "
            "future"
        )
    if scheduled <= now:
        return DECISION_REJECT, (
            "scheduled start %s is not greater than now %s"
            % (scheduled.isoformat(), now.isoformat())
        )
    return DECISION_SCHEDULED, (
        "scheduled live broadcast starts %s (greater than now)"
        % scheduled.isoformat()
    )


def filter_candidates(
    candidates: list, now: datetime | None = None
) -> list[dict]:
    """Classify every candidate, returning the promotable ones annotated.

    Each returned dict is a shallow copy of the candidate plus
    `_decision` and `_reason`.
    """
    now = now or datetime.now(timezone.utc)
    promoted = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        decision, reason = classify_stream(candidate, now=now)
        if decision == DECISION_REJECT:
            continue
        annotated = dict(candidate)
        annotated["_decision"] = decision
        annotated["_reason"] = reason
        promoted.append(annotated)
    return promoted


def _load_candidates(path: Path) -> list:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: youtube_live: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"FAIL: youtube_live: {path}: invalid JSON ({exc})", file=sys.stderr)
        sys.exit(2)
    if isinstance(payload, dict):
        payload = payload.get("candidates", [])
    if not isinstance(payload, list):
        print(
            "FAIL: youtube_live: input must be a JSON list or "
            '{"candidates": [...]}',
            file=sys.stderr,
        )
        sys.exit(2)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter YouTube candidates down to real live streams that "
        "start in the future (or are live right now).",
    )
    parser.add_argument(
        "--input",
        help="JSON file with a candidate list (or {'candidates': [...]})",
    )
    parser.add_argument(
        "--now",
        help="override 'now' with an ISO-8601 timestamp (use for testing/docs)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the promoted candidates as JSON instead of OK/FAIL lines",
    )
    parser.add_argument(
        "--print-urls",
        metavar="QUERY",
        help="print the live-filtered HTML search URL for QUERY (no API key)",
    )
    args = parser.parse_args()

    if args.print_urls:
        print(f"OK: html: {build_live_search_url(args.print_urls)}")
        return

    if not args.input:
        parser.error("--input is required (or use --print-urls)")

    now = parse_iso(args.now) if args.now else datetime.now(timezone.utc)
    if args.now and now is None:
        print(
            f"FAIL: youtube_live: --now {args.now!r} is not a valid ISO-8601 "
            "timestamp",
            file=sys.stderr,
        )
        sys.exit(2)

    candidates = _load_candidates(Path(args.input))
    promoted = filter_candidates(candidates, now=now)

    if args.json:
        print(
            json.dumps(
                {"now": now.isoformat(), "promoted": promoted}, indent=2
            )
        )
    else:
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            decision, reason = classify_stream(candidate, now=now)
            url = candidate.get("url", "<no url>")
            if decision == DECISION_REJECT:
                print(f"FAIL: youtube_live: {url}: {reason}", file=sys.stderr)
            else:
                print(f"OK: youtube_live: {url}: {decision} — {reason}")

    sys.exit(0 if promoted else 1)


if __name__ == "__main__":
    main()
