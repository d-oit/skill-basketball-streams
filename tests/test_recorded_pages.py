"""Tests for the recorded-page corpus and the stream-evidence gate.

`FetchResult.has_stream_evidence()` is the last check before the pipeline may
write a calendar event, and until now it had only ever been judged against
strings this repository invented. The corpus in `tests/fixtures/pages/` is real
responses, so these tests are the first place the gate meets the actual web.

The most important test here is `test_the_old_vocabulary_fooled_by_real_pages`:
it asserts that the *historical* rule accepts the recorded negatives, so the
corpus demonstrably captures the defect it exists to prevent. If someone
"simplifies" the fixture, that test goes red rather than quietly passing.

All offline: the pages are on disk and no rung is ever called.
"""
from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from record_pages import (  # noqa: E402
    EVIDENCE,
    NO_EVIDENCE,
    PAGES,
    _json_object_at,
    baseline_verdicts,
    check,
    diff_verdicts,
    extract_youtube,
)
from render_ladder import (  # noqa: E402
    LIVE_STATE_MARKERS_DATA,
    MEDIA_MARKERS_MARKUP,
    FetchResult,
    _strip_scripts,
    anchor_game,
)

CORPUS = REPO_ROOT / "tests" / "fixtures" / "pages"
MANIFEST = CORPUS / "manifest.json"
RECORD = REPO_ROOT / "scripts" / "record_pages.py"

# The rule as it stood before the 2026-09-15 capture: bare vocabulary, matched
# against the whole document, including the inline JS runtime.
OLD_LIVE_MARKERS = ("live", "Live", "LIVE", "Jetzt live", "läuft")
OLD_PLAYER_MARKERS = ("<video", ".m3u8", "shaka", "dash", "player")


def old_rule_matches(body: str) -> bool:
    low = body.lower()
    return any(m.lower() in low for m in OLD_PLAYER_MARKERS) and any(
        m.lower() in low for m in OLD_LIVE_MARKERS
    )


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def read_stored(entry: dict) -> str:
    path = CORPUS / entry["stored"]
    raw = path.read_bytes()
    return (gzip.decompress(raw) if path.suffix == ".gz" else raw).decode(
        "utf-8", "replace"
    )


def stored_entries() -> list[dict]:
    return [e for e in load_manifest()["pages"] if e.get("stored")]


class TestCorpusIntegrity:
    def test_every_stored_page_classifies_as_its_expectation(self):
        problems, rows = check(CORPUS)
        assert problems == [], problems
        assert len(rows) == len(load_manifest()["pages"])

    def test_manifest_covers_every_declared_page(self):
        assert {e["name"] for e in load_manifest()["pages"]} == {
            p.name for p in PAGES
        }

    def test_no_orphan_files_in_the_corpus(self):
        declared = {e["stored"] for e in stored_entries()} | {
            "manifest.json",
            "README.md",
        }
        on_disk = {p.name for p in CORPUS.iterdir()}
        assert on_disk == declared

    def test_corpus_stays_small_enough_to_commit(self):
        """A 1.3 MB watch page gzips to ~320 KB, which is why the YouTube
        fixtures are extracts and two pages are verify-only. Pin that so the
        corpus cannot quietly grow back into megabytes."""
        total = sum((CORPUS / e["stored"]).stat().st_size for e in stored_entries())
        assert total < 300_000, f"corpus is {total:,} bytes"

    def test_every_page_states_what_it_proves(self):
        for entry in load_manifest()["pages"]:
            assert entry["note"].strip(), entry["name"]
            assert entry["expect"] in {EVIDENCE, NO_EVIDENCE}

    def test_manifest_records_provenance(self):
        manifest = load_manifest()
        for entry in manifest["pages"]:
            assert entry["status"] == 200, entry["name"]
            assert entry["url"].startswith("https://")
            assert len(entry["response_sha256"]) == 64
            assert entry["fetched_at"].endswith("Z")


