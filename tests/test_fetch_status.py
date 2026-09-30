"""Tests for the fetch-status diagnosis in `scripts/fixtures.py`.

The behaviour under test is a *diagnosis*, and the failure it prevents is a
misdiagnosis: before this change every failed fetch collapsed to `""` and the
caller reported "the page fetched but no fixture parsed (markup change?)". A
source answering **HTTP 429 to every user agent** — the measured state of
`euroleaguebasketball.net` from a datacenter IP on 2026-09-30 — was therefore
reported as a parser problem. Acting on that message means writing a parser that
is already correct and is still blocked.

So the assertions are about which words appear and, just as importantly, which
do not. A test that only checked "it failed" would have passed against the old
code.

No test touches the network: `urllib.request.urlopen` is patched to raise the
exact exception `fetch` is meant to classify.
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

import scripts.fixtures as mod
from scripts.fixtures import (
    BLOCKED_CODES,
    GONE_CODES,
    FetchResult,
    _empty_cause,
    _overall_advice,
    fetch,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid/", code, f"HTTP {code}", {}, io.BytesIO(b"")
    )


def _url_error(reason: str) -> urllib.error.URLError:
    return urllib.error.URLError(reason)


class TestFetchResultIsStillAString:
    """`fetch` is monkeypatched suite-wide with `lambda url: "<html>"`.

    Returning a non-str would have turned every one of those patches into a
    failure unrelated to the behaviour being tested, so the result type is a
    `str` subclass. These tests exist to keep that property from being "cleaned
    up" later.
    """

    def test_a_plain_string_body_still_works(self):
        result = FetchResult("<html>ok</html>")
        assert isinstance(result, str)
        assert result == "<html>ok</html>"
        assert result.status == "ok"

    def test_the_body_is_unaffected_by_the_status(self):
        assert FetchResult("body", "blocked", 429) == "body"


class TestStatusClassification:
    @pytest.mark.parametrize("code", sorted(BLOCKED_CODES))
    def test_block_codes_are_blocked(self, monkeypatch, code):
        monkeypatch.setattr(
            mod.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_http_error(code))
        )
        result = fetch("https://example.invalid/")
        assert result == ""
        assert result.status == "blocked"
        assert result.code == code

    @pytest.mark.parametrize("code", sorted(GONE_CODES))
    def test_gone_codes_are_not_found(self, monkeypatch, code):
        monkeypatch.setattr(
            mod.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_http_error(code))
        )
        assert fetch("https://example.invalid/").status == "not_found"

    def test_5xx_is_a_server_fault_not_a_markup_change(self, monkeypatch):
        monkeypatch.setattr(
            mod.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_http_error(503))
        )
        assert fetch("https://example.invalid/").status == "server_error"

    def test_dns_and_tls_failures_are_unreachable(self, monkeypatch):
        monkeypatch.setattr(
            mod.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(_url_error("SNI fail"))
        )
        assert fetch("https://example.invalid/").status == "unreachable"

    def test_success_carries_ok(self, monkeypatch):
        class _Resp(io.BytesIO):
            headers = type("H", (), {"get_content_charset": staticmethod(lambda: "utf-8")})()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: _Resp(b"page"))
        result = fetch("https://example.invalid/")
        assert result == "page"
        assert result.status == "ok"


class TestEmptyCauseWording:
    def test_blocked_says_so_and_denies_a_markup_change(self):
        cause = _empty_cause("blocked", 429)
        assert "BLOCKED" in cause
        assert "429" in cause
        assert "not a markup change" in cause
        # The words that send a reader to write a parser must be absent.
        assert "markup change?)" not in cause

    def test_gone_points_at_the_url_not_the_parser(self):
        cause = _empty_cause("not_found", 404)
        assert "GONE" in cause
        assert "fix the source" in cause

    def test_server_error_says_retry(self):
        assert "retry" in _empty_cause("server_error", 503).lower()

    def test_unreachable_names_the_transport(self):
        assert "DNS" in _empty_cause("unreachable", None)

    def test_an_empty_body_with_no_status_keeps_the_legacy_wording(self):
        # Only reachable when a caller (or a test's monkeypatch) hands back a
        # plain empty string, so there is no status to report. The wording stays
        # deliberately non-committal rather than guessing a cause. The
        # "markup change" wording for a page that *arrived* but did not parse
        # lives in a different branch and is asserted end-to-end below.
        cause = _empty_cause("ok", None)
        assert "network failure, bot block or TLS error" in cause
        assert "BLOCKED" not in cause


class TestOverallAdviceAgreesWithTheCauses:
    """The summary must not contradict the per-source lines it summarises."""

    def test_all_blocked_says_no_parser_work(self):
        advice = _overall_advice({"euroleague": _empty_cause("blocked", 429)})
        assert "no parser work" in advice
        assert "add a parser fixture" not in advice

    def test_mixed_names_only_the_blocked_ones(self):
        advice = _overall_advice(
            {
                "euroleague": _empty_cause("blocked", 429),
                "bbl": _empty_cause("ok", None),
            }
        )
        assert "'euroleague'" in advice
        assert "parser fixture" in advice

    def test_all_parse_failures_keeps_the_capture_advice(self):
        advice = _overall_advice({"bbl": _empty_cause("ok", None)})
        assert "capture a page" in advice


class TestEndToEndMessage:
    """The message the daily run actually prints, via `main()`."""

    page = "<html><body>no structured data here</body></html>"

    def _argv(self, *args):
        return ["fixtures.py", *args]

    def test_a_blocked_source_does_not_ask_for_a_parser(self, monkeypatch, capsys):
        monkeypatch.setattr(
            mod, "fetch", lambda url: FetchResult("", "blocked", 429)
        )
        monkeypatch.setattr(
            sys, "argv", self._argv("--source", "euroleague", "--now", "2026-10-06T08:30:00Z")
        )
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "BLOCKED (HTTP 429)" in err
        assert "not a markup change" in err
        # The old wording is what this test exists to prevent.
        assert "the sites may have changed their markup" not in err

    def test_a_page_that_arrives_but_does_not_parse_still_asks_for_a_capture(
        self, monkeypatch, capsys
    ):
        page = "<html><body>no structured data here</body></html>"
        monkeypatch.setattr(mod, "fetch", lambda url: FetchResult(page, "ok"))
        monkeypatch.setattr(
            sys, "argv", self._argv("--source", "bbl", "--now", "2026-10-06T08:30:00Z")
        )
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "no fixture parsed" in err
        assert "BLOCKED" not in err

    def test_a_blocked_response_with_a_body_is_still_reported_as_blocked(
        self, monkeypatch, capsys
    ):
        """The regression this module was written for, found by its own ledger.

        `fetch` reads the interstitial body off the `HTTPError` so the cause can
        be reported — which means a **blocked** response is no longer an empty
        string. The caller then branched on `if not html`, never reached the
        blocked branch, and classified a 31 KB Vercel challenge page as "the page
        fetched but no fixture parsed (markup change?)" — reintroducing the exact
        misdiagnosis by a different route. The ledger row said `blocked` while
        the FAIL line said "markup change?", which is how it was caught.

        So the branch is on `status`, not on body emptiness, and this test fails
        if that ever reverts to a truthiness check on the body.
        """
        challenge = (
            "<html><head><title>Vercel Security Checkpoint</title></head>"
            "<body>checking your browser</body></html>"
        )
        monkeypatch.setattr(
            mod,
            "fetch",
            lambda url: FetchResult(
                challenge, "blocked", 429, {"server": "Vercel", "x-vercel-mitigated": "challenge"}
            ),
        )
        monkeypatch.setattr(
            sys, "argv", self._argv("--source", "euroleague", "--now", "2026-10-06T08:30:00Z")
        )
        with pytest.raises(SystemExit) as exc:
            mod.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "BLOCKED (HTTP 429)" in err
        assert "not a markup change" in err
        assert "markup change?)" not in err
        # And the evidence that identifies the block travels with the message.
        assert "Vercel" in err
        assert "Vercel Security Checkpoint" in err


class TestFetchLedger:
    """Append-only diagnostics, in the shape `rung_health.py` already uses."""

    page = "<html><body>no structured data here</body></html>"

    def _argv(self, *args):
        return ["fixtures.py", *args]

    def test_nothing_is_written_unless_asked(self, monkeypatch, capsys, tmp_path):
        """Opt-in, because the suite drives main() with a patched `fetch`.

        Writing by default appended synthetic test bodies to the real ledger:
        8 of 13 rows were fixtures, mixed in with real attempts, which is a
        ledger that cannot be trusted to describe anything. A test that writes
        real telemetry is not hermetic, so the guard lives in the code rather
        than in each test.
        """
        monkeypatch.setattr(
            mod, "fetch", lambda url: FetchResult(self.page, "ok")
        )
        monkeypatch.setattr(
            sys,
            "argv",
            self._argv(
                "--source", "bbl", "--root", str(tmp_path),
                "--now", "2026-10-06T08:30:00Z",
            ),
        )
        with pytest.raises(SystemExit):
            mod.main()
        assert not (tmp_path / mod.FETCH_LEDGER_RELATIVE_PATH).exists()

    def test_a_row_is_appended_when_asked(self, monkeypatch, capsys, tmp_path):
        ledger = tmp_path / "fetch.jsonl"
        monkeypatch.setattr(
            mod,
            "fetch",
            lambda url: FetchResult(
                "<html><title>Vercel Security Checkpoint</title></html>",
                "blocked", 429, {"server": "Vercel", "x-vercel-mitigated": "challenge"},
            ),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            self._argv(
                "--source", "euroleague", "--fetch-ledger", str(ledger),
                "--now", "2026-10-06T08:30:00Z",
            ),
        )
        with pytest.raises(SystemExit):
            mod.main()
        rows = mod.read_fetch_ledger(ledger)
        assert len(rows) == 1
        row = rows[0]
        assert row["source"] == "euroleague"
        assert row["status"] == "blocked"
        assert row["code"] == 429
        assert row["page_title"] == "Vercel Security Checkpoint"
        assert "challenge" in row["why"]
        assert row["ts"] and row["run_id"]

    def test_a_body_never_reaches_the_ledger(self, monkeypatch, tmp_path):
        """The rung_health rule: named fields only, so the ledger stays small.

        A blocked response here is a 31 KB interstitial; storing it would make
        the ledger unkeepable, and storing arbitrary response content is how a
        telemetry file starts echoing requests back.
        """
        big = "<html><title>T</title><body>" + ("x" * 50_000) + "</body></html>"
        row = mod.append_fetch_row(
            tmp_path / "l.jsonl",
            source="s",
            url="https://example.invalid/x",
            result=FetchResult(big, "blocked", 429),
        )
        assert "body" not in row
        assert "x" * 1000 not in json.dumps(row)
        # The size is recorded as a number, and the title as a fingerprint.
        assert row["body_bytes"] > 50_000
        assert row["page_title"] == "T"

    def test_a_ledger_write_failure_does_not_change_the_verdict(self, tmp_path):
        """Losing evidence is bad; changing the answer over it is worse."""
        blocked_dir = tmp_path / "a_file_not_a_dir"
        blocked_dir.write_text("not a directory")
        # `append_fetch_row` must not raise even though mkdir will fail.
        row = mod.append_fetch_row(
            blocked_dir / "sub" / "l.jsonl",
            source="s",
            url="https://example.invalid/",
            result=FetchResult("", "blocked", 429),
        )
        assert row["status"] == "blocked"

    def test_the_reader_outputs_the_failing_and_its_cause(self, capsys, tmp_path):
        ledger = tmp_path / "l.jsonl"
        ledger.write_text(
            "\n".join(
                json.dumps(r)
                for r in [
                    {"source": "bcl", "url": "https://b/", "status": "ok", "code": 200},
                    {
                        "source": "euroleague",
                        "url": "https://e/",
                        "status": "blocked",
                        "code": 429,
                        "why": "server: Vercel; mitigation: challenge",
                    },
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        assert mod.report_fetch_log(ledger) == 1
        out = capsys.readouterr().out
        assert "euroleague" in out
        assert "HTTP 429" in out
        assert "mitigation: challenge" in out
        # The success is not in the default view.
        assert "bcl" not in out

    def test_the_reader_can_include_successes(self, capsys, tmp_path):
        ledger = tmp_path / "l.jsonl"
        ledger.write_text(
            json.dumps({"source": "bcl", "url": "https://b/", "status": "ok", "code": 200}),
            encoding="utf-8",
        )
        mod.report_fetch_log(ledger, only_failing=False)
        assert "bcl" in capsys.readouterr().out

    def test_a_corrupt_line_is_skipped_not_fatal(self, tmp_path):
        ledger = tmp_path / "l.jsonl"
        ledger.write_text(
            '{"source": "a", "status": "blocked", "code": 429}\nnot json\n\n',
            encoding="utf-8",
        )
        rows = mod.read_fetch_ledger(ledger)
        assert [r["source"] for r in rows] == ["a"]

    def test_a_missing_ledger_is_not_a_failure(self, capsys, tmp_path):
        assert mod.report_fetch_log(tmp_path / "nope.jsonl") == 0
        assert "no fetch attempts recorded" in capsys.readouterr().out
