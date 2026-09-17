#!/usr/bin/env python3
"""extract_candidates.py — turn an agent transcript into planner input.

Phase 1 of `live-stream-runtime-spec.md`. This is the seam between the
model-driven half of the runtime and the deterministic half.

Why the seam exists at all: the runtime agent is deliberately denied `edit` and
`bash` (`self-improve.yml` and `runtime-daily.yml` both rely on that), so it
**cannot write `.tmp/candidates.json` itself**. The transcript is the only
channel it has, which makes the extraction contract a security-relevant
boundary rather than a parsing convenience. It is therefore enforced here, in
code with tests, instead of being assumed of the model.

Contract, deliberately narrow:

* The transcript may be the rich shape `{"transcripts": [{"output": "..."}]}`,
  the flat `{"<id>": "..."}` shape, an `opencode run --format json` event
  stream, or plain text. Text is collected by walking the document, so a change
  in the event envelope does not silently produce zero candidates.
* Candidates arrive in a fenced ```` ```json ```` block containing either a list
  of objects or an object with a `candidates` list.
* A candidate is accepted only with a non-empty `league`, exactly two `teams`
  (a list, or an `"A vs B"` string) and a parseable `start`. Anything else is
  reported by index, so a malformed emission is diagnosable rather than silent.

The output feeds `scripts/upsert_events.py`, which does the dedupe and the state
labelling. This script validates but never decides.

Usage:
    python3 scripts/extract_candidates.py --transcript .tmp/transcript.json --out .tmp/candidates.json
    python3 scripts/extract_candidates.py --transcript run.json --json

Exit codes:
    0  PASS — at least one usable candidate
    1  FAIL — no usable candidates (reported with the reason, never silent)
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:  # direct CLI execution: `python3 scripts/extract_candidates.py`
    from evidence import coerce_evidence
    from upsert_events import game_key as build_game_key, parse_dt
except ImportError:  # imported as a package module, e.g. scripts.extract_candidates
    from scripts.evidence import coerce_evidence  # type: ignore
    from scripts.upsert_events import game_key as build_game_key, parse_dt  # type: ignore

FENCED_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)
# Envelope keys that hold model output. Everything else is walked structurally.
TEXT_KEYS = ("output", "text", "content", "message", "result", "reasoning")
STATE_VERIFIED = "VERIFIED"
STATE_UNVERIFIED = "UNVERIFIED"


def _collect(node: object, into: list[str], *, only_text_keys: bool) -> None:
    """Walk a transcript, collecting model output.

    In `only_text_keys` mode only values under a known output key count. In
    fallback mode *every* string counts — including envelope noise such as an
    event's `"type": "message"`. That noise is harmless because a candidate
    still has to survive `normalise_candidate`, and because returning nothing is
    the failure mode that looks like "no games today".
    """
    if isinstance(node, str):
        if not only_text_keys:
            into.append(node)
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if key in TEXT_KEYS and isinstance(value, str):
                into.append(value)
            elif not only_text_keys:
                _collect(value, into, only_text_keys=False)
        return
    if isinstance(node, list):
        for item in node:
            _collect(item, into, only_text_keys=only_text_keys)


def transcript_text(payload: object) -> str:
    """All model-output text in a transcript, in document order.

    Prefers known output keys, then falls back to every string leaf. The fallback
    exists because the envelope is a third-party format that can change without
    notice, and returning nothing is a silent zero-candidate run — the failure
    mode most likely to look like \"no games today\".
    """
    if isinstance(payload, str):
        return payload
    chunks: list[str] = []
    _collect(payload, chunks, only_text_keys=True)
    if not chunks:
        _collect(payload, chunks, only_text_keys=False)
    return "\n".join(chunk for chunk in chunks if chunk)


def json_blocks(text: str) -> list[object]:
    """Parse every fenced JSON block, plus a bare top-level JSON document."""
    parsed: list[object] = []
    for match in FENCED_BLOCK.finditer(text or ""):
        body = match.group(1).strip()
        if not body:
            continue
        try:
            parsed.append(json.loads(body))
        except json.JSONDecodeError:
            continue
    if not parsed and (text or "").strip():
        try:
            parsed.append(json.loads(text.strip()))
        except json.JSONDecodeError:
            pass
    return parsed


def candidate_objects(payload: object) -> list[dict]:
    """Pull candidate objects out of a parsed JSON block."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("candidates", "streams", "games", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        # A single candidate emitted on its own is still a candidate.
        if payload.get("league") and payload.get("start"):
            return [payload]
    return []


