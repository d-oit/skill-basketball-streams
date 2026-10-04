# The Calendar Decision Log (`telemetry/calendar-log.jsonl`)

One JSONL row per **game the planner considered**, including the rows nothing is
sent for. `telemetry/events.jsonl` records the events that reached the calendar
(`create`/`update` only); `.tmp/plan.json` holds every decision but is scratch in
the runner's working directory and is discarded when the job ends. Between the
two, a `skip` or an `unchanged` row was recorded **nowhere**, and neither was the
planner's own `reason` for it ("verified event exists", "audit verdict WRONG
stands", "refreshed unverified event"). This artifact is the missing record.

## Writer

`scripts/calendar_log.py record --plan .tmp/plan.json --applied .tmp/applied.json
--existing .tmp/existing.json --dest .tmp --run-id <ISO>`.

It runs in the `runtime` job of `.github/workflows/runtime-daily.yml`, right
after "Record what was written (Phase 3 input)". That job holds the calendar
credential and therefore deliberately holds **no `contents: write`**, so it
cannot publish anything itself. It writes `.tmp/calendar-log.jsonl` and hands it
over in the `run-ledger` artifact; the `audit` job — the job that holds the
branch-publishing permission — appends the run's rows to the branch copy and
commits them. The append is explicit rather than a download-overwrite, because
this file is append-only across runs.

## Schema

| Field | Meaning |
|---|---|
| `ts` | When the row was written (ISO-8601, UTC). |
| `run_id` | The run's id (`2026-10-04T08:30Z`). |
| `game_key` | The planner's identity for the game. |
| `event_id` | The stored calendar event, or `""`. Never invented: a create in a dry run has none, while a skip, an unchanged row, or a dry-run update carries the id the planner read back from the calendar and matched. |
| `action` | `create`, `update`, `skip` or `unchanged` — the action actually taken. |
| `reason` | The planner's own reason string, copied verbatim. It is not re-derived here. |
| `state` | The normalised verification state the decision is about. |
| `league`, `teams`, `start`, `end` | The decided payload (from the applied body where one exists, so a filled-in default end is recorded as sent). |
| `dry_run` | Taken from the apply result. A `true` row is a plan, not a write. |
| `fields_changed` | Payload fields (`summary`, `state`, `color_id`, `start`, `end`, `teams`, `links`) that differ from the stored event named by `--existing`. A list may be empty — "compared and nothing differs". `null` means **no comparison was made** (no `--existing` snapshot, or no stored event for a create), which is not the same claim. |
| `links` | The link fields the plan row carries, in `stream_links.normalise_links`' shape. `{}` when it carries none. |

## Readers

* Link revalidation: the `links` field records what a later pass should
  re-check, alongside `telemetry/candidates.jsonl` and `telemetry/events.jsonl`.
* The job summary renders it with `--markdown`.
* `telemetry/events.jsonl` remains the audit's input; this log is **not** a
  verdict ledger and never feeds `audit_events`. It is the record of decisions,
  which is what makes a quiet day distinguishable from a broken one.

## Retention

Append-only, one JSON object per line, opened in append mode. A re-run of the
same `run_id` appends a second row rather than rewriting the first, so the file
is the evidence trail and is never pruned. It lives on the orphan `telemetry`
branch, not in the repository's `logs/` directory (which `.gitignore` excludes —
a log written there is invisible to every CI job).