class TestTheOldVocabularyIsFooledByRealPages:
    """The regression this corpus exists to pin."""

    def test_the_old_vocabulary_fooled_by_real_pages(self):
        fooled = set()
        for entry in stored_entries():
            if entry["expect"] != NO_EVIDENCE:
                continue
            if old_rule_matches(read_stored(entry)):
                fooled.add(entry["name"])
        assert {
            "magentasport-home",
            "clb-home",
            "youtube-watch-recorded",
        } <= fooled, fooled

    def test_only_the_app_shell_was_correctly_rejected_before(self):
        shell = next(e for e in stored_entries() if e["name"] == "magenta-tv-shell")
        assert old_rule_matches(read_stored(shell)) is False
        assert FetchResult(ok=True, text=read_stored(shell)).has_stream_evidence() is False

    def test_the_real_vod_page_passes_the_media_half(self):
        """The VOD is not rejected by *absence* of media evidence — it publishes
        `og:video`. Only the live-state half rules it out, so that half has to
        stay honest."""
        vod = next(e for e in stored_entries() if e["name"] == "youtube-watch-recorded")
        body = read_stored(vod)
        assert any(m in _strip_scripts(body.lower()) for m in MEDIA_MARKERS_MARKUP)
        assert '"islivecontent":true' not in body.lower()


class TestGateKeepsRealLivePages:
    def test_both_live_pages_keep_their_evidence(self):
        for name in ("youtube-watch-live", "youtube-channel-live"):
            entry = next(e for e in stored_entries() if e["name"] == name)
            assert FetchResult(ok=True, text=read_stored(entry)).has_stream_evidence()

    def test_a_hydrated_magenta_player_is_evidence(self):
        """Synthetic shape of what a browser rung returns for magenta.tv: a real
        player element, an HLS manifest in markup, and the German live badge."""
        assert FetchResult(
            ok=True,
            html='<video-js><video src="/live/x.m3u8"></video></video-js>'
            "<span>Jetzt live</span>",
        ).has_stream_evidence()

    def test_markup_alone_or_badge_alone_is_not_evidence(self):
        assert FetchResult(ok=True, html="<video src='/x.mp4'>").has_stream_evidence() is False
        assert FetchResult(ok=True, html="<p>Jetzt live</p>").has_stream_evidence() is False

    def test_the_live_state_set_is_data_only(self):
        """Every live-state marker is either a structured YouTube field or the
        German badge — no bare word that page copy can supply."""
        assert all(m.startswith('"') for m in LIVE_STATE_MARKERS_DATA)
        assert FetchResult(ok=True, html="<p>live läuft jetzt</p>").has_stream_evidence() is False


class TestScriptStripping:
    def test_markup_markers_do_not_count_inside_scripts(self):
        bundle = '<script>var a="player",b=".m3u8",c="live";</script>'
        assert _strip_scripts(bundle).strip() == ""
        assert FetchResult(ok=True, html=bundle + "<p>Jetzt live</p>").has_stream_evidence() is False

    def test_structured_keys_count_inside_scripts(self):
        body = '<script>var r = {"isLiveContent":true,"isLiveNow":true};</script>'
        assert FetchResult(ok=True, html=body).has_stream_evidence() is True

    def test_an_unterminated_script_removes_nothing(self):
        body = "<script>var a=1; <video>Jetzt live"
        assert _strip_scripts(body) == body
        assert FetchResult(ok=True, html=body).has_stream_evidence() is True

    def test_markers_are_case_insensitive_and_scripts_are_still_stripped(self):
        assert FetchResult(
            ok=True, html="<SCRIPT>player .m3u8 player</SCRIPT><p>JETZT LIVE</p>"
        ).has_stream_evidence() is False
        assert FetchResult(ok=True, html="<VIDEO>JETZT LIVE").has_stream_evidence() is True


