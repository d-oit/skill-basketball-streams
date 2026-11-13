#!/usr/bin/env python3
"""upsert_events.py — Phase 2: check the calendar first, then write.

Implements the write policy from `live-stream-runtime-spec.md` §5.5 and §6. The
ordering is a safety property, not a detail: the runtime lists existing events
*today…today+10d* and reconciles in memory **before** any write, so it can never
duplicate an event it is about to create.

The planner's runtime entry point also enforces candidate eligibility before
reconciliation: ProB is excluded; start time must fall strictly after the
captured `--now` and no later than its ten-day cutoff; and free/live status
must be explicitly supported by evidence flags, a direct stream URL, a source
reference, free-access wording, a validation timestamp and explanatory notes.
An `UNVERIFIED` state cannot substitute for any of those facts.

Reconciliation rules:

| Existing | New candidate | Action |
|---|---|---|
| nothing | any | **create** |
| `VERIFIED` | any | **skip** — never touch a confirmed event |
| `UNVERIFIED` | `VERIFIED` | **update** — promote (colour 5 → 6, drop the prefix) |
| `UNVERIFIED` | `UNVERIFIED`, anything differs | **update** — refresh title/links/time/colour |
| `UNVERIFIED` | `UNVERIFIED`, nothing differs | **unchanged** — no write at all |
| `WRONG` | anything but `WRONG` | **skip** — an audit verdict outranks a fresh guess |
| `WRONG` | `WRONG` | **skip** — already labelled |

The `unchanged` row is the difference between *identity* and *equality*.
`events_match` decides whether a stored event is **the same game**; it says nothing
about whether anything about it is different. The planner used to treat "the same
game" as "write it again", so every unverified match was re-PATCHed on every run —
a real HTTP write, and an `action: "update"` row in the applied result claiming
the calendar changed — even when the stored event already held exactly what the
writer would send. `body_matches_event` (in `calendar_io.py`) now compares the
full payload the calendar would end up holding (title, start, end, description,
colour) against the stored event, and a match that changes nothing emits
`unchanged`: the event is still ours, it just needs no write. A stored event that
is *missing* a field we compare — a legacy event with an empty description, say —
is never `unchanged`; it needs the update that fills it in. A `VERIFIED` promotion
is never `unchanged` either: the label on a public calendar is the change.

That last-but-one row is a deliberate choice: a `WRONG` label means a human-visible
audit concluded the broadcast was not free or never live. Silently overwriting it
with a new unverified claim would erase the only record of that conclusion.

**Where a `WRONG` comes from, which is the subtle part.** The table reads the
stored state off the calendar, and the calendar recovers it from the **title
prefix** (`[WRONG] …`) — but nothing wrote that prefix. `audit_events.py` computes
the relabelled title and puts it in `audit.jsonl`, and no component applied it, so
on the real path the `WRONG` rows above were unreachable: an event the audit had
proved was paid or never live stayed on the subscriber's calendar looking exactly
like a game awaiting confirmation,        and the next run's fresh guess was free to
promote it back to `VERIFIED` — the one thing §17 calls a hard requirement.

So `--verdicts` takes the audit ledger itself and honours it from the *record*:
an event whose newest verdict is `WRONG` is skipped, whatever its title says. The
join is by `event_id`, which is the only identity the two sides share (a calendar
event parsed by `calendar_io` has no `game_key`). The dedupe uses
`audit_events.latest_per_event`, deliberately the same function the audit uses:
if the two disagreed about which row is current, this guarantee could invert.

Run without `--apply`, this prints the plan only — the calendar API call itself
belongs to the workflow, so this module stays testable with no credentials.

Usage:
    python3 scripts/upsert_events.py --existing existing.json --candidates candidates.json --now 2026-10-13T19:00:00Z
    python3 scripts/upsert_events.py --existing existing.json --candidates candidates.json --now 2026-10-13T19:00:00Z --json
    python3 scripts/upsert_events.py --existing existing.json \\
        --candidates candidates.json --verdicts telemetry/audit.jsonl \\
        --now 2026-10-13T19:00:00Z --window-days 10

Exit codes:
    0  PASS — a plan was produced
    1  FAIL — nothing to do
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

try:  # direct CLI execution
    # `latest_per_event` is shared with `audit_events.py` rather than copied: the
    # two must agree about which ledger row is current or this guarantee inverts.
    from audit_events import latest_per_event
    from calendar_config import get_window_days
    from calendar_io import body_matches_event, build_event_body, description_for
    from evidence import coerce_evidence
    from stream_links import normalise_links
    from team_tokens import team_matches, team_tokens, text_names_all
    from verification import (
        DEFAULT_LEAGUE_COLOR_ID,
        STATE_UNVERIFIED,
        STATE_VERIFIED,
        STATE_WRONG,
        color_for,
        normalise_state,
        title_for,
    )
except ImportError:  # imported as a package module
    from scripts.audit_events import latest_per_event  # type: ignore
    from scripts.calendar_config import get_window_days  # type: ignore
    from scripts.calendar_io import (  # type: ignore
        body_matches_event,
        build_event_body,
        description_for,
    )
    from scripts.evidence import coerce_evidence  # type: ignore
    from scripts.stream_links import normalise_links  # type: ignore
    from scripts.team_tokens import team_matches, team_tokens, text_names_all  # type: ignore
    from scripts.verification import (  # type: ignore
        DEFAULT_LEAGUE_COLOR_ID,
        STATE_UNVERIFIED,
        STATE_VERIFIED,
        STATE_WRONG,
        color_for,
        normalise_state,
        title_for,
    )

MATCH_WINDOW = timedelta(minutes=30)
VERDICT_WRONG = "WRONG"
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_SKIP = "skip"
# A matching stored event whose full payload is already what this run would send.
# It is not a skip: the event *is* ours to maintain, it just needs no write. See
# the reconciliation table above and `plan_upsert`.
ACTION_UNCHANGED = "unchanged"
# The calendar's current access policy excludes all ProB games. This is a hard
# planner rule so a model-provided `free_confirmed: true` cannot promote one.
PROB_LABEL_RE = re.compile(r"\bpro[\s-]*b\b", re.IGNORECASE)


def parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def game_key(league: str, teams: str | list[str], start: object) -> str:
    """Stable, order-independent identity for one broadcast.

    Team order varies between sources (`A vs B` / `B vs A`), so the pair is
    sorted — otherwise the same game would produce two keys and defeat dedupe.
    """
    if isinstance(teams, str):
        parts = [part.strip() for part in teams.split(" vs ")]
    else:
        parts = [str(part).strip() for part in teams]
    parts = [part for part in parts if part]
    if len(parts) == 2:
        parts = sorted(parts)
    moment = parse_dt(start)
    stamp = moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if moment else ""
    return "|".join([(league or "").strip(), *parts, stamp])


def _teams_of(event: dict) -> list[str]:
    teams = event.get("teams")
    if isinstance(teams, list):
        return [str(t).strip() for t in teams if str(t).strip()]
    if isinstance(teams, str):
        return [part.strip() for part in teams.split(" vs ") if part.strip()]
    for key in ("team1", "team2"):
        if event.get(key):
            return [str(event[key]).strip()]
    return []


# Canonical club-name matching lives in `team_tokens.py` because the render
# ladder asks the same question ("does the page name this game?"). Re-exported
# here under the names this module's callers and tests already use, so the rule
# has exactly one implementation.
_team_tokens = team_tokens
_team_matches = team_matches


def _event_text(event: dict) -> str:
    """Everything an entry says about itself, for the fallback match below.

    The `summary` is where a human writes the clubs; the `description` is where
    this system writes them. Both are read, because either may be the only side
    present and neither is more authoritative than the other.
    """
    return " ".join(
        str(event.get(key) or "") for key in ("summary", "title", "description")
    )


def events_match(existing: dict, candidate: dict) -> bool:
    """Duplicate when the slot overlaps AND the teams and league agree.

    **The teams check is not skippable.** It used to be — `if existing_teams and
    candidate_teams:` meant that an empty team list on *either* side removed the
    check entirely, leaving the 30-minute window (and, only if the stored event
    happened to carry a league, the league check) as the sole gate. Measured on
    the live calendar on 2026-09-15: of 102 events, **78 carry no `Teams:` line**,
    so **76 % of the calendar matched any candidate that landed in its slot**.
    Every one of them is `VERIFIED`, and a `VERIFIED` match is a **skip** — so the
    effect was not a duplicate but a **silently dropped game**: a real fixture
    within 30 minutes of one of those entries was treated as already present and
    never written.

    With no teams recorded on the stored side, the entry must therefore *name the
    candidate's clubs* in its own text. On the live calendar those 78 entries all
    do — `ALBA BERLIN vs. NINERS Chemnitz (easyCredit BBL)`, `FIBA U20 Women's
    EuroBasket 2026 - Day 1` — which is why this is a fallback rather than a
    loosening: the correct game still matches, and a *different* game at the same
    time now gets created instead of vanishing.

    An exact `game_key` on both sides short-circuits all of that. It is written
    into `extendedProperties.private` by `calendar_io.build_event_body`, so a
    later run matches the game it created even if a second source spells a club
    differently. Equality only ever *adds* a match; a differing key still falls
    through to the slot/teams rule so a legacy candidate is not re-created.
    """
    left = parse_dt(existing.get("start") or existing.get("startTime"))
    right = parse_dt(candidate.get("start") or candidate.get("startTime"))
    # The exact identity, when both sides have it. New events carry
    # `extendedProperties.private.game_key`; a match here is the same game even
    # when the two sources would spell a club differently, and it does not need
    # the 30-minute window to agree. Old events have no key, so the rule below
    # remains the fallback rather than being replaced.
    left_key = str(existing.get("game_key") or "")
    right_key = str(candidate.get("game_key") or "")
    if left_key and right_key and left_key == right_key:
        return True
    if left is None or right is None:
        return False
    if abs(left - right) > MATCH_WINDOW:
        return False

    existing_teams = _teams_of(existing)
    candidate_teams = _teams_of(candidate)
    # The pairing rule lives in `_pairing_matches` (shared with the fixture
    # anchor and the same-day guard below): one implementation, or the three
    # disagree about which clubs name the same game. A candidate with no teams
    # is left alone here — it has nothing to be checked against, and
    # `extract_candidates.py` refuses such a candidate before it can reach
    # the planner.
    if candidate_teams and not _pairing_matches(
        existing_teams, candidate_teams, _event_text(existing)
    ):
        return False

    if not _leagues_compatible(candidate.get("league"), existing.get("league")):
        return False
    return True


def _league_tokens(label: object) -> frozenset:
    """The alphanumeric tokens of a league label, lowercased."""
    return frozenset(re.findall(r"[a-z0-9]+", str(label or "").lower()))


def _leagues_compatible(candidate_league: object, other_league: object) -> bool:
    """Can two league labels name the same competition?

    Token-subset, not substring: the 2026-11-13 dry run stored "FIBA Women's
    EuroBasket 2027 Qualifiers" while the agent re-read "FIBA Women's
    EuroBasket Qualifiers 2027" — same tokens in a different order, which the
    old substring rule read as two competitions and let a duplicate through.
    A shorter label naming a longer one ("FIBA" vs the full qualifier name,
    "BBL" vs "BBL Pokal") still matches, while disjoint labels ("BBL" vs
    "EuroLeague") never do. An empty label on either side constrains
    nothing. Shared with the fixture anchor and the same-day guard below, so
    the three agree about which labels name the same competition.
    """
    league = _league_tokens(candidate_league)
    other = _league_tokens(other_league)
    if league and other and not (league <= other or other <= league):
        return False
    return True


def _pairing_matches(
    existing_teams: list[str], candidate_teams: list[str], existing_text: str = ""
) -> bool:
    """Do both sides name the same pairing — the teams half of `events_match`.

    *Both* teams must match: two different games can share one team name on a
    double-header day, so a single overlap is not enough. When the stored side
    records no teams of its own, that is not "matches anything": it must name
    the candidate's clubs in its own text.
    """
    if existing_teams and candidate_teams:
        return all(
            any(_team_matches(team, other) for other in candidate_teams)
            for team in existing_teams
        ) and all(
            any(_team_matches(mate, team) for team in existing_teams)
            for mate in candidate_teams
        )
    if candidate_teams:
        return text_names_all(existing_text, candidate_teams)
    return False


def _fixture_for(candidate: dict, fixtures: list[dict]) -> dict | None:
    """The single fixture naming this candidate's game, or None.

    Unambiguous-only: zero matches means no fixture coverage (DBB national-team
    games, for example — the feed carries BBL, EuroLeague and BCL), and two or
    more means a same-day double-header the planner must not guess between.
    Either way the candidate keeps the agent's own reading.
    """
    candidate_teams = _teams_of(candidate)
    right = parse_dt(candidate.get("start") or candidate.get("startTime"))
    if not candidate_teams or right is None:
        return None
    day = right.astimezone(timezone.utc).date()
    hits = []
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        fixture_teams = fixture.get("teams")
        if isinstance(fixture_teams, str):
            fixture_teams = [
                part.strip()
                for part in fixture_teams.split(" vs ")
                if part.strip()
            ]
        fixture_teams = [
            str(team).strip() for team in (fixture_teams or []) if str(team).strip()
        ]
        if not _pairing_matches(fixture_teams, candidate_teams):
            continue
        if not _leagues_compatible(candidate.get("league"), fixture.get("league")):
            continue
        start = parse_dt(fixture.get("start") or fixture.get("startTime"))
        if start is None:
            continue
        if start.astimezone(timezone.utc).date() != day:
            continue
        hits.append(fixture)
    if len(hits) != 1:
        return None
    return hits[0]


def anchor_to_fixtures(
    candidates: list[dict], fixtures: list[dict]
) -> tuple[list[dict], dict[int, str]]:
    """Pin candidate tip times to the fixture feed before planning.

    The agent reads tip times off announcement pages and drifts — 2026-11-13:
    13:50 against a 15:00 game, 70 minutes past the match window, so dedupe
    missed the VERIFIED event already holding it and planned a duplicate. The
    league feed (ICS/schedule) is authoritative for *when* a game is, so a
    candidate that unambiguously names a fixture's game takes the fixture's
    start; the snap covers planning, matching and the created event together,
    because every reader (`eligibility_reason`, `game_key`, `_event_fields`)
    takes `start` from the candidate.

    Returns the anchored candidates plus notes keyed by `id()` of each snapped
    copy, so the planner can say *why* a time differs from the agent's reading.
    Copies, never mutates: the caller's list still holds the agent's own
    readings. A candidate end the agent supplied shifts by the same delta, so a
    two-hour broadcast stays two hours; a missing end stays missing.
    """
    anchored: list[dict] = []
    notes: dict[int, str] = {}
    if not fixtures:
        return list(candidates), notes
    for candidate in candidates:
        if not isinstance(candidate, dict):
            anchored.append(candidate)
            continue
        fixture = _fixture_for(candidate, fixtures)
        agent_start = parse_dt(candidate.get("start") or candidate.get("startTime"))
        fixture_start = (
            parse_dt(fixture.get("start") or fixture.get("startTime"))
            if fixture is not None
            else None
        )
        if fixture is None or agent_start is None or fixture_start is None:
            anchored.append(candidate)
            continue
        if agent_start == fixture_start:
            anchored.append(candidate)
            continue
        snapped = dict(candidate)
        fixture_text = str(fixture.get("start") or fixture.get("startTime") or "")
        snapped["start"] = fixture_text
        agent_end = parse_dt(candidate.get("end") or candidate.get("endTime"))
        if agent_end is not None and ("end" in snapped or "endTime" in snapped):
            key = "end" if snapped.get("end") else "endTime"
            snapped[key] = (agent_end + (fixture_start - agent_start)).isoformat()
        anchored.append(snapped)
        notes[id(snapped)] = (
            f"tip anchored to fixture {fixture_text} "
            f"(agent read {candidate.get('start') or candidate.get('startTime')})"
        )
    return anchored, notes


def same_day_collision(existing_events: list[dict], candidate: dict) -> dict | None:
    """A settled event for the same pairing, league and date outside the window.

    The backstop for games the fixture feed does not cover: without a fixture
    row the anchor cannot fire, and the old path planned a `create` next to the
    VERIFIED event already holding the game — a visible duplicate on a public
    calendar. A same-pair, same-competition, same-date event is the same game
    all but certainly (basketball has no same-day double-headers), so the
    planner skips and names the collision instead of creating.

    Only VERIFIED and WRONG entries guard, checked by the caller: an
    UNVERIFIED entry is still being worked and a later run may promote it, so
    blocking that path would trade a possible duplicate for a certain dropped
    confirmation.
    """
    candidate_teams = _teams_of(candidate)
    right = parse_dt(candidate.get("start") or candidate.get("startTime"))
    if not candidate_teams or right is None:
        return None
    day = right.astimezone(timezone.utc).date()
    for event in existing_events:
        if not isinstance(event, dict):
            continue
        if events_match(event, candidate):
            continue
        left = parse_dt(event.get("start") or event.get("startTime"))
        if left is None:
            continue
        if left.astimezone(timezone.utc).date() != day:
            continue
        if not _leagues_compatible(candidate.get("league"), event.get("league")):
            continue
        if not _pairing_matches(
            _teams_of(event), candidate_teams, _event_text(event)
        ):
            continue
        return event
    return None


def load_verdicts(path: Path) -> dict[str, dict]:
    """The audit ledger as `{event_id: newest verdict row}`.

    Accepts JSONL (what `audit.jsonl` is) as well as a JSON document, and keeps
    the **last** row per `event_id` — the ledger is append-only, so the newest
    observation is the latest one written.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"FAIL: upsert_events: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except OSError as exc:
        print(f"FAIL: upsert_events: {path}: unreadable ({exc})", file=sys.stderr)
        sys.exit(2)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        rows: list = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(
                    f"FAIL: upsert_events: {path}:{number}: invalid JSON ({exc})",
                    file=sys.stderr,
                )
                sys.exit(2)
    else:
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and any(
            isinstance(payload.get(key), list)
            for key in ("verdicts", "rows", "events")
        ):
            rows = next(
                payload[key]
                for key in ("verdicts", "rows", "events")
                if isinstance(payload.get(key), list)
            )
        elif isinstance(payload, dict) and payload.get("event_id"):
            # A **single** ledger row is itself valid JSON, so a one-row file
            # takes this branch rather than the JSONL one. Reading it as a
            # wrapper object would yield no verdicts at all — and "no verdicts"
            # is the state in which the wrong event gets promoted, so this is
            # the silent-empty failure rather than a cosmetic one.
            rows = [payload]
        else:
            rows = []

    rows, _ = latest_per_event(rows)
    return {
        str(row.get("event_id")): row
        for row in rows
        if isinstance(row, dict) and str(row.get("event_id") or "")
    }


