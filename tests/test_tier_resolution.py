"""The tier gate must be able to fail — that is the property, not the pass.

`telemetry_db.py report` was written reading the run log's own `tier` field, and
every runtime row stores `tier: 0`. The report therefore could not propose
anything on the real ledger, while its unit tests passed because they supplied a
tier by hand. `scripts/check_tier_resolution.py` exists so that cannot recur, and
these tests are the mutation check: remove the registry from the build and the
gate has to red, add a tier to a fixture row and it has to red.

They call the script the same way `do-harness.toml` does (`--root`), against
temporary roots built from the committed fixture, so a change to the fixture
cannot silently make the gate unfalsifiable.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_tier_resolution.py"
FIXTURE = REPO / "tests" / "fixtures" / "tier_resolution" / "run-log.jsonl"
REGISTRY = REPO / "config" / "sources.json"

REQUIRED_SOURCES = {
    "magentasport.de",  # approved tier 4, 6/10 creates: a promotion
    "sporteurope.tv",  # approved tier 4, 10/10 blocked: a demotion
    "euroleaguebasketball.net",  # approved tier 1, the bare host
    "www.euroleaguebasketball.net",  # its alias, which must not demote it
    "unapproved.example",  # 12 creates, but not in the registry: never reordered
}


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
    )


def _fixture_rows() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _copy_root(root: Path, *, mutate=None, registry: bool = True) -> Path:
    target = root / "tests" / "fixtures" / "tier_resolution"
    target.mkdir(parents=True)
    rows = _fixture_rows()
    if mutate is not None:
        mutate(rows)
    (target / "run-log.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    if registry:
        (root / "config").mkdir()
        (root / "config" / "sources.json").write_text(
            REGISTRY.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return root


class TestGate:
    def test_the_committed_fixture_passes(self):
        result = _run(REPO)
        assert result.returncode == 0, result.stderr
        assert "OK: check_tier_resolution" in result.stdout
        assert "2 move(s) fired" in result.stdout

    def test_dropping_the_registry_reds_the_gate(self, tmp_path):
        """The defect in one line: build without `--sources` and nothing fires."""
        root = _copy_root(tmp_path)
        (root / "config" / "sources.json").write_text(
            json.dumps({"sources": []}), encoding="utf-8"
        )
        result = _run(root)
        assert result.returncode == 1
        assert "no source resolved its tier" in result.stderr

    def test_a_fixture_row_that_states_a_tier_is_rejected(self, tmp_path):
        """A hand-supplied tier is exactly how the vacuous report passed."""
        root = _copy_root(tmp_path, mutate=lambda rows: rows[0].update(tier=4))
        result = _run(root)
        assert result.returncode == 1
        assert "state a tier" in result.stderr

    def test_a_missing_registry_is_a_usage_error(self, tmp_path):
        root = _copy_root(tmp_path, registry=False)
        result = _run(root)
        assert result.returncode == 2
        assert "config/sources.json is missing" in result.stderr

    def test_an_unapproved_domain_is_not_reordered(self, tmp_path):
        """Give the unapproved source a registry entry and it must move, which
        proves the refusal above comes from approval, not from a dead rule."""
        root = _copy_root(tmp_path)
        payload = json.loads((root / "config" / "sources.json").read_text(encoding="utf-8"))
        payload["sources"].append({"tier": 2, "domains": ["unapproved.example"], "name": "x"})
        (root / "config" / "sources.json").write_text(json.dumps(payload), encoding="utf-8")
        result = _run(root)
        assert result.returncode == 1
        assert "unapproved.example" in result.stderr


class TestFixtureShape:
    def test_every_row_carries_the_tier_the_runtime_writes(self):
        rows = _fixture_rows()
        assert rows
        assert {row["tier"] for row in rows} == {0}, (
            "the fixture must reproduce the runtime shape (`tier: 0`); a supplied "
            "tier would let the report pass without the registry"
        )

    def test_the_fixture_still_covers_every_case_the_gate_asserts(self):
        sources = {row["source"] for row in _fixture_rows()}
        assert REQUIRED_SOURCES <= sources, REQUIRED_SOURCES - sources
