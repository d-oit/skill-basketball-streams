#!/usr/bin/env python3
"""stream_links.py — the link half of an event description, in one place.

`references/calendar-setup.md` specifies a six-part event description: the
`League:`/`Teams:` identity lines, a `Date/Time:` line, a `FREE STREAM LINKS:`
list, an `Access:` line and a `SOURCE REFERENCE:` block (`Found at:` /
`Validated:` / `Validation Notes:`).

Only two of those six parts were ever written. `calendar_io.description_for`
emitted `League:` and `Teams:` and nothing else, and `upsert_events._event_fields`
carried no link at all, so a candidate's `directLink` was dropped at the planner
boundary. Three things then read what nothing wrote:

    SKILL.md Constraint 9          every description carries `sourceReference`
                                   and a validation timestamp
    references/self-learning.md    loop 5 quarantines a stored link by removing
                                   it from the event description
    scripts/link_check.py          "every calendar event carries a
                                   `sourceReference` and one or more direct
                                   stream links"

which is why `link_check.py --input links.json` had no producer: there was no
link record to build one from. `scripts/link_inventory.py` is that producer, and
this module is the vocabulary it shares with the writer.

It exists as its own module for the reason the repo keeps re-learning: a reader
and a writer that each know their own field names drift apart silently. The
planner that *carries* the link, the writer that *renders* it and the inventory
that *reads it back* all import from here, so a rename cannot land on one side
only.

Nothing here touches the network or the calendar API.
"""
from __future__ import annotations

import re

# The product speaks camelCase (`directLink`, `sourceReference` — SKILL.md,
# `references/lessons-learned.md`), the runtime speaks snake_case (`game_key`,
# `league_color_id`). Both are accepted, so a candidate written to the documented
# contract and a plan row written by this runtime are the same thing to every
# caller. First alias present and non-empty wins.
_ALIASES: dict[str, tuple[str, ...]] = {
    "direct_links": ("directLinks", "direct_links", "directLink", "direct_link"),
    "source_reference": ("sourceReference", "source_reference"),
    "access": ("access",),
    "validated_at": (
        "validatedAt",
        "validated_at",
        "validationTimestamp",
        "validated",
    ),
    "validation_notes": ("validationNotes", "validation_notes"),
}

LINKS_HEADER = "FREE STREAM LINKS"
SOURCE_HEADER = "SOURCE REFERENCE"
FOUND_AT = "found at"
VALIDATED = "validated"
NOTES = "validation notes"

_URL_RE = re.compile(r"https?://\S+")


def _first(row: dict, keys: tuple[str, ...]) -> object:
    """The first alias on `row` that carries a value, or `""`."""
    for key in keys:
        value = row.get(key)
        if value:
            return value
    return ""


def _as_entry(value: object) -> dict | None:
    """One `{source, url}` pair from a list item, or `None` if unusable."""
    if isinstance(value, str):
        url = value.strip()
        return {"source": "", "url": url} if url else None
    if isinstance(value, dict):
        url = str(value.get("url") or value.get("link") or "").strip()
        if not url:
            return None
        return {"source": str(value.get("source") or value.get("name") or "").strip(),
                "url": url}
    return None