class TestGameAnchorAgainstRealPages:
    """The gate passes for a page playing a live stream. These pin the *next*
    question — which game — against the same recorded responses."""

    def _body(self, name: str) -> str:
        return read_stored(next(e for e in stored_entries() if e["name"] == name))

    def test_a_real_single_game_title_is_attributed(self):
        # Real title: "LIVE - Tauranga Whai v Northern Kāhu | Tauihi Basketball
        # Aotearoa 2026", including a `v` separator and a macron on Kāhu.
        body = self._body("youtube-channel-live")
        anchor = anchor_game(body, "Tauranga Whai vs Northern Kāhu")
        assert anchor.matched is True and anchor.ambiguous is False
        assert anchor.title.startswith("LIVE - Tauranga Whai")

    def test_the_same_page_does_not_claim_a_different_game(self):
        anchor = anchor_game(self._body("youtube-channel-live"), "ALBA Berlin vs FC Bayern")
        assert anchor.matched is False

    def test_a_competition_name_is_not_a_game(self):
        """`Tauihi Basketball Aotearoa` appears in the same title, and its tokens
        are a subset of the clause, so it matched until the anchor started
        requiring the clause to *be* a pairing."""
        anchor = anchor_game(self._body("youtube-channel-live"), "Tauihi Basketball Aotearoa")
        assert anchor.matched is False

    def test_the_real_keyword_stuffed_title_is_ambiguous_not_matched(self):
        """Real title: "... World Cup Final USA vs France live | Germany vs
        Spain". It satisfies "both teams appear" for *either* pairing, so it must
        be reported as ambiguous rather than silently attributed to one."""
        body = self._body("youtube-watch-live")
        for pair in ("USA vs France", "Germany vs Spain"):
            anchor = anchor_game(body, pair)
            assert anchor.matched is False, pair
            assert anchor.ambiguous is True, pair
            assert "more than one pairing" in anchor.reason()

    def test_negative_pages_anchor_nothing(self):
        for name in ("youtube-watch-recorded", "magentasport-home", "clb-home"):
            anchor = anchor_game(self._body(name), "ALBA Berlin vs FC Bayern")
            assert anchor.matched is False, name

    def test_the_app_shell_has_no_title_to_anchor_on(self):
        anchor = anchor_game(self._body("magenta-tv-shell"), "ALBA Berlin vs FC Bayern")
        assert anchor.matched is False
        assert "no title" in anchor.reason()

    def test_evidence_and_attribution_are_separate_questions(self):
        """The recorded live page passes the *evidence* gate and fails the
        *anchor* for an unrelated game — which is exactly why both are needed:
        evidence alone would put an arbitrary live stream in the calendar."""
        body = self._body("youtube-watch-live")
        assert FetchResult(ok=True, text=body).has_stream_evidence() is True
        assert anchor_game(body, "ALBA Berlin vs FC Bayern").matched is False


