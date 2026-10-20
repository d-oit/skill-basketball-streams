"""Tests for scripts/approve_source.py: the runtime may widen the allow-list,
but only on evidence, and only by the narrow rules pinned here."""

import json
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
