# Development contract

This repository uses do-harness for computational verification.

## Prerequisites

The `do-harness` CLI must be on `PATH`, or `DO_HARNESS_BIN` must point at it.
Pin the version this repository was initialized with:

    curl -fsSL https://raw.githubusercontent.com/d-o-hub/do-harness/main/scripts/install.sh \
      | sh -s -- --version v0.1.0

Rust developers can instead build from a checkout with
`cargo install --path <do-harness>/crates/do-harness`.

## Working loop

During implementation:

    do-harness verify --set feedback --changed

Before claiming completion:

    do-harness verify --set verification --changed --strict

Do not disable, bypass, remove, or weaken a required signal merely to obtain a
passing result. Fix the underlying cause and re-run the sensor.

## Completion

A change is complete only when current verification evidence is green:

    do-harness status --set verification

`status` never runs sensors. `green` means passing evidence matches the
current workspace and policy. `stale` means the workspace or `do-harness.toml`
changed after verification: re-run the verification set. `red` means required
checks failed. `missing` means no current evidence exists yet.

## Repository knowledge

- Follow the nearest `AGENTS.md` and the project documentation.
- Load project-specific skills from `.agents/skills/` only when applicable.
- Sensors and signal sets live in `do-harness.toml`; run
  `do-harness explain --set verification --changed` to see which sensors apply
  to the current change and why.

## Adoption notes

- `do-harness init` writes this file non-destructively; `--force` overwrites.
- The generic pack ships zero sensors: its pass is vacuous, not evidence.
- Git hooks: `do-harness hook install`. CI:
  `do-harness verify --set verification --format json --strict`.

## This repository: skill-basketball-streams

### What is the product, and what is tooling

The **product** is the root skill:

    SKILL.md          the contract an agent executes
    references/       domain references the contract points at
    evals/evals.json  the eval cases the contract must satisfy
    config/           calendar + source configuration
    scripts/          the offline helpers SKILL.md invokes

`.agents/skills/{harness,skill-creator}` are **development tooling** — they
belong to do-harness itself, not to this skill. Editing them is never a change
to the product, and a green `do-harness eval` says nothing about SKILL.md.

### Grading authority: the product graders stay

`do-harness eval` resolves skills **only** under `.agents/skills` (the path is
hardcoded; there is no config key to widen it). It therefore grades `harness`
and `skill-creator` and **never** the root skill. The plan of record
(`docs/do-harness.md`) originally intended to collapse grading onto
`do-harness eval`; that step is withdrawn, because demoting
`scripts/validate.py --check evals` would leave `evals/evals.json` ungraded.

The product gate is these four commands, run in order:

    python3 scripts/validate.py --root . --check all
    python3 scripts/runtime_eval.py --root .
    python3 scripts/synthesise_eval_case.py --verify
    python3 -m pytest tests/ -q

`do-harness eval` runs **in addition** to them, as a check on the tooling and
for its pass-rate history. `tests/test_harness_boundary.py` pins this boundary,
so a future change cannot demote the product graders on the false belief that
`eval` covers them.

### Release flow

    feature branch -> PR -> rebase-merge (gh pr merge --rebase --admin)
                   -> annotated tag (git tag -a v<X.Y.Z>)
                   -> push tag (git push origin v<X.Y.Z>)

`scripts/bump_version.py --check` keeps `SKILL.md` frontmatter `version` and the
newest dated `CHANGELOG.md` heading in sync; the `version-sync` sensor enforces
it. See `CONTRIBUTING.md` for the full workflow.

### The pre-completion gate

    do-harness verify --set verification --changed --strict

Sensors are offline and deterministic by design: `live-gate` and `link-gate`
run against fixtures in `--dry-run` mode so CI never depends on a live
broadcast existing. Adding a sensor means adding a `[[sensors]]` entry **and**
a fixture under `tests/fixtures/` that makes it assert a real outcome rather
than an arbitrary exit code. A sensor that needs network validation records the
responses and asserts against those (`tests/fixtures/pages/`,
`scripts/record_pages.py`), so a free provider having a bad day cannot red the
build.

Seven traps, each learned the hard way:

- **A `SKIP:`-prefixed output line makes a sensor `warned`, and under `--strict`
  a warned sensor is "weak evidence"** — `verify --set verification --strict`
  then reports `error: weak evidence` for a sensor that exited 0 and was
  entirely correct. Say `OK: <tool>: … not stored (verify-only)` instead. Pinned
  by `tests/test_recorded_pages.py`.
- **Record signal sets bottom-up** (`feedback`, `verification`, `release`); see
  below.
- **`--json` leaves stdout as the payload alone.** Human lines go to stderr.
  This has been re-learned four times (`upsert_events`, `calendar_io`,
  `synthesise_eval_case`, `run_daily`) and once more inside a test that encoded
  the workaround instead of the contract.
- **A safety decision written as a `${{ }}` expression cannot be tested, and it
  fails silently.** `runtime-daily.yml` resolved dry-run with
  `DRY_RUN: ${{ inputs.dry_run || 'true' }}`, which on a `schedule` event is
  *always* `'true'` — a cron has no `inputs` object — so with
  `ENABLE_CALENDAR_WRITES=true` the daily job planned every event, wrote none,
  and ran green every morning. The switch was dead on the one path nothing
  watches. The decision now lives in `scripts/write_mode.py`, where the matrix is
  a pure function; a step must **read** the resolved value
  (`steps.mode.outputs.dry_run`) rather than recompute it, and the outcome goes
  in the job summary, because "nothing was created" means something different in
  dry-run than with writes permitted.