class TestFlipReport:
    """`--refresh` reports a *verdict* change, and only a verdict change.

    The weekly `corpus-refresh` workflow opens a GitHub issue from this report,
    so an over-eager report is a product defect in the same way a real flip is:
    the stored YouTube extracts change hash whenever the live page does, and a
    byte diff would fire every week until nobody reads the issue any more.

    Network is stubbed at `_fetch`; nothing here leaves the machine.
    """

    # `"isLiveContent":true` is in both marker sets on purpose (YouTube's own
    # structured boolean), so one body can pin the evidence verdict.
    LIVE = b'<script>var ytInitialPlayerResponse = {"isLiveContent":true};</script>'
    # Extractable (so the `youtube-extract` pages still record) and not evidence.
    DEAD = b'<script>var ytInitialPlayerResponse = {"isLiveContent":false};</script>'

    def _stub(self, monkeypatch, live: set[str]):
        import record_pages

        def fake_fetch(url: str, timeout: float = 30.0):
            return 200, (self.LIVE if url in live else self.DEAD)

        monkeypatch.setattr(record_pages, "_fetch", fake_fetch)
        return record_pages

    def test_a_flip_is_reported_with_its_direction_and_the_url(self, tmp_path, monkeypatch):
        record_pages = self._stub(monkeypatch, live={"https://www.youtube.com/watch?v=DpMb8luALvE"})
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        (baseline_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "pages": [
                        # Recorded as a stream once; not one now, which is what the
                        # corpus always said, so this flip moves *towards* it.
                        {"name": "clb-home", "evidence_now": True},
                        # Not recognised before, is now.
                        {"name": "youtube-watch-live", "evidence_now": False},
                        # The one that matters most: a real live page that the gate
                        # has stopped recognising. expect=evidence, so this flip
                        # moves AWAY from the corpus -> missed games.
                        {"name": "youtube-channel-live", "evidence_now": True},
                        # Unchanged, so it must NOT appear.
                        {"name": "magenta-tv-shell", "evidence_now": False},
                    ]
                }
            )
        )

        entries, flips = record_pages.record(tmp_path / "out", baseline_dir=baseline_dir)

        by_name = {flip["name"]: flip for flip in flips}
        assert sorted(by_name) == ["clb-home", "youtube-channel-live", "youtube-watch-live"]
        lost = by_name["clb-home"]
        assert (lost["was"], lost["now"]) == (EVIDENCE, NO_EVIDENCE)
        assert lost["direction"] == "lost-evidence" and lost["agrees_with_expect"] is True
        assert lost["url"] == "https://www.championsleague.basketball/"
        gained = by_name["youtube-watch-live"]
        assert gained["direction"] == "gained-evidence" and gained["agrees_with_expect"] is True
        # A flip towards the recorded expectation and one away from it need
        # different fixes, so the report carries which it is rather than leaving
        # the reader to cross-reference `PAGES`.
        regression = by_name["youtube-channel-live"]
        assert regression["direction"] == "lost-evidence"
        assert regression["agrees_with_expect"] is False
        assert len(entries) == len(PAGES)

    def test_an_in_place_refresh_compares_against_what_it_is_replacing(self, tmp_path, monkeypatch):
        """The baseline is the manifest this run is about to overwrite. Read it
        after the write and every flip is invisible — and in-place is the
        documented way to refresh, so that is the case that must work."""
        live = "https://www.youtube.com/watch?v=DpMb8luALvE"
        self._stub(monkeypatch, live={live})
        import record_pages

        out = tmp_path / "corpus"
        first, flips_first = record_pages.record(out)
        assert first and flips_first == []  # nothing to compare against yet

        record_pages._fetch = lambda url, timeout=30.0: (  # noqa: E731
            200,
            self.DEAD,
        )
        _, flips_second = record_pages.record(out)
        assert [f["name"] for f in flips_second] == ["youtube-watch-live"]
        assert flips_second[0]["direction"] == "lost-evidence"

    def test_an_unchanged_corpus_reports_no_flip(self, tmp_path, monkeypatch):
        self._stub(monkeypatch, live=set())
        import record_pages

        out = tmp_path / "corpus"
        record_pages.record(out)
        entries, flips = record_pages.record(out)
        assert flips == [], flips
        assert entries

    def test_a_page_absent_from_the_baseline_is_not_a_flip(self):
        entries = [
            {"name": "brand-new", "url": "u", "expect": EVIDENCE,
             "evidence_now": True, "note": ""}
        ]
        assert diff_verdicts(entries, {"something-else": True}) == []

    def test_the_committed_corpus_has_not_flipped_against_itself(self):
        entries = load_manifest()["pages"]
        assert diff_verdicts(entries, baseline_verdicts(CORPUS)) == []

    def test_a_missing_or_unreadable_baseline_is_not_an_error(self, tmp_path):
        assert baseline_verdicts(tmp_path / "nowhere") == {}
        broken = tmp_path / "broken"
        broken.mkdir()
        (broken / "manifest.json").write_text("{not json")
        assert baseline_verdicts(broken) == {}

    def test_refresh_json_is_payload_only_on_stdout(self, tmp_path, monkeypatch, capsys):
        """The workflow redirects this straight into a file that the next step
        parses, so a human line mixed into stdout turns a clean run into a
        harness failure — the defect this repo has now found four times."""
        self._stub(monkeypatch, live=set())
        import record_pages

        out = tmp_path / "corpus"
        monkeypatch.setattr(
            sys, "argv",
            ["record_pages.py", "--refresh", "--out", str(out), "--json"],
        )
        record_pages.main()
        payload = json.loads(capsys.readouterr().out)  # raises if a line leaked
        assert payload["flips"] == []
        assert len(payload["pages"]) == len(PAGES)
        assert payload["refreshed_at"].endswith("Z")


