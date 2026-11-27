"""Tests for scripts/approve_source.py: the runtime may widen the allow-list,
but only on evidence, and only by the narrow rules pinned here."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "approve_source.py"
sys.path.insert(0, str(ROOT / "scripts"))

import approve_source as mod  # noqa: E402


def _candidate(direct, **overrides):
    base = {
        "directLink": direct,
        "sourceReference": "https://league.example/live",
        "validationTimestamp": "2026-10-20T10:00:00Z",
        "evidence": {"free_confirmed": True, "live_confirmed": True},
        "teams": "A vs B",
    }
    base.update(overrides)
    return base


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "sources.json", tmp_path / "config" / "sources.json")
    shutil.copy(ROOT / "config" / "retired-sources.json", tmp_path / "config" / "retired-sources.json")
    return tmp_path


def _sources(root):
    return json.loads((root / "config" / "sources.json").read_text(encoding="utf-8"))


APPROVED_DOC = ROOT / "references" / "approved-sources.md"


def _documented_exclusions() -> list[tuple[str, set[str]]]:
    """`(Source cell, hosts named in that cell)` from the doc's excluded table.

    Only the first cell is read: the Reason column may name an approved host
    (`dazn.com` in the DAZN row) while explaining a policy, and treating that as
    a declaration would demand an exclusion that would break per-game approval.
    """
    text = APPROVED_DOC.read_text(encoding="utf-8")
    marker = "## Explicitly Excluded Sources"
    if marker not in text:
        return []
    section = text.split(marker, 1)[1].split("\n## ", 1)[0]
    rows: list[tuple[str, set[str]]] = []
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or cells[0].lower() == "source":
            continue
        if set(cells[0]) <= set("-: "):  # the markdown separator row
            continue
        rows.append((cells[0], set(re.findall(r"`([^`]+)`", cells[0]))))
    return rows


class TestTheDocumentedExclusionsAreEnforced:
    """A doc row is a policy; a registry `domains` entry is the enforced half."""

    def test_every_registry_exclusion_is_in_the_doc_table(self):
        cells = [cell for cell, _ in _documented_exclusions()]
        for entry in _sources(ROOT)["excluded"]:
            assert any(cell.startswith(entry["name"]) for cell in cells), (
                f"{entry['name']} is excluded in config/sources.json but absent "
                "from references/approved-sources.md's excluded table"
            )

    def test_the_hosts_the_doc_names_are_the_hosts_the_registry_refuses(self):
        rows = _documented_exclusions()
        for entry in _sources(ROOT)["excluded"]:
            _cell, hosts = next(
                (cell, named) for cell, named in rows if cell.startswith(entry["name"])
            )
            assert hosts == set(entry.get("domains") or []), (
                f"{entry['name']}: doc names {sorted(hosts)}, registry refuses "
                f"{sorted(entry.get('domains') or [])}"
            )

    def test_a_documented_host_is_not_left_unenforced(self):
        """The trap: a doc row naming a host that no `excluded` entry refuses."""
        names = [entry["name"] for entry in _sources(ROOT)["excluded"]]
        for cell, hosts in _documented_exclusions():
            if not hosts:
                continue
            assert any(cell.startswith(name) for name in names), (
                f"{cell} documents excluded host(s) {sorted(hosts)} that "
                "config/sources.json does not refuse"
            )

    def test_a_host_is_never_both_excluded_and_approved(self):
        data = _sources(ROOT)
        approved = {
            domain.lower()
            for source in data["sources"]
            for domain in (source.get("domain"), *(source.get("domains") or []))
            if isinstance(domain, str)
        }
        excluded = {
            domain.lower()
            for entry in data["excluded"]
            for domain in (entry.get("domain"), *(entry.get("domains") or []))
            if isinstance(domain, str)
        }
        assert excluded, "the excluded list lost every domain: nothing is enforced"
        assert not (excluded & approved)


def test_approves_verified_new_host(repo):
    added = mod.approve(repo, [_candidate("https://stream.league-example.de/g/1")], dry_run=False)
    assert added == ["stream.league-example.de"]
    entry = _sources(repo)["sources"][-1]
    assert entry["domains"] == ["stream.league-example.de"]
    assert entry["approved_by"] == "runtime"
    assert entry["tier"] == 7


def test_refuses_without_free_or_live_evidence(repo):
    bad = [
        _candidate("https://a.league-example.de/x", evidence={"free_confirmed": False, "live_confirmed": True}),
        _candidate("https://b.league-example.de/x", evidence={"free_confirmed": True, "live_confirmed": False}),
        _candidate("https://c.league-example.de/x", validationTimestamp=None),
        _candidate("http://d.league-example.de/x"),
    ]
    assert mod.approve(repo, bad, dry_run=False) == []


def test_refuses_excluded_evidence_only_and_retired(repo):
    cands = [
        _candidate("https://dyn.sport/x"),
        _candidate("https://fiba.com/x"),
        _candidate("https://sportbild.de/x"),
    ]
    assert mod.approve(repo, cands, dry_run=False) == []
    retired = json.loads((repo / "config" / "retired-sources.json").read_text(encoding="utf-8"))
    if retired["retired"]:
        dead = retired["retired"][0]["domain"]
        assert mod.approve(repo, [_candidate(f"https://{dead}/x")], dry_run=False) == []


def test_refuses_a_host_the_excluded_list_names(repo):
    """`excluded` is enforced through `domains`; the human `name` never matched."""
    before = (repo / "config" / "sources.json").read_bytes()
    added = mod.approve(
        repo,
        [
            _candidate("https://euroleague.tv/watch/1"),
            _candidate("https://secure.fibatv.com/live/1"),
            _candidate("https://www.euroleague.tv/watch/2"),
            _candidate("https://fubo.tv/sports/basketball"),
        ],
        dry_run=False,
    )
    assert added == []
    assert (repo / "config" / "sources.json").read_bytes() == before


def test_excluded_domains_are_read_from_the_registry(repo):
    hosts, _, excluded = mod._known(_sources(repo))
    assert {
        "euroleague.tv",
        "fibatv.com",
        "secure.fibatv.com",
        "fubo.tv",
        "flohoops.com",
        "tv.youtube.com",
    } <= excluded
    assert "sky sport" not in excluded  # a name, not a host: nothing to match
    assert "dazn.com" not in excluded  # approved per game, never platform-wide
    assert "sportplus.tv" not in excluded  # checked free, not paid: a real candidate


def test_youtube_is_approved_by_handle_never_by_host(repo):
    added = mod.approve(repo, [_candidate("https://www.youtube.com/@hoopsfree/live")], dry_run=False)
    assert added == ["@hoopsfree"]
    entry = _sources(repo)["sources"][-1]
    assert entry["handles"] == ["@hoopsfree"]
    assert "youtube.com" not in entry["domains"]


def test_youtube_without_handle_is_refused(repo):
    assert mod.approve(repo, [_candidate("https://www.youtube.com/watch?v=abc")], dry_run=False) == []
    assert mod.approve(repo, [_candidate("https://youtu.be/abc")], dry_run=False) == []


def test_idempotent(repo):
    cand = [_candidate("https://stream.league-example.de/g/1")]
    mod.approve(repo, cand, dry_run=False)
    before = (repo / "config" / "sources.json").read_bytes()
    assert mod.approve(repo, cand, dry_run=False) == []
    assert (repo / "config" / "sources.json").read_bytes() == before


def test_dry_run_writes_nothing(repo):
    before = (repo / "config" / "sources.json").read_bytes()
    added = mod.approve(repo, [_candidate("https://stream.league-example.de/g/1")], dry_run=True)
    assert added == ["stream.league-example.de"]
    assert (repo / "config" / "sources.json").read_bytes() == before


def test_insertion_preserves_existing_text(repo):
    original = (repo / "config" / "sources.json").read_bytes()
    mod.approve(repo, [_candidate("https://stream.league-example.de/g/1")], dry_run=False)
    updated = (repo / "config" / "sources.json").read_bytes()
    # Everything before the insertion point is untouched: no reformatting drift.
    cut = original.index(b'\n  ],\n  "excluded"')
    assert updated.startswith(original[:cut])
    assert updated.endswith(original[cut:])


def test_cli_rejects_unreadable_input(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path), "--candidates", str(tmp_path / "missing.json")],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
