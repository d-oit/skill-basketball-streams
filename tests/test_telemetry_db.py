"""The derived read model: reward per row, tier moves only on enough evidence.

Three properties matter more than the SQL:

1. **Derived, never authoritative.** Rebuilding from a changed ledger must drop
   rows the ledger no longer carries; a stale database is worse than none.
2. **`reward` is a judgement, not a rewrite.** It lives in the read model only,
   so the append-only run log keeps its exact shape and `record_run_outcomes`
   still compares bytes.
3. **The floor is enforced.** Under `--min-attempts` (default 10) a source is
   counted but never reordered: a 2-attempt hit rate is an anecdote.
4. **The tier is resolved, not trusted.** The runtime writes `tier: 0` on every
   row, so a fixture that supplies a real tier proves nothing about production.
   `TestRegistryTier` drives rows in the shape the pipeline actually emits and
   requires the approved registry to supply the tier.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "telemetry_db.py"

sys.path.insert(0, str(REPO / "scripts"))

from telemetry_db import (  # noqa: E402
    DEFAULT_MIN_ATTEMPTS,
    build,
    load_tiers,
    normalise_source,
    propose_tier_change,
    report,
    resolve_tier,
    reward_for,
)


def _rows(source: str, tier: int, counts: dict[str, int]) -> list[dict]:
    rows: list[dict] = []
    index = 0
    for outcome, count in counts.items():
        for _ in range(count):
            index += 1
            rows.append(
                {
                    "ts": f"2026-11-{index:02d}T08:30:00+00:00",
                    "run_id": "2026-11-01T08:30Z",
                    "source": source,
                    "tier": tier,
                    "url": f"https://{source}/live-{index}",
                    "outcome": outcome,
                    "reason": "fixture",
                }
            )
    return rows


def _ledger(tmp_path: Path) -> Path:
    directory = tmp_path / "ledgers"
    directory.mkdir()
    rows = (
        # 6/10 reach the calendar: a promotion the sample supports.
        _rows("ndr.de", 3, {"create": 6, "skip": 4})
        # Nothing created, everything unreachable: a dead source, not a quiet one.
        + _rows("dead.example", 4, {"blocked": 8, "error": 2})
        # 9 attempts: one short of the floor, so not a claim either way.
        + _rows("quiet.example", 2, {"skip": 9})
        # Already the best tier: nothing to promote.
        + _rows("league.example", 1, {"create": 12})
        # Quiet but reachable: no demotion.
        + _rows("reject.example", 5, {"reject": 6, "skip": 6})
    )
    (directory / "run-log.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return directory


class TestReward:
    def test_each_outcome_has_one_reward(self):
        assert reward_for({"outcome": "create"}) == 1.0
        assert reward_for({"outcome": "skip"}) == 0.0
        assert reward_for({"outcome": "reject"}) == -0.5
        assert reward_for({"outcome": "blocked"}) == -1.0
        assert reward_for({"outcome": "error"}) == -1.0

    def test_unknown_outcome_scores_zero_rather_than_guessing(self):
        assert reward_for({"outcome": "teleported"}) == 0.0
        assert reward_for({}) == 0.0


class TestBuild:
    def test_rows_and_rewards_land_in_the_table(self, tmp_path):
        directory = _ledger(tmp_path)
        db = tmp_path / "telemetry.db"
        assert build(directory, db) == 53
        with sqlite3.connect(db) as conn:
            total = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
            assert total == 53
            rewards = dict(
                conn.execute(
                    "SELECT outcome, AVG(reward) FROM outcomes GROUP BY outcome"
                ).fetchall()
            )
            assert rewards == {
                "create": 1.0,
                "skip": 0.0,
                "reject": -0.5,
                "blocked": -1.0,
                "error": -1.0,
            }

    def test_rebuild_drops_rows_the_ledger_no_longer_carries(self, tmp_path):
        directory = _ledger(tmp_path)
        db = tmp_path / "telemetry.db"
        build(directory, db)
        (directory / "run-log.jsonl").write_text(
            "\n".join(json.dumps(row) for row in _rows("ndr.de", 3, {"create": 1}))
            + "\n",
            encoding="utf-8",
        )
        assert build(directory, db) == 1
        sources = {stats["source"] for stats in report(db)["sources"]}
        assert sources == {"ndr.de"}

    def test_an_empty_ledger_is_a_failure_not_an_empty_database(self, tmp_path):
        directory = tmp_path / "ledgers"
        directory.mkdir()
        (directory / "run-log.jsonl").write_text("", encoding="utf-8")
        db = tmp_path / "telemetry.db"
        assert build(directory, db) == 0
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "build", "--ledgers", str(directory),
             "--db", str(db)],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "no run-log rows" in result.stderr


class TestTierEvidence:
    def test_promotion_and_demotion_need_the_floor(self, tmp_path):
        payload = report(_built(tmp_path))
        changes = {change["source"]: change for change in payload["tier_changes"]}
        assert changes["ndr.de"]["direction"] == "promote"
        assert (changes["ndr.de"]["from"], changes["ndr.de"]["to"]) == (3, 2)
        assert changes["dead.example"]["direction"] == "demote"
        assert (changes["dead.example"]["from"], changes["dead.example"]["to"]) == (4, 5)
        # 9 attempts: counted, never reordered.
        assert "quiet.example" not in changes
        sources = {stats["source"]: stats for stats in payload["sources"]}
        assert sources["quiet.example"]["attempts"] == 9
        # Already tier 1, and a reachable-but-unproductive source, are not moved.
        assert "league.example" not in changes
        assert "reject.example" not in changes

    def test_the_exact_boundary_is_ten_attempts(self):
        nine = {"source": "x", "tier": 3, "attempts": 9, "creates": 9, "mean_reward": 1.0}
        ten = {"source": "x", "tier": 3, "attempts": 10, "creates": 10, "mean_reward": 1.0}
        assert propose_tier_change(nine) is None
        assert propose_tier_change(ten)["to"] == 2
        assert DEFAULT_MIN_ATTEMPTS == 10

    def test_a_quiet_source_is_not_demoted(self):
        # 10 attempts, none created, but all reachable: no verdict on the game,
        # which is not the same as a source that stopped answering.
        stats = {"source": "x", "tier": 4, "attempts": 10, "creates": 0, "mean_reward": 0.0}
        assert propose_tier_change(stats) is None


def _write_ledger(directory: Path, rows: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run-log.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return directory


class TestRegistryTier:
    """The rows production writes say `tier: 0` — for every one of 142k rows.

    `record_run_outcomes.derive_specs` hardcodes that value. A report that read
    the field at face value would pass a fixture that supplied a tier and never
    propose anything against the real ledger, which is the "a rule that reads a
    field nothing writes" trap. These cases drive the emitted shape.
    """

    @staticmethod
    def _registry(
        tmp_path: Path, tiers: dict[str, int], excluded: tuple[str, ...] = ()
    ) -> Path:
        path = tmp_path / "sources.json"
        path.write_text(
            json.dumps(
                {
                    "sources": [
                        {"tier": tier, "domains": [domain], "name": domain}
                        for domain, tier in tiers.items()
                    ],
                    "excluded": [
                        {"name": domain, "domains": [domain]} for domain in excluded
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_a_logged_zero_is_resolved_from_the_registry(self, tmp_path):
        directory = _write_ledger(tmp_path / "ledgers", _rows("ndr.de", 0, {"create": 6, "skip": 4}))
        db = tmp_path / "telemetry.db"
        assert build(directory, db, self._registry(tmp_path, {"ndr.de": 3})) == 10
        payload = report(db)
        stats = {entry["source"]: entry for entry in payload["sources"]}["ndr.de"]
        assert (stats["tier"], stats["tier_source"]) == (3, "registry")
        change = payload["tier_changes"][0]
        assert (change["direction"], change["from"], change["to"]) == ("promote", 3, 2)
        assert change["tier_source"] == "registry"

    def test_a_logged_tier_wins_over_the_registry(self, tmp_path):
        directory = _write_ledger(tmp_path / "ledgers", _rows("ndr.de", 4, {"create": 1}))
        db = tmp_path / "telemetry.db"
        build(directory, db, self._registry(tmp_path, {"ndr.de": 2}))
        stats = report(db)["sources"][0]
        assert (stats["tier"], stats["tier_source"]) == (4, "log")

    def test_a_subdomain_resolves_from_its_approved_parent(self, tmp_path):
        """Discovery treats `live.x` as approved; the report must agree."""
        directory = _write_ledger(
            tmp_path / "ledgers", _rows("about.fiba.basketball", 0, {"skip": 20})
        )
        db = tmp_path / "telemetry.db"
        build(directory, db, self._registry(tmp_path, {"fiba.basketball": 1}))
        payload = report(db)
        stats = payload["sources"][0]
        assert (stats["tier"], stats["tier_source"]) == (1, "registry")
        assert payload["unapproved_sources"] == []

    def test_an_excluded_host_never_inherits_its_approved_parent(self, tmp_path):
        """`tv.youtube.com` is paid; `youtube.com` being tier 6 must not win."""
        rows = _rows("tv.youtube.com", 0, {"skip": 20}) + _rows(
            "youtube.com", 0, {"skip": 5}
        )
        directory = _write_ledger(tmp_path / "ledgers", rows)
        db = tmp_path / "telemetry.db"
        build(
            directory,
            db,
            self._registry(tmp_path, {"youtube.com": 6}, excluded=("tv.youtube.com",)),
        )
        payload = report(db)
        by_source = {entry["source"]: entry for entry in payload["sources"]}
        assert (
            by_source["tv.youtube.com"]["tier"],
            by_source["tv.youtube.com"]["tier_source"],
        ) == (None, "excluded")
        assert by_source["youtube.com"]["tier"] == 6
        assert payload["excluded_sources"] == ["tv.youtube.com"]
        assert payload["unapproved_sources"] == []

    def test_a_subdomain_of_an_excluded_host_is_excluded(self, tmp_path):
        directory = _write_ledger(
            tmp_path / "ledgers", _rows("secure.fibatv.com", 0, {"skip": 3})
        )
        db = tmp_path / "telemetry.db"
        build(
            directory,
            db,
            self._registry(tmp_path, {"fiba.basketball": 1}, excluded=("fibatv.com",)),
        )
        stats = report(db)["sources"][0]
        assert (stats["tier"], stats["tier_source"]) == (None, "excluded")

    def test_an_unapproved_domain_is_reported_but_never_reordered(self, tmp_path):
        # Twelve creates would be a promotion if the source were approved. It is
        # not, so the report names it as unapproved instead of inventing tier 0.
        directory = _write_ledger(tmp_path / "ledgers", _rows("nowhere.example", 0, {"create": 12}))
        db = tmp_path / "telemetry.db"
        build(directory, db, self._registry(tmp_path, {"ndr.de": 3}))
        payload = report(db)
        stats = payload["sources"][0]
        assert (stats["tier"], stats["tier_source"]) == (None, "unapproved")
        assert payload["tier_changes"] == []
        assert payload["unapproved_sources"] == ["nowhere.example"]
        assert propose_tier_change(stats) is None

    def test_a_plumbing_label_is_not_an_unapproved_source(self, tmp_path):
        """`tinyfish` is a rung, `urllib` a library, `NBBL` a league label."""
        rows = (
            _rows("ndr.de", 0, {"blocked": 3})
            + _rows("tinyfish", 0, {"error": 5})
            + _rows("urllib", 0, {"error": 2})
            + _rows("NBBL", 0, {"create": 2})
        )
        directory = _write_ledger(tmp_path / "ledgers", rows)
        db = tmp_path / "telemetry.db"
        build(directory, db, self._registry(tmp_path, {"ndr.de": 3}))
        payload = report(db)
        assert payload["unapproved_sources"] == []
        # Sources are normalised on build, so a label is reported lowercased.
        assert payload["plumbing_sources"] == ["nbbl", "tinyfish", "urllib"]
        # Reported, just not as a source to approve: the rows are still evidence.
        assert {entry["source"] for entry in payload["sources"]} == {
            "ndr.de", "nbbl", "tinyfish", "urllib"
        }

    def test_an_unapproved_host_and_a_handle_are_still_reported(self, tmp_path):
        """The classification must not hide the candidates a review exists for."""
        rows = _rows("nowhere.example", 0, {"skip": 2}) + _rows(
            "youtube.com/@newchannel", 0, {"skip": 2}
        )
        directory = _write_ledger(tmp_path / "ledgers", rows)
        db = tmp_path / "telemetry.db"
        build(directory, db, self._registry(tmp_path, {"ndr.de": 3}))
        assert report(db)["unapproved_sources"] == [
            "nowhere.example",
            "youtube.com/@newchannel",
        ]

    def test_a_www_alias_is_one_source_not_a_dead_one(self, tmp_path):
        # The real ledger holds 1,479 rows for the bare host and 405 blocked
        # rows for `www.`. Scored separately, the alias alone is 10/10 blocked
        # — a demotion under the rule — while the merged site is 10/50, which
        # is a site that answered most of the time and must not be demoted.
        rows = _rows("euroleaguebasketball.net", 0, {"skip": 40}) + _rows(
            "www.euroleaguebasketball.net", 0, {"blocked": 10}
        )
        directory = _write_ledger(tmp_path / "ledgers", rows)
        db = tmp_path / "telemetry.db"
        build(directory, db, self._registry(tmp_path, {"euroleaguebasketball.net": 1}))
        payload = report(db)
        assert [entry["source"] for entry in payload["sources"]] == [
            "euroleaguebasketball.net"
        ]
        assert payload["sources"][0]["attempts"] == 50
        assert payload["sources"][0]["blocked"] == 10
        assert payload["tier_changes"] == []

    def test_the_committed_registry_resolves_both_entry_shapes(self):
        tiers = load_tiers(REPO / "config" / "sources.json")
        assert tiers["fiba.basketball"] == 1  # `domains`
        assert tiers["championsleague.basketball"] == 1  # singular `domain`
        assert tiers and all(1 <= tier <= 7 for tier in tiers.values())

    def test_resolve_tier_states_where_the_number_came_from(self):
        assert resolve_tier({"source": "a.example", "tier": 0}, {}) == (None, None)
        assert resolve_tier({"source": "a.example", "tier": 0}, {"a.example": 5}) == (5, "registry")
        assert resolve_tier({"source": "a.example", "tier": 2}, {"a.example": 5}) == (2, "log")
        assert resolve_tier({"source": "WWW.A.example", "tier": 0}, {"a.example": 5}) == (5, "registry")
        assert normalise_source("WWW.A.example") == "a.example"

    def test_an_unparseable_registry_is_a_usage_error(self, tmp_path):
        directory = _ledger(tmp_path)
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "build", "--ledgers", str(directory),
             "--db", str(tmp_path / "t.db"), "--sources", str(broken)],
            capture_output=True, text=True,
        )
        assert result.returncode == 2
        assert "broken.json" in result.stderr


class TestCli:
    def test_round_trip_keeps_stdout_a_payload(self, tmp_path):
        directory = _ledger(tmp_path)
        db = tmp_path / "telemetry.db"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "build", "--ledgers", str(directory),
             "--db", str(db)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "report", "--db", str(db),
             "--min-attempts", "10", "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)  # stdout is the payload alone
        assert payload["min_attempts"] == 10
        assert payload["tier_changes"]
        assert "tier change(s)" in result.stderr

    def test_build_accepts_the_registry_and_report_shows_its_tiers(self, tmp_path):
        directory = _write_ledger(tmp_path / "ledgers", _rows("ndr.de", 0, {"create": 6, "skip": 4}))
        registry = TestRegistryTier._registry(tmp_path, {"ndr.de": 3})
        db = tmp_path / "telemetry.db"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "build", "--ledgers", str(directory),
             "--db", str(db), "--sources", str(registry)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "report", "--db", str(db), "--json"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["tier_changes"][0]["source"] == "ndr.de"
        assert "0 unapproved source(s)" in result.stderr

    def test_report_without_a_database_names_build(self, tmp_path):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "report", "--db", str(tmp_path / "none.db")],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "run `build` first" in result.stderr

    def test_report_on_an_empty_table_is_a_failure(self, tmp_path):
        db = _built(tmp_path)
        with sqlite3.connect(db) as conn:
            conn.execute("DELETE FROM outcomes")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "report", "--db", str(db)],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "nothing to report" in result.stderr


class TestNoWrite:
    def test_the_registry_is_never_touched(self, tmp_path):
        sources = REPO / "config" / "sources.json"
        before = sources.read_text(encoding="utf-8")
        _built(tmp_path)
        assert sources.read_text(encoding="utf-8") == before
        assert not (tmp_path / "sources.json").exists()


def _built(tmp_path: Path) -> Path:
    db = tmp_path / "telemetry.db"
    build(_ledger(tmp_path), db)
    return db