class TestAnUnusableResponseStopsTheWrite:
    """A response that cannot be a page is named, collected, and never written.

    The workflow that runs this weekly tolerates the failure with a warning
    telling the reader which `FAIL:` line names the page, so every failure mode
    has to print one. Two did not, and the live web produced both on 2026-09-16:
    a 429 was *classified as content* (`_fetch` returns an error's status and
    body like any other response, so only a connection failure counted as a
    failure), and a document that fetched but carries no extract raised a bare
    `ValueError` traceback.

    The page is *collected* rather than fatal because the run is still worth
    finishing: the comparison it can make is the answer someone is waiting for.
    What is all-or-nothing is the WRITE, which is the property that was not true
    before — see `test_nothing_is_written_when_a_page_is_unreducible`.
    """

    CB = "https://www.championsleague.basketball/"
    RECORDED = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    LIVE = b'<script>var ytInitialPlayerResponse = {"isLiveContent":true};</script>'

    def _stub(self, monkeypatch, overrides, default=None):
        import record_pages

        fallback = (200, self.LIVE) if default is None else default

        def fake_fetch(url: str, timeout: float = 30.0):
            return overrides.get(url, fallback)

        monkeypatch.setattr(record_pages, "_fetch", fake_fetch)
        return record_pages

    def _with_baseline(self, tmp_path, name: str, was_evidence: bool):
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        (baseline_dir / "manifest.json").write_text(
            json.dumps({"pages": [{"name": name, "evidence_now": was_evidence}]})
        )
        return baseline_dir

    def test_an_error_status_is_not_recorded_as_the_page(self, tmp_path, monkeypatch):
        """A rate limit must not be able to fabricate a finding."""
        body = b"<html><body>429 Too Many Requests</body></html>"
        record_pages = self._stub(monkeypatch, {self.CB: (429, body)})
        baseline_dir = self._with_baseline(tmp_path, "clb-home", True)

        with pytest.raises(record_pages.UnreduciblePage) as excinfo:
            record_pages.record(tmp_path / "out", baseline_dir=baseline_dir)

        (unusable,) = excinfo.value.unusable
        assert unusable["name"] == "clb-home" and unusable["url"] == self.CB
        assert "HTTP 429" in unusable["reason"]
        assert "not the page" in unusable["reason"]
        # And nothing was written: the write is all-or-nothing.
        assert not (tmp_path / "out" / "manifest.json").exists()

    def test_the_error_status_guard_is_not_decorative(self):
        """The counterfactual, so the guard above is pinned to a real hazard."""
        body = b"<html><body>429 Too Many Requests</body></html>"
        assert FetchResult(ok=True, text=body.decode()).has_stream_evidence() is False
        flips = diff_verdicts(
            [
                {
                    "name": "clb-home",
                    "url": self.CB,
                    "expect": EVIDENCE,
                    "evidence_now": False,
                }
            ],
            {"clb-home": True},
        )
        # Classified as a page, the error body is a `lost-evidence` flip: an
        # issue saying real broadcasts stopped being recognised, caused by
        # nothing but a rate limit.
        assert flips[0]["direction"] == "lost-evidence"
        assert flips[0]["agrees_with_expect"] is False

    def test_a_document_with_no_extract_names_the_page(self, tmp_path, monkeypatch):
        """A consent wall is not a traceback; it is a FAIL line naming the URL."""
        consent = b"<html><body>Before you continue to YouTube</body></html>"
        record_pages = self._stub(monkeypatch, {self.RECORDED: (200, consent)})

        with pytest.raises(record_pages.UnreduciblePage) as excinfo:
            record_pages.record(tmp_path / "out")

        (unusable,) = excinfo.value.unusable
        assert unusable["url"] == self.RECORDED
        assert "no ytInitialPlayerResponse" in unusable["reason"]
        assert "cannot be refreshed" in unusable["reason"]

    def test_the_failure_is_printed_so_the_warning_has_its_line(
        self, tmp_path, monkeypatch, capsys
    ):
        """The workflow points the reader at a `FAIL:` line; it has to exist."""
        record_pages = self._stub(monkeypatch, {self.CB: (429, b"too many")})

        with pytest.raises(record_pages.UnreduciblePage):
            record_pages.record(tmp_path / "out")

        err = capsys.readouterr().err
        assert f"FAIL: record_pages: {self.CB}" in err

    def test_a_partial_comparison_is_still_reported(self, tmp_path, monkeypatch):
        """The reachable pages are compared even when another page is blocked.

        This is the whole point of collecting instead of aborting: a week when a
        provider rate-limits the runner used to be a week with no verdict
        comparison at all, which reads exactly like a week where nothing flipped.
        """
        # `DEAD` is extractable but not evidence, so the reachable pages record
        # cleanly and `clb-home` has genuinely changed: recorded as a stream, not
        # one now.
        dead = b'<script>var ytInitialPlayerResponse = {"isLiveContent":false};</script>'
        record_pages = self._stub(
            monkeypatch, {self.RECORDED: (429, b"too many")}, default=(200, dead)
        )
        baseline_dir = self._with_baseline(tmp_path, "clb-home", True)

        with pytest.raises(record_pages.UnreduciblePage) as excinfo:
            record_pages.record(tmp_path / "out", baseline_dir=baseline_dir)

        (flip,) = excinfo.value.flips
        assert flip["name"] == "clb-home"
        assert flip["direction"] == "lost-evidence"
        # The blocked page is not in the comparison, and says so.
        assert [u["name"] for u in excinfo.value.unusable] == ["youtube-watch-recorded"]
        assert len(excinfo.value.entries) == len(PAGES) - 1

    def test_nothing_is_written_when_a_page_is_unreducible(self, tmp_path, monkeypatch):
        """All-or-nothing, which the previous version was not.

        It wrote each stored file as it went and the manifest last, so an abort
        halfway through an in-place refresh left freshly fetched files beside a
        manifest holding the old hashes: `--check` then reported `hash mismatch`
        for every rewritten page, and the only way out was to restore the corpus
        from git — on exactly the day re-recording was impossible.
        """
        out = tmp_path / "out"
        out.mkdir()
        (out / "sentinel.html").write_bytes(b"pre-existing")
        record_pages = self._stub(monkeypatch, {self.CB: (429, b"too many")})

        with pytest.raises(record_pages.UnreduciblePage):
            record_pages.record(out)

        # Nothing new, nothing removed, nothing rewritten.
        assert [p.name for p in out.iterdir()] == ["sentinel.html"]
        assert (out / "sentinel.html").read_bytes() == b"pre-existing"

    def test_a_2xx_response_is_still_recorded(self, tmp_path, monkeypatch):
        """The guard must not reject the pages it exists to accept."""
        record_pages = self._stub(monkeypatch, {})

        entries, flips = record_pages.record(tmp_path / "out")

        assert len(entries) == len(PAGES)
        assert all(entry["status"] == 200 for entry in entries)
        assert flips == []
        # The other half of all-or-nothing: a complete run DOES write.
        assert (tmp_path / "out" / "manifest.json").exists()
        assert any(p.name.startswith("clb-home") for p in (tmp_path / "out").iterdir())


