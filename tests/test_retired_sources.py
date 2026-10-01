"""Tests for `scripts/check_retired_sources.py` — the negative registry.

`config/sources.json` can say which hosts are approved. It cannot say which ones
**failed**, and that half is the one that bit this repository: the BBL's old host
stopped serving, the correction lived in a prose note, and `fixtures.py` kept
fetching it long afterwards — a TLS error whose real cause was a stale string.

So the failures are committed with their evidence, and this gate reads the three
places a source can be declared. Most of what is here is proving it can fail,
including one case where a reasonable-looking rule was **wrong** and the gate
caught the repository's own fix for the defect it was written to prevent.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_retired_sources import (
    _declared_domains_cell,
    _host_of,
    check,
    load_retired,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_retired_sources.py"
REGISTRY = REPO_ROOT / "config" / "retired-sources.json"
DEAD_HOST = "basketball-bundesliga.de"
LIVE_SUBDOMAIN = "api.basketball-bundesliga.de"


def _write_registry(root: Path, retired: list[dict]) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "retired-sources.json").write_text(
        json.dumps({"version": "1.0.0", "retired": retired}, indent=2), encoding="utf-8"
    )


def _entry(domain: str = DEAD_HOST, **overrides) -> dict:
    base = {
        "domain": domain,
        "retired_on": "2026-09-17",
        "reason": "TLS SNI failure: no request reaches a body.",
        "replaced_by": ["easycredit-bbl.de"],
    }
    return {**base, **overrides}


class TestTheRealRepositoryPasses:
    def test_the_gate_is_green(self):
        assert check(REPO_ROOT) == 0

    def test_the_cli_is_green(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(REPO_ROOT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "OK: check_retired_sources:" in result.stdout

    def test_the_dead_host_is_actually_retired(self):
        # Without this the gate could be green because nothing was ever listed.
        retired = load_retired(REPO_ROOT)
        assert DEAD_HOST in {entry["domain"] for entry in retired}

    def test_every_retirement_carries_its_evidence(self):
        for entry in load_retired(REPO_ROOT):
            assert entry["reason"].strip()
            assert entry["retired_on"].strip()

    def test_the_live_feed_host_is_not_retired(self):
        """The BBL's own API host, and the reason the rule is host-exact.

        `api.basketball-bundesliga.de` is a subdomain of the retired web host and
        is where the league serves the iCalendar feed that *fixed* the defect. A
        blanket subdomain rule fails here, and it fails on the fix.
        """
        assert LIVE_SUBDOMAIN not in {
            entry["domain"] for entry in load_retired(REPO_ROOT)
        }


class TestItCanFail:
    def test_the_dead_host_back_in_the_registry(self, tmp_path):
        _write_registry(tmp_path, [_entry()])
        (tmp_path / "config" / "sources.json").write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "sources": [
                        {
                            "name": "BBL",
                            "domains": [DEAD_HOST],
                            "tier": 1,
                            "type": "league",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        assert check(tmp_path) == 1

    def test_the_dead_host_back_as_a_social_account(self, tmp_path):
        # A social handle is a declaration too; the trap is a host that moved
        # into the social field because that field was not being read.
        _write_registry(tmp_path, [_entry()])
        (tmp_path / "config" / "sources.json").write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "sources": [
                        {
                            "name": "BBL",
                            "domains": ["easycredit-bbl.de"],
                            "social": [f"x.com/{DEAD_HOST}"],
                            "tier": 1,
                            "type": "league",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        assert check(tmp_path) == 1

    def test_the_dead_host_back_in_the_approved_doc_domains_column(
        self, tmp_path
    ):
        # The whole reason the doc check reads column 3 and not the file: this
        # row is what a re-introduction actually looks like.
        _write_registry(tmp_path, [_entry()])
        (tmp_path / "references").mkdir(parents=True, exist_ok=True)
        (tmp_path / "references" / "approved-sources.md").write_text(
            "| Source | Type | Domains | Notes |\n"
            "|--------|------|---------|-------|\n"
            f"| Basketball Bundesliga (BBL) | League | `{DEAD_HOST}` | The league site. |\n",
            encoding="utf-8",
        )
        assert check(tmp_path) == 1


class TestTheDocMayStillExplainARetirement:
    def test_a_prose_mention_is_evidence_not_a_declaration(self, tmp_path):
        # Forbidding this would force a retirement to go undocumented, and an
        # undocumented retirement is how the next person re-adds the host.
        _write_registry(tmp_path, [_entry()])
        (tmp_path / "references").mkdir(parents=True, exist_ok=True)
        (tmp_path / "references" / "approved-sources.md").write_text(
            "| Source | Type | Domains | Notes |\n"
            "|--------|------|---------|-------|\n"
            "| Basketball Bundesliga (BBL) | League | `easycredit-bbl.de` | "
            f"**Renamed** — `{DEAD_HOST}` no longer serves it (TLS SNI failure). |\n",
            encoding="utf-8",
        )
        assert check(tmp_path) == 0

    def test_the_domains_cell_is_column_three(self):
        assert _declared_domains_cell(
            "| BBL | League | `a.de`, `b.de` | notes |"
        ) == "`a.de`, `b.de`"
        # A two-column row has no Domains column to check.
        assert _declared_domains_cell("| just | two |") == ""
        assert _declared_domains_cell("not a table row") == ""


class TestTheGateItselfCannotBeFooled:
    def test_an_empty_registry_is_refused(self, tmp_path):
        # The vacuous-pass trap, one level down: a gate with nothing to check
        # passes on anything, which is what this whole script exists to stop.
        _write_registry(tmp_path, [])
        assert check(tmp_path) == 1

    def test_a_missing_registry_is_refused(self, tmp_path):
        assert check(tmp_path) == 1

    def test_a_retirement_without_a_reason_is_refused(self, tmp_path):
        # A retirement with no stated cause cannot be re-evaluated in a year,
        # so it is not a fact yet.
        _write_registry(tmp_path, [_entry(reason="   ")])
        assert check(tmp_path) == 1

    def test_a_retirement_pointing_at_another_dead_host_is_refused(self, tmp_path):
        _write_registry(tmp_path, [_entry(replaced_by=[DEAD_HOST + "/schedule"])])
        assert check(tmp_path) == 1

    def test_a_malformed_entry_is_refused_rather_than_crashing(self, tmp_path):
        _write_registry(tmp_path, ["basketball-bundesliga.de"])
        assert check(tmp_path) == 1


class TestSubdomainsAreExactUnlessAsked:
    def test_a_subdomain_is_not_covered_by_default(self):
        from scripts.check_retired_sources import _matches

        assert _matches(_entry(), DEAD_HOST)
        assert not _matches(_entry(), LIVE_SUBDOMAIN)

    def test_takes_subdomains_opts_into_the_stricter_rule(self):
        from scripts.check_retired_sources import _matches

        entry = _entry(takes_subdomains=True)
        assert _matches(entry, LIVE_SUBDOMAIN)

    def test_a_www_prefixed_dead_host_is_still_the_dead_host(self):
        from scripts.check_retired_sources import _matches

        assert _matches(_entry(), "www." + DEAD_HOST)


class TestHostNormalisation:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("https://www.easycredit-bbl.de/spielplan", "easycredit-bbl.de"),
            ("easycredit-bbl.de", "easycredit-bbl.de"),
            ("x.com/FIBA", "x.com"),
            ("WWW.EasyCredit-BBL.DE", "easycredit-bbl.de"),
            ("easycredit-bbl.de:443", "easycredit-bbl.de"),
            ("easycredit-bbl.de.", "easycredit-bbl.de"),
        ],
    )
    def test_both_shapes_reduce_to_a_host(self, value, expected):
        # The registry stores social accounts as host+path and the fetcher
        # stores full URLs, so one normaliser has to accept both or a retired
        # host is missed by whichever form it happens to be written in.
        assert _host_of(value) == expected
