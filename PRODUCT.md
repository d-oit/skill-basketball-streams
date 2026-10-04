# Product contract

This repository is a skill: a written contract plus the offline helpers that
make its claims testable. `PRODUCT.md` states what the product is and which
sensor enforces each promise. `tasks.md` states what is currently being built.
Both are machine-checked by `scripts/check_product_contract.py`; neither is
decorative prose, and a change that makes them disagree with the repository
fails the `product-contract` sensor.

## Contents

- [What the product is](#what-the-product-is)
- [Who it is for](#who-it-is-for)
- [What makes it trustworthy](#what-makes-it-trustworthy)
- [Enforcement sensors](#enforcement-sensors)
- [How to tell a change is safe](#how-to-tell-a-change-is-safe)
- [The task board](#the-task-board)
- [Keeping this document honest](#keeping-this-document-honest)

## What the product is

`SKILL.md` is the product. It is the contract an agent executes when a user asks
for free basketball live streams in Germany on a calendar: search the approved
official sources, run every candidate through the 7-check pipeline, and publish
only what survives.

The supporting files are part of the same artifact, not extras:

- `references/` — the domain knowledge the contract points at (the 7-check
  pipeline, the magenta.tv fetch ladder, the YouTube live-only gate, the
  calendar event schema, the approved-source list, the self-learning loop).
- `evals/evals.json` — 39 eval cases the contract must satisfy; the count is
  checked against the file itself by the `product-contract` sensor, so adding a
  case without updating this line fails the gate.
- `config/` — the approved/disallowed source registry, the calendar
  configuration, and the retired-source registry.
- `scripts/` — the offline helpers the contract invokes, plus the gates that
  keep the product honest.
- `.github/workflows/` — the daily runtime, the corpus refresh, and CI.

`.agents/skills/{harness,skill-creator}` are **development tooling** belonging
to do-harness; editing them is never a change to the product. The product
graders (`scripts/validate.py`, `scripts/runtime_eval.py`,
`scripts/synthesise_eval_case.py --verify`, `pytest`) are the four commands
`AGENTS.md` names; `do-harness eval` grades only `.agents/skills` and runs in
addition to them, never instead of them.

## Who it is for

- **Subscribers of the public calendar.** They must be able to trust that a
  listed event is a free, live, official stream. A wrong entry is worse than a
  missing one, which is why every invariant below errs toward `UNVERIFIED`
  rather than toward a confident claim.
- **The daily runtime.** An unattended workflow executes `SKILL.md` each
  morning. It is denied `edit` and `bash`, so it can only act through the tools
  the contract names and the fenced JSON block Step 7 requires.
- **The maintainer.** A person changing the contract needs to know, before they
  start, which sensor will tell them a change is safe and which artifact proves
  it.

## What makes it trustworthy

Three mechanisms, each enforced somewhere in the repository:

1. **The 7-check pipeline.** No calendar event is created unless all seven
   checks pass (`references/validation-workflow.md`). The gates it delegates to
   have their own sensors — `live-gate` for the YouTube live-only rule,
   `page-corpus` for stream evidence — and `evals/evals.json` grades the
   contract itself.
2. **Three verification states.** Every event is `VERIFIED`, `UNVERIFIED` or
   `WRONG`, and the state — not a boolean — decides the title prefix and the
   colour (`scripts/verification.py`, `references/calendar-setup.md`). An
   uncertain state overrides the league colour, so an unconfirmed game never
   looks confirmed.
3. **Nothing on the automatic path deletes an event, and a condemned game is not
   promoted back.** `scripts/link_check.py` classifies a stored link
   (`OK`/`BROKEN`/`BLOCKED`/`ERROR`/`UNREACHABLE`/`INVALID`) and reports it; the
   event itself is left in place. `scripts/audit_events.py` produces a post-hoc
   verdict, and `scripts/upsert_events.py` reads the newest `WRONG` row from the
   audit ledger by `event_id`, so a game the audit proved was paid or never live
   is not promoted to `VERIFIED` on a later run. Two halves of the design are
   not producers yet: the durable "quarantine the link" write promised by
   `references/self-learning.md` §4, and applying an audit's relabelled title to
   the calendar. What is enforced today is the planner's refusal and the absence
   of any delete path.

The full set of promises lives in `plans/invariants.json` (a bare array in the
shape do-harness `seed` writes; `tests/test_harness_boundary.py` pins the shape
and requires every `sensor` to be declared). Rather than duplicate 26 invariant
strings here — where they would drift between two hand-edited copies — this
document names the sensors that enforce them, and the checker requires that
every sensor an invariant relies on appears in the table below.

## Enforcement sensors

Every name in this table is a `[[sensors]]` entry in `do-harness.toml`. A
sensor mentioned in the table but not declared there fails the
`product-contract` gate: a promise guarded by a sensor that does not exist is
the same defect as a rule that reads a field nothing writes. Prose elsewhere in
this file is evidence, not a declaration — that asymmetry is deliberate, so a
retired sensor may still be explained.

| Sensor | What a green means |
| --- | --- |
| `skill-contract` | `scripts/validate.py --check all`: the skill, its references, its eval cases and the calendar config agree |
| `validate-smoke` | the validator's own smoke test passes |
| `runtime-eval` | `evals/evals.json` grades structurally, and every assertion needle resolves inside its case |
| `pytest` | the offline, deterministic test suite — the product graders' largest half |
| `live-gate` | the YouTube live-only/future-only gate, against a pinned `--now` |
| `retired-sources` | no retired domain is declared as a source again; the corrected BBL feed host stays allowed |
| `link-gate` | `link_check.py` classifies stored links without network access |
| `link-inventory` | a candidate's link survives the planner and the description writer and is read back out |
| `version-sync` | `SKILL.md` and `CHANGELOG.md` carry the same version |
| `upsert-plan` | the dedupe planner creates/skips the expected rows and touches no calendar |
| `upsert-verdicts` | a `WRONG` audit verdict is not promoted back to `VERIFIED` |
| `candidates-recall` | the recall denominator is non-zero on the pinned ledger |
| `recall-seam` | a run's outcome row is written in the real shape, so recall is computable from production data |
| `audit-verdict` | the post-hoc audit returns `WRONG`/`VERIFIED`/`INCONCLUSIVE` from evidence |
| `event-ledger` | the audit's inputs are written by a producer, not assumed |
| `eval-synthesis` | a synthesised case fails before the fix and its needles resolve |
| `metrics` | the metric contract is checked against fixture inputs, offline |
| `workflow-refs` | every repo path a workflow names exists |
| `page-corpus` | the stream-evidence gate is judged against recorded real pages |
| `feed-corpus` | the recorded iCalendar bytes are unchanged **and** still readable |
| `invariants-shape` | `plans/invariants.json` is a bare JSON array |
| `product-contract` | this document's sections and eval-case count agree with the repository, it names only declared sensors, the task board matches the export, and every method/invariant reference resolves |

## How to tell a change is safe

A change is complete when it satisfies the four product graders and the
verification signal set:

    python3 scripts/validate.py --root . --check all
    python3 scripts/runtime_eval.py --root .
    python3 scripts/synthesise_eval_case.py --verify
    python3 -m pytest tests/ -q

    do-harness verify --set verification --changed --strict
    do-harness status --set verification

`do-harness verify` selects the sensors whose `when-changed` paths the diff
touches, so `explain --set verification --changed` answers "why is this sensor
running?" before you spend the run. A sensor that exits 0 without being able to
fail is not evidence, so `AGENTS.md` requires every new sensor to ship with a
fixture under `tests/fixtures/` (or a dedicated test module) that makes it
assert a real outcome — a green with nothing behind it is a claim no reader can
distinguish from the vacuous pass the generic pack ships.

For this document and the board specifically:

    python3 scripts/check_product_contract.py --root .

It exits `0` only when `PRODUCT.md`'s table of contents matches its sections,
every sensor it declares exists, any eval-case count it states matches
`evals/evals.json`, every `sensor` in `plans/methods.json` and
`plans/invariants.json` exists, and `tasks.md` matches `plans/tasks.json`.

## The task board

Task state lives in `.do-harness/agent_state.db`, which is owned by do-harness
and gitignored — it is the authoritative, live store. Two mirrors exist because
two readers need one:

- `plans/tasks.json` is the committed export, written by
  `do-harness task export`. Machine-readable; `do-harness task import` validates
  it against the database.
- `tasks.md` is the human/agent-readable board. It is derived from
  `plans/tasks.json`, never edited from memory.

The `product-contract` sensor fails if `tasks.md` and `plans/tasks.json`
disagree on any id, title or status, or if the export's own `summary` block
disagrees with its tasks. To update the board:

    do-harness task export          # refresh plans/tasks.json from the database
    # then edit tasks.md to match, and re-run the checker

## Keeping this document honest

`PRODUCT.md` and `tasks.md` are inputs to the `product-contract` sensor, which
is listed in the `verification` and `release` signal sets (never in `feedback`,
which must stay sub-second for a pre-commit hook). If this document ever names a
sensor that `do-harness.toml` does not declare, states an eval-case count
`evals/evals.json` does not have, or promises a section its own contents list
cannot find, the gate fails — the same way a method catalog once kept naming a
sensor for months after that sensor had been deleted.
