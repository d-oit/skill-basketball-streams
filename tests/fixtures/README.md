# Test fixtures

> **Two subdirectories are recorded corpora, not hand-authored fixtures:**
>
> - **`pages/`** — real HTTP responses captured from the live web, pinning the
>   stream-evidence gate in `scripts/render_ladder.py`. See
>   [`pages/README.md`](pages/README.md).
> - **`composio/`** — the real argument schema of every Google Calendar tool
>   `scripts/calendar_io.py` calls, pinning what the transport may send. See
>   [`composio/README.md`](composio/README.md).
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
the answer back.

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
| `audit_events.json` | hand-written (safe) | JSONL event ledger (not JSON — the telemetry format is the documented input) |
| `audit_evidence.json` | hand-written (safe) | Evidence covering all three verdicts: `WRONG`, promotion to `VERIFIED`, and `INCONCLUSIVE` |
| `candidates_ledger.jsonl` | hand-written (safe) | Two-row recall ledger so `candidates.py recall` has a non-zero denominator in CI |
| `synthesise_verdicts.jsonl` | hand-written (safe) | Audit verdicts covering all three misjudgement classes (paid, never-live, over-claim) plus one clean row that must be filtered out |
| `league_fixtures.jsonl` | hand-written (safe) | Three league fixtures, one of which appears in no ledger row — so Phase 5's `unseen` join has a non-zero answer |
| `candidates_ledger_runs.jsonl` | hand-written (safe) | Three runs over the same two games with recall 0.000 → 0.500 → 1.000, so the trend's direction is pinned rather than asserted |
| `audit_runs.jsonl` | hand-written (safe) | Audit rows for two runs, one of which has **no** candidate rows — pins that a run present only in the audit stream still appears in the trend |
| `fixtures_page_bbl.html` | hand-written (safe) | A league page carrying JSON-LD `SportsEvent` nodes, a microdata block the JSON-LD path must ignore, one event with no `startDate`, and one node that is not an event |
| `fixtures_page_microdata.html` | hand-written (safe) | The same idea with **no** JSON-LD, so the microdata fallback is exercised |
| `agent_transcript_sample.json` | hand-written (safe) | An **envelope sample** for `extract_candidates.py` — not a captured run. It is an input to the extractor, not a grading fixture, and must never be used to grade the skill |

Unlike transcripts, a *candidate input* fixture is a legitimate hand-written
test input — it feeds the gate rather than asserting the gate's own output. The
distinction matters: inputs may be authored, outputs must be captured.

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
