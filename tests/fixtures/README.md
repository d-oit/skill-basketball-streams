# Test fixtures

> **Two subdirectories are recorded corpora, not hand-authored fixtures:**
>
> - **`pages/`** — real HTTP responses captured from the live web, pinning the
>   stream-evidence gate in `scripts/render_ladder.py`. See
>   [`pages/README.md`](pages/README.md).
> - **`composio/`** — the real argument schema of every Google Calendar tool
>   `scripts/calendar_io.py` calls, pinning what the transport may send. See
>   [`composio/README.md`](composio/README.md).
> - **`feeds/`** — real iCalendar responses, pinning the format the BBL feed
>   actually publishes. See [`feeds/README.md`](feeds/README.md).
>
> Everything else in this directory is hand-authored. The distinction is the one
> stated below: **inputs may be authored, outputs must be captured.** A provider's
> schema and a provider's response are outputs, so a fixture that stands in for one
> would prove the test rather than the transport.

## `runtime_transcripts.json` — NOT COMMITTED YET, BY DESIGN

This file must be produced by **`scripts/capture_transcripts.py` against a real
run**. It is deliberately absent rather than hand-written: the whole point of
the transcript upgrade (see `live-stream-runtime-spec.md` §9.1) is that the
grader stops comparing against a fixture authored to pass.

```bash
# Which rungs this machine has, and which models the free key can reach.
# Spends no generate call.
python3 scripts/capture_transcripts.py --list-models

# Whole eval set, failing over across the ladder (Gemini free -> OpenCode
# Zen free -> OpenRouter free). No CLI needed for rung 1.
python3 scripts/capture_transcripts.py --runner ladder \
  --out tests/fixtures/runtime_transcripts.json

# Or the production CLI path specifically.
opencode serve --port 4096 --hostname 127.0.0.1 &
python3 scripts/capture_transcripts.py --runner opencode \
  --attach http://127.0.0.1:4096 --model "$LLM_MODEL" --format json \
  --out tests/fixtures/runtime_transcripts.json
```

Capturing requires a credential (`GEMINI_API_KEY` is the free rung) and network
access, so it cannot run in the default CI job. `--list-models` reports what is
configured and whether the key is *accepted*; `--check-rungs` is the offline gate
the scheduled run preflights on. The capture prompt states the output form only —
it does **not** quote `expected_output`, so a transcript cannot pass by echoing
the answer back. The prompt does carry each case's declared `files`, inlined:
the HTTP rungs cannot read a filesystem, and the grader matches check ids that
`references/validation-workflow.md` names on its check headings, so the
vocabulary has to travel with the question. 15 of the 38 cases also assert
tokens no document uses yet (`duplicateCheck`, `liveOnly`, `linkStatus`, …);
until those references name them too, a live capture can pass the check-only
cases but not those.

`capture_transcripts.py` exits non-zero, names every case that produced no
output, and **writes no file at all** when any case failed. That last part is
load-bearing rather than tidy: the grading gate keys on this file *existing*, so
a partial capture would satisfy the gate while covering fewer cases than the
eval set — the failure mode this fixture is most likely to cause. There is
therefore no `|| true` on that path, and none is needed: with no file present the
step reports a notice, and with one present it grades and fails hard.

Until this file exists, use the offline paths, which need no credentials:

```bash
python3 scripts/runtime_eval.py --root .                       # structural pass
python3 scripts/runtime_eval.py --root . --stubs <canned.json>  # equality-graded
python3 scripts/capture_transcripts.py --check-rungs            # ladder preflight
python3 scripts/capture_transcripts.py --runner replay --from <file> --check
python3 -m pytest tests/test_transcripts.py
```

## Other expected fixtures

