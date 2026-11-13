"""Tests for scripts/youtube_tab.py — keyless channel-tab discovery.

The parsers are pinned to REAL recorded bytes (the 2026-11-11 captures that
proved the Live-filter results pages return boilerplate while the channel
surfaces are server-rendered), not to invented strings: a shape change on
YouTube's side must fail these tests rather than pass against a guess.

The orchestration (`discover` union + enrichment cap) runs against small
inline inputs, so a network fault in CI cannot red it.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

import scripts.youtube_tab as youtube_tab
from scripts.youtube_tab import (
    discover,
    resolve_channel_id,
    rss_entries,
    tab_video_ids,
    watch_info,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "youtube_tab"


def _gz(name: str) -> str:
    with gzip.open(FIXTURES / name, "rt", encoding="utf-8") as handle:
        return handle.read()


class TestRssEntriesOnTheRecordedFeed:
    def test_the_recorded_enbl_feed_lists_fifteen_entries(self):
        entries = rss_entries(
            (FIXTURES / "enbl-rss.xml").read_text(encoding="utf-8")
        )
        assert len(entries) == 15

    def test_the_first_entry_names_the_november_4_game(self):
        first = rss_entries(
            (FIXTURES / "enbl-rss.xml").read_text(encoding="utf-8")
        )[0]
        assert first["video_id"] == "TkTe7_97F1U"
        assert "VOLUNTARI" in first["title"] and "Fyllingen" in first["title"]
        assert first["published"].startswith("2026-11-05")

    def test_every_entry_carries_id_title_and_published(self):
        for entry in rss_entries(
            (FIXTURES / "enbl-rss.xml").read_text(encoding="utf-8")
        ):
            assert len(entry["video_id"]) == 11
            assert entry["title"].strip()
            assert entry["published"].strip()

    def test_garbage_xml_is_no_entries_not_an_exception(self):
        assert rss_entries("not xml at all <") == []
        assert rss_entries("") == []


class TestTabVideoIdsOnTheRecordedTab:
    def test_the_recorded_enbl_tab_lists_thirty_unique_ids(self):
        ids = tab_video_ids(_gz("enbl-streams.html.gz"))
        assert len(ids) == 30
        assert all(len(video_id) == 11 for video_id in ids)

    def test_empty_html_is_no_ids(self):
        assert tab_video_ids("") == []


class TestWatchInfoOnTheRecordedUpcomingGame:
    def test_the_recorded_watch_page_dates_tonights_taltech_game(self):
        info = watch_info(_gz("enbl-watch-upcoming.html.gz"))
        assert "TalTech" in info["title"] and "Spartak" in info["title"]
        assert info["scheduled_start"] == "2026-11-11T16:30:00+00:00"
        assert info["is_live_now"] is False

    def test_a_live_payload_reads_live_now(self):
        html = (
            '{"videoDetails":{"videoId":"abc123abc12","title":"Live game"},'
            '"liveBroadcastDetails":{"isLiveNow":true,'
            '"startTimestamp":"2026-11-11T16:00:00+00:00"}}'
        )
        info = watch_info(html)
        assert info["is_live_now"] is True
        assert info["scheduled_start"] == "2026-11-11T16:00:00+00:00"

    def test_a_vod_without_live_details_is_undated_not_live(self):
        html = '{"videoDetails":{"videoId":"abc123abc12","title":"Highlights"}}'
        info = watch_info(html)
        assert info == {"title": "Highlights", "scheduled_start": None, "is_live_now": False}

    def test_garbage_is_empty_not_an_exception(self):
        assert watch_info("") == {
            "title": "",
            "scheduled_start": None,
            "is_live_now": False,
        }


class TestResolveChannelId:
    def test_a_mapped_handle_needs_no_fetch(self):
        def _exploding(url: str) -> str:
            raise AssertionError(f"must not fetch: {url}")

        assert (
            resolve_channel_id("ENBLeague", fetch=_exploding)
            == "UC4nW9T_6d6e4DKa4A9_3Ouw"
        )

    def test_an_unmapped_handle_resolves_from_its_page(self):
        page = '{"browseId":"UCxxxxxxxxxxxxxxxxxxxxxx"}'
        assert (
            resolve_channel_id("someleague", fetch=lambda url: page)
            == "UCxxxxxxxxxxxxxxxxxxxxxx"
        )

    def test_an_unresolvable_handle_is_none_not_an_exception(self):
        def _failing(url: str) -> str:
            raise OSError("no route to host")

        assert resolve_channel_id("someleague", fetch=_failing) is None
        assert resolve_channel_id("", fetch=_failing) is None


RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <yt:videoId>rsS00000001</yt:videoId>
    <title>Club A v Club B |Nov 12</title>
    <published>2026-11-10T08:00:00+00:00</published>
  </entry>
  <entry>
    <yt:videoId>tab00000001</yt:videoId>
    <title>Club C v Club D |Nov 9</title>
    <published>2026-11-08T08:00:00+00:00</published>
  </entry>
</feed>"""