def verdict_wrong(verdict: dict | None) -> bool:
    """Whether a ledger row is a `WRONG` verdict (case-insensitive, tolerant)."""
    if not isinstance(verdict, dict):
        return False
    return str(verdict.get("verdict") or "").strip().upper() == VERDICT_WRONG


def eligibility_reason(
    candidate: dict,
    league: str,
    *,
    now: datetime,
    window_days: int = 10,
) -> str | None:
    """Return why a candidate cannot be written to the upcoming-game calendar."""
    labels = " ".join(
        str(candidate.get(key) or "") for key in ("competition", "summary")
    )
    if PROB_LABEL_RE.search(f"{league} {labels}"):
        return "ProB is excluded by the calendar's free-access policy"

    start = parse_dt(candidate.get("start") or candidate.get("startTime"))
    if start is None:
        return "candidate start is missing or is not a valid ISO-8601 datetime"
    start = start.astimezone(timezone.utc)
    current = (
        now.replace(tzinfo=timezone.utc)
        if now.tzinfo is None
        else now.astimezone(timezone.utc)
    )
    if start <= current:
        return f"candidate start {start.isoformat()} is not after now {current.isoformat()}"
    cutoff = current + timedelta(days=window_days)
    if start > cutoff:
        return (
            f"candidate start {start.isoformat()} is beyond the next "
            f"{window_days} days (through {cutoff.isoformat()})"
        )

    evidence = coerce_evidence(candidate)
    if evidence.get("paid") is True:
        return "candidate evidence says the stream is paid"
    if evidence.get("free_confirmed") is not True:
        return "free access is not explicitly confirmed by the candidate evidence"
    if evidence.get("live_confirmed") is not True:
        return "live-stream status is not explicitly confirmed by the candidate evidence"

    links = normalise_links(candidate)
    direct_links = links.get("direct_links") or []
    if not any(_is_http_url(row.get("url")) for row in direct_links):
        return "candidate has no valid direct stream URL"
    if not _is_http_url(links.get("source_reference")):
        return "candidate has no valid sourceReference URL"

    access = str(links.get("access") or "").strip().casefold()
    if re.search(r"\b(paid|paywall|pay-per-view|subscription|kostenpflichtig|abo)\b", access):
        return "candidate access field indicates paid or subscription access"
    if not re.search(r"\b(free|gratis|kostenlos)\b", access):
        return "candidate access field does not explicitly say the stream is free"

    validated_at = parse_dt(links.get("validated_at"))
    if validated_at is None:
        return "candidate has no valid validationTimestamp"
    if validated_at.astimezone(timezone.utc) > current:
        return "candidate validationTimestamp is in the future"
    if not links.get("validation_notes"):
        return "candidate has no validationNotes describing the free live stream"
    return None


