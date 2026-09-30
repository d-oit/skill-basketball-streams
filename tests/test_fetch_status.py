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