TAB_HTML = (
    '{"videoId":"tab00000001"} filler '
    '{"videoId":"tab00000002"} filler {"videoId":"rsS00000001"}'
)

WATCH_UPCOMING = (
    '{"videoDetails":{"videoId":"tab00000002","title":"Club E v Club F |Nov 13"},'
    '"liveBroadcastDetails":{"isLiveNow":false,'
    '"startTimestamp":"2026-11-13T18:00:00+00:00"}}'
)


def _fake_fetch(mapping: dict) -> object:
    def _fetch(url: str) -> str:
        for needle, body in mapping.items():
            if needle in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(f"unexpected fetch: {url}")

    return _fetch


class TestDiscover:
    def _mapping(self) -> dict:
        return {
            "videos.xml": RSS_XML,
            "/streams": TAB_HTML,
            "watch?v=rsS00000001": WATCH_UPCOMING.replace(
                "tab00000002", "rsS00000001"
            ).replace("Club E v Club F |Nov 13", "Club A v Club B |Nov 12"),
            "watch?v=tab00000001": WATCH_UPCOMING.replace(
                "tab00000002", "tab00000001"
            ).replace("Club E v Club F |Nov 13", "Club C v Club D |Nov 9"),
            "watch?v=tab00000002": WATCH_UPCOMING,
        }

    def test_union_covers_rss_and_tab_only_ids_exactly_once(self):
        result = discover(
            "enbleague",
            channel_id="UC4nW9T_6d6e4DKa4A9_3Ouw",
            fetch=_fake_fetch(self._mapping()),
            max_watch=0,
        )
        ids = [video["video_id"] for video in result["videos"]]
        assert ids == ["rsS00000001", "tab00000001", "tab00000002"]
        assert all(video["url"].endswith(video["video_id"]) for video in result["videos"])

    def test_enrichment_fills_scheduled_start_and_the_cap_holds(self):
        result = discover(
            "enbleague",
            channel_id="UC4nW9T_6d6e4DKa4A9_3Ouw",
            fetch=_fake_fetch(self._mapping()),
            max_watch=1,
        )
        by_id = {video["video_id"]: video for video in result["videos"]}
        assert by_id["rsS00000001"]["scheduled_start"] == "2026-11-13T18:00:00+00:00"
        assert by_id["tab00000001"]["scheduled_start"] is None
        assert by_id["rsS00000001"]["title"] == "Club A v Club B |Nov 12"

    def test_a_failed_watch_fetch_keeps_the_lead_with_its_rss_title(self):
        mapping = dict(self._mapping())
        mapping["watch?v=rsS00000001"] = OSError("reset by peer")
        result = discover(
            "enbleague",
            channel_id="UC4nW9T_6d6e4DKa4A9_3Ouw",
            fetch=_fake_fetch(mapping),
            max_watch=8,
        )
        by_id = {video["video_id"]: video for video in result["videos"]}
        assert by_id["rsS00000001"]["title"] == "Club A v Club B |Nov 12"
        assert by_id["rsS00000001"]["scheduled_start"] is None

    def test_an_unresolved_handle_discovers_nothing(self):
        def _failing(url: str) -> str:
            raise OSError("no route to host")

        assert discover("nosuchleague", fetch=_failing)["videos"] == []


