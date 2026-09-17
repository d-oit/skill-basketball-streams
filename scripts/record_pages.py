#!/usr/bin/env python3
"""record_pages.py — record and re-check the page corpus behind the evidence gate.

`FetchResult.has_stream_evidence()` is the last thing that runs before the
pipeline may write a calendar event, so the corpus it is judged against must be
**real pages**, not strings we invented. This tool keeps that corpus honest.

    # Re-fetch every page and rewrite the corpus (network; needs a real UA)
    python3 scripts/record_pages.py --refresh

    # Gate: do the stored pages still classify the way the manifest says?
    python3 scripts/record_pages.py --check

    # Human report of what each stored page proves
    python3 scripts/record_pages.py --list

`--refresh` is a *report*, not a gate: it rewrites the corpus and exits 0, and
reports any page whose gate verdict differs from the previous manifest as
`FLIP:` (machine-readable under `--json`, which is what the weekly
`corpus-refresh` workflow reads). Only a **verdict** change is reported, never a
byte change: the YouTube fixtures are extracts of a 1.3 MB document whose hash
moves whenever a sidebar suggestion is edited, so a hash diff here would be
constant noise with the real signal buried in it. `--check` remains the gate.

Why an extract for YouTube and a whole page elsewhere: the YouTube documents are
1.2–1.4 MB and deciding one of them is about two fields (`isLiveContent`,
`isLiveNow`) plus the `og:video` meta. Those are stored verbatim — the real
substrings, at the real nesting — while the site pages (≤ 1 MB, and much smaller
after gzip) are stored whole, because the *whole document* is the thing that
used to produce the false positive: the markers for `player`, `dash`, `live` and
`läuft` live in the inline runtime bundle.

Exit codes:
    0  PASS — stored pages classify as the manifest says (or --refresh/--list)
    1  FAIL — a page is missing, unreadable, or misclassified
    2  USAGE — bad arguments
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from render_ladder import FetchResult, USER_AGENT  # noqa: E402

GZIP_THRESHOLD = 8192
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pages"
EVIDENCE = "evidence"
NO_EVIDENCE = "no-evidence"
PLAYER_RESPONSE_KEY = "ytInitialPlayerResponse"


@dataclass(frozen=True)
class RecordedPage:
    """One page that pins a decision of the evidence gate."""

    name: str
    url: str
    expect: str
    store: str = "full"          # "full" | "youtube-extract" | "verify-only"
    note: str = ""


PAGES: tuple[RecordedPage, ...] = (
    RecordedPage(
        "magenta-tv-shell",
        "https://www.magenta.tv/",
        NO_EVIDENCE,
        note="JS app shell: 942 bytes, no readable text, no media token at all",
    ),
    RecordedPage(
        "magentasport-home",
        "https://www.magentasport.de/",
        NO_EVIDENCE,
        note="announce site; its bundle contains '.mpd' and 'läuft', the page has no stream",
    ),
    RecordedPage(
        "clb-home",
        "https://www.championsleague.basketball/",
        NO_EVIDENCE,
        note="league homepage; prose says 'live now' with no media token",
    ),
    RecordedPage(
        "youtube-home",
        "https://www.youtube.com/",
        NO_EVIDENCE,
        store="verify-only",
        note=(
            "YouTube home: bundle carries 'dash', 'player', 'live', 'livestream'. "
            "Not stored — 258 KB gz for a page with no media token anywhere; the "
            "same false-positive class is stored whole as magentasport-home"
        ),
    ),
    RecordedPage(
        "youtube-watch-recorded",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        NO_EVIDENCE,
        store="youtube-extract",
        note="a recorded video that still passes the media half via og:video",
    ),
    RecordedPage(
        "youtube-live-search",
        "https://www.youtube.com/results?search_query=basketball&sp=EgJAAQ%3D%3D",
        NO_EVIDENCE,
        store="verify-only",
        note=(
            "the Live-filtered results page itself is not a stream. Not stored — "
            "256 KB gz of a results page; URL shape is rejected before rendering"
        ),
    ),
    RecordedPage(
        "youtube-watch-live",
        "https://www.youtube.com/watch?v=DpMb8luALvE",
        EVIDENCE,
        store="youtube-extract",
        note="positive control: a real live broadcast (isLiveContent/isLiveNow true)",
    ),
    RecordedPage(
        "youtube-channel-live",
        "https://www.youtube.com/@FIBA/live",
        EVIDENCE,
        store="youtube-extract",
        note="channel live tab with a broadcast attached (URL shape is gated elsewhere)",
    ),
)

SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
OG_VIDEO_RE = re.compile(r'<meta[^>]+og:video[^>]*>', re.IGNORECASE)


def _fetch(url: str, timeout: float = 30.0) -> tuple[int | None, bytes]:
    """Real HTTP GET with a browser-ish UA. Returns (status, body)."""
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
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(exc).encode("utf-8")


def _json_object_at(text: str, start: int) -> str:
    """Slice the balanced `{...}` object starting at/after `start`.

    String literals are skipped so a brace inside a title cannot unbalance the
    scan. Returns "" when the object never closes.
    """
    begin = text.find("{", start)
    if begin < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(begin, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[begin : index + 1]
    return ""


def extract_youtube(html: str) -> str:
    """The decision-bearing slice of a YouTube document, verbatim.

    Keeps the real `og:video` meta tag (markup) and the real
    `ytInitialPlayerResponse` object (structured data), and nothing else.
    """
    parts = [match.group(0) for match in OG_VIDEO_RE.finditer(html)]
    response = _json_object_at(html, html.find(PLAYER_RESPONSE_KEY))
    if response:
        parts.append(
            f"<script>var {PLAYER_RESPONSE_KEY} = {response};</script>"
        )
    if not parts:
        raise ValueError(f"no {PLAYER_RESPONSE_KEY} and no og:video in document")
    return "<!doctype html><html><head>" + "\n".join(parts) + "</head></html>"


def _store(name: str, payload: bytes) -> tuple[str, bytes]:
    """Return (filename, bytes_on_disk) — gzipped above the threshold."""
    if len(payload) >= GZIP_THRESHOLD:
        return f"{name}.html.gz", gzip.compress(payload, 9)
    return f"{name}.html", payload


def _load(path: Path) -> bytes:
    raw = path.read_bytes()
    return gzip.decompress(raw) if path.suffix == ".gz" else raw


def baseline_verdicts(corpus_dir: Path) -> dict[str, bool]:
    """The verdict each page carried in a previous manifest.

    A missing or unreadable manifest is not an error: `--refresh` has to be able