def _is_http_url(value: object) -> bool:
    """Whether a field is an absolute HTTP(S) URL with a hostname."""
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def plan_upsert(
    existing_events: list[dict],
    candidates: list[dict],
    verdicts: dict | None = None,
    *,
    now: datetime | None = None,
    window_days: int = 10,
    fixtures: list[dict] | None = None,
) -> list[dict]:
    """Reconcile candidates against existing events. Returns one plan row each.

    `verdicts` maps `event_id` to the newest audit row for that event. It is the
    only way the `WRONG` rows in the table above are reachable on the real path:
    the calendar's own state comes from the title prefix, which `audit_events`
    computes but nothing applies.
    """
    verdicts = verdicts or {}
    # The fixture feed is authoritative for tip times: snap the agent's
    # readings to it before eligibility, matching and event creation, so a
    # drifted re-reading meets the event it names instead of duplicating it.
    candidates, anchor_notes = anchor_to_fixtures(candidates, fixtures or [])

    def _anchored_note(row_candidate: dict) -> str:
        note = anchor_notes.get(id(row_candidate), "")
        return f" ({note})" if note else ""
    plan: list[dict] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        new_state = normalise_state(candidate.get("state"), default=STATE_UNVERIFIED)
        league = str(candidate.get("league") or "")
        league_color = str(candidate.get("league_color_id") or DEFAULT_LEAGUE_COLOR_ID)
        key = candidate.get("game_key") or game_key(
            league, _teams_of(candidate), candidate.get("start")
        )

        # The command-line writer always supplies `now`. Keeping this optional
        # preserves the pure planner's use in historical fixture comparisons;
        # production planning receives an explicit, freshly resolved timestamp.
        if now is not None:
            ineligible = eligibility_reason(
                candidate, league, now=now, window_days=window_days
            )
            if ineligible:
                plan.append(
                    {
                        "action": ACTION_SKIP,
                        "reason": ineligible,
                        "game_key": key,
                        "event_id": "",
                        "state": new_state,
                        "title": "",
                        "color_id": "",
                        **_event_fields(candidate, league),
                    }
                )
                continue

        title = title_for(new_state, str(candidate.get("summary") or ""))
        color = color_for(new_state, league_color)

        match = None
        for event in existing_events:
            if isinstance(event, dict) and events_match(event, candidate):
                match = event
                break

        if match is None:
            collision = same_day_collision(existing_events, candidate)
            if collision is not None and normalise_state(
                collision.get("state"), default=STATE_UNVERIFIED
            ) in (STATE_VERIFIED, STATE_WRONG):
                plan.append(
                    {
                        "action": ACTION_SKIP,
                        "reason": (
                            "same-day pairing already on the calendar "
                            f"({collision.get('start') or collision.get('startTime') or '?'}, "
                            f"{collision.get('event_id') or collision.get('id') or 'no id'}) "
                            "— a drifted re-reading, not a second game"
                            + _anchored_note(candidate)
                        ),
                        "game_key": key,
                        "event_id": "",
                        "state": new_state,
                        "title": "",
                        "color_id": "",
                    }
                )
                continue
            plan.append(
                {
                    "action": ACTION_CREATE,
                    "reason": "no existing event for this slot"
                    + _anchored_note(candidate),
                    "game_key": key,
                    "event_id": "",
                    "state": new_state,
                    "title": title,
                    "color_id": color,
                    **_event_fields(candidate, league),
                }
            )
            continue

        existing_state = normalise_state(
            match.get("state"), default=STATE_UNVERIFIED
        )
        event_id = str(match.get("event_id") or match.get("id") or "")
        # Prefer the *existing* key once a match is found: it is the identity the
        # telemetry ledger already recorded, so a join by game_key stays stable
        # even when the two sources spell the clubs differently.
        key = str(match.get("game_key") or key)

        # The audit's own record, checked FIRST because it outranks both the
        # calendar's label and this run's guess. A game the audit proved was paid
        # or never live must not be re-promoted to VERIFIED when a later run
        # surfaces a fresh "confirmation" — that is what §17 calls a hard
        # requirement, and the `[WRONG]` prefix the table reads is written by
        # nobody on the real path.
        verdict = verdicts.get(event_id)
        if verdict_wrong(verdict):
            plan.append(
                {
                    "action": ACTION_SKIP,
                    "reason": (
                        "audit verdict WRONG stands"
                        f" (recorded {verdict.get('ts') or 'unknown'})"
                        " — a fresh guess does not overwrite it"
                    ),
                    "game_key": key,
                    "event_id": event_id,
                    "state": STATE_WRONG,
                    "title": "",
                    "color_id": "",
                }
            )
            continue

        if existing_state == STATE_VERIFIED:
            plan.append(
                {
                    "action": ACTION_SKIP,
                    "reason": "verified event exists — never modified"
                    + _anchored_note(candidate),
                    "game_key": key,
                    "event_id": event_id,
                    "state": existing_state,
                    "title": "",
                    "color_id": "",
                }
            )
            continue

        if existing_state == STATE_WRONG:
            plan.append(
                {
                    "action": ACTION_SKIP,
                    "reason": "audit verdict WRONG stands — not overwritten by a "
                    "fresh candidate",
                    "game_key": key,
                    "event_id": event_id,
                    "state": existing_state,
                    "title": "",
                    "color_id": "",
                }
            )
            continue

        promoted = new_state == STATE_VERIFIED
        row = {
            "action": ACTION_UPDATE,
            "reason": (
                "promoted UNVERIFIED -> VERIFIED (free access confirmed)"
                if promoted
                else "refreshed unverified event"
            ),
            "game_key": key,
            "event_id": event_id,
            "state": new_state,
            "title": title,
            "color_id": color,
            # The existing event's own times win on an update: a source
            # re-announcing a game an hour later must not silently move an
            # event subscribers already have in their calendars.
            **_event_fields({**candidate, "start": match.get("start") or candidate.get("start")}, league),
        }
        # **Two different questions live here.** `events_match` above answers "is
        # this the same game?" — identity. `body_matches_event` answers "is
        # anything different?" — equality. Only the first was ever asked, so
        # every unverified match was re-PATCHed on every run: a real HTTP write,
        # and an `action: "update"` ledger row claiming the calendar changed,
        # even when the stored event already held exactly what the writer would
        # send. A quiet day is common, so this ran most days.
        #
        # The body is built through the SAME two functions `apply_plan` uses to
        # send it (`build_event_body` + `description_for`), so the comparison is
        # against the payload the calendar would end up holding, not a second
        # opinion about what that payload is. `apply_plan` may be handed explicit
        # `descriptions`, but nothing on the runtime path passes them, so the
        # derived description is what actually travels. `visibility` is not
        # compared: `parse_event` does not read it back, so the stored side has no
        # field for it — a rule reading a field nothing writes is the trap this
        # repository already documents.
        #
        # A promotion is never `unchanged`. The new state changes the title
        # prefix *and* is stated explicitly, so the two cannot drift apart even
        # if a future colour rule made the colour alone ambiguous. A state
        # transition is a change because it is the event's label on a public
        # calendar; UNVERIFIED -> UNVERIFIED with identical content is not.
        if new_state == existing_state:
            body = build_event_body(row, description=description_for(row))
            if body_matches_event(body, match):
                row["action"] = ACTION_UNCHANGED
                row["reason"] = "stored event already matches — nothing to send"
        plan.append(row)
    return plan


