"""Tests for scripts/stream_links.py — the link vocabulary, writer and reader.

The failures worth pinning here are the ones the module exists to prevent: a
reader and a writer that disagree, and a parser that reads a source name out of a
URL's own `://`.
"""
from __future__ import annotations

from scripts.stream_links import (
    links_from_description,
    normalise_links,
    render_block,
    source_reference_from_description,
    validated_at_from_description,
    validation_notes_from_description,
)

FULL = {
    "directLink": "https://www.championsleague.basketball/live/tenerife-vs-bonn",
    "sourceReference": "https://www.championsleague.basketball/",
    "access": "free, no login",
    "validationTimestamp": "2026-09-17T08:00:00Z",
    "validationNotes": "free-game marker on the league page",
}


class TestNormalise:
    def test_the_documented_camel_case_candidate_is_understood(self):
        fields = normalise_links(FULL)
        assert fields["direct_links"] == [
            {
                "source": "",
                "url": "https://www.championsleague.basketball/live/tenerife-vs-bonn",
            }
        ]
        assert fields["source_reference"] == "https://www.championsleague.basketball/"
        assert fields["validated_at"] == "2026-09-17T08:00:00Z"
        assert fields["access"] == "free, no login"
        assert fields["validation_notes"] == "free-game marker on the league page"

    def test_the_planners_snake_case_row_is_understood(self):
        assert normalise_links(
            {"direct_link": "https://a.example/live", "source_reference": "https://a.example/"}
        ) == {
            "direct_links": [{"source": "", "url": "https://a.example/live"}],
            "source_reference": "https://a.example/",
        }

    def test_a_list_of_links_keeps_its_source_labels(self):
        fields = normalise_links(
            {
                "directLinks": [
                    {"source": "magenta.tv", "url": "https://www.magenta.tv/tv/live-1"},
                    "https://www.youtube.com/@fiba/live",
                ]
            }
        )
        assert fields["direct_links"] == [
            {"source": "magenta.tv", "url": "https://www.magenta.tv/tv/live-1"},
            {"source": "", "url": "https://www.youtube.com/@fiba/live"},
        ]

    def test_nothing_is_invented_for_a_row_with_no_links(self):
        assert normalise_links({"league": "BBL", "teams": ["A", "B"]}) == {}
        assert normalise_links({}) == {}
        assert normalise_links("junk") == {}  # type: ignore[arg-type]

    def test_an_unusable_entry_is_dropped_rather_than_recorded_empty(self):
        assert normalise_links({"directLinks": [{"source": "x"}, "", None]}) == {}

    def test_duplicate_links_collapse(self):
        fields = normalise_links(
            {"directLinks": ["https://a.example/live", "https://a.example/live"]}
        )
        assert fields["direct_links"] == [{"source": "", "url": "https://a.example/live"}]

    def test_it_is_idempotent(self):
        assert normalise_links(normalise_links(FULL)) == normalise_links(FULL)


class TestRender:
    def test_a_row_with_no_link_information_renders_nothing(self):
        assert render_block({"league": "BBL"}) == []

    def test_the_sections_follow_the_documented_template(self):
        assert render_block(FULL) == [
            "",
            "FREE STREAM LINKS:",
            "- https://www.championsleague.basketball/live/tenerife-vs-bonn",
            "",
            "Access: free, no login",
            "",
            "SOURCE REFERENCE:",
            "- Found at: https://www.championsleague.basketball/",
            "- Validated: 2026-09-17T08:00:00Z",
            "- Validation Notes: free-game marker on the league page",
        ]

    def test_a_named_source_is_rendered_as_the_label(self):
        assert "- magenta.tv: https://www.magenta.tv/tv/live-1" in render_block(
            {
                "directLinks": [
                    {"source": "magenta.tv", "url": "https://www.magenta.tv/tv/live-1"}
                ]
            }
        )

    def test_a_source_reference_alone_still_renders_its_section(self):
        assert render_block({"sourceReference": "https://a.example/"}) == [
            "",
            "SOURCE REFERENCE:",
            "- Found at: https://a.example/",
        ]


class TestParse:
    def test_a_bare_bullet_does_not_read_its_scheme_as_the_source(self):
        # `- https://…` split on ":" would name the source `https`, which is a
        # label nothing can act on.
        assert links_from_description(
            "FREE STREAM LINKS:\n- https://a.example/live\n"
        ) == [{"source": "", "url": "https://a.example/live"}]

    def test_a_labelled_bullet_keeps_its_source(self):
        assert links_from_description(
            "FREE STREAM LINKS:\n- magenta.tv: https://m.example/live\n"
        ) == [{"source": "magenta.tv", "url": "https://m.example/live"}]

    def test_a_bullet_list_outside_the_section_is_not_a_link_list(self):
        # Why the header is required rather than every bullet being harvested:
        # an event description is prose, and any list in it would otherwise read
        # as stored stream links.
        assert links_from_description("- https://a.example/live\n") == []

    def test_only_the_link_section_is_read(self):
        text = (
            "League: BBL\n"
            "\nFREE STREAM LINKS:\n"
            "- https://a.example/live\n"
            "\nAccess: free\n"
            "\nSOURCE REFERENCE:\n"
            "- Found at: https://b.example/page\n"
        )
        assert links_from_description(text) == [
            {"source": "", "url": "https://a.example/live"}
        ]
        assert source_reference_from_description(text) == "https://b.example/page"

    def test_the_list_ends_at_the_next_section(self):
        text = "FREE STREAM LINKS:\n- https://a.example/live\nAccess: free\n- not-a-link\n"
        assert links_from_description(text) == [
            {"source": "", "url": "https://a.example/live"}
        ]

    def test_the_source_block_readers_agree_with_the_writer(self):
        text = "\n".join(render_block(FULL))
        assert source_reference_from_description(text) == FULL["sourceReference"]
        assert validated_at_from_description(text) == FULL["validationTimestamp"]
        assert validation_notes_from_description(text) == FULL["validationNotes"]

    def test_a_description_with_no_block_reads_as_empty(self):
        assert links_from_description("") == []
        assert links_from_description(None) == []  # type: ignore[arg-type]
        assert links_from_description("League: BBL\nTeams: A vs B\n") == []
        assert source_reference_from_description("") == ""
        assert validated_at_from_description("") == ""
        assert validation_notes_from_description("") == ""

    def test_a_found_at_line_without_a_url_is_not_a_reference(self):
        assert source_reference_from_description("SOURCE REFERENCE:\n- Found at: n/a\n") == ""