def normalise_candidate(raw: dict) -> dict:
    """Validate and normalise one candidate. Raises ValueError with the reason."""
    league = str(raw.get("league") or "").strip()
    if not league:
        raise ValueError("missing 'league'")

    teams = raw.get("teams")
    if isinstance(teams, str):
        teams = [part.strip() for part in teams.split(" vs ") if part.strip()]
    if isinstance(teams, list):
        teams = [str(part).strip() for part in teams if str(part).strip()]
    else:
        teams = []
    if len(teams) != 2:
        raise ValueError(
            f"expected exactly two teams, got {teams or raw.get('teams')!r}"
        )

    start = str(raw.get("start") or raw.get("startTime") or "").strip()
    if parse_dt(start) is None:
        raise ValueError(f"unparseable start {start!r}")

    state = str(raw.get("state") or STATE_UNVERIFIED).strip().upper()
    if state not in (STATE_VERIFIED, STATE_UNVERIFIED):
        # `WRONG` is an audit verdict, never something a run may assert about a
        # game it is proposing. A model emitting it would be overwriting the one
        # label a human is supposed to trust.
        raise ValueError(
            f"state {state!r} is not proposable (only VERIFIED/UNVERIFIED)"
        )

    summary = str(raw.get("summary") or "").strip()
    if not summary:
        summary = f"{league}: {' vs '.join(teams)}"

    candidate = {
        "league": league,
        "teams": teams,
        "start": start,
        "state": state,
        "summary": summary,
        "game_key": str(raw.get("game_key") or "") or build_game_key(league, teams, start),
        "url": str(raw.get("url") or ""),
        "backend": str(raw.get("backend") or ""),
    }
    end = str(raw.get("end") or raw.get("endTime") or "").strip()
    if end:
        candidate["end"] = end
    if raw.get("league_color_id"):
        candidate["league_color_id"] = str(raw["league_color_id"])
    # Carried through, never invented. Phase 3 audits against these: a missing
    # flag means INCONCLUSIVE and an explicit `false` means WRONG, so a default
    # here would be a verdict about a broadcast nobody observed. This whitelist
    # used to drop them, which is why the audit could never get past "no
    # evidence recorded" no matter what the agent emitted.
    evidence = coerce_evidence(raw)
    if evidence:
        candidate["evidence"] = evidence
    return candidate


def extract(payload: object) -> tuple[list[dict], list[str]]:
    """Returns (accepted candidates, rejection reasons). Deduped by game_key."""
    accepted: list[dict] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for index, block in enumerate(json_blocks(transcript_text(payload))):
        for offset, raw in enumerate(candidate_objects(block)):
            label = f"block {index} item {offset}"
            try:
                candidate = normalise_candidate(raw)
            except ValueError as exc:
                rejected.append(f"{label}: {exc}")
                continue
            if candidate["game_key"] in seen:
                continue
            seen.add(candidate["game_key"])
            accepted.append(candidate)
    return accepted, rejected


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract candidate games from an agent transcript.",
    )
    parser.add_argument("--transcript", required=True, help="transcript JSON")
    parser.add_argument("--out", help="write {\"candidates\": [...]} here")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.transcript)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"FAIL: extract_candidates: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except OSError as exc:
        print(f"FAIL: extract_candidates: {path}: unreadable ({exc})", file=sys.stderr)
        sys.exit(2)

    try:
        payload: object = json.loads(text)
    except json.JSONDecodeError:
        # Plain-text transcripts are legitimate: `--format json` is an option,
        # not a guarantee.
        payload = text

    candidates, rejected = extract(payload)

    for reason in rejected:
        print(f"WARN: extract_candidates: rejected {reason}", file=sys.stderr)

    if not candidates:
        print(
            "FAIL: extract_candidates: no usable candidates "
            f"({len(rejected)} rejected) — the agent must emit a fenced ```json "
            "block of candidate objects",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"candidates": candidates}, indent=2) + "\n", encoding="utf-8"
        )
        print(f"OK: extract_candidates: wrote {len(candidates)} candidates to {target}")
    else:
        print(
            f"OK: extract_candidates: {len(candidates)} candidates "
            f"({len(rejected)} rejected)"
        )

    if args.json:
        print(json.dumps({"candidates": candidates, "rejected": rejected}, indent=2))


if __name__ == "__main__":
    main()
