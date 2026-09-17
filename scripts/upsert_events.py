#!/usr/bin/env python3
"""upsert_events.py — Phase 2: check the calendar first, then write.

Implements the write policy from `live-stream-runtime-spec.md` §5.5 and §6. The
ordering is a safety property, not a detail: the runtime lists existing events
*today…today+7d* and reconciles in memory **before** any write, so it can never
duplicate an event it is about to create.

Reconciliation rules:

| Existing | New candidate | Action |
|---|---|---|
| nothing | any | **create** |
| `VERIFIED` | any | **skip** — never touch a confirmed event |
| `UNVERIFIED` | `VERIFIED` | **update** — promote (colour 5 → 6, drop the prefix) |
| `UNVERIFIED` | `UNVERIFIED` | **update** — refresh links and timestamp |
| `WRONG` | anything but `WRONG` | **skip** — an audit verdict outranks a fresh guess |
| `WRONG` | `WRONG` | **skip** — already labelled |

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
    python3 scripts/upsert_events.py --existing existing.json --candidates candidates.json
    python3 scripts/upsert_events.py --existing existing.json --candidates candidates.json --json
    python3 scripts/upsert_events.py --existing existing.json \\
        --candidates candidates.json --verdicts telemetry/audit.jsonl

Exit codes:
    0  PASS — a plan was produced
    1  FAIL — nothing to do
    2  USAGE — bad arguments or unreadable input
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # direct CLI execution
    # `latest_per_event` is shared with `audit_events.py` rather than copied: the
    # two must agree about which ledger row is current or this guarantee inverts.
    from audit_events import latest_per_event
    from evidence import coerce_evidence
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
    from scripts.evidence import coerce_evidence  # type: ignore
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
    """
    left = parse_dt(existing.get("start") or existing.get("startTime"))
    right = parse_dt(candidate.get("start") or candidate.get("startTime"))
    if left is None or right is None:
        return False
    if abs(left - right) > MATCH_WINDOW:
        return False

    existing_teams = _teams_of(existing)
    candidate_teams = _teams_of(candidate)
    if existing_teams and candidate_teams:
        # *Both* teams must match: two different games can share one team name on
        # a double-header day, so a single overlap is not enough.
        if not all(
            any(_team_matches(team, other) for other in candidate_teams)
            for team in existing_teams
        ):
            return False
        if not all(
            any(_team_matches(other, team) for other in existing_teams)
            for team in candidate_teams
        ):
            return False
    elif candidate_teams:
        # The stored entry records no teams of its own. Do not treat that as
        # "matches anything": require it to name the candidate's clubs. A
        # candidate with no teams is left alone here — it has nothing to be
        # checked against, and `extract_candidates.py` refuses such a candidate
        # before it can reach the planner.
        if not text_names_all(_event_text(existing), candidate_teams):
            return False

    league = str(candidate.get("league") or "").lower()
    existing_league = str(existing.get("league") or "").lower()
    if league and existing_league and league not in existing_league and (
        existing_league not in league
    ):
        return False
    return True


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


def plan_upsert(
    existing_events: list[dict],
    candidates: list[dict],
    verdicts: dict | None = None,
) -> list[dict]:
    """Reconcile candidates against existing events. Returns one plan row each.

    `verdicts` maps `event_id` to the newest audit row for that event. It is the
    only way the `WRONG` rows in the table above are reachable on the real path:
    the calendar's own state comes from the title prefix, which `audit_events`
    computes but nothing applies.
    """
    verdicts = verdicts or {}
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
        title = title_for(new_state, str(candidate.get("summary") or ""))
        color = color_for(new_state, league_color)

        match = None
        for event in existing_events:
            if isinstance(event, dict) and events_match(event, candidate):
                match = event
                break

        if match is None:
            plan.append(
                {
                    "action": ACTION_CREATE,
                    "reason": "no existing event for this slot",
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
                    "reason": "verified event exists — never modified",
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
        plan.append(
            {
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
        )
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
    return fields


def summarise(plan: list[dict]) -> dict:
    summary = {ACTION_CREATE: 0, ACTION_UPDATE: 0, ACTION_SKIP: 0}
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
        payload = payload.get(key, [])
    if not isinstance(payload, list):
        print(
            f'FAIL: upsert_events: {path}: expected a list or {{"{key}": [...]}}',
            file=sys.stderr,
        )
        sys.exit(2)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plan calendar creates/updates/skips without duplicating events.",
    )
    parser.add_argument("--existing", required=True, help="existing events JSON")
    parser.add_argument("--candidates", required=True, help="candidate events JSON")
    parser.add_argument(
        "--verdicts",
        help=("the audit ledger (JSONL); a WRONG verdict for an event is honoured "
              "from the record, not only from the calendar's title prefix"),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    existing = _load(Path(args.existing), "events")
    candidates = _load(Path(args.candidates), "candidates")
    # Only when asked for: an absent ledger is not an error, it is a repository
    # whose audit has not run yet, and defaulting to "no verdicts" would be a
    # silent way to lose the one input that enforces the hard requirement.
    verdicts = load_verdicts(Path(args.verdicts)) if args.verdicts else {}
    plan = plan_upsert(existing, candidates, verdicts)
    counts = summarise(plan)

    if not plan:
        print("FAIL: upsert_events: no candidates to plan", file=sys.stderr)
        sys.exit(1)

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
            "OK: upsert_events: create={create} update={update} skip={skip}".format(
                **counts
            )
        )


if __name__ == "__main__":
    main()