| Fixture | Produced by | Purpose |
|---|---|---|
| `youtube_live_candidates.json` | hand-written (safe) | Deterministic input for the `live-gate` sensor: at least one `upcoming` candidate whose `scheduled_start` is after the pinned `--now` |
| `upsert_existing.json` | hand-written (safe) | One `VERIFIED` event, so the planner's skip path is exercised |
| `upsert_candidates.json` | hand-written (safe) | One candidate matching the verified event, one new `UNVERIFIED` game — expects `create=1 skip=1` |
| `upsert_verdict_existing.json` | hand-written (safe) | Two `UNVERIFIED` events that both candidates would promote — so the `upsert-verdicts` sensor has something to hold and something to let through |
| `upsert_verdict_candidates.json` | hand-written (safe) | Both candidates claim free access **and** a live stream, so the ledger is the only thing that can stop either `update` |
| `upsert_verdicts.jsonl` | hand-written (safe) | Audit ledger: `WRONG` for one event (with an earlier `INCONCLUSIVE` beneath it, so newest-wins is exercised) and `INCONCLUSIVE` for the other. Without it the plan is `update=2`; with it, `update=1 skip=1` |
| `upsert_unchanged_identical.json` | hand-written (safe) | A stored UNVERIFIED event already holding exactly what `build_event_body` + `description_for` would send — the case that must plan `unchanged` so `apply_plan` issues no HTTP. Pinned by `tests/test_calendar_idempotency.py`, which also builds this stored shape through the real writer and reader |
| `upsert_unchanged_title.json` | hand-written (safe) | Same game, a corrected name in the title — must be `update` |
| `upsert_unchanged_description.json` | hand-written (safe) | A new `directLinks` entry the stored description lacks — the link block is a compared field, so it must be `update` |
| `upsert_unchanged_colour.json` | hand-written (safe) | Stored UNVERIFIED event in the wrong colour — must be repainted (`update`), because the colour is a visible label |
| `upsert_unchanged_end.json` | hand-written (safe) | Stored end differs from the candidate's (start is deliberately held from the stored event) — must be `update` |
| `upsert_unchanged_legacy_description.json` | hand-written (safe) | Everything matches except an **empty** stored description — must be `update`: a blank stored field is never a wildcard for a non-blank planned one |
| `upsert_unchanged_promotion.json` | hand-written (safe) | Content matches, state moves UNVERIFIED -> VERIFIED — must be `update`: the promotion is the change |
| `audit_events.json` | hand-written (safe) | JSONL event ledger (not JSON — the telemetry format is the documented input) |
| `audit_evidence.json` | hand-written (safe) | Evidence covering all three verdicts: `WRONG`, promotion to `VERIFIED`, and `INCONCLUSIVE` |
| `candidates_ledger.jsonl` | hand-written (safe) | Two-row recall ledger so `candidates.py recall` has a non-zero denominator in CI. **Read it as a counter-example, not a model of production**: it carries a `created` row, which is exactly the field the real pipeline never wrote — the 1322-row ledger on the telemetry branch is 100 % `unverifiable` with no `game_key` at all. That gap is why `recall_seam_*` below exists; this file is the reason the defect stayed invisible |
| `recall_seam_ledger.jsonl` | hand-written from the **real** Phase 0 shape (see the 2026-09-26 ledger) | Five rows, all `unverifiable`, all `game_key: ""` — exactly what `run_daily.py` writes, because Phase 0 has a search hit and not a parsed game. One page (`pluto.tv/…6866525c…`) backs **two** rows, as in the Dyn free-games run, so the URL is genuinely ambiguous there. The defect as an executable fact: this file alone can never report `captured > 0` |
| `recall_seam_candidates.json` | hand-written (safe) | The `extract_candidates.py` output shape, and the **only** place a URL survives into the outcome (a plan row carries none — the planner is not a scraper) |
| `recall_seam_plan.json` | hand-written (safe) | Two `create` rows and one `skip`, with **no** `url` field, so the writer cannot be caught joining by a field the planner does not have |
| `recall_seam_applied.json` | hand-written (safe) | `dry_run: false` with `created: 2`. The dry-run half is written by the test itself, because "records nothing" is the property and a committed file would only ever prove one side of it |
| `synthesise_verdicts.jsonl` | hand-written (safe) | Audit verdicts covering all three misjudgement classes (paid, never-live, over-claim) plus one clean row that must be filtered out |
| `league_fixtures.jsonl` | hand-written (safe) | Three league fixtures, one of which appears in no ledger row — so Phase 5's `unseen` join has a non-zero answer |
| `candidates_ledger_runs.jsonl` | hand-written (safe) | Three runs over the same two games with recall 0.000 → 0.500 → 1.000, so the trend's direction is pinned rather than asserted |
| `audit_runs.jsonl` | hand-written (safe) | Audit rows for two runs, one of which has **no** candidate rows — pins that a run present only in the audit stream still appears in the trend |
| `fixtures_page_bbl.html` | hand-written (safe) | A league page carrying JSON-LD `SportsEvent` nodes, a microdata block the JSON-LD path must ignore, one event with no `startDate`, and one node that is not an event |
| `fixtures_page_microdata.html` | hand-written (safe) | The same idea with **no** JSON-LD, so the microdata fallback is exercised |
| `feeds/bbl_all_games.ics` | **recorded, then trimmed** — real `api.basketball-bundesliga.de/calendar/ical/all-games` response, 2026-09-30 | The feed format the league actually publishes: the competition glued onto the first club name, `DTSTART` floating with the zone declared once at calendar level, CRLF and folded `SUMMARY` lines. Trimmed to 7 of 321 events by the declared selection rule in [`feeds/manifest.json`](feeds/manifest.json); recorded and gated by `scripts/record_feeds.py`. The sibling `fixtures_feed_bbl.ics` stays hand-authored, because it is an *input* that covers cases the live feed happens not to contain (all-day, cancelled, a comma after the pair) |
| `fixtures_feed_bbl.ics` | hand-written (safe) | A synthetic feed covering the **refusals**: `VALUE=DATE`, `STATUS:CANCELLED`, a comma after the pair, a `VTIMEZONE` carrying a 1970 `DTSTART`. The real feed contains none of these, so a fixture claiming to be a capture of it would be false |
| `fixtures_page_bcl_payload.html` | **recorded, then trimmed** — real `/en/games` response, 2026-09-18 | The third parser rung in `scripts/fixtures.py`: `championsleague.basketball` publishes neither JSON-LD nor microdata (a game page's only `ld+json` node is a `BreadcrumbList`), and its game list is a Next.js flight payload. Three of that list's 136 games are kept verbatim — two scheduled, and one season placeholder (`hasTimeGameDateTime: false`, `teamA: null`) that must **not** become a fixture — plus the empty `"games": []` array the real page sends first. Its own header records source, date and reduction |
| `link_inventory_events.json` | hand-written (safe) | A three-event `calendar_io list` export (the shape that CLI writes, `{"events": […]}`): one event carrying the full documented description block — two links, a source reference, a validation timestamp — one legacy event carrying **no** block, and one whose stored link is a rejected `/channel/` YouTube shape. Pins the producer's round trip and its registry coverage, and that "the calendar has no dead links" cannot be confused with "no event had a link to check" |
| `agent_transcript_sample.json` | hand-written (safe) | An **envelope sample** for `extract_candidates.py` — not a captured run. It is an input to the extractor, not a grading fixture, and must never be used to grade the skill |
| `agent_transcript_jsonl.txt` | hand-written in the **real** shape — `opencode run --format json` stdout, 2026-09-29 | **The only shape the runtime ever produces, and it was untested.** Every other transcript fixture is a single JSON *document*; this one is a **JSONL event stream** (one object per line), which is what `runtime-daily.yml` `tee`s to `.tmp/transcript.json`. Fed undecoded, the escapes inside each event's `text` survive into the fenced block, `json.loads` rejects it, and a run whose agent emitted a perfect `{"candidates": […]}` reported **no usable candidates**. Kept as two lines so the *shape* is the point, not the content |

Unlike transcripts, a *candidate input* fixture is a legitimate hand-written
test input — it feeds the gate rather than asserting the gate's own output. The
distinction matters: inputs may be authored, outputs must be captured.

`fixtures_page_bcl_payload.html` is the one exception among the league pages,
and it is recorded for the reason above: the shape it pins is a **provider's
response** — a Next.js flight payload — so a hand-written version would encode
this repository's guess at that shape and prove the guess. It is *reduced*
rather than invented: the games in it are the ones the live response sent,
trimmed to three because the real page streams 93 chunks. The distinction is
worth keeping straight, because a trimmed capture still fails when the provider
changes, while a synthesised one quietly keeps passing.

Because CI invokes these fixtures directly
(`.github/workflows/validate.yml`), their documented outcomes are pinned in
`tests/test_fixture_contracts.py`. A fixture that silently drifts must fail
there rather than turn a CI step vacuous.

The distinction that matters, stated once more because it is easy to get wrong:
`agent_transcript_sample.json` is an **input** — it proves the extractor can read
the envelope. `runtime_transcripts.json` would be an **output**, namely what the
skill actually did, and a hand-written version of it would grade the fixture
rather than the skill. That is why the file above is absent and this one is not.

## Not a fixture: `fixtures.jsonl`

`fixtures.jsonl` (the league fixture list that `scripts/fixtures.py` writes) is a
**telemetry output** that lives on the `telemetry` branch, not here. It is named
in this note only because the near-identical name invites confusion with the
hand-written `league_fixtures.jsonl` above.

- `league_fixtures.jsonl` — authored, three rows, pins the `unseen` join in CI.
- `fixtures.jsonl` — fetched from the leagues, append-only-but-idempotent, and the
  real denominator for `fixture_recall`.

`tests/test_metrics.py` and `tests/test_candidates.py` cover the arithmetic using
the authored file, so the metric contract is checked without a telemetry branch
existing at all.
