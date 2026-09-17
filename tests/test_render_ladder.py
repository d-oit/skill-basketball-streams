"""Tests for scripts/render_ladder.py.

Entirely offline: every rung is a fake, so the ladder's *decision logic* is
pinned without a network call or a browser.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.render_ladder as render_ladder_module
from scripts.render_ladder import (
    ALL_RUNGS,
    DEFAULT_LADDER,
    LICENCE_AGPL,
    LICENCE_MIT,
    MAX_STRIKES,
    Rung,
    RungHealth,
    FetchResult,
    LicenceError,
    anchor_game,
    assert_shippable,
    attempt_state,
    build_ladder,
    fetch_with_ladder,
    host_of,
    js_hint,
    page_titles,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "render_ladder.py"

EVIDENCE = "<video src='x.m3u8'></video><span>Jetzt live</span>"


class FakeRung(Rung):
    """A rung that scripts its own outcome."""

    def __init__(self, name, *, ok=False, blocked=False, text="", error="",
                 kind="hosted", requires_js=True, license=LICENCE_MIT,
                 opt_in=False, installed=True, skipped=False):
        self.name = name
        self.kind = kind
        self.requires_js = requires_js
        self.license = license
        self.opt_in = opt_in
        self._ok = ok
        self._blocked = blocked
        self._skipped = skipped
        self._text = text
        self._error = error
        self._installed = installed
        self.calls = 0

    def installed(self):
        return self._installed

    def fetch(self, url, *, timeout=20.0):
        self.calls += 1
        return FetchResult(
            ok=self._ok, blocked=self._blocked, skipped=self._skipped,
            text=self._text, error=self._error, rung=self.name,
            status=403 if self._blocked else (200 if self._ok else None),
        )


class TestLicenceGuards:
    def test_default_ladder_is_agpl_free(self):
        """The guard that stops nodriver being quietly promoted."""
        ladder = build_ladder("https://example.com/x")
        assert ladder, "default ladder must not be empty"
        assert all(rung.license != LICENCE_AGPL for rung in ladder)
        assert_shippable(ladder)

    def test_every_non_opt_in_shipped_rung_is_agpl_free(self):
        assert_shippable(ALL_RUNGS.values())

    def test_nodriver_is_opt_in_only(self):
        assert ALL_RUNGS["nodriver"].opt_in is True
        assert ALL_RUNGS["nodriver"].license == LICENCE_AGPL

    def test_opt_in_agpl_rung_can_be_appended_explicitly(self):
        ladder = build_ladder("https://example.com/x", allow_opt_in=True)
        assert any(rung.name == "nodriver" for rung in ladder)

    def test_assert_shippable_rejects_agpl_non_opt_in(self):
        rogue = FakeRung("rogue", license=LICENCE_AGPL, opt_in=False)
        with pytest.raises(LicenceError, match="opt-in"):
            assert_shippable([rogue])

    def test_fetch_with_ladder_also_enforces_the_guard(self):
        rogue = FakeRung("rogue", license=LICENCE_AGPL, ok=True, text=EVIDENCE)
        with pytest.raises(LicenceError):
            fetch_with_ladder("https://example.com/x", rungs=[rogue])


class TestHostAndJsHint:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.magenta.tv/tv/live-x", "magenta.tv"),
            ("https://championsleague.basketball/live/a", "championsleague.basketball"),
            ("not a url", ""),
        ],
    )
    def test_host_of(self, url, expected):
        assert host_of(url) == expected

    def test_magenta_needs_js(self):
        assert js_hint("https://www.magenta.tv/tv/live-x") is True

    def test_champions_league_does_not_need_js(self):
        assert js_hint("https://championsleague.basketball/live/a") is False

    def test_youtube_has_no_render_rungs(self):
        # YouTube goes through the API/live filter, never the render ladder.
        assert build_ladder("https://www.youtube.com/@fiba/live") == []

    def test_host_ordering_prefers_cheapest_for_server_rendered(self):
        # `urllib` is first now: on a server-rendered host it is the cheapest
        # rung that can actually carry evidence, and it needs no key and no
        # package (this repo is stdlib-only by intent).
        ladder = build_ladder("https://championsleague.basketball/live/a")
        assert [r.name for r in ladder][:2] == ["urllib", "curl_cffi"]

    def test_host_ordering_prefers_hosted_for_spa(self):
        ladder = build_ladder("https://www.magenta.tv/tv/live-x")
        assert ladder[0].name == "firecrawl"

    def test_spa_ladder_keeps_the_keyless_rung_as_a_last_resort(self):
        """A browserless GET cannot render the SPA, so it must never pre-empt a
        real renderer — but with nothing installed it is the only rung that can
        report *why* (the app shell), instead of 'no rung existed'."""
        ladder = build_ladder("https://www.magenta.tv/tv/live-x")
        assert ladder[-1].name == "urllib"

    def test_every_ladder_contains_the_keyless_rung(self):
        """The ladder must never be empty for a host we route through it.

        Without this, a stdlib-only environment (which is what CI installs and
        what `requirements-dev.txt` promises) had every rung unavailable and
        stopped at the first one — so no game could ever be evidence.
        """
        for url in (
            "https://www.magenta.tv/tv/live-x",
            "https://championsleague.basketball/live/a",
            "https://www.magentasport.de/live",
            "https://example.com/x",
        ):
            ladder = build_ladder(url)
            assert "urllib" in [r.name for r in ladder], url
            assert any(r.installed() for r in ladder), url

    def test_the_keyless_rung_is_stdlib_and_never_opt_in(self):
        rung = ALL_RUNGS["urllib"]
        assert rung.license == LICENCE_MIT
        assert rung.opt_in is False and rung.requires_js is False
        assert rung.package == ""

    def test_enabled_filter_restricts_rungs(self):
        ladder = build_ladder("https://example.com/x", enabled=["curl_cffi"])
        assert [rung.name for rung in ladder] == ["curl_cffi"]

    def test_unknown_host_uses_default_ladder(self):
        ladder = build_ladder("https://example.com/x")
        assert [rung.name for rung in ladder] == list(DEFAULT_LADDER[: len(ladder)])


class TestFetchWithLadder:
    def test_first_rung_with_evidence_wins(self):
        first = FakeRung("a", ok=True, text=EVIDENCE)
        second = FakeRung("b", ok=True, text=EVIDENCE)
        result, attempts = fetch_with_ladder(
            "https://example.com/x", rungs=[first, second]
        )
        assert result.rung == "a" and result.has_stream_evidence()
        assert len(attempts) == 1
        assert second.calls == 0

    def test_blocked_climbs_the_ladder(self):
        blocked = FakeRung("a", blocked=True)
        winner = FakeRung("b", ok=True, text=EVIDENCE)
        result, attempts = fetch_with_ladder(
            "https://example.com/x", rungs=[blocked, winner]
        )
        assert result.rung == "b"
        assert [a.rung for a in attempts] == ["a", "b"]

    def test_real_error_stops_the_ladder(self):
        failing = FakeRung("a", ok=False, error="connection reset")
        stronger = FakeRung("b", ok=True, text=EVIDENCE)
        result, attempts = fetch_with_ladder(
            "https://example.com/x", rungs=[failing, stronger]
        )
        assert result.rung == "a"
        assert stronger.calls == 0
        assert len(attempts) == 1

    def test_an_unavailable_rung_climbs_instead_of_stopping(self):
        """`curl_cffi not installed` used to be read as a real error, so a
        stdlib-only environment gave up on its first rung and every game became
        UNVERIFIED for a reason unrelated to the game."""
        missing = FakeRung("curl_cffi", ok=False, error="curl_cffi not installed",
                           skipped=True, kind="local", requires_js=False)
        winner = FakeRung("b", ok=True, text=EVIDENCE)
        result, attempts = fetch_with_ladder(
            "https://example.com/x", rungs=[missing, winner]
        )
        assert result.rung == "b"
        assert [a.rung for a in attempts] == ["curl_cffi", "b"]

    def test_all_rungs_unavailable_reports_every_reason(self):
        rungs = [
            FakeRung("firecrawl", ok=False, error="FIRECRAWL_API_KEY not set", skipped=True),
            FakeRung("tinyfish", ok=False, error="TINYFISH_API_KEY not set", skipped=True),
        ]
        result, attempts = fetch_with_ladder("https://example.com/x", rungs=rungs)
        assert result.ok is False
        assert [a.error for a in attempts] == [
            "FIRECRAWL_API_KEY not set", "TINYFISH_API_KEY not set",
        ]
        assert all(a.skipped for a in attempts)

    def test_unavailable_rungs_do_not_accrue_strikes(self):
        health = RungHealth()
        for _ in range(MAX_STRIKES + 1):
            health.record(FetchResult(ok=False, rung="firecrawl",
                                      error="key not set", skipped=True))
        assert health.is_parked("firecrawl") is False

    def test_ok_without_stream_evidence_keeps_climbing(self):
        shell = FakeRung("a", ok=True, text="<html>app shell</html>")
        winner = FakeRung("b", ok=True, text=EVIDENCE)
        result, _ = fetch_with_ladder("https://example.com/x", rungs=[shell, winner])
        assert result.rung == "b"

    def test_js_host_skips_browserless_local_rung(self):
        cheap = FakeRung("curl_cffi", ok=True, text=EVIDENCE,
                         kind="local", requires_js=False)
        browser = FakeRung("patchright", ok=True, text=EVIDENCE,
                           kind="local", requires_js=True)
        result, attempts = fetch_with_ladder(
            "https://www.magenta.tv/tv/live-x", rungs=[cheap, browser]
        )
        assert result.rung == "patchright"
        assert cheap.calls == 0
        assert "requires JS" in attempts[0].error

    def test_js_host_still_tries_cheap_rung_when_no_browser_available(self):
        cheap = FakeRung("curl_cffi", ok=True, text=EVIDENCE,
                         kind="local", requires_js=False)
        browser = FakeRung("patchright", installed=False,
                           kind="local", requires_js=True)
        result, _ = fetch_with_ladder(
            "https://www.magenta.tv/tv/live-x", rungs=[cheap, browser]
        )
        assert result.rung == "curl_cffi"
        assert cheap.calls == 1

    def test_all_blocked_reports_blocked(self):
        rungs = [FakeRung("a", blocked=True), FakeRung("b", blocked=True)]
        result, attempts = fetch_with_ladder("https://example.com/x", rungs=rungs)
        assert result.blocked is True
        assert len(attempts) == 2

    def test_empty_ladder(self):
        result, attempts = fetch_with_ladder("https://example.com/x", rungs=[])
        assert attempts == [] and result.ok is False


class TestStreamEvidence:
    """The gate is a conjunction of a media marker and a live-state marker.

    Both halves are structural because the only thing judged here is a raw
    document whose inline JS runtime contains every plausible word. The
    recorded-page corpus and the reason each marker survives are in
    `tests/test_recorded_pages.py`.
    """

    def test_requires_media_and_live_state_markers(self):
        assert FetchResult(ok=True, text=EVIDENCE).has_stream_evidence() is True
        assert FetchResult(ok=True, text="<video src='x.m3u8'>").has_stream_evidence() is False
        assert FetchResult(ok=True, text="Jetzt live").has_stream_evidence() is False

    def test_empty_body_is_not_evidence(self):
        assert FetchResult(ok=True, text="", html="").has_stream_evidence() is False

    def test_html_body_alone_can_be_evidence(self):
        result = FetchResult(ok=True, html="<video></video><span>Jetzt live</span>")
        assert result.has_stream_evidence() is True

    def test_bare_live_text_is_no_longer_evidence(self):
        """Behaviour change (2026-09-15): `LIVE` as page text passed the old
        rule and is present on essentially every page, including two recorded
        league home pages. It is a badge or a structured field now."""
        assert FetchResult(ok=True, html="<video>LIVE</video>").has_stream_evidence() is False
        assert FetchResult(
            ok=True, html='<script>{"isLiveContent":true}</script>'
        ).has_stream_evidence() is True


class TestGameAnchor:
    """`has_stream_evidence` says a page is playing a live stream; the anchor says
    *which* game. Against real pages, see tests/test_recorded_pages.py."""

    BODY = (
        "<html><head><meta property=\"og:title\" content=\"ALBA Berlin vs FC Bayern\">"
        "<title>Liveticker</title></head><body><video src=\"x.m3u8\">"
        "<span>Jetzt live</span></body></html>"
    )

    def test_every_title_source_is_collected_and_deduped(self):
        titles = page_titles(
            '<meta property="og:title" content="A vs B">'
            "<title>A vs B</title>"
            '<meta name="twitter:title" content="C vs D">'
        )
        assert titles == ("A vs B", "C vs D")

    def test_youtube_player_title_is_a_title_source(self):
        body = '<script>var ytInitialPlayerResponse = {"videoDetails":{"videoId":"x","title":"Tauranga Whai v Northern Kahu"}};</script>'
        assert page_titles(body) == ("Tauranga Whai v Northern Kahu",)

    def test_a_title_that_names_the_game_matches(self):
        anchor = anchor_game(self.BODY, "ALBA Berlin vs FC Bayern")
        assert anchor.matched is True and anchor.title == "ALBA Berlin vs FC Bayern"

    def test_a_different_game_does_not_match(self):
        assert anchor_game(self.BODY, "Real Madrid vs Barcelona").matched is False

    def test_a_clause_that_merely_mentions_both_clubs_is_not_a_pairing(self):
        body = '<meta property="og:title" content="ALBA Berlin und FC Bayern im Pokal">'
        assert anchor_game(body, "ALBA Berlin vs FC Bayern").matched is False

    def test_a_multi_game_title_is_ambiguous_not_matched(self):
        body = '<meta property="og:title" content="A vs B live | C vs D">'
        anchor = anchor_game(body, "A vs B")
        assert anchor.matched is False and anchor.ambiguous is True
        assert "more than one pairing" in anchor.reason()

    def test_no_title_means_no_attribution(self):
        anchor = anchor_game("<html><body><video>Jetzt live</video></body></html>", "A vs B")
        assert anchor.matched is False
        assert "no title" in anchor.reason()

    def test_no_expectation_is_not_a_failure(self):
        assert anchor_game(self.BODY, "").matched is False
        assert anchor_game(self.BODY, []).titles == ("ALBA Berlin vs FC Bayern", "Liveticker")

    def test_body_wording_never_anchors(self):
        """Only the document's own name counts: a body links to every club it
        mentions, which is the same trap as a body-wide live marker."""
        body = '<meta property="og:title" content="Sport im TV"><p>ALBA Berlin vs FC Bayern</p>'
        assert anchor_game(body, "ALBA Berlin vs FC Bayern").matched is False

    def test_body_judges_a_recorded_page_without_fetching(self):
        """The ladder never renders YouTube (it goes through the API/live filter),
        so `--body` is how its pages — and the recorded corpus — get a verdict."""
        result = _run([
            "--body", "tests/fixtures/pages/youtube-channel-live.html.gz",
            "--expect-teams", "Tauranga Whai vs Northern Kāhu",
        ], cwd=REPO_ROOT)
        assert result.returncode == 0, result.stderr
        assert "stream evidence present" in result.stdout
        assert "game anchor: named in" in result.stdout

    def test_body_refuses_a_real_keyword_stuffed_title(self):
        result = _run([
            "--body", "tests/fixtures/pages/youtube-watch-live.html.gz",
            "--expect-teams", "USA vs France",
        ], cwd=REPO_ROOT)
        assert result.returncode == 1
        assert "more than one pairing" in result.stdout
        assert "refusing to attribute" in result.stderr

    def test_body_refuses_the_wrong_game_on_a_real_live_page(self):
        result = _run([
            "--body", "tests/fixtures/pages/youtube-channel-live.html.gz",
            "--expect-teams", "ALBA Berlin vs FC Bayern",
        ], cwd=REPO_ROOT)
        assert result.returncode == 1
        assert "refusing to attribute" in result.stderr

    def test_body_without_expectation_only_judges_evidence(self):
        result = _run(["--body", "tests/fixtures/pages/magenta-tv-shell.html"], cwd=REPO_ROOT)
        assert result.returncode == 1
        assert "stream evidence ABSENT" in result.stdout

    def test_body_that_does_not_exist_is_usage_error(self):
        assert _run(["--body", "tests/fixtures/pages/nope.html"]).returncode == 2

    def test_plan_ignores_expect_teams(self):
        result = _run(
            ["--url", "https://www.magenta.tv/tv/live-x", "--plan",
             "--expect-teams", "A vs B"]
        )
        assert result.returncode == 0

    def test_probe_for_an_unreachable_host_reports_the_anchor_as_unattributed(self):
        """With `--expect-teams` a reachable-but-empty page can never pass, and
        the JSON says which question failed."""
        result = _run(
            ["--url", "https://no-such-host.invalid/x", "--probe", "--json",
             "--expect-teams", "ALBA Berlin vs FC Bayern", "--timeout", "5"]
        )
        payload = json.loads(result.stdout)
        assert result.returncode == 1
        assert payload["ok"] is False
        assert payload["expected_teams"] == ["ALBA Berlin", "FC Bayern"]
        assert payload["game"]["matched"] is False


class TestRungHealth:
    def test_parks_after_max_consecutive_failures(self):
        health = RungHealth()
        for _ in range(MAX_STRIKES):
            health.record(FetchResult(ok=False, rung="a", error="boom"))
        assert health.is_parked("a") is True

    def test_success_clears_strikes(self):
        health = RungHealth()
        health.record(FetchResult(ok=False, rung="a"))
        health.record(FetchResult(ok=False, rung="a"))
        health.record(FetchResult(ok=True, rung="a"))
        health.record(FetchResult(ok=False, rung="a"))
        assert health.is_parked("a") is False
        assert health.strikes["a"] == 1

    def test_parked_rung_is_skipped(self):
        health = RungHealth()
        for _ in range(MAX_STRIKES):
            health.record(FetchResult(ok=False, rung="a"))
        a = FakeRung("a", ok=True, text=EVIDENCE)
        b = FakeRung("b", ok=True, text=EVIDENCE)
        result, attempts = fetch_with_ladder(
            "https://example.com/x", rungs=[a, b], health=health
        )
        assert result.rung == "b"
        assert a.calls == 0
        assert "parked" in attempts[0].error

    def test_reset_unparks(self):
        health = RungHealth()
        for _ in range(MAX_STRIKES):
            health.record(FetchResult(ok=False, rung="a"))
        health.reset()
        assert health.is_parked("a") is False


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
        cwd=str(cwd) if cwd else None,
    )


@pytest.fixture
def offline(monkeypatch):
    """Make the keyless `urllib` rung fail instantly, with no network at all.

    The ladder must be exercisable in CI (`do-harness` sensors are offline and
    deterministic), and `urllib` is the one rung that would otherwise reach the
    internet from a test. A closed loopback proxy port is a connection error to
    it, which is exactly the 'real error stops the ladder' path.
    """
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    return monkeypatch


class TestCli:
    def test_list_reports_licences(self):
        result = _run(["--list"])
        assert result.returncode == 0
        assert "nodriver" in result.stdout
        assert "AGPL-3.0" in result.stdout
        assert "curl_cffi" in result.stdout

    def test_plan_for_magenta(self):
        result = _run(["--url", "https://www.magenta.tv/tv/live-x", "--plan"])
        assert result.returncode == 0
        assert "rung 0 firecrawl" in result.stdout

    def test_plan_for_youtube_reports_no_rungs(self):
        result = _run(["--url", "https://www.youtube.com/@fiba/live", "--plan"])
        assert result.returncode == 0
        assert "no rendering rungs" in result.stdout

    def test_missing_url_is_usage_error(self):
        assert _run([]).returncode == 2

    def test_probe_fails_without_credentials(self, monkeypatch, offline):
        """Offline by construction: `offline` points the keyless `urllib` rung at
        a closed local proxy port.

        This used to pass for a different reason — with no key and no optional
        package the ladder had *nothing* to try. The keyless rung means the
        ladder now genuinely attempts, and a real connection error stops the
        climb (a stronger rung cannot fix an unreachable host).
        """
        for key in ("FIRECRAWL_API_KEY", "TINYFISH_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        result = _run(
            ["--url", "https://www.championsleague.basketball/live/a", "--probe",
             "--timeout", "5"]
        )
        assert result.returncode == 1
        assert "no rung produced" in result.stderr
        assert "urllib" in result.stdout

    def test_probe_reports_every_unavailable_rung(self, offline):
        """The reason each rung was unusable is the point: 'nothing was tried' and
        'everything was tried and nothing had a key' look identical in a
        one-line failure. The four hosted/optional rungs come before `urllib` on
        this host, so their reasons are printed before it is attempted."""
        result = _run(
            ["--url", "https://www.magenta.tv/tv/live-x", "--probe", "--timeout", "5"]
        )
        assert result.returncode == 1
        assert "firecrawl" in result.stdout and "FIRECRAWL_API_KEY" in result.stdout
        # And the label is the honest one: no credential is not a dead backend,
        # which is the distinction the rung ledger is built on.
        assert "firecrawl: skipped" in result.stdout
        assert "firecrawl: failed" not in result.stdout
        assert "urllib: failed" in result.stdout


class TestAttemptState:
    """One classification, shared by the report, the JSON payload and the ledger.

    Before this existed the prose derived its own label and `skipped` was not in
    the chain, so a rung with no key printed `failed` — the same word as a rung
    whose backend was retired. `scripts/rung_health.py` now records that label on
    an append-only ledger in the telemetry branch, so the two readings would have
    persisted for months.
    """

    def test_the_five_states(self):
        assert attempt_state(FetchResult(ok=True, text=EVIDENCE)) == "evidence"
        assert attempt_state(FetchResult(ok=True, text="<html>x</html>")) == (
            "ok-no-evidence"
        )
        assert attempt_state(FetchResult(ok=False, blocked=True, status=403)) == "blocked"
        assert attempt_state(FetchResult(ok=False, error="boom")) == "failed"
        assert attempt_state(
            FetchResult(ok=False, skipped=True, error="not installed")
        ) == "skipped"

    def test_skipped_is_checked_before_ok(self):
        # A skipped rung is not `ok` in either direction, and it must not fall
        # through to `failed`.
        assert attempt_state(FetchResult(ok=False, skipped=True)) != "failed"

    def test_a_js_skip_is_flagged_as_a_skip(self):
        cheap = FakeRung("curl_cffi", ok=True, text=EVIDENCE, kind="local",
                         requires_js=False)
        browser = FakeRung("patchright", ok=True, text=EVIDENCE, kind="local",
                           requires_js=True)
        _, attempts = fetch_with_ladder(
            "https://www.magenta.tv/tv/live-x", rungs=[cheap, browser]
        )
        assert attempts[0].skipped is True
        assert attempt_state(attempts[0]) == "skipped"

    def test_the_json_payload_carries_the_state(self, monkeypatch, capsys):
        monkeypatch.setattr(
            render_ladder_module,
            "build_ladder",
            lambda url, **kwargs: [FakeRung("urllib", skipped=True, error="not installed")],
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["render_ladder.py", "--url", "https://example.com/x", "--probe", "--json"],
        )
        with pytest.raises(SystemExit) as exit_info:
            render_ladder_module.main()
        assert exit_info.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["attempts"][0]["state"] == "skipped"
        assert payload["evidence"] is False