to *start* a corpus, and the flip report is a statement about change over time.
    """
    path = corpus_dir / "manifest.json"
    if not path.exists():
        return {}
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        entry["name"]: bool(entry.get("evidence_now"))
        for entry in manifest.get("pages", [])
        if entry.get("name")
    }


def diff_verdicts(entries: list[dict], baseline: dict[str, bool]) -> list[dict]:
    """Pages whose gate verdict differs from the recorded one.

    A page absent from the baseline is new, not a change, so it is not reported
here — a fresh corpus would otherwise report every page as a flip on its first
run, which is exactly the kind of noise that makes a signal ignorable.
    """
    flips = []
    for entry in entries:
        name = entry["name"]
        if name not in baseline:
            continue
        was = baseline[name]
        now = bool(entry.get("evidence_now"))
        if was == now:
            continue
        flips.append(
            {
                "name": name,
                "url": entry["url"],
                "was": EVIDENCE if was else NO_EVIDENCE,
                "now": EVIDENCE if now else NO_EVIDENCE,
                "expect": entry["expect"],
                # Direction is the urgency, and the two are not symmetrical:
                # losing evidence means real broadcasts stop being recognised
                # (missed games), gaining it means the false positives this
                # corpus exists to prevent have come back.
                "direction": "lost-evidence" if was else "gained-evidence",
                "agrees_with_expect": now == (entry["expect"] == EVIDENCE),
                "note": entry.get("note", ""),
            }
        )
    return flips


class UnreduciblePage(RuntimeError):
    """A page that could not be reduced to the response the corpus asserts.

    Carries the pages that *could* be reduced and the flips among them, so the
    run still answers what it was asked — did a verdict flip? — for everything it
    reached. Never raised after a write: the write is all-or-nothing, so the
    corpus on disk is either fully re-recorded or untouched.
    """

    def __init__(
        self,
        unusable: list[dict],
        entries: list[dict],
        flips: list[dict],
    ) -> None:
        self.unusable = unusable
        self.entries = entries
        self.flips = flips
        named = "; ".join(
            f"{item['name']}: {item['reason']}" for item in unusable
        )
        super().__init__(named)


def _reduce(
    page: RecordedPage, status: int | None, body: bytes
) -> tuple[str, bytes]:
    """`("", payload)` when this response is the page, `(reason, b"")` when not.

    Three things qualify as "not the page", and all three are stated rather than
    raised here so the caller can collect them — an error status is the one that
    had to be learned the hard way: `_fetch` returns an error's status and body
    like any other response, so a 429 body used to be classified as content. On
    an `expect=evidence` page that reads as a `lost-evidence` flip, which files an
    issue saying the gate broke, off nothing but a rate limit.
    """
    if status is None:
        return f"connection failed: {body[:200]!r}", b""
    if not 200 <= status < 300:
        return (
            f"HTTP {status} — an error response is not the page: {body[:200]!r}",
            b"",
        )
    html = body.decode("utf-8", "replace")
    if page.store != "youtube-extract":
        return "", body
    try:
        return "", extract_youtube(html).encode("utf-8")
    except ValueError as exc:
        return (
            f"fetched {len(body):,} bytes but {exc} — the corpus cannot be "
            "refreshed from this response",
            b"",
        )


def record(out_dir: Path, baseline_dir: Path | None = None) -> tuple[list[dict], list[dict]]:
    """Re-fetch every page, compare verdicts, and rewrite the corpus.

    Returns `(entries, flips)`. The baseline is read BEFORE anything is written,
    because the common case is an in-place refresh where the baseline and the
    output are the same directory.

    **Nothing is written until every page has been reduced.** An earlier version
    wrote each stored file as it went and the manifest only at the end, so an
    abort halfway through an in-place refresh left freshly fetched files beside a
    manifest holding the old hashes: the corpus stopped describing itself,
    `--check` reported `hash mismatch` for every rewritten page, and the only way
    out was to restore it from git. Measured, not reasoned — a refresh blocked on
    a YouTube `429` rewrote two of the three stored files exactly that way.

    A page that cannot be reduced is therefore *collected* rather than fatal, and
    the flips among the pages that were reduced are still returned. If any page
    failed, `UnreduciblePage` carries that partial comparison to the caller.
    """
    baseline = baseline_verdicts(out_dir if baseline_dir is None else baseline_dir)
    entries = []
    pending: list[tuple[str, bytes]] = []
    unusable: list[dict] = []
    for page in PAGES:
        status, body = _fetch(page.url)
        reason, payload = _reduce(page, status, body)
        if reason:
            # Named on stderr AND collected: the weekly workflow tolerates this
            # failure with a warning telling the reader to look for the line.
            print(f"FAIL: record_pages: {page.url}: {reason}", file=sys.stderr)
            unusable.append(
                {"name": page.name, "url": page.url, "reason": reason}
            )
            continue
        result = FetchResult(ok=True, status=status, text=payload.decode("utf-8", "replace"))
        if page.store == "verify-only":
            stored_name, on_disk = None, b""
        else:
            stored_name, on_disk = _store(page.name, payload)
            pending.append((stored_name, on_disk))
        stored_sha = hashlib.sha256(payload).hexdigest()
        entries.append(
            {
                "name": page.name,
                "url": page.url,
                "expect": page.expect,
                "store": page.store,
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": status,
                "response_bytes": len(body),
                "response_sha256": hashlib.sha256(body).hexdigest(),
                "stored": stored_name,
                "stored_bytes": len(on_disk) or None,
                # The payload's own hash, so an edited corpus file is caught rather
                # than silently believed. `response_sha256` covers the whole
                # response, which is only the stored bytes for `store == "full"`.
                "stored_sha256": stored_sha,
                "note": page.note,
                "evidence_now": result.has_stream_evidence(),
            }
        )
        print(
            f"recorded {page.name:26s} status={status} raw={len(body):>9,} "
            f"stored={len(on_disk or b''):>7,} evidence={result.has_stream_evidence()}",
            file=sys.stderr,
        )
    flips = diff_verdicts(entries, baseline)
    if unusable:
        # Raised before ANY write, so the corpus is untouched and the caller can
        # still report the comparison that was possible.
        raise UnreduciblePage(unusable, entries, flips)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stored_name, on_disk in pending:
        (out_dir / stored_name).write_bytes(on_disk)
    manifest = {
        "recorded_at": entries[0]["fetched_at"] if entries else "",
        "user_agent": USER_AGENT,
        "gzip_threshold": GZIP_THRESHOLD,
        "pages": entries,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return entries, flips


def check(out_dir: Path) -> tuple[list[str], list[dict]]:
    """Classify every stored page. Returns (problems, rows)."""
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return [f"missing manifest: {manifest_path}"], []
    manifest = json.loads(manifest_path.read_text())
    problems: list[str] = []
    rows: list[dict] = []
    for entry in manifest.get("pages", []):
        if not entry.get("stored"):
            # Not stored (too large for its marginal value) — still sanity-check
            # that the manifest is internally consistent, so a --refresh that
            # flips a verdict cannot pass silently.
            if bool(entry.get("evidence_now")) != (entry["expect"] == EVIDENCE):
                problems.append(
                    f"{entry['name']}: manifest says {entry['expect']} but recorded "
                    f"evidence_now={entry.get('evidence_now')}"
                )
            rows.append(
                {
                    "name": entry["name"],
                    "expect": entry["expect"],
                    "got": "not-stored",
                    "stored": None,
                    "note": entry.get("note", ""),
                }
            )
            continue
        path = out_dir / entry["stored"]
        if not path.exists():
            problems.append(f"{entry['name']}: missing {entry['stored']}")
            continue
        raw = _load(path)
        expected_sha = entry.get("stored_sha256")
        if expected_sha and hashlib.sha256(raw).hexdigest() != expected_sha:
            problems.append(
                f"{entry['name']}: {entry['stored']} is not the recorded bytes "
                "(hash mismatch) — re-record it rather than editing it"
            )
            continue
        payload = raw.decode("utf-8", "replace")
        got = FetchResult(ok=True, text=payload).has_stream_evidence()
        want = entry["expect"] == EVIDENCE
        if got != want:
            problems.append(
                f"{entry['name']}: expected {entry['expect']}, gate said "
                f"{'evidence' if got else 'no-evidence'}"
            )
        rows.append({"name": entry["name"], "expect": entry["expect"],
                     "got": "evidence" if got else "no-evidence", "stored": entry["stored"],
                     "note": entry.get("note", "")})
    return problems, rows


def _report_partial(exc: "UnreduciblePage", *, json_output: bool) -> None:
    """Report the comparison that WAS possible, then fail.

    The flips come first because they are the answer; the pages that could not be
    reached follow, and say the comparison is partial. Without this an aborted
    refresh reported nothing at all, so a week when a provider rate-limits the
    runner was a week with no verdict comparison — which reads exactly like a
    week in which nothing flipped.

    Nothing was written, so this is a report and not a corpus: the payload says
    so (`ok: false`, plus `unusable`) rather than leaving the reader to infer it
    from the exit code, which the workflow's step turns into a warning.
    """
    if json_output:
        print(
            json.dumps(
                {
                    "refreshed_at": (
                        exc.entries[0]["fetched_at"] if exc.entries else ""
                    ),
                    "pages": exc.entries,
                    "flips": exc.flips,
                    "unusable": exc.unusable,
                    "ok": False,
                },
                indent=2,
            )
        )
        return
    for flip in exc.flips:
        print(
            f"FLIP: record_pages: {flip['name']:26s} {flip['was']} -> "
            f"{flip['now']} ({flip['direction']}) {flip['url']}"
        )
    print(
        f"FAIL: record_pages: {len(exc.unusable)} page(s) could not be reduced, "
        f"so NOTHING was written and the comparison is partial — "
        f"{len(exc.flips)} flip(s) among {len(exc.entries)} reachable page(s); "
        f"{exc}",
        file=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="manifest to compare verdicts against (default: --out, i.e. in-place)",
    )
    parser.add_argument("--refresh", action="store_true", help="re-fetch every page")
    parser.add_argument("--check", action="store_true", help="gate the stored corpus")
    parser.add_argument("--list", action="store_true", help="describe the corpus")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.refresh:
        try:
            entries, flips = record(args.out, baseline_dir=args.baseline)
        except UnreduciblePage as exc:
            _report_partial(exc, json_output=args.json)
            sys.exit(1)
        if args.json:
            # Payload alone on stdout, like every other --json in this repo.
            print(
                json.dumps(
                    {
                        "refreshed_at": entries[0]["fetched_at"] if entries else "",
                        "pages": entries,
                        "flips": flips,
                    },
                    indent=2,
                )
            )
        elif flips:
            for flip in flips:
                print(
                    f"FLIP: record_pages: {flip['name']:26s} {flip['was']} -> "
                    f"{flip['now']} ({flip['direction']}) {flip['url']}"
                )
        else:
            print(
                f"OK: record_pages: refreshed {len(entries)} page(s), "
                "no verdict changed"
            )
        return

    if args.list:
        for page in PAGES:
            print(
                f"OK: record_pages: {page.name:26s} {page.expect:12s} "
                f"store={page.store:16s} {page.url}"
            )
        return

    problems, rows = check(args.out)
    if args.json:
        print(json.dumps({"ok": not problems, "pages": rows, "problems": problems}, indent=2))
    else:
        for row in rows:
            if row["got"] == "not-stored":
                # Deliberately NOT a `SKIP:` prefix: `do-harness` treats a SKIP
                # line as a warning, and under `--strict` a warned sensor is
                # "weak evidence" — so a clean corpus would report an error.
                print(
                    f"OK: record_pages: {row['name']:26s} not stored "
                    "(verify-only — checked at record time)"
                )
                continue
            state = "OK" if row["expect"] == row["got"] else "MISMATCH"
            print(f"{state}: record_pages: {row['name']:26s} {row['got']}")
        for problem in problems:
            print(f"FAIL: record_pages: {problem}", file=sys.stderr)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
