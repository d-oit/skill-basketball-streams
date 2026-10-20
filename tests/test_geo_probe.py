"""The geo probe: German-exit Tor config and the verdict rules.

The verdict is evidence, not a gate. These tests pin the rule that a Tor
failure can never be reported as a geo block, and that only a German exit can
prove reachability.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("geo_probe", REPO_ROOT / "scripts" / "geo_probe.py")
geo_probe = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(geo_probe)


class TestTorrc:
    def test_restricts_exits_to_germany(self):
        text = geo_probe.torrc("/tmp/x", 9050)
        assert "ExitNodes {DE}" in text.splitlines()

    def test_refuses_other_exits(self):
        # Without StrictNodes Tor falls back to any exit, which would silently
        # turn a German check into a check from elsewhere.
        assert "StrictNodes 1" in geo_probe.torrc("/tmp/x", 9050).splitlines()

    def test_binds_socks_locally_only(self):
        assert "SocksPort 127.0.0.1:9050" in geo_probe.torrc("/tmp/x", 9050).splitlines()


class TestLatestStreamUrl:
    def test_takes_newest_live_create(self, tmp_path):
        log = tmp_path / "calendar-log.jsonl"
        rows = [
            {"action": "create", "dry_run": False, "links": {"source_reference": "https://a.example/old"}},
            {"action": "skip", "dry_run": False, "links": {"source_reference": "https://b.example/x"}},
            {"action": "create", "dry_run": False, "links": {"source_reference": "https://a.example/new"}},
            {"action": "create", "dry_run": True, "links": {"source_reference": "https://c.example/dry"}},
        ]
        log.write_text("\n".join(__import__("json").dumps(r) for r in rows), encoding="utf-8")
        assert geo_probe.latest_stream_url(log) == "https://a.example/new"

    def test_missing_log_gives_none(self, tmp_path):
        assert geo_probe.latest_stream_url(tmp_path / "absent.jsonl") is None


class TestVerdict:
    def test_german_exit_and_2xx_is_reachable(self):
        assert geo_probe.verdict("DE", 200, None) == "reachable_from_de"

    def test_non_german_exit_is_never_reachable(self):
        # A 200 from another country proves nothing about Germany.
        assert geo_probe.verdict("NL", 200, None) == "no_german_exit"

    def test_missing_exit_country_is_not_reachable(self):
        assert geo_probe.verdict(None, 200, None) == "no_german_exit"

    @pytest.mark.parametrize("status", [401, 403, 451])
    def test_blocked_status_is_inconclusive_not_geo_blocked(self, status):
        # Tor exits are widely blocked by CDNs, so this cannot prove a geo block.
        assert geo_probe.verdict("DE", status, None) == "inconclusive"

    def test_transport_error_is_inconclusive(self):
        assert geo_probe.verdict("DE", None, "timeout") == "inconclusive"

    def test_other_status_is_unexpected(self):
        assert geo_probe.verdict("DE", 500, None) == "unexpected_status"
