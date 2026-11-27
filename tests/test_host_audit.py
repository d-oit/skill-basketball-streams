"""Tests for `scripts/host_audit.py` — the offline host snapshot.

The gate exists because eight Tier 5 rows in `references/approved-sources.md`
named hosts that could never have served a stream (six did not resolve, one was
a supporters' club, one failed the handshake), and nothing compared the approved
list to the network. A snapshot alone would repeat the defect — it would be a
document nothing reads — so these tests drive the deciding half: an approved
host that was never measured, and one measured dead, must both fail.

The success case is the committed snapshot itself, so a hand-edited row that
turns a dead host into a live-looking one fails here rather than in production.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "host_audit.py"
SNAPSHOT = REPO_ROOT / "config" / "host-audit.json"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

from host_audit import approved_hosts, host_of, load_snapshot  # noqa: E402


def _root(tmp_path: Path, approved: list[str], hosts: dict[str, dict]) -> Path:
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "sources.json").write_text(
        json.dumps(
            {"sources": [{"name": host, "domain": host, "tier": 5} for host in approved]}
        ),
        encoding="utf-8",
    )
    (tmp_path / "config" / "host-audit.json").write_text(
        json.dumps({"measured_on": "2026-11-27", "hosts": hosts}), encoding="utf-8"
    )
    return tmp_path


def _check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "check", "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


LIVE = {"dns": ["192.0.2.1"], "tls_ok": True, "status": 200, "final": "https://example.com/"}


class TestCommittedSnapshot:
    def test_the_repository_snapshot_passes(self):
        result = _check(REPO_ROOT)
        assert result.returncode == 0, result.stderr
        assert "0 approved host without a DNS answer" in result.stdout

    def test_every_approved_host_is_recorded(self):
        snapshot = load_snapshot(REPO_ROOT)
        recorded = {host_of(host) for host in snapshot["hosts"]}
        missing = sorted(set(approved_hosts(REPO_ROOT)) - recorded)
        assert missing == [], f"approved hosts with no measurement: {missing}"

    def test_no_recorded_host_is_dead(self):
        snapshot = load_snapshot(REPO_ROOT)
        dead = sorted(
            host for host, row in snapshot["hosts"].items() if not row.get("dns")
        )
        assert dead == [], (
            f"recorded hosts that do not resolve: {dead} — correct the domain and "
            "retire it, rather than leaving the row"
        )

    def test_the_snapshot_is_dated(self):
        assert load_snapshot(REPO_ROOT)["measured_on"]


class TestTheGateCanFail:
    def test_an_unmeasured_approved_host_fails(self, tmp_path):
        # The snapshot is non-empty on purpose: an empty one is refused earlier
        # for being vacuous, and this test is about the other failure.
        root = _root(tmp_path, ["measured.example"], {"other.example": LIVE})
        result = _check(root)
        assert result.returncode == 1
        assert "no recorded measurement" in result.stderr
        assert "measured.example" in result.stderr

    def test_an_approved_host_that_does_not_resolve_fails(self, tmp_path):
        root = _root(
            tmp_path,
            ["dead.example"],
            {"dead.example": {"dns": [], "tls_ok": False, "status": None}},
        )
        result = _check(root)
        assert result.returncode == 1
        assert "do not resolve" in result.stderr

    def test_an_empty_snapshot_fails_instead_of_passing_vacuously(self, tmp_path):
        root = _root(tmp_path, ["measured.example"], {})
        (root / "config" / "host-audit.json").write_text(
            json.dumps({"measured_on": "2026-11-27", "hosts": {}}), encoding="utf-8"
        )
        result = _check(root)
        assert result.returncode == 1
        assert "records no hosts" in result.stderr

    def test_a_snapshot_without_a_date_fails(self, tmp_path):
        root = _root(tmp_path, ["measured.example"], {"measured.example": LIVE})
        (root / "config" / "host-audit.json").write_text(
            json.dumps({"hosts": {"measured.example": LIVE}}), encoding="utf-8"
        )
        result = _check(root)
        assert result.returncode == 1
        assert "measured_on" in result.stderr

    def test_a_registry_with_no_host_fails(self, tmp_path):
        root = _root(tmp_path, [], {})
        result = _check(root)
        assert result.returncode == 1
        assert "assert nothing" in result.stderr


class TestShapingIsNotDeath:
    def test_a_host_that_resolves_but_answers_nothing_passes(self, tmp_path):
        # A timeout is a server deciding not to answer this client; the host
        # exists, and a broadcast can still be watched in a browser.
        root = _root(
            tmp_path,
            ["slow.example"],
            {
                "slow.example": {
                    "dns": ["192.0.2.7"],
                    "tls_ok": False,
                    "status": None,
                    "final": None,
                    "error": "TimeoutError: timed out",
                }
            },
        )
        result = _check(root)
        assert result.returncode == 0, result.stderr
        assert "1 answered none" in result.stdout

    def test_a_server_that_answers_403_counts_as_answering(self, tmp_path):
        # Bot protection is a response: the host is alive, so the summary must
        # not present it as silence.
        root = _root(
            tmp_path,
            ["blocked.example"],
            {
                "blocked.example": {
                    "dns": ["192.0.2.7"],
                    "tls_ok": True,
                    "status": 403,
                    "final": "https://blocked.example/",
                    "error": "HTTP 403",
                }
            },
        )
        result = _check(root)
        assert result.returncode == 0, result.stderr
        assert "1 answered a request, 0 answered none" in result.stdout

    def test_the_www_alias_is_the_same_host(self):
        assert host_of("https://www.albaberlin.de/") == "albaberlin.de"
        assert host_of("x.com/FIBA") == "x.com"
        assert host_of("WWW.Example.COM.") == "example.com"