class TestYoutubeExtract:
    def test_brace_matching_ignores_braces_inside_strings(self):
        text = 'var ytInitialPlayerResponse = {"t":"}{","n":{"nested":2}};'
        assert _json_object_at(text, text.find("ytInitialPlayerResponse")) == (
            '{"t":"}{","n":{"nested":2}}'
        )

    def test_extract_keeps_the_meta_and_the_player_response(self):
        html = (
            '<head><meta property="og:video:url" content="/embed/x"></head>'
            '<body><script>var ytInitialPlayerResponse = {"isLiveContent":true};</script></body>'
        )
        out = extract_youtube(html)
        assert 'og:video:url' in out and '"isLiveContent":true' in out
        assert FetchResult(ok=True, html=out).has_stream_evidence() is True

    def test_extract_refuses_a_document_with_neither(self):
        with pytest.raises(ValueError):
            extract_youtube("<html><body>nothing</body></html>")


class TestCli:
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(RECORD), *args],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )

    def test_check_passes_on_the_committed_corpus(self):
        result = self._run("--check")
        assert result.returncode == 0, result.stderr
        assert "not stored" in result.stdout

    def test_check_never_prints_a_warning_prefix(self):
        """`do-harness` marks a sensor warned when a line starts with `SKIP:`,
        and under `--strict` a warned sensor is weak evidence — so a green
        corpus would report an error. Verified against the harness directly.
        """
        for stream in (self._run("--check").stdout, self._run("--check").stderr):
            for line in stream.splitlines():
                assert not line.startswith(("SKIP:", "WARN:")), line

    def test_check_fails_when_a_stored_page_no_longer_matches(self, tmp_path: Path):
        corpus = tmp_path / "pages"
        corpus.mkdir()
        for path in CORPUS.iterdir():
            (corpus / path.name).write_bytes(path.read_bytes())
        manifest = json.loads((corpus / "manifest.json").read_text())
        target = next(e for e in manifest["pages"] if e["name"] == "magenta-tv-shell")
        # Simulate the shell starting to serve a hydrated player page.
        (corpus / target["stored"]).write_text("<video src='/x.m3u8'>Jetzt live</video>")
        result = self._run("--check", "--out", str(corpus))
        assert result.returncode == 1
        assert "magenta-tv-shell" in result.stderr

    def test_check_rejects_a_manifest_that_contradicts_a_verify_only_page(self, tmp_path: Path):
        corpus = tmp_path / "pages"
        corpus.mkdir()
        for path in CORPUS.iterdir():
            (corpus / path.name).write_bytes(path.read_bytes())
        manifest = json.loads((corpus / "manifest.json").read_text())
        for entry in manifest["pages"]:
            if entry["name"] == "youtube-home":
                entry["evidence_now"] = True
        (corpus / "manifest.json").write_text(json.dumps(manifest))
        result = self._run("--check", "--out", str(corpus))
        assert result.returncode == 1
        assert "youtube-home" in result.stderr

    def test_check_rejects_an_edited_corpus_file(self, tmp_path: Path):
        """A fixture that still classifies correctly is not necessarily the
        recorded bytes: editing one must be caught, or the corpus becomes an
        assertion about a file we wrote rather than about the web."""
        corpus = tmp_path / "pages"
        corpus.mkdir()
        for path in CORPUS.iterdir():
            (corpus / path.name).write_bytes(path.read_bytes())
        shell = corpus / "magenta-tv-shell.html"
        shell.write_text(shell.read_text() + " ")
        result = self._run("--check", "--out", str(corpus))
        assert result.returncode == 1
        assert "not the recorded bytes" in result.stderr

    def test_every_stored_file_records_its_payload_hash(self):
        for entry in stored_entries():
            assert len(entry["stored_sha256"]) == 64, entry["name"]

    def test_check_reports_a_missing_stored_file(self, tmp_path: Path):
        corpus = tmp_path / "pages"
        corpus.mkdir()
        for path in CORPUS.iterdir():
            (corpus / path.name).write_bytes(path.read_bytes())
        (corpus / "clb-home.html.gz").unlink()
        result = self._run("--check", "--out", str(corpus))
        assert result.returncode == 1
        assert "missing clb-home.html.gz" in result.stderr

    def test_json_stdout_is_payload_only(self):
        result = self._run("--check", "--json")
        payload = json.loads(result.stdout)  # raises if a human line is mixed in
        assert payload["ok"] is True
        assert len(payload["pages"]) == len(load_manifest()["pages"])

    def test_list_names_every_page_and_exits_zero(self):
        result = self._run("--list")
        assert result.returncode == 0
        for page in PAGES:
            assert page.name in result.stdout
