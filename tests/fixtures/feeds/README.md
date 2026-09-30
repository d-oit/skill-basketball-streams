# Recorded feed corpus — the iCalendar fixture source

Real iCalendar responses, recorded once, that pin the reader in
[`scripts/fixtures.py`](../../../scripts/fixtures.py) (`parse_ics` and its
helpers). They exist for the reason [`../pages/`](../pages/README.md) exists: a
feed's `SUMMARY` format is a **provider's output**, so a fixture standing in for
one would encode this repository's guess at that format and prove the guess.

Recorded: **2026-09-30** against `https://api.basketball-bundesliga.de/calendar/ical/all-games`
(HTTP 200, 90,568 bytes, 321 `VEVENT`s, no credential).

## The file

| File | Source | Events kept / in response |
|---|---|---|
| `bbl_all_games.ics` | the league's own `all-games` calendar | 7 / 321 |

`manifest.json` records the source URL, HTTP status, **full response byte length
and SHA-256**, the event count, the selection rule, and — for the stored
payload — its byte length and `stored_sha256`, plus one row per kept event (UID,
unfolded summary, which combination it fills).

`scripts/record_feeds.py --check` is the gate, and
`tests/test_ics.py::TestRecordedOfficialFeed::test_the_capture_is_the_recorded_bytes`
asserts the manifest's URL is the URL `DEFAULT_SOURCES["bbl"]` actually fetches.
A fixture that was quietly edited to agree with a weakened reader would otherwise
keep classifying correctly, and nothing else would notice.

## Why trimmed, and what the trim keeps

Seven events out of 321, chosen to cover every
(**competition prefix** × **folded `SUMMARY`** × **non-ASCII**) combination the
response contains. Trimmed rather than synthesised: the properties below are
what the live response sent, which is the part a hand-written fixture would have
had to guess. The reduction is stated in the manifest so a reviewer can tell
"we kept three games" from "the feed has three games".

The format properties this corpus pins — all measured across all 321 events,
2026-09-30, not inferred from a sample:

| Property | Measurement |
|---|---|
| calendar name | `NAME` and `X-WR-CALNAME`: `easyCredit BBL - Spielplan` |
| declared zone | `X-WR-TIMEZONE:Europe/Berlin` and `TIMEZONE-ID:Europe/Berlin` |
| `DTSTART` form | **floating** — `DTSTART:20260910T183000`, never `Z`, never `TZID` |
| `SUMMARY` shape | 321/321 carry ` vs `; 0 right-hand sides carry a comma or trailing context |
| competition prefixes | exactly two, all left-hand side: `easyCredit BBL Spiel` (306), `BBL Pokal Spiel` (15) |
| `STATUS` | absent on all 321 — the refusal path is covered by the synthetic feed instead |
| properties present | `UID`, `SEQUENCE`, `DTSTAMP`, `DTSTART`, `DTEND`, `SUMMARY`, `LOCATION`, `DESCRIPTION` (321 each) |

The two prefixes are the reason this corpus could not be skipped. Left
unstripped, every club becomes `easyCredit BBL Spiel ALBA BERLIN`: the
`game_key` stops matching what search surfaces, and BBL reports **zero recall
while looking healthy** — a fixture set that is entirely wrong and entirely
green.

## The synthetic feed next door

`../fixtures_feed_bbl.ics` is **hand-authored**, and stays that way. It is an
*input*, not a captured response, and it exists to author the cases the live
feed happens not to contain: an all-day `VALUE=DATE` event, a `STATUS:CANCELLED`
event, a comma after the pair, a `VTIMEZONE` carrying a 1970 `DTSTART`. None of
those appear in the real response, so a fixture claiming to be a capture of it
would be false. The two files answer different questions — "is the format read
right?" and "are the refusals refusals?" — and `tests/test_ics.py` asks each of
its own.

## Refreshing

```bash
python3 scripts/record_feeds.py --refresh   # re-fetch, rewrite payload + manifest
python3 scripts/record_feeds.py --check     # gate: bytes and readability
python3 scripts/record_feeds.py --list      # what the corpus covers
```

`--check` is also a `validate.yml` step and the `feed-corpus` sensor. It does two
things, and the second is the one that matters: it re-derives the stored bytes
against the manifest's SHA-256, **and** it asks `parse_ics` to read the payload.
A hash proves the bytes are *unchanged*; only the reader can prove they are
*readable*. That distinction was not theoretical — the first version of the
recorder stripped `BEGIN:VEVENT`/`END:VEVENT` while selecting events, so the
corpus held seven events with no envelope at all. It parsed to nothing, and
`--check` was green, because the bytes it had recorded were exactly the wrong
bytes. `tests/test_record_feeds.py::test_a_payload_no_reader_can_parse_is_refused`
is that mutation kept as a guard.

There is no `--json` and no verdict FLIP reporting, unlike `../pages/`. The page
corpus has **seven** URLs whose verdicts must stay comparable week to week,
which is what a FLIP report is for. This has one URL, and its payload is trimmed
against a selection rule stated in the manifest rather than against a verdict, so
there is nothing to compare — a re-record that changed the event count fails the
manifest assertion and is read by a human instead.