def _event_fields(candidate: dict, league: str) -> dict:
    """The calendar fields a plan row needs to be executable.

    A plan that only carries a title and a colour cannot be applied, which would
    push the caller back into re-deriving the event body — the exact duplication
    this planner exists to remove.

    Two fields exist for the record rather than for the write. `league_color_id`
    is the colour a later `VERIFIED` promotion must restore (the plan's
    `color_id` is already the *state* colour, so the league one is otherwise
    lost), and `evidence` is what we observed about free access and liveness.
    Carrying them here is what lets `scripts/event_ledger.py` record an event
    from the plan and the apply result alone — no re-join against the candidate
    list, whose `game_key` an update row deliberately replaces with the existing
    event's.
    """
    fields = {
        "start": str(candidate.get("start") or candidate.get("startTime") or ""),
        "end": str(candidate.get("end") or candidate.get("endTime") or ""),
        "league": league,
        "teams": _teams_of(candidate),
        "league_color_id": str(
            candidate.get("league_color_id") or DEFAULT_LEAGUE_COLOR_ID
        ),
    }
    evidence = coerce_evidence(candidate)
    if evidence:
        fields["evidence"] = evidence
    # The link the event was built from, carried rather than dropped. `directLink`
    # and `sourceReference` are required by SKILL.md Constraint 9 and read back by
    # `calendar_io.description_for`/`link_inventory`, but this projection used to
    # discard them: they were the one part of a candidate the planner did not name,
    # so a link reached the calendar only if an agent wrote the description by
    # hand, and `link_check.py --input links.json` had nothing to read on the
    # runtime path.
    fields.update(normalise_links(candidate))
    return fields


