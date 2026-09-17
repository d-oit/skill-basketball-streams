# Recorded Composio tool schemas — the calendar transport's wire contract

Real API responses, recorded once, that pin the argument names `scripts/calendar_io.py`
sends to Composio and the two facts the transport is built around:

- **`CREATE_EVENT` has no `color_id` property**, so a coloured create *must* be two calls.
- **`visibility` is a real property** of both `CREATE_EVENT` and `PATCH_EVENT`, which is
  what makes the config-file setting enforceable rather than aspirational.

Neither is checkable against a hand-built fixture: a fixture that supplied `color_id`
would prove the test, not the tool. This is the same rule `tests/fixtures/README.md`
states — *inputs may be authored, outputs must be captured* — and a schema is an output.

Recorded: **2026-09-15**, read-only, by hand (see *Refreshing* below). Consumed by
`tests/test_composio_contract.py`.

## What was recorded

`GET /api/v3.1/tools/{slug}` for the five tools this repository calls, reduced to what the
assertions need: the property names and types, the `required` list, the toolkit slug, and
the resolved toolkit `version`.

| File | Tool | Why it is here |
|---|---|---|
| `tool_schemas.json` | `GOOGLECALENDAR_EVENTS_LIST` | the read path's argument names (`calendarId`, `timeMin`, `timeMax`, …) |
| | `GOOGLECALENDAR_CREATE_EVENT` | proves `color_id` is absent and `visibility` is present |
| | `GOOGLECALENDAR_PATCH_EVENT` | proves `color_id` is present here and nowhere else |
| | `GOOGLECALENDAR_DELETE_EVENT` | the tool used to clean up after a live smoke test |
| | `GOOGLECALENDAR_EVENTS_GET` | reads one event back, raw, to check what was stored |

**Full descriptions are stored, not summarised.** `color_id`'s absence is only meaningful
alongside the reason: the `create_meeting_room` and `exclude_organizer` descriptions in this
file are the provider's own statement that both default to the opposite of what a public
streams calendar wants, which is why `create_arguments()` passes `False`/`True` explicitly
rather than relying on the default.

## Refreshing

There is no recorder script and no `--check`, deliberately. A recorder's offline mode could
only re-assert that stored bytes are well-formed, which `pytest` already does; the
interesting question — *does the provider still have these properties?* — needs the network
and a credential, so it is a deliberate, occasional act rather than a CI step.

```bash
# Re-record (read-only; no tool is executed). Requires a Composio API key.
python3 - <<'PY'
import json, os, urllib.request
from pathlib import Path

key = os.environ["COMPOSIO_API_KEY"]
def get(path):
    req = urllib.request.Request("https://backend.composio.dev/api/v3.1" + path,
                                 headers={"x-api-key": key, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

SLUGS = ["GOOGLECALENDAR_EVENTS_LIST", "GOOGLECALENDAR_CREATE_EVENT",
         "GOOGLECALENDAR_PATCH_EVENT", "GOOGLECALENDAR_DELETE_EVENT",
         "GOOGLECALENDAR_EVENTS_GET"]
recorded = {}
for slug in SLUGS:
    tool = get(f"/tools/{slug}")
    params = tool.get("input_parameters") or {}
    recorded[slug] = {
        "version": tool.get("version"),
        "toolkit": (tool.get("toolkit") or {}).get("slug"),
        "required": sorted(params.get("required") or []),
        "properties": {name: {"type": spec.get("type"),
                              "description": (spec.get("description") or "").strip()
                              .replace("\n", " ")[:160]}
                       for name, spec in sorted((params.get("properties") or {}).items())},
    }
out = Path("tests/fixtures/composio/tool_schemas.json")
old = json.loads(out.read_text())["_note"] if out.exists() else ""
out.write_text(json.dumps({"_note": old, "tools": recorded}, indent=2, sort_keys=True) + "\n")
print("flipped:", [s for s in SLUGS if s in old and
                   json.loads(out.read_text())["tools"][s] != recorded[s]])
PY
```

Re-run `pytest tests/test_composio_contract.py` afterwards. A property that **disappears**
is a real finding — the transport would start sending an argument the tool ignores, which is
exactly the failure `color_id` produced on the create path — so fix the code or the call
site, never the expectation.

## Residual risk, stated plainly

**The `CREATE_EVENT` *response* envelope is not stored here, and cannot be** without a
write. Its shape is `data.response_data` rather than `data`, which is why `_payload()` reads
both — and that nesting was verified against a live account on 2026-09-15 rather than
recorded, by creating one labelled event and reading its id back out of the applied result.
The id came back, so the envelope is right; the bytes are simply not committed, because
recording them means writing to a public calendar on a schedule. `tests/test_calendar_io.py`
pins the parsing with hand-built envelopes in both shapes, which is the strongest offline
form available and is **weaker** than a recording. Stated here rather than left implicit.

**A version bump is not a defect by itself.** `COMPOSIO_TOOLKIT_VERSION` is `latest`, so the
provider may add or rename properties at any time and the stored `version` is a record of
what was seen, not a pin. What the test asserts is the direction that matters: every argument
this repository *sends* must exist. An extra property nobody sends is fine and expected.

**A schema is not a behaviour.** The schema says `visibility` exists; it does not say the
value is stored. That was checked separately, and the schema file cannot replace the check.
