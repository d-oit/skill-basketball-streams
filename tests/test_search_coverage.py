"""Every free live stream option must be searchable from every search surface.

The skill finds games through four independent surfaces, and a game no
surface asks for is a game the calendar can never hold:

1. `config/youtube-live-queries.json` — YouTube Live-filter searches.
2. `scripts/run_daily.py::DEFAULT_QUERIES` — Phase 0 ladder queries.
3. `references/implementation-notes.md` — the agent's canonical web queries.
4. `config/direct-lists.json` — keyless free listing pages.

These tests pin the *coverage* (which free options are asked for), not the
results (which no offline test can fetch). ProB stays excluded everywhere:
it is not free of charge, so a query promising it would manufacture
eligibility failures rather than coverage.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent


def _youtube_config() -> dict:
    return json.loads((REPO / "config" / "youtube-live-queries.json").read_text(encoding="utf-8"))


def _direct_lists() -> list[dict]:
    return json.loads((REPO / "config" / "direct-lists.json").read_text(encoding="utf-8"))["lists"]


def _approved_domains() -> set[str]:
    payload = json.loads((REPO / "config" / "sources.json").read_text(encoding="utf-8"))
    domains: set[str] = set()
    for source in payload.get("sources", []):
        for key in ("domains", "domain"):
            value = source.get(key)
            items = value if isinstance(value, list) else ([value] if value else [])
            for item in items:
                host = urlparse(f"https://{item}").netloc.lower()
                domains.add(host[4:] if host.startswith("www.") else host)
    return domains


class TestYouTubeQueries:
    def test_enough_free_options_are_asked_for(self):
        queries = _youtube_config()["queries"]
        assert len(queries) >= 20, f"expected broad free-stream coverage, got {len(queries)}"

    def test_core_leagues_and_free_options_present(self):
        lowered = [q.lower() for q in _youtube_config()["queries"]]
        for topic in ("regionalliga", "nbbl", "bbl-pokal", "dbb", "dyn sport mix", "sporteurope", "champions league"):
            assert any(topic in q for q in lowered), f"no YouTube query asks for {topic!r}"

    def test_no_paid_only_promises(self):
        lowered = [q.lower() for q in _youtube_config()["queries"]]
        assert not any("prob" in q for q in lowered), "ProB is excluded: not free of charge"

    def test_every_query_builds_a_live_filtered_url(self):
        from scripts.youtube_live import build_live_searches, LIVE_SEARCH_SP

        searches = build_live_searches(_youtube_config()["queries"])
        assert len(searches) == len(_youtube_config()["queries"])
        assert all(f"sp={LIVE_SEARCH_SP}" in row["url"] for row in searches)


class TestPhase0Queries:
    def test_default_queries_cover_youth_regional_and_free_tiers(self):
        from scripts.run_daily import DEFAULT_QUERIES

        lowered = [q.lower() for q in DEFAULT_QUERIES]
        assert not any("prob" in q for q in lowered)
        for topic in ("nbbl", "jbbl", "regionalliga", "dyn sport mix", "sporteurope", "magenta", "bbl-pokal", "nationalmannschaft"):
            assert any(topic in q for q in lowered), f"no Phase 0 query asks for {topic!r}"


class TestCanonicalQueries:
    def test_implementation_notes_match_phase0_free_options(self):
        from scripts.run_daily import DEFAULT_QUERIES

        text = (REPO / "references" / "implementation-notes.md").read_text(encoding="utf-8").lower()
        for topic in ("dyn sport mix free spiele", "sporteurope nbbl", "bbl-pokal", "nationalmannschaft", "enbl", "serie a", "dazn"):
            assert topic in text, f"canonical queries never ask for {topic!r}"
        assert len(DEFAULT_QUERIES) >= 15


class TestDirectLists:
    def test_every_free_listing_page_is_https_on_an_approved_domain(self):
        approved = _approved_domains()
        entries = _direct_lists()
        assert len(entries) >= 7, f"expected broad listing coverage, got {len(entries)}"
        for entry in entries:
            url = entry["url"]
            assert url.startswith("https://"), f"{entry['name']}: not https"
            host = urlparse(url).netloc.lower()
            host = host[4:] if host.startswith("www.") else host
            assert any(host == d or host.endswith("." + d) for d in approved), (
                f"{entry['name']}: {host} is not an approved source domain"
            )

    def test_tier2_and_free_tier_platforms_have_a_listing(self):
        urls = " ".join(entry["url"] for entry in _direct_lists())
        for host in ("ardmediathek.de", "zdf.de", "dyn.sport"):
            assert host in urls, f"no direct listing covers {host}"