class TestCli:
    def _run_main(self, monkeypatch, argv, mapping) -> int:
        monkeypatch.setattr(sys, "argv", ["youtube_tab.py", *argv])
        monkeypatch.setattr(
            youtube_tab, "fetch_text", _fake_fetch({"videos.xml": RSS_XML, **mapping})
        )
        with pytest.raises(SystemExit) as excinfo:
            youtube_tab.main()
        return excinfo.value.code

    def test_handle_with_videos_exits_zero(self, monkeypatch, capsys):
        mapping = {
            "@enbleague/streams": TAB_HTML,
            "watch?v=": WATCH_UPCOMING,
        }
        code = self._run_main(monkeypatch, ["--handle", "enbleague"], mapping)
        assert code == 0
        assert "enbleague" in capsys.readouterr().out

    def test_json_mode_is_parseable(self, monkeypatch, capsys):
        mapping = {
            "@enbleague/streams": TAB_HTML,
            "watch?v=": WATCH_UPCOMING,
        }
        code = self._run_main(
            monkeypatch, ["--handle", "enbleague", "--json", "--max-watch", "1"], mapping
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["channels"][0]["handle"] == "enbleague"

    def test_unresolvable_handle_exits_one(self, monkeypatch, capsys):
        def _failing(url: str) -> str:
            raise OSError("no route to host")

        monkeypatch.setattr(sys, "argv", ["youtube_tab.py", "--handle", "nosuchleague"])
        monkeypatch.setattr(youtube_tab, "fetch_text", _failing)
        with pytest.raises(SystemExit) as excinfo:
            youtube_tab.main()
        assert excinfo.value.code == 1
        assert "no videos discovered" in capsys.readouterr().err

    def test_invalid_now_exits_two(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["youtube_tab.py", "--handle", "enbleague", "--now", "soon"]
        )
        with pytest.raises(SystemExit) as excinfo:
            youtube_tab.main()
        assert excinfo.value.code == 2


class TestHandlesFromUrls:
    def test_handle_urls_are_extracted_lowercased(self):
        from scripts.youtube_tab import handles_from_urls

        assert handles_from_urls(
            ["https://www.youtube.com/@New-Club/live", "https://example.com/x"]
        ) == [{"handle": "new-club"}]

    def test_channel_urls_yield_ids(self):
        from scripts.youtube_tab import handles_from_urls

        assert handles_from_urls(
            ["https://www.youtube.com/channel/UC4nW9T_6d6e4DKa4A9_3Ouw/videos"]
        ) == [{"channel_id": "UC4nW9T_6d6e4DKa4A9_3Ouw"}]

    def test_bare_watch_urls_yield_nothing(self):
        from scripts.youtube_tab import handles_from_urls

        assert (
            handles_from_urls(["https://www.youtube.com/watch?v=abcdefghijk"])
            == []
        )


class TestDiscoverHandlesFromUrls:
    def test_approved_handles_are_skipped(self):
        from scripts.youtube_tab import discover_handles_from_urls

        def _boom(url: str) -> str:
            raise AssertionError(f"must not fetch: {url}")

        out = discover_handles_from_urls(
            ["https://www.youtube.com/@FIBA/live"],
            known_handles=["fiba"],
            fetch=_boom,
        )
        assert out == []

    def test_new_handle_is_discovered_with_max_watch_zero(self):
        from scripts.youtube_tab import discover_handles_from_urls

        rss = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns:atom="http://www.w3.org/2005/Atom"'
            ' xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
            "<atom:entry><yt:videoId>abcdefghijk</yt:videoId>"
            "<atom:title>Club A vs Club B LIVESTREAM</atom:title>"
            "<atom:published>2026-11-13T10:00:00+00:00</atom:published>"
            "</atom:entry></feed>"
        )

        def _fetch(url: str) -> str:
            if "channel_id" in url or "videos.xml" in url:
                return rss
            if url.startswith("https://www.youtube.com/@"):
                return (
                    "<html><head><title>x</title></head><body>"
                    '"browseId":"UCAAAAAAAAAAAAAAAAAAAAAA"</body></html>'
                )
            raise AssertionError(f"unexpected fetch: {url}")

        out = discover_handles_from_urls(
            ["https://www.youtube.com/@new-club/live"],
            known_handles=["fiba"],
            fetch=_fetch,
        )
        assert len(out) == 1
        assert out[0]["videos"][0]["video_id"] == "abcdefghijk"

    def test_channel_id_url_uses_rss_not_tab(self):
        from scripts.youtube_tab import discover_by_id

        seen: list[str] = []

        def _fetch(url: str) -> str:
            seen.append(url)
            return "<feed/>"

        out = discover_by_id("UC4nW9T_6d6e4DKa4A9_3Ouw", fetch=_fetch)
        assert out["channel_id"] == "UC4nW9T_6d6e4DKa4A9_3Ouw"
        assert out["videos"] == []
        assert seen == [
            "https://www.youtube.com/feeds/videos.xml?channel_id=UC4nW9T_6d6e4DKa4A9_3Ouw"
        ]

    def test_max_new_bounds_the_discovery(self):
        from scripts.youtube_tab import discover_handles_from_urls

        calls: list[str] = []

        def _fetch(url: str) -> str:
            calls.append(url)
            return "<html>no ids here</html>"

        out = discover_handles_from_urls(
            [
                "https://www.youtube.com/@club-one/live",
                "https://www.youtube.com/@club-two/live",
            ],
            fetch=_fetch,
            max_new=1,
        )
        assert out == []
        assert sum(1 for u in calls if "/@club-two/" in u) == 0