def _dedupe(entries: list[dict]) -> list[dict]:
    """Drop repeats of the same `(source, url)`, keeping the first."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for entry in entries:
        key = (entry.get("source", ""), entry.get("url", ""))
        if key not in seen:
            seen.add(key)
            out.append(entry)
    return out


def normalise_links(row: dict) -> dict:
    """The canonical link fields `row` actually carries. Never invents one.

    Accepts a candidate (documented camelCase) or a plan row (snake_case) and
    returns only the keys that were present and non-empty, so a row with no link
    information produces `{}` and the description stays byte-identical to what it
    was before this module existed.
    """
    if not isinstance(row, dict):
        return {}
    fields: dict = {}

    raw = _first(row, _ALIASES["direct_links"])
    entries: list[dict] = []
    if isinstance(raw, (list, tuple)):
        entries = [entry for entry in (_as_entry(item) for item in raw) if entry]
    elif raw:
        entry = _as_entry(raw)
        if entry:
            entries = [entry]
    entries = _dedupe(entries)
    if entries:
        fields["direct_links"] = entries

    for field in ("source_reference", "access", "validated_at", "validation_notes"):
        value = _first(row, _ALIASES[field])
        if value:
            fields[field] = str(value).strip()
    return fields


def render_block(row: dict) -> list[str]:
    """The description lines *after* the identity block, or `[]`.

    The section headers and the `- ` bullet form are copied from
    `references/calendar-setup.md` verbatim. A source with no name renders as a
    bare bullet (`- https://…`) rather than `- : https://…`, because a reader
    parsing a label out of the line would otherwise record `":"` as a source.
    """
    fields = normalise_links(row)
    lines: list[str] = []

    links = fields.get("direct_links") or []
    if links:
        lines += ["", f"{LINKS_HEADER}:"]
        for entry in links:
            source, url = entry.get("source", ""), entry.get("url", "")
            lines.append(f"- {source}: {url}" if source else f"- {url}")

    if fields.get("access"):
        lines += ["", f"Access: {fields['access']}"]

    reference = fields.get("source_reference", "")
    validated = fields.get("validated_at", "")
    notes = fields.get("validation_notes", "")
    if reference or validated or notes:
        lines += ["", f"{SOURCE_HEADER}:"]
        if reference:
            lines.append(f"- Found at: {reference}")
        if validated:
            lines.append(f"- Validated: {validated}")
        if notes:
            lines.append(f"- Validation Notes: {notes}")
    return lines


def _section(text: str, header: str) -> list[str]:
    """The `- ` bullets under a section header, up to the next non-bullet line.

    A blank line inside the list is skipped rather than terminating it, because
    the template in `references/calendar-setup.md` separates every paragraph with
    one. The list ends at the next line that carries content and is not a bullet —
    which is how `Access:` and `SOURCE REFERENCE:` terminate `FREE STREAM LINKS:`.
    """
    bullets: list[str] = []
    inside = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(header.upper()):
            inside = True
            continue
        if not inside:
            continue
        if stripped.startswith("-"):
            bullets.append(stripped)
        elif stripped:
            break
    return bullets


def _after_label(line: str, label: str) -> str:
    """The value of `- <label>: value`, or `""` when the label is not there."""
    body = line.lstrip("-").strip()
    head, separator, tail = body.partition(":")
    if separator and head.strip().lower() == label:
        return tail.strip()
    return ""


def links_from_description(text: str) -> list[dict]:
    """Read the link list back out of a description.

    The URL is located by pattern rather than by splitting on `:`, because a bare
    bullet (`- https://…`) has a colon of its own and splitting would read `https`
    as the source name.
    """
    found: list[dict] = []
    for line in _section(text, LINKS_HEADER):
        match = _URL_RE.search(line)
        if not match:
            continue
        url = match.group(0).rstrip(".,;")
        source = line[1:match.start()].strip().rstrip(":").strip()
        found.append({"source": source, "url": url})
    return _dedupe(found)


def source_reference_from_description(text: str) -> str:
    """The `Found at:` URL, or `""`."""
    for line in _section(text, SOURCE_HEADER):
        match = _URL_RE.search(line)
        if match and _after_label(line, FOUND_AT):
            return match.group(0).rstrip(".,;")
    return ""


def validated_at_from_description(text: str) -> str:
    """The `Validated:` timestamp, or `""`."""
    for line in _section(text, SOURCE_HEADER):
        value = _after_label(line, VALIDATED)
        if value:
            return value
    return ""


def validation_notes_from_description(text: str) -> str:
    """The `Validation Notes:` text, or `""`."""
    for line in _section(text, SOURCE_HEADER):
        value = _after_label(line, NOTES)
        if value:
            return value
    return ""
