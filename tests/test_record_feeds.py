"""Tests for `scripts/record_feeds.py` — the writer behind the feed corpus.

The corpus in `tests/fixtures/feeds/` is only worth anything if it is a
**captured response** rather than a plausible-looking one, and a captured
response is only checkable if something re-derives it. That something is
`--check`, and the tests here are mostly about proving `--check` can fail.

Two of these guards exist because the guard itself was wrong first:

* the payload was written with `BEGIN:VEVENT`/`END:VEVENT` stripped, and
  `--check` passed — the bytes were the recorded bytes, of a calendar no reader
  could parse. A hash proves *unchanged*, never *readable*.
* the selection accepted any event, so a kept event the reader refuses could sit
  in the corpus asserting a format nothing can read.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.record_feeds import (
    COMPETITION_PREFIXES,
    build,
    check,
    fetch,
    select,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "tests" / "fixtures" / "feeds"
SCRIPT = REPO_ROOT / "scripts" / "record_feeds.py"

A_FED_FEED = "\r\n".join(
    [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "X-WR-TIMEZONE:Europe/Berlin",
        "BEGIN:VEVENT",
        "UID:a@x",
        "DTSTART:20260910T183000",
        "SUMMARY:easyCredit BBL Spiel ALBA BERLIN vs Telekom Baskets Bonn",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "UID:b@x",
        "DTSTART:20260911T183000",
        "SUMMARY:BBL Pokal Spiel Eisbären Bremerh",
        " aven vs NINERS Chemnitz",
        "END:VEVENT",
        "BEGIN:VEVENT",
        "UID:c@x",
        "DTSTART:20260912T183000",
        "SUMMARY:easyCredit BBL Spiel ALBA BERLIN vs NINERS Chemnitz",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )


class TestTheCheckPassesOnTheStoredCorpus:
    def test_check_is_green(self):
        assert check(CORPUS) == 0

    def test_it_makes_no_network_request(self, monkeypatch):
        # The gate runs in CI and on the sensor; a network call here would make a
        # free provider's bad day a red build, which is the rule the page corpus
        # is explicit about.
        def explode(*_a, **_k):
            raise AssertionError("--check must not touch the network")

        monkeypatch.setattr("scripts.record_feeds.fetch", explode)
        assert check(CORPUS) == 0

    def test_the_cli_gate_is_green(self):
        result = _run("--check")
        assert result.returncode == 0, result.stderr
        assert "OK: record_feeds:" in result.stdout

    def test_the_corpus_is_strictly_smaller_than_the_response(self):
        # A "trim" that kept everything is not a trim; and the manifest must
        # still record the whole response's hash, so a re-record that quietly
        # changed the event count is visible rather than absorbed.
        manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
        assert 0 < len(manifest["kept"]) < manifest["events_in_response"] == 321
        assert manifest["response_bytes"] > manifest["stored_bytes"]
        assert manifest["http_status"] == 200


class TestTheCheckCanFail:
    """Each mutation below is a way the corpus could lie, made executable."""

    def test_an_edited_fixture_is_refused(self, tmp_path):
        for name in ("bbl_all_games.ics", "manifest.json"):
            (tmp_path / name).write_bytes((CORPUS / name).read_bytes())
        fixture = tmp_path / "bbl_all_games.ics"
        fixture.write_bytes(
            fixture.read_bytes().replace(b"ALBA BERLIN", b"ALBA BERLIN GmbH")
        )
        assert check(tmp_path) == 1

    def test_a_payload_no_reader_can_parse_is_refused(self, tmp_path):
        """The guard that was missing when this defect was written.

        Stripping the event envelope keeps the file a byte-different copy of a
        calendar, and the hash check alone cannot see it — only the reader can.
        """
        for name in ("bbl_all_games.ics", "manifest.json"):
            (tmp_path / name).write_bytes((CORPUS / name).read_bytes())
        fixture = tmp_path / "bbl_all_games.ics"
        lines = fixture.read_bytes().split(b"\r\n")
        fixture.write_bytes(
            b"\r\n".join(
                line for line in lines if line not in (b"BEGIN:VEVENT", b"END:VEVENT")
            )
        )
        assert check(tmp_path) == 1

    def test_a_missing_fixture_is_refused(self, tmp_path):
        (tmp_path / "manifest.json").write_bytes((CORPUS / "manifest.json").read_bytes())
        assert check(tmp_path) == 1

    def test_no_manifest_is_refused(self, tmp_path):
        assert check(tmp_path) == 1

    def test_a_manifest_naming_an_undeclared_source_is_refused(self, tmp_path):
        # Otherwise the corpus becomes a statement about a URL nothing reads:
        # the source moves, the fixture stays, and it keeps passing.
        for name in ("bbl_all_games.ics", "manifest.json"):
            (tmp_path / name).write_bytes((CORPUS / name).read_bytes())
        manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        manifest["source_url"] = "https://example.invalid/calendar.ics"
        (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        assert check(tmp_path) == 1

    def test_a_kept_event_the_reader_refuses_is_refused(self, tmp_path):
        # A corpus entry that yields no fixture pins nothing while passing every
        # other check: the bytes are the recorded bytes and the reader is silent.
        unusable = A_FED_FEED.replace(
            "DTSTART:20260910T183000", "DTSTART;VALUE=DATE:20260910"
        )
        # The recorder's own selection must skip it — the *third* event of the
        # feed fills the same cell and is readable, so the all-day one is a
        # fallback that never gets used.
        _, kept, _ = select(unusable)
        assert not any("VALUE=DATE" in line for block in kept for line in block)
        # And when a cell has nothing readable at all, the recorder keeps the
        # event rather than silently dropping coverage...
        only_unusable = (
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\n"
            "DTSTART;VALUE=DATE:20260910\r\nSUMMARY:A vs B\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        _, kept_only, _ = select(only_unusable)
        assert len(kept_only) == 1
        # ...and `--check` refuses a corpus holding it, rather than letting an
        # unreadable entry pass as coverage.
        payload, manifest = build("bbl", only_unusable, 200)
        (tmp_path / "bbl_all_games.ics").write_bytes(payload)
        (tmp_path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        assert check(tmp_path) == 1


class TestSelection:
    def test_every_combination_is_kept_once(self):
        header, kept, combos = select(A_FED_FEED)
        assert len(combos) == len(set(combos))
        # Two cells in this feed: `easyCredit x plain`, `Pokal x folded+umlaut`.
        # The third event repeats the first cell and is not kept again.
        assert len(kept) == 2
        assert header[0] == "BEGIN:VCALENDAR"
        assert header[-1] == "X-WR-TIMEZONE:Europe/Berlin"

    def test_the_envelope_is_kept(self):
        """Regression: the payload once had events with no BEGIN/END.

        The reader is right to refuse a `DTSTART` outside a `VEVENT` — that is
        what makes a stray line in a header harmless — so a payload stripped of
        its envelope parses to nothing at all, silently.
        """
        _, kept, _ = select(A_FED_FEED)
        for block in kept:
            assert block[0] == "BEGIN:VEVENT"
            assert block[-1] == "END:VEVENT"

    def test_a_response_with_no_events_is_an_error(self):
        with pytest.raises(ValueError, match="no VEVENT"):
            select("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")

    def test_an_undeclared_competition_is_labelled_unknown(self):
        # Not stripped, not dropped: it surfaces as `unknown`, which `--check`
        # refuses, so a new competition cannot pass unnoticed.
        _, _, combos = select(
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:20260910T183000\r\n"
            "SUMMARY:Neue Liga Spiel ALBA BERLIN vs Bonn\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        assert combos == ["unknown x folded=False x non-ascii=False"]

    def test_the_declared_prefixes_are_the_two_measured_ones(self):
        assert COMPETITION_PREFIXES == ("easyCredit BBL Spiel", "BBL Pokal Spiel")


class TestFetch:
    def test_an_http_error_is_a_failure_not_a_body(self):
        # Recording an error page as a calendar would produce a fixture that
        # parses to nothing and a manifest that says HTTP 200.
        import urllib.error

        def raise_404(*_a, **_k):
            raise urllib.error.HTTPError(
                "u", 404, "Not Found", {}, None  # type: ignore[arg-type]
            )

        import scripts.record_feeds as mod

        original = mod.urllib.request.urlopen
        mod.urllib.request.urlopen = raise_404  # type: ignore[assignment]
        try:
            with pytest.raises(RuntimeError, match="HTTP 404"):
                fetch("https://example.invalid/feed.ics")
        finally:
            mod.urllib.request.urlopen = original  # type: ignore[assignment]


class TestListIsReportOnly:
    def test_list_names_the_source_and_every_kept_event(self):
        result = _run("--list")
        assert result.returncode == 0, result.stderr
        assert "api.basketball-bundesliga.de/calendar/ical/all-games" in result.stdout
        assert "selection rule" in result.stdout
        assert result.stdout.count("\n  - [") == 7