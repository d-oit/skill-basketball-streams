"""The calendar transport's argument contract, checked against recorded schemas.

`tests/test_calendar_io.py` pins the *parsing* of what Composio returns using
hand-built envelopes. That is necessary and not sufficient: a hand-built fixture
supplies whatever the test author believed the API sends, so a review of those
tests cannot tell you whether the argument names go anywhere. This file closes
that gap in the only direction that is checkable offline — every argument
`calendar_io.py` **sends** must be a real property of the tool it is sent to,
according to the provider's own schema, recorded in
`tests/fixtures/composio/tool_schemas.json` (see the README beside it).

Two of those assertions are load-bearing beyond naming:

* `CREATE_EVENT` has **no `color_id`** property, which is the whole reason a
  coloured create is two calls. A test that only asserted "we send `color_id`"
  would pass against a hand-built fixture and fail against the provider.
* `visibility` **is** a property of both event tools, which is what makes the
  setting in `config/calendar.json` enforceable rather than aspirational.

The arguments are obtained by *calling* the mappers rather than by restating
their names, so this cannot drift from the code it describes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import calendar_io
from scripts.calendar_io import (
    ACTION_CREATE,
    TOOL_CREATE_EVENT,
    TOOL_EVENTS_LIST,
    TOOL_PATCH_EVENT,
    build_event_body,
    create_arguments,
    patch_arguments,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = REPO_ROOT / "tests" / "fixtures" / "composio" / "tool_schemas.json"

# The slug the audit's cleanup path uses; it is in the corpus because a live
# smoke test needs it, and a tool nobody checks is a tool that can vanish.
TOOL_DELETE_EVENT = "GOOGLECALENDAR_DELETE_EVENT"


@pytest.fixture(scope="module")
def schemas() -> dict:
    return json.loads(SCHEMAS.read_text(encoding="utf-8"))["tools"]


def _properties(schemas: dict, slug: str) -> set[str]:
    assert slug in schemas, f"{slug} is not in the recorded corpus"
    return set(schemas[slug]["properties"])


def _row(**overrides) -> dict:
    row = {
        "action": ACTION_CREATE,
        "game_key": "BBL|A|B|2026-09-16T17:00Z",
        "event_id": "",
        "state": "UNVERIFIED",
        "title": "[UNVERIFIED] BBL: A vs B",
        "color_id": "5",
        "start": "2026-09-16T19:00:00+02:00",
        "end": "",
    }
    row.update(overrides)
    return row


def _create_args(**kwargs) -> dict:
    body = build_event_body(_row(), visibility=kwargs.pop("visibility", "public"))
    return create_arguments(body, kwargs.pop("calendar_id", "cal"), "Europe/Berlin")


def _patch_args(**kwargs) -> dict:
    body = build_event_body(
        _row(color_id=kwargs.pop("color_id", "5")),
        visibility=kwargs.pop("visibility", "public"),
    )
    return patch_arguments(body, "cal", "evt-1", "Europe/Berlin")


class TestEveryArgumentSentIsAccepted:
    def test_create_sends_only_real_properties(self, schemas):
        sent = set(_create_args())
        unknown = sent - _properties(schemas, TOOL_CREATE_EVENT)
        assert not unknown, (
            f"create_arguments sends {sorted(unknown)}, which "
            f"{TOOL_CREATE_EVENT} does not accept — those values go nowhere"
        )

    def test_patch_sends_only_real_properties(self, schemas):
        sent = set(_patch_args())
        unknown = sent - _properties(schemas, TOOL_PATCH_EVENT)
        assert not unknown, f"patch_arguments sends {sorted(unknown)}"

    def test_the_read_path_sends_only_real_properties(self, schemas, monkeypatch):
        """`list_events` builds its arguments inline, so page one is asserted.

        The fake returns a `nextPageToken` on its first call and none on the
        second, so the pagination loop terminates: a fake that always offered a
        token would spin forever rather than fail, which is a worse test than none.
        """
        accepted = _properties(schemas, TOOL_EVENTS_LIST)
        seen: list[dict] = []

        def fake(slug, arguments, *, api_key, scope, timeout=30):
            assert slug == TOOL_EVENTS_LIST
            seen.append(arguments)
            if len(seen) == 1:
                return {"items": [], "nextPageToken": "p2"}
            return {"items": []}

        monkeypatch.setattr(calendar_io, "_execute_tool", fake)
        events = calendar_io.list_events(
            "k", {"user_id": "u"}, "cal", time_min="A", time_max="B"
        )
        assert events == []
        assert len(seen) == 2, "expected the paginating call then the terminal one"
        for arguments in seen:
            unknown = set(arguments) - accepted
            assert not unknown, f"list_events sends {sorted(unknown)}"
        # The second page's `pageToken` is the one argument that only the
        # paginating call sends, so it is asserted rather than assumed.
        assert "pageToken" not in seen[0]
        assert seen[1]["pageToken"] == "p2"


class TestTheTwoFactsTheTransportIsBuiltOn:
    def test_create_event_really_has_no_color_id(self, schemas):
        """The reason a coloured create is two calls, not an assumption about it."""
        assert "color_id" not in _properties(schemas, TOOL_CREATE_EVENT)

    def test_patch_event_really_has_color_id(self, schemas):
        assert "color_id" in _properties(schemas, TOOL_PATCH_EVENT)

    def test_create_does_not_send_the_colour_it_cannot_set(self):
        assert "color_id" not in _create_args()

    def test_visibility_is_real_on_both_event_tools(self, schemas):
        for slug in (TOOL_CREATE_EVENT, TOOL_PATCH_EVENT):
            assert "visibility" in _properties(schemas, slug), slug

    def test_visibility_is_actually_sent_to_both(self):
        assert _create_args(visibility="public")["visibility"] == "public"
        assert _patch_args(visibility="public")["visibility"] == "public"

    def test_the_meeting_room_and_attendee_defaults_are_the_providers_claim(self, schemas):
        """Explicit `False`/`True` is only justified if the defaults really invert.

        These two were the subtlest defect on this path: a throwaway event came
        back with a Meet link and the connected user as an attendee, and the tool
        reported success. Pinning the *provider's own description* is what keeps
        the arguments from being dropped as redundant.
        """
        properties = schemas[TOOL_CREATE_EVENT]["properties"]
        assert "Defaults to True" in properties["create_meeting_room"]["description"]
        assert "Default is Fa" in properties["exclude_organizer"]["description"]
        arguments = _create_args()
        assert arguments["create_meeting_room"] is False
        assert arguments["exclude_organizer"] is True


class TestRequiredArgumentsAreSatisfied:
    def test_create_satisfies_every_required_property(self, schemas):
        required = set(schemas[TOOL_CREATE_EVENT]["required"])
        assert required, "the recorded schema declares no required property"
        assert required <= set(_create_args()), sorted(required - set(_create_args()))

    def test_patch_satisfies_every_required_property(self, schemas):
        required = set(schemas[TOOL_PATCH_EVENT]["required"])
        assert required <= set(_patch_args()), sorted(required - set(_patch_args()))

    def test_calendar_id_is_not_required_which_is_why_it_is_always_sent(self, schemas):
        """The sharpest fact in the corpus, and the opposite of what it looks like.

        `start_datetime` is the *only* required property of `CREATE_EVENT` —
        `calendar_id` is optional, and its description says `primary` is the
        recommended value. So omitting it does not fail: it silently writes to
        the user's primary calendar. This repository always passes an explicit
        id, and this assertion is why that must not be "simplified" away as
        redundant.
        """
        assert set(schemas[TOOL_CREATE_EVENT]["required"]) == {"start_datetime"}
        assert "calendar_id" not in schemas[TOOL_CREATE_EVENT]["required"]
        assert "primary" in schemas[TOOL_CREATE_EVENT]["properties"]["calendar_id"][
            "description"
        ]
        assert _create_args()["calendar_id"] == "cal"


class TestTheCorpusItselfIsSound:
    def test_every_recorded_tool_belongs_to_the_calendar_toolkit(self, schemas):
        for slug, entry in schemas.items():
            assert slug.startswith("GOOGLECALENDAR_"), slug
            assert entry["toolkit"] == "googlecalendar", slug

    def test_the_tools_the_module_calls_are_all_recorded(self, schemas):
        called = {
            value
            for name, value in vars(calendar_io).items()
            if name.startswith("TOOL_") and isinstance(value, str)
        }
        assert called <= set(schemas), sorted(called - set(schemas))

    def test_every_recorded_tool_records_a_resolved_version(self, schemas):
        """A schema with no version cannot be told apart from a remembered one."""
        for slug, entry in schemas.items():
            assert entry["version"], slug
            assert entry["properties"], slug

    def test_the_module_asks_for_the_version_this_was_recorded_at(self, schemas):
        versions = {entry["version"] for entry in schemas.values()}
        assert len(versions) == 1, versions
        # The corpus records one resolved version; the module asks for `latest`,
        # which is a deliberate choice (a pin can drop timeMin/timeMax) and is
        # pinned there. Asserting the two disagree is the point: this file must
        # not be read as a pin.
        assert calendar_io.COMPOSIO_TOOLKIT_VERSION == "latest"
        assert next(iter(versions)) not in calendar_io.COMPOSIO_TOOLKIT_VERSION

    def test_the_fixture_stays_small_enough_to_commit(self):
        assert SCHEMAS.stat().st_size < 60_000, SCHEMAS.stat().st_size