- **A documented artifact with no producer is not a feature.** `§10` listed
  `telemetry/rungs.json` and `§18.4` promised an issue after N parked days, for
  months, while `RungHealth` lived and died inside one process — the promise
  could not be observed even in principle, and a retired backend left one
  `failed` line in one morning's log. `§10.1`'s `events.jsonl` and `docs/runtime.md`'s
  `evidence.json` were worse: the audit job *read* both, so `Phase 3` was wired
  end to end and had simply never run. Every morning it found its inputs absent,
  printed a notice, and did nothing — `precision` was `n/a` for the life of the
  project and `self-improve` had no ledger to learn from. A reader is not a
  producer. Name the *writer* in the same change and test that removing it fails
  the suite. Two corollaries from writing this one:
  keep the **recorded** verdict and its **derivation** apart (the ledger's
  `parked` flag is the truth; the snapshot is regenerable), and when a second job
  starts pushing an existing branch, serialise the writers with a **job-level
  `concurrency` group** — two pushes to one branch is a non-fast-forward
  rejection, which reads as a mystery rather than a queue. Set `queue: max`
  there: the default `queue: single` is documented to **cancel and replace** a
  *pending* job in the group, so one extra writer silently cancels another
  instead of waiting, and a cancelled job is indistinguishable from a quiet day.
  A sensor is deliberately *not* added for it: `parked` cannot assert an exit
  code, and a sensor that cannot fail is the vacuous pass this list exists to
  prevent. `pytest` is the gate, and `pytest` already watches `scripts/**`.
- **A rule that reads a field nothing writes is not enforced.** Two instances in
  one week, both about a fact that existed everywhere except where the rule
  looked. `upsert_events.events_match` compares the `Teams:` line of the event
  description — `references/calendar-setup.md` specifies it, `list_events` parses
  it back out — while every event the runtime created carried `description: ""`,
  because `apply_plan`'s `descriptions=` parameter was never passed by anything;
  since the teams check is skipped whenever either side is empty, a stored event
  matched **any** candidate inside the 30-minute window. Worse: the policy table's
  "an audit verdict outranks a fresh guess" reads a stored `WRONG` from the
  calendar, which recovers it from a `[WRONG] ` title prefix that `audit_events`
  *computes* and nothing applies — so §17's "a `WRONG` event is never shown as
  `VERIFIED`" was a 100 % requirement that could not hold, and every run was free
  to promote a game the audit had proved was paid. When a rule reads a field,
  assert the writer emits it and that a value written and read back still decides
  the same way — the reader's unit tests passing proves nothing about the pair.
  The tell is a *unit test with a hand-built input*: the fixture supplied the
  field the real pipeline never did.
- **A step that can write to GitHub is denied in `replay_ci.py`, by capability
  not by step name.** A replay cannot delete what it created: a workflow that
  opens a real issue or PR on the public repository would be a permanent,
  irreversible side effect of a local debugging tool. Deny the flag that grants
  the capability (`--live`, `--file`), the way `calendar_io` is handled — and
  match it across line continuations (`re.DOTALL`), since the flag is almost
  always on the next line of a wrapped command.

### Never hide a failure behind `|| true`

A workflow step that references a missing file and swallows the exit code is not
a tolerant step, it is a step that has never run. `runtime-daily.yml` shipped
`capture_transcripts.py --from tests/fixtures/runtime_transcripts.json --check
|| true` for a fixture that is deliberately absent, so it no-opped on every run
and still read as a pass in the job summary. Two rules follow:

- A step may tolerate a failure only if the log says **why**. A bare `|| true`
says nothing; `|| echo "::notice::<reason>"` does.
- Every workflow path is checked: `python3 scripts/check_workflow_refs.py --root .`
(`workflow-refs` sensor). If a file may legitimately be absent, guard the step
with `[ -f X ]` — the check treats an existence test as a question rather than a
claim.

### Recording evidence for several signal sets

Record them **bottom-up** — `feedback`, then `verification`, then `release`.
`--record` stamps each set with the policy snapshot in force at that moment, so
recording a subset after its superset leaves the superset `stale`
(`policy_changed`) even though nothing regressed. If in doubt, run `release`
last; it is the superset, and a green `release` is the strongest single claim.

    for s in feedback verification release; do
      do-harness verify --set "$s" --strict --record
    done

### Seeding is local, never in CI

`do-harness seed` writes `plans/invariants.json` into the state database. Run it
in a working copy after editing the invariants:

    do-harness seed

It is deliberately **not** part of CI. On an ephemeral runner the database is
thrown away, so seeding proves nothing that the `invariants-shape` sensor and
`tests/test_harness_boundary.py` do not already prove — and it is one of the ways
to trigger a `do-harness eval` segfault (`docs/do-harness.md` → Known defects).

### When a sensor stays red for no visible reason

`verify --record` bumps an error signature per failing sensor, and after **3
consecutive failures** that sensor is *halted*: it is not executed and is
reported failed with a `halted` diagnostic. A long local red streak can
therefore look like a permanent, unexplained failure. Check with:

    do-harness metrics            # per-sensor runs/failures + open strikes
    do-harness errors list        # the open error signatures
    do-harness errors clear --sensor <name>

Never silence a sensor to get a green: fix the cause, or remove the sensor
deliberately in `do-harness.toml` and say why in the commit message.
