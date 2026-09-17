"""Tests for scripts/link_inventory.py — the producer `link_check.py --input` lacked.

The property that matters is the round trip: a link recorded on a candidate must
survive the planner, the description writer and the reader, and come out of the
inventory attached to the event it was published on. Every one of those hops used
to drop it, and each hop is covered by a test here rather than by a unit test with
a hand-built input, which is how the previous two instances of this defect hid.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.calendar_io import description_for
from scripts.link_inventory import build_inventory, load_events
from scripts.source_learning import load_sources
from scripts.upsert_events import _event_fields

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "link_inventory.py"
LINK_CHECK = REPO_ROOT / "scripts" / "link_check.py"
EVENTS_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "link_inventory_events.json"


def _run(script: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def _fixture_payload() -> dict:
    events = load_events(EVENTS_FIXTURE)
    payload, _ = build_inventory(events, load_sources(REPO_ROOT))
    return payload


def _entries(payload: dict, kind: str) -> list[dict]:
    return [entry for entry in payload["links"] if entry["kind"] == kind]


class TestRegistryHalf:
    """The half no check covered: 18 of 22 approved domains were unverified."""

    def test_every_approved_domain_is_inventoried(self):
        sources = load_sources(REPO_ROOT)
        domains = {
            str(domain).strip()
            for source in sources["sources"]
            for domain in [source.get("domain"), *(source.get("domains") or [])]
            if domain
        }
        inventoried = {entry["url"] for entry in _entries(_fixture_payload(), "approved-domain")}
        assert {f"https://{domain}/" for domain in domains} <= inventoried

    def test_the_youtube_handles_are_inventoried_as_channels(self):
        handles = {
            str(handle).strip()
            for source in load_sources(REPO_ROOT)["sources"]
            for handle in source.get("handles") or []
        }
        inventoried = {entry["url"] for entry in _entries(_fixture_payload(), "approved-channel")}
        assert {f"https://{handle}" for handle in handles} <= inventoried

    def test_social_accounts_are_tagged_as_validation_evidence(self):
        # Not a directLink by contract (SKILL.md Constraint 15), so a dead one is
        # acted on differently from a dead stream link — hence its own kind.
        entries = _entries(_fixture_payload(), "validation-account")
        assert entries
        assert {entry["url"] for entry in entries} >= {
            "https://x.com/BasketballCL",
            "https://facebook.com/MagentaSport",
        }

    def test_a_domain_claimed_twice_is_inventoried_once(self):
        # The registry gives BCL both `domain` and `domains`, the same value.
        payload, _ = build_inventory([], load_sources(REPO_ROOT))
        urls = [entry["url"] for entry in _entries(payload, "approved-domain")]
        assert urls.count("https://championsleague.basketball/") == 1


class TestCalendarHalf:
    def test_stored_links_come_out_attached_to_their_event(self):
        entries = _entries(_fixture_payload(), "calendar-event")
        assert {
            (entry["url"], entry["event_id"]) for entry in entries
        } == {
            (
                "https://www.championsleague.basketball/live/tenerife-vs-bonn",
                "evt-bcl-tenerife-bonn",
            ),
            ("https://www.youtube.com/@BasketballCL/live", "evt-bcl-tenerife-bonn"),
            ("https://www.youtube.com/channel/UCxyz123", "evt-unverified-bbl"),
        }

    def test_an_event_without_a_link_block_is_counted_not_silently_dropped(self):
        # "No links found" and "no event had a link to find" must not read the
        # same: the first is a healthy calendar, the second is a writer that
        # never ran.
        payload = _fixture_payload()
        assert payload["events_scanned"] == 3
        assert payload["events_without_links"] == 1

    def test_a_stored_source_label_is_kept(self):
        entries = _entries(_fixture_payload(), "calendar-event")
        labelled = [entry for entry in entries if entry["source"] == "championsleague.basketball"]
        assert len(labelled) == 1

    def test_a_bare_bullet_falls_back_to_the_host_for_its_label(self):
        # The label is for a human reading the report; the URL is the evidence.
        entries = _entries(_fixture_payload(), "calendar-event")
        bare = [entry for entry in entries if entry["url"].endswith("@BasketballCL/live")]
        assert bare[0]["source"] == "youtube.com"


class TestDeterminism:
    def test_two_runs_over_the_same_inputs_agree(self):
        events = load_events(EVENTS_FIXTURE)
        sources = load_sources(REPO_ROOT)
        first, _ = build_inventory(events, sources, now=None)
        second, _ = build_inventory(events, sources, now=None)
        first["generated_at"] = second["generated_at"] = "pinned"
        assert first == second

    def test_the_inventory_is_sorted_so_a_diff_shows_a_real_change(self):
        entries = _fixture_payload()["links"]
        keys = [(entry["kind"], entry["url"], entry["event_id"]) for entry in entries]
        assert keys == sorted(keys)

    def test_the_stamp_comes_from_now(self):
        from scripts.link_inventory import parse_moment

        payload, _ = build_inventory(
            [], load_sources(REPO_ROOT), now=parse_moment("2026-09-17T09:00:00Z")
        )
        assert payload["generated_at"] == "2026-09-17T09:00:00+00:00"


class TestTheRoundTrip:
    """Candidate -> plan row -> description -> reader -> inventory, end to end.

    This is the seam that was broken at four points at once, so it is asserted as
    one path rather than four units: a hand-built dictionary at any single hop
    would supply the field the real pipeline dropped.
    """

    def _inventory_of(self, candidate: dict) -> list[dict]:
        row = _event_fields(candidate, candidate["league"])
        text = description_for(row)
        # The shape a stored event has when it arrives back from the API, built
        # from the description the writer just produced.
        api_event = {
            "id": "evt-round-trip",
            "summary": candidate["summary"],
            "start": {"dateTime": candidate["start"]},
            "end": {"dateTime": candidate["start"]},
            "description": text,
        }
        payload, _ = build_inventory([api_event], {"sources": []})
        return payload["links"]

    def test_a_candidate_link_reaches_the_inventory(self):
        entries = self._inventory_of(
            {
                "league": "BCL",
                "summary": "BCL: Lenovo Tenerife vs Telekom Baskets Bonn",
                "teams": ["Lenovo Tenerife", "Telekom Baskets Bonn"],
                "start": "2026-09-17T20:00:00+02:00",
                "directLink": "https://www.championsleague.basketball/live/tenerife-vs-bonn",
                "sourceReference": "https://www.championsleague.basketball/",
            }
        )
        assert entries == [
            {
                "url": "https://www.championsleague.basketball/live/tenerife-vs-bonn",
                "source": "championsleague.basketball",
                "event_id": "evt-round-trip",
                "kind": "calendar-event",
                "label": "BCL: Lenovo Tenerife vs Telekom Baskets Bonn",
            }
        ]

    def test_several_links_on_one_candidate_all_survive(self):
        entries = self._inventory_of(
            {
                "league": "BBL",
                "summary": "BBL: A vs B",
                "teams": ["A", "B"],
                "start": "2026-09-17T20:00:00+02:00",
                "directLinks": [
                    {"source": "magenta.tv", "url": "https://www.magenta.tv/tv/live-1"},
                    {"source": "youtube", "url": "https://www.youtube.com/@basketballbundesliga/live"},
                ],
            }
        )
        assert [entry["url"] for entry in entries] == [
            "https://www.magenta.tv/tv/live-1",
            "https://www.youtube.com/@basketballbundesliga/live",
        ]

    def test_a_candidate_with_no_link_invents_no_entry(self):
        assert self._inventory_of(
            {
                "league": "BBL",
                "summary": "BBL: A vs B",
                "teams": ["A", "B"],
                "start": "2026-09-17T20:00:00+02:00",
            }
        ) == []


class TestCli:
    def test_json_puts_the_payload_alone_on_stdout(self, tmp_path):
        out = tmp_path / "links.json"
        result = _run(
            SCRIPT,
            [
                "--events", str(EVENTS_FIXTURE),
                "--root", str(REPO_ROOT),
                "--now", "2026-09-17T09:00:00Z",
                "--out", str(out),
                "--json",
            ],
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)  # a stray line here is a parse error
        assert payload["total"] == len(payload["links"])
        assert json.loads(out.read_text(encoding="utf-8")) == payload
        assert "wrote" in result.stderr

    def test_dry_run_writes_nothing(self, tmp_path):
        out = tmp_path / "links.json"
        result = _run(
            SCRIPT,
            ["--events", str(EVENTS_FIXTURE), "--root", str(REPO_ROOT), "--out", str(out), "--dry-run"],
        )
        assert result.returncode == 0
        assert not out.exists()

    def test_the_registry_alone_is_a_valid_inventory(self):
        # No calendar access at all: still a real inventory, which is the point of
        # covering the registry half.
        result = _run(SCRIPT, ["--root", str(REPO_ROOT), "--json"])
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["counts"]["approved-domain"] > 0
        assert payload["events_scanned"] == 0

    def test_a_missing_events_file_is_a_usage_error(self):
        result = _run(SCRIPT, ["--events", "nope.json", "--root", str(REPO_ROOT)])
        assert result.returncode == 2
        assert "file not found" in result.stderr

    def test_nothing_to_check_exits_one(self, tmp_path):
        empty = tmp_path / "root"
        empty.mkdir()
        result = _run(SCRIPT, ["--root", str(empty)])
        assert result.returncode == 1
        assert "nothing to check" in result.stderr

    def test_the_report_is_what_link_check_reads(self, tmp_path):
        # The pair, offline: the inventory is written, link_check consumes it, and
        # the deliberately-rejected YouTube shape in the fixture is classified
        # rather than passed through.
        links = tmp_path / "links.json"
        assert _run(
            SCRIPT,
            ["--events", str(EVENTS_FIXTURE), "--root", str(REPO_ROOT), "--out", str(links)],
        ).returncode == 0
        report = tmp_path / "report.json"
        result = _run(
            LINK_CHECK,
            [
                "--input", str(links),
                "--dry-run",
                "--checked-at", "2026-09-17T09:00:00Z",
                "--out", str(report),
            ],
        )
        # exit 1 because the fixture stores a `/channel/` URL, which the contract
        # rejects: the inventory must surface it, not swallow it.
        assert result.returncode == 1
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["summary"]["INVALID"] == 1
        assert payload["summary"]["OK"] == 40
