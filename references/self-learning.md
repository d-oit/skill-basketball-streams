# Self-Learning, Link Revalidation, and New-Source Discovery

The skill improves between runs without ever loosening a gate by itself. Five
loops, all backed by append-only files:

| Loop | Command | Artifact | Auto-applied? |
|---|---|---|---|
| Run log | `scripts/source_learning.py record …` | `logs/run-log.jsonl` | yes (append only) |
| Post-hoc audit | `scripts/audit_events.py --events … --evidence …` | `logs/audit.jsonl` | yes — relabels events (§6) |
| Source scoring | `scripts/source_learning.py score --root .` | stdout / `--json` | no — informs tier order |
| New-source discovery | `scripts/source_learning.py candidates --root .` | `logs/source-candidates.json` (`"status": "quarantined"`) | **no** — human approval |
| Link revalidation | `scripts/link_check.py --input links.json` | report JSON | no — quarantine only |

`logs/` is gitignored: learning data is per-installation, not repository state.

## 1. Run log

One JSONL row per candidate stream, appended at the end of every run:

```json
{"ts":"2026-09-14T09:12:00+00:00","run_id":"2026-09-14T09","source":"magenta.tv","tier":4,"url":"https://www.magenta.tv/tv/live-basketball-euroleague-88213","outcome":"reject","reason":"no matching free-access announcement on magentasport.de"}
```

`outcome` ∈ `create | skip | reject | blocked | error`. `ts`, `--now` and
`--dry-run` make the write deterministic for tests; without `--dry-run` the row
is appended and never rewritten. The log is the audit trail behind Step 7's
results table.

## 2. Source scoring

`score` turns the log into a hit rate per source:

```
OK: source_learning: championsleague.basketball: attempts=4 creates=3 hit_rate=0.75
OK: source_learning: magenta.tv: attempts=6 creates=1 hit_rate=0.167
```

Use it to re-tune the search order in `references/approved-sources.md`, with two
rules:

- **Never demote a source below a tier because of one bad run.** Require ≥10
  attempts before re-ordering.
- **Never promote an unapproved source no matter how high its hit rate.** That
  is what the candidate loop is for.

A `blocked`-heavy source (high 403/429 rate) gets a browser-rendering backend
attached in the fetch ladder rather than being dropped — see
`references/magenta-tv.md`.

## 3. New-source discovery (quarantine, never auto-promote)

`candidates` extracts every domain that produced a search hit but is absent from
`config/sources.json`, and writes them quarantined:

```json
{
  "candidates": [
    {"domain": "example-basketball.tv", "status": "quarantined",
     "hits": 3, "examples": ["https://example-basketball.tv/live/123"]}
  ]
}
```

Promotion requires **all four** of these, done by a human in a PR:

1. The domain is an official league / federation / club / public broadcaster
   (never a pirate or aggregator site).
2. Free access is documented (or "selected games" with an announcement channel).
3. `references/approved-sources.md` gains a tier row **and** the matching entry
   is added to `config/sources.json`.
4. `evals/evals.json` gains one accept case and one reject case for the new
   source (reject = its paid variant).

Until then the domain stays quarantined and any URL from it is rejected at
Check 3.

## 4. Link revalidation

Calendar links rot. Revalidate on this cadence:

| When | Action on failure |
|---|---|
| At creation (pre-flight) | never create the event |
| Daily, for events inside today…today+7d | `BROKEN`/`INVALID` → remove the dead link from the event description, append `Validation Notes: link quarantined <ISO ts>` |
| ~2 h before start | `BROKEN` → same quarantine; `BLOCKED`/`UNREACHABLE` → retry via the fetch ladder, keep the event |
| After the event ends | YouTube `/live` URLs always 404 afterwards — expected, do **not** rewrite the event |

```bash
python3 scripts/link_check.py --input links.json --out logs/link-report.json
```

Statuses and their meaning: `OK`, `BROKEN` (404/410 → quarantine the link),
`BLOCKED` (401/403/429/451 → anti-bot, climb the fetch ladder),
`ERROR` (5xx → retry, do not delete), `UNREACHABLE` (network), `INVALID`
(malformed or a rejected YouTube shape).

The skill never deletes a calendar event on a link failure. It quarantines the
link and records the reason, so a past event still documents what was broadcast.

## 5. Post-hoc audit and promotion sweep

The audit runs the **next day**, over events that have finished. A false positive
can only be confirmed after the broadcast ends, so this is the only place a
verdict is trustworthy.

```bash
python3 scripts/audit_events.py \
  --events telemetry/events.jsonl \
  --evidence telemetry/evidence.json \
  --out telemetry/audit.jsonl --run-id "$(date -u +%Y-%m-%dT%H:%MZ)"
```

`--events` accepts both JSON and JSONL. `evidence.json` is a mapping keyed by
`event_id`: `{"live_confirmed": bool, "free_confirmed": bool, "paid": bool}`.

| Verdict | When | Effect on the event |
|---|---|---|
| `VERIFIED` | `live_confirmed` **and** `free_confirmed` are `true` | colour `5` → `6`, `[UNVERIFIED]` prefix dropped |
| `WRONG` | `paid: true`, or `live_confirmed: false` on a finished broadcast | `[WRONG]` prefix, colour `7` |
| `INCONCLUSIVE` | not finished yet, already `WRONG`, or evidence missing | **untouched** |

Three rules make this safe:

1. **Absence of evidence is not evidence.** A missing key yields
   `INCONCLUSIVE`; only an explicit `live_confirmed: false` on a *finished*
   broadcast yields `WRONG`. Reporting a game as wrong because a scraper failed
   would poison the audit and generate bogus eval cases.
2. **The sweep is idempotent and `WRONG` is terminal.** Re-running over the same
   day produces the same verdicts, and a `WRONG` verdict is never promoted back
   — even if later evidence looks positive.
3. **Nothing is deleted.** A verdict relabels the event and appends to
   `audit.jsonl`; the event and its history stay, which is the only way the audit
   trail survives.

`INCONCLUSIVE` rows are recorded too. "We checked and could not tell" is itself
evidence — it is what distinguishes a quiet day from a day the audit silently
did nothing.

Without this sweep the calendar accumulates amber `[UNVERIFIED]` events forever,
which is how a cautious label turns into noise nobody reads.

## 6. Eval feedback loop

Every new failure mode discovered in a run becomes **two** artefacts before the
next release:

1. an eval case in `evals/evals.json` whose `expected_output` carries the
   `Decision=…; checks: <name>=PASS|FAIL` needles, and
2. a `## Rationalizations` row or `## Red Flags` item in `SKILL.md` naming the
   shortcut, so the gate is defended in prose as well as in tests.

The same rule applies to the audit: every `WRONG` verdict, and every case where
an event was promoted or left `INCONCLUSIVE` against the evidence, becomes an
eval case. **The new case must pass against the fix before the fix is accepted**
— otherwise the system can "learn" by weakening the gate that caught it, and a
re-baseline (`--bless`) of a red run is never legitimate.

Worked example — the `magenta.tv` 200-but-empty-shell failure mode produced eval
cases 25–26 and the "The URL returns 200, so it works" rationalization row in
`references/magenta-tv.md`.

Run the loop end-to-end before a release:

```bash
python3 scripts/validate.py --root . --check all
python3 scripts/validate.py --check smoke-test
python3 scripts/runtime_eval.py --root .
python3 scripts/source_learning.py score --root .
python3 scripts/link_check.py --url https://www.youtube.com/@fiba/live --dry-run
python3 scripts/audit_events.py --help >/dev/null
python3 -m pytest tests/
```