def _load_fixtures(path: Path) -> list:
    """Read the league fixture feed: a JSON array, a `{"fixtures": [...]}`
    document, or JSONL with one fixture object per line (what `fixtures.py`
    writes). A malformed file is a caller bug and fails loudly — silently
    planning without the anchor would re-introduce the drifted duplicates.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"FAIL: upsert_events: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except OSError as exc:
        print(f"FAIL: upsert_events: {path}: unreadable ({exc})", file=sys.stderr)
        sys.exit(2)
    rows: list = []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("fixtures"), list):
        rows = payload["fixtures"]
    elif payload is None:
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(
                    f"FAIL: upsert_events: {path}:{number}: invalid JSON ({exc})",
                    file=sys.stderr,
                )
                sys.exit(2)
    else:
        print(
            f"FAIL: upsert_events: {path}: expected a list, a "
            f'{{"fixtures": [...]}} document, or JSONL',
            file=sys.stderr,
        )
        sys.exit(2)
    return rows


def summarise(plan: list[dict]) -> dict:
    # All four actions are seeded at zero, so a reader can always tell "no
    # unchanged events this run" from "this plan does not speak about unchanged
    # events". The counter is first-class, not a category that appears only when
    # it fires.
    summary = {
        ACTION_CREATE: 0,
        ACTION_UPDATE: 0,
        ACTION_UNCHANGED: 0,
        ACTION_SKIP: 0,
    }
    for row in plan:
        summary[row["action"]] = summary.get(row["action"], 0) + 1
    return summary


def _load(path: Path, key: str) -> list:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"FAIL: upsert_events: {path}: file not found", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"FAIL: upsert_events: {path}: invalid JSON ({exc})", file=sys.stderr)
        sys.exit(2)
    if isinstance(payload, dict):
        if key not in payload:
            # An absent key is a malformed input, not "zero candidates". The
            # default to `[]` here made `{}` or `{"rows": []}` indistinguishable
            # from a real quiet day, so a typo in the producer could green a run
            # that never had any candidates at all.
            print(
                f'FAIL: upsert_events: {path}: expected "{key}" in the object',
                file=sys.stderr,
            )
            sys.exit(2)
        payload = payload[key]
    if not isinstance(payload, list):
        print(
            f'FAIL: upsert_events: {path}: expected a list or {{"{key}": [...]}}',
            file=sys.stderr,
        )
        sys.exit(2)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Plan calendar creates/updates/unchanged/skips without duplicating "
            "events."
        ),
    )
    parser.add_argument("--existing", required=True, help="existing events JSON")
    parser.add_argument("--candidates", required=True, help="candidate events JSON")
    parser.add_argument(
        "--verdicts",
        help=("the audit ledger (JSONL); a WRONG verdict for an event is honoured "
              "from the record, not only from the calendar's title prefix"),
    )
    parser.add_argument(
        "--now",
        required=True,
        help="required current ISO-8601 datetime used to enforce the upcoming-game window",
    )
    parser.add_argument(
        "--fixtures",
        help=(
            "league fixture feed JSONL (scripts/fixtures.py output): candidate "
            "tip times are pinned to it before planning, so a drifted "
            "re-reading meets the event it names instead of duplicating it"
        ),
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=get_window_days(),
        help="allow starts after --now through --now + this many days (default: 10)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.window_days < 1:
        parser.error("--window-days must be at least 1")
    now = parse_dt(args.now)
    if now is None:
        parser.error(f"--now {args.now!r} is not a valid ISO-8601 datetime")

    existing = _load(Path(args.existing), "events")
    candidates = _load(Path(args.candidates), "candidates")
    fixtures = _load_fixtures(Path(args.fixtures)) if args.fixtures else []
    # Only when asked for: an absent ledger is not an error, it is a repository
    # whose audit has not run yet, and defaulting to "no verdicts" would be a
    # silent way to lose the one input that enforces the hard requirement.
    verdicts = load_verdicts(Path(args.verdicts)) if args.verdicts else {}
    plan = plan_upsert(
        existing,
        candidates,
        verdicts,
        now=now,
        window_days=args.window_days,
        fixtures=fixtures,
    )
    counts = summarise(plan)

    if not plan:
        # Zero candidates is a **quiet day**, not a broken planner. The runtime
        # contract requires the model to emit `{"candidates": []}` when it found
        # nothing, and the 2026-10-13 run did exactly that; failing here meant a
        # correct quiet day could never reach `calendar_io`, so the job reddened.
        # A malformed input is a different fault and `_load` rejects it with exit
        # 2 — this branch only sees a well-formed list that happens to be empty.
        if args.json:
            print(json.dumps({"plan": [], "summary": counts, "candidates": 0}, indent=2))
        else:
            print("OK: upsert_events: no candidates this run (0 planned)")
        return

    # `--json` prints the payload and nothing else, so stdout stays parseable
    # (the repo-wide convention; see scripts/README.md).
    if args.json:
        print(json.dumps({"plan": plan, "summary": counts}, indent=2))
    else:
        for row in plan:
            marker = "OK  " if row["action"] != ACTION_SKIP else "SKIP"
            print(
                f"{marker} {row['action']:6s} {row['game_key']} — {row['reason']}"
            )
        print(
            "OK: upsert_events: create={create} update={update} "
            "unchanged={unchanged} skip={skip}".format(**counts)
        )


if __name__ == "__main__":
    main()
