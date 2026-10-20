"""Pin source_hints: calendar learning and non-free feedback, advisory only."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import source_hints  # noqa: E402

CAL_CREATE = {
    "action": "create",
    "state": "VERIFIED",
    "links": {
        "source_reference": "https://www.dyn.sport/deinsender/dyn-sport-mix",
        "direct_links": [{"url": "https://pluto.tv/gsa/live-tv/abc"}],
    },
}
CAL_SKIP = {"action": "skip", "state": "VERIFIED", "links": {}}
CAL_WRONG = {
    "action": "create",
    "state": "WRONG",
    "links": {"direct_links": [{"url": "https://evil.example/x"}]},
}
RUN_SKIP_PAID = {"outcome": "skip", "source": "tipico.de", "reason": "paid"}
RUN_SKIP_SAME_AS_GOOD = {"outcome": "skip", "source": "pluto.tv", "reason": "x"}
RUN_CREATE = {"outcome": "create", "source": "NBBL", "reason": "ok"}


def test_preferred_hosts_come_from_verified_create_rows_only():
    hosts = source_hints.preferred_hosts([CAL_CREATE, CAL_SKIP, CAL_WRONG])
    assert "pluto.tv" in hosts
    assert "dyn.sport" in hosts
    assert "evil.example" not in hosts


def test_skipped_non_free_host_is_deprioritized():
    assert source_hints.deprioritized_hosts([RUN_SKIP_PAID], []) == ["tipico.de"]


def test_host_that_delivered_a_verified_game_is_not_deprioritized():
    # One skipped game on a host that also delivered a verified one is not a
    # host-level verdict, so it must stay a preferred lead.
    preferred = source_hints.preferred_hosts([CAL_CREATE])
    assert source_hints.deprioritized_hosts([RUN_SKIP_SAME_AS_GOOD], preferred) == []


def test_create_outcomes_are_never_deprioritized():
    assert source_hints.deprioritized_hosts([RUN_CREATE], []) == []


def test_build_reads_both_logs_and_missing_inputs_yield_empty_lists(tmp_path):
    empty = source_hints.build(tmp_path)
    assert empty["preferred_hosts"] == [] and empty["deprioritized_hosts"] == []
    (tmp_path / "calendar-log.jsonl").write_text(json.dumps(CAL_CREATE) + "\n", encoding="utf-8")
    (tmp_path / "run-log.jsonl").write_text(json.dumps(RUN_SKIP_PAID) + "\n", encoding="utf-8")
    hints = source_hints.build(tmp_path)
    assert "pluto.tv" in hints["preferred_hosts"]
    assert hints["deprioritized_hosts"] == ["tipico.de"]


def test_malformed_lines_are_skipped_not_fatal(tmp_path):
    (tmp_path / "run-log.jsonl").write_text(
        "not json\n" + json.dumps(RUN_SKIP_PAID) + "\n", encoding="utf-8"
    )
    assert source_hints.build(tmp_path)["deprioritized_hosts"] == ["tipico.de"]


def test_cli_writes_the_hints_file(tmp_path):
    (tmp_path / "calendar-log.jsonl").write_text(json.dumps(CAL_CREATE) + "\n", encoding="utf-8")
    out = tmp_path / "out" / "source-hints.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "source_hints.py"),
         "--dest", str(tmp_path), "--out", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "pluto.tv" in json.loads(out.read_text(encoding="utf-8"))["preferred_hosts"]
