# Adopting `do-harness` — Adoption Plan

> **Status: EXECUTED, with one step withdrawn.**
>
> Steps 0–5 and 7–9 are in place: a real 16-sensor `do-harness.toml`,
> `AGENTS.md`, 19 seeded invariants, `scripts/check-commitlint.sh`, and
> `.github/workflows/verify.yml`. `do-harness verify --set verification --strict`
> passes with evidence.
>
> **Step 6 is withdrawn.** Its premise was that `do-harness eval` would grade
> `evals/evals.json`. Measured, it does not: `eval` resolves skills only under
> `.agents/skills` and never sees the root skill, so demoting
> `scripts/validate.py --check evals` as the step instructed would leave the eval
> set ungraded behind a green tick. See **Step 6 (withdrawn)** below.
>
> The plan remains the record of the reasoning. Where it disagrees with the
> executed state, the executed state is authoritative and the disagreement is
> called out inline.

Upstream: [`d-o-hub/do-harness`](https://github.com/d-o-hub/do-harness) — a
compiled agent-execution harness CLI (MIT, Rust). Feedforward guides
(`AGENTS.md`, `.agents/skills/`) plus feedback sensors (`[[sensors]]`) that
agents and CI must pass.

## Decisions already taken

| Decision | Choice |
|---|---|
| Adoption depth | **Executed** — config, sensors, invariants, contract, hooks and CI. |
| Binary acquisition | **Pinned prebuilt installer**, `--version v0.1.0`, checksum-verified into `$HOME/.local/bin`. |
| Grading authority | **The four product graders stay** (withdrawn from "`do-harness eval` only" — `eval` never sees the root skill). `do-harness eval` runs *in addition*, for the tooling skills. |

## Relationship to the runtime that now exists

`.github/workflows/self-improve.yml` (Phase 4 of `live-stream-runtime-spec.md`) already runs
the loop this document was written around: an audit misjudgement becomes a regression eval
case on a branch, gated by `validate.py` + `runtime_eval.py` + `synthesise_eval_case.py
--verify` + `pytest`. That gate is the thing `do-harness eval` would replace — its
"Gate — the repo's own graders" step is the single place to swap, and nothing else in the
workflow depends on the grader's identity.

So the adoption question is now narrower and more concrete: **would `do-harness eval` grade
`evals/evals.json` at least as strictly as those four commands do today?** The plan's step 4
(prove coverage before demoting anything) is the only step that must be answered before the
swap, and the drift warning below is why the answer must be demonstrated rather than assumed.

**It was measured, and the answer is no.**

```console
$ do-harness eval --list-skills
harness
skill-creator
$ do-harness eval --skill skill-basketball-streams
error: skill 'skill-basketball-streams' not found under .agents/skills
```

`eval` discovers skills under `.agents/skills` and nowhere else; the path is not a config key.
Both discovered skills are do-harness's own development tooling. The root product skill — the
one whose `evals/evals.json` actually matters — is invisible to it. The swap described in
Step 6 is therefore impossible rather than merely unwise, and the four graders remain the
product gate.

## Why adopt it at all

This repository is already harness-shaped without a harness: four validator
checks plus a smoke-test in `scripts/validate.py`, `scripts/runtime_eval.py`, a
schema-validated `evals/evals.json`, a pytest suite, and a CI matrix — but the
contract is spread across `CONTRIBUTING.md`, `scripts/README.md`, and
`.github/workflows/validate.yml`, and **every gate is invoked by hand**.

do-harness supplies the single entry point (`verify`), the change-scoped
selection (`--changed`, `explain`), the machine-readable evidence artifact
(`--evidence`, `--strict`), and — the strongest fit — a mechanical
`trace add` → `distill --to-fixture` loop that *is* the self-learning loop
described in `references/self-learning.md`.

## Step 0 — Pin and install the binary (no repo changes)

```bash
curl -fsSL https://raw.githubusercontent.com/d-o-hub/do-harness/main/scripts/install.sh \
  | sh -s -- --version v0.1.0
export PATH="$HOME/.local/bin:$PATH"
do-harness --version
```

The installer verifies the artifact against the release `checksums.txt` before
installing. Those checksums share the release origin, so they detect corruption
and truncated downloads — **not** a compromised origin. Record the pin in CI as
a literal `v0.1.0`; do not track `latest`.

Do **not** vendor the Rust source, and never build it into this repo's shared
`target/` directory if the decision ever changes — `cargo clean` in this
workspace would delete the binary and silently break installed git hooks.

## Step 1 — `scripts/check-commitlint.sh` (blocking prerequisite)

**Do this before anything else.** `do-harness hook install` writes a `commit-msg`
hook that runs `scripts/check-commitlint.sh --message <file>` and is
**fail-closed**: it blocks the commit when the repo or script is missing. This
repo currently has **no such script**, so installing hooks first would make
every subsequent commit fail.

The script must enforce the prefixes already documented in `CONTRIBUTING.md`:

```
feat fix chore docs test refactor perf build ci style revert
```

Contract: read the message from `--message <path>` or stdin, strip comments and
`Merge`/`Revert`/`fixup!` lines, assert the first line matches
`^(feat|fix|chore|docs|test|refactor|perf|build|ci|style|revert)(\([a-z0-9._-]+\))?!?: .+`
and that the subject is ≤72 chars, then exit `0`/`1`. Keep it dependency-free
(`bash` + `grep`/`sed`) so it runs on the CI runner and on the maintainer's box.

## Step 2 — `do-harness init --language generic`

```bash
do-harness init --language generic
```

`--language generic` is **mandatory here.** A bare `do-harness init` assumes the
Rust pack and, when no `Cargo.toml` exists, scaffolds a minimal crate
(`Cargo.toml` + `src/lib.rs`). This is a Python + Markdown repository; a
scaffolded crate would be pure pollution.

What `init` writes (existing files are left untouched unless `--force`):

```
do-harness.toml          workspace marker + config
AGENTS.md                generated routing/completion contract
plans/invariants.json    seeded invariants
.agents/skills/          harness skill + skill-creator
.gitignore               additional entries (state DB, evidence)
.do-harness/agent_state.db   local libSQL state
```

`init` then runs the generated contract once and reports
`Initial verification: GREEN | RED | VACUOUS`, exiting non-zero on `RED`.

> **The `VACUOUS` case is the trap.** The generic pack ships **no sensors**, so
> a fresh `verify` exits `0` having executed nothing. That is a vacuous pass,
> **not evidence**. Treat no sensor output as no proof until Step 3 is done.

## Step 3 — Sensors (the actual work)

All sensors are offline and deterministic — no network, no credentials. The two
network-adjacent commands (`link_check`, `youtube_live`) run in `--dry-run` /
fixture mode so CI never depends on a live broadcast existing. A sensor that must
reason about real remote content records the responses and asserts against those
(`page-corpus` → `scripts/record_pages.py --check`, corpus in
`tests/fixtures/pages/`), so a page being down cannot red the build and the
judgement is still pinned to the real web.

**Output contract: never start a line with `SKIP:`.** The harness marks a sensor
`warned` when it does, and under `--strict` a warned sensor is *weak evidence* —
so a sensor that exits `0` and is entirely correct makes `verify --strict` report
`error: weak evidence`. Use `OK: <tool>: …` and put the caveat in the message.

```toml
# do-harness.toml — sketch
language = "generic"

[hooks]
pre-commit = ["skill-contract", "validate-smoke"]
pre-push   = []          # empty = full suite

[[sensors]]
name = "skill-contract"
argv = ["python3", "scripts/validate.py", "--root", ".", "--check", "all"]
when-changed = ["SKILL.md", "README.md", "evals/**", "references/**", "config/**"]

[[sensors]]
name = "validate-smoke"
argv = ["python3", "scripts/validate.py", "--check", "smoke-test"]
when-changed = ["scripts/validate.py", "tests/**"]

[[sensors]]
name = "runtime-eval"
argv = ["python3", "scripts/runtime_eval.py", "--root", "."]
when-changed = ["scripts/runtime_eval.py", "evals/**"]

[[sensors]]
name = "pytest"
argv = ["python3", "-m", "pytest", "tests/", "-q"]
when-changed = ["tests/**", "scripts/**"]

[[sensors]]
name = "live-gate"
argv = ["python3", "scripts/youtube_live.py", "--input",
        "tests/fixtures/youtube_live_candidates.json", "--now",
        "2026-09-14T09:00:00Z"]
when-changed = ["scripts/youtube_live.py", "tests/fixtures/**"]

[[sensors]]
name = "source-registry"
argv = ["python3", "scripts/source_learning.py", "candidates", "--root", ".",
        "--dry-run"]
when-changed = ["config/sources.json", "scripts/source_learning.py"]

[[sensors]]
name = "link-gate"
argv = ["python3", "scripts/link_check.py", "--url",
        "https://www.youtube.com/@fiba/live", "--dry-run"]
when-changed = ["scripts/link_check.py"]

[[sensors]]
name = "version-sync"
argv = ["python3", "scripts/bump_version.py", "--check"]
when-changed = ["SKILL.md", "CHANGELOG.md"]

[signal-sets]
feedback     = ["skill-contract", "validate-smoke"]
verification = ["skill-contract", "validate-smoke", "runtime-eval", "pytest",
                "live-gate", "source-registry"]
release      = ["skill-contract", "validate-smoke", "runtime-eval", "pytest",
                "live-gate", "source-registry", "link-gate", "version-sync"]
```

Two prerequisites this table assumes:

1. **`version-sync` needs a new mode.** `scripts/bump_version.py` today requires
   `--bump {major,minor,patch}` and mutates files. Add a non-mutating
   `--check` that compares `SKILL.md` frontmatter `version` against the newest
   dated `CHANGELOG.md` heading and exits `0`/`1`. Until that exists, omit the
   sensor.
2. **`live-gate` needs a fixture.** Add
   `tests/fixtures/youtube_live_candidates.json` containing at least one
   `upcoming` candidate with `scheduled_start` after the pinned `--now`, so the
   sensor asserts a real promotion rather than an arbitrary exit code.

Verify the selection before trusting it:

```bash
do-harness list
do-harness explain --changed      # which sensors the current diff selects
do-harness verify --set verification --format json --strict
```

## Step 4 — `plans/invariants.json`

Seed with the gates that must never be relaxed. These mirror the `CRITICAL`
banner and `## Constraints` in `SKILL.md`:

- No calendar event unless all 7 checks pass.
- `magenta.tv` URL requires a matching free-access announcement on
  `magentasport.de` or official MagentaSport social media.
- YouTube candidates require `scheduled_start` strictly greater than now, or a
  live-now broadcast with no `actualEndTime`; VODs, replays, highlights and
  ended broadcasts are never promotable.
- Basketball Champions League free access is decided per game via the site plus
  `@BasketballCL` and `facebook.com/BasketballCL` — silence is not consent.
- Social media may validate free access, never provide the `directLink`.
- Paid broadcasters (Sky / DAZN / Prime / Sport1+) are rejected even when the
  domain is otherwise well-known.
- A source discovered by `source_learning.py candidates` stays quarantined until
  a human promotes it in a PR that also adds an eval case.

## Step 5 — `AGENTS.md`

`init` generates a routing/completion contract only. Extend it with:

- the product artifact is the **root `SKILL.md`** (plus `references/`,
  `evals/`, `config/`);
- `.agents/skills/{harness,skill-creator}` are **development tooling**, not the
  product;
- the release flow from `CONTRIBUTING.md` (branch → PR → rebase-merge → tag);
- `do-harness verify --set verification` is the pre-completion gate.

## Step 6 (withdrawn) — `do-harness eval` was not made the grading authority

**Original plan:** make `do-harness eval` the sole grading authority and demote
`scripts/validate.py --check evals`, on the assumption that `eval` grades
`evals/evals.json`.

**What actually happened:** step 2 of the original migration was *"confirm `eval`
covers `evals/evals.json`"*, and running it answered the question the other way.
`do-harness eval` discovers skills only under `.agents/skills`:

```console
$ do-harness eval --list-skills
harness
skill-creator
$ do-harness eval --skill skill-basketball-streams
error: skill 'skill-basketball-streams' not found under .agents/skills
```

Both discovered skills are do-harness's own tooling. There is no config key to
point `eval` at the repository root, so it **cannot** grade the root product
skill. Step 3 — demoting `--check evals` — would have left the eval set ungraded
while `eval` reported `structure=ok evals=8/8 pass_rate=1.00` on an unrelated
skill. That is the precise failure the plan's own drift warning describes: the
check that still reports green is the one that gets trusted.

**Resolution.** The four product graders stay and are the gate for the root
skill:

```bash
python3 scripts/validate.py --root . --check all
python3 scripts/runtime_eval.py --root .
python3 scripts/synthesise_eval_case.py --verify
python3 -m pytest tests/ -q
```

`do-harness eval` runs **in addition** — useful for its pass-rate history on the
tooling skills, meaningless as evidence about `SKILL.md`. `.github/workflows/
verify.yml` runs both, in two jobs, and `tests/test_harness_boundary.py` asserts
that wiring so the swap cannot be performed by a later edit that reads this
document as an instruction.

The general lesson is worth keeping: "at least as strict" is a claim about
*coverage*, and the only way to settle it was to run the tool against this
repository's actual layout. A plausible-sounding grader that grades a different
artifact is worse than no extra grader, because its green tick displaces
attention from the one that matters.

## Step 7 — Git hooks

```bash
do-harness hook install
do-harness doctor
```

Managed hooks: `pre-commit` (`verify --fail-fast --only fmt --only loc` by
default — override via `[hooks].pre-commit`), `commit-msg` (the fail-closed
commitlint script from Step 1), `pre-push` (full suite). Hooks locate the binary
via `$DO_HARNESS_BIN`, then `do-harness` on `PATH`, then
`<repo>/target/release/do-harness`. `hook status` reports which are present and
managed; `hook uninstall` removes only managed ones.

Two operational notes:

- **Strike halting.** `verify --record` persists results as beats and bumps an
  error signature per failing sensor. After **3 consecutive failures** the sensor
  is *halted*: it is not executed and is reported failed with a `halted`
  diagnostic. A long red streak locally can therefore look like a permanent,
  unexplained failure. `do-harness errors list` / `errors clear --sensor <name>`
  is the escape hatch.
- **No HTML-sized hook output.** `pre-commit` runs only the two fast sensors, so
  a docs-only change stays sub-second.

## Step 8 — CI

New `.github/workflows/verify.yml`, **separate job, separate workflow** — leave
`.github/workflows/validate.yml` and `codeql.yml` untouched so a release-outage
or harness regression cannot red the existing Python 3.9/3.12 matrix.

```yaml
- run: |
    curl -fsSL https://raw.githubusercontent.com/d-o-hub/do-harness/main/scripts/install.sh \
      | sh -s -- --version v0.1.0
    echo "$HOME/.local/bin" >> "$GITHUB_PATH"
- run: do-harness doctor
- run: do-harness verify --set verification --format json --strict --evidence .do-harness/evidence.json
- run: do-harness status --set verification
- run: do-harness eval
```

Keep the binary pin identical to Step 0. `verify --format json` emits one object
(`ok`, `root`, `failed[]`, `sensors[]`); failure tails go to **stderr** so stdout
stays parseable.

## Step 9 — Evidence loop (self-learning, made mechanical)

`references/self-learning.md` currently describes the loop in prose
(run log → score → quarantine candidates → promote in a PR that adds an eval
case). do-harness gives it a runtime:

```bash
do-harness trace add --session <run-id> --task <id> \
  --command "python3 scripts/link_check.py --input links.json" \
  --error-diff "<BROKEN links>" \
  --resolution-steps "<quarantine the dead link and re-run>"
do-harness distill --skill skill-basketball-streams \
  --pattern "<the failure pattern>" --from-trace <id> --to-fixture
```

`do-harness metrics` reports sensor stats, strike counts and eval pass-rate
history; `do-harness compliance` emits the OWASP Agentic Top 10 / NIST AI RMF /
EU AI Act mapping if that artifact is ever needed.

### What Step 9 actually does (exercised, not assumed)

The loop above was **run end to end** against two real bugs found in this
repository, and three things about it differ from how it reads:

```console
$ do-harness task add "Phase 5 fixture parsing"
Added task 1: Phase 5 fixture parsing
$ do-harness trace add --session sess-2026-09-14-fixtures --task 1 \
    --command "python3 scripts/fixtures.py --input … --source bbl" \
    --error-diff "FAIL: fixtures: no parsable fixtures" \
    --resolution-steps "Tracked the microdata block by element depth …"
Recorded trace 1 in session sess-2026-09-14-fixtures
$ do-harness distill --skill harness --pattern "…" --from-trace 1 --to-fixture
Distilled heuristic 1 for harness; appended to references/heuristics.md
Bar ratcheted for harness: floor now 0.95
```

1. **`--task` must be a numeric id, and the task must exist.** `task add`
   allocates it. Passing a slug fails with `invalid digit found in string`.
2. **`distill` targets a skill under `.agents/skills` and cannot name the root
   skill.** This is the same boundary as Step 6, confirmed independently:

   ```console
   $ do-harness distill --skill skill-basketball-streams --pattern "…" --from-trace 1
   error: unknown skill 'skill-basketball-streams': no SKILL.md under .agents/skills
   ```

   So distilling a heuristic from a real bug in the root skill lands in the
   **tooling** skill. That is the wrong home for it, and it is why the
   experiment above was reverted rather than kept.
3. **`--to-fixture` does not write a fixture, and does not add an eval case.**
   It appends the heuristic to the target skill's `references/heuristics.md` and
   ratchets that skill's pass-rate floor (the run above reported `floor now
   0.95`). An earlier draft of this document claimed the flag *enforces* the
   "every new failure mode becomes an eval case" rule from
   `references/self-learning.md`. **It does not** — it only makes the bar
   stricter. The rule that actually creates the case for this repository is
   `scripts/synthesise_eval_case.py` plus `.github/workflows/self-improve.yml`,
   which is a separate mechanism with a separate credential posture. Recording
   the flag's real behaviour matters more than its name suggests: a raised floor
   on a skill that has no case for the failure is a stricter bar over the same
   coverage.

`do-harness metrics` is worth running whenever a sensor is red for no visible
reason: `verify --record` bumps an error signature per failing sensor, and after
**3 consecutive failures** that sensor is *halted* — not executed, reported
failed, with a `halted` diagnostic. A long red streak can therefore look like a
permanent, unexplained failure. `do-harness errors list` and
`errors clear --sensor <name>` are the escape hatch; both `AGENTS.md` and
`.github/workflows/verify.yml` surface this.

## Known defects in `do-harness` v0.1.0 (observed, not inferred)

Both of these are invisible in a long-lived working copy and appear only on a
fresh checkout — which is to say, only in CI. They were found by replaying the
workflow against a fresh tree rather than by trusting a green local run.

The replay is now a script rather than a recipe, because it was re-derived by
hand three times and has two traps that fail *misleadingly*:

```bash
python3 scripts/replay_ci.py --workflow .github/workflows/validate.yml
python3 scripts/replay_ci.py --workflow .github/workflows/verify.yml --job product-graders
```

A clean copy must exclude `.git`/`.do-harness`/`.tmp`/caches, or the replay
inherits exactly the recorded state that hides the defect. And `GITHUB_STEP_SUMMARY`
plus `GITHUB_OUTPUT` must be set: Actions always provides them and a local shell
does not, so a step doing `>> "$GITHUB_STEP_SUMMARY"` dies with
`: No such file or directory` and the replay blames the workflow for a defect of
the replay. Both are handled by the script, which also refuses to execute any
step that could write outside the replay directory (`--live`, `git push`, Google
auth, `opencode`) and strips `*_API_KEY`/`*_TOKEN` from the environment first.

**A third trap: the step's own `env:` block is not injected, and `${{ }}` is not
expanded.** Only the `run:` script is read from the YAML. So a step that declares
`EVENT: ${{ github.event_name }}` runs with `$EVENT` empty — and one that
interpolates `${{ }}` directly into the script gets a bash *bad substitution*
(`bash: | Write mode | ${{ … }} |: bad substitution`), which is why the two job
summaries in `runtime-daily.yml` now pass their values through `env`. Two
consequences worth knowing before you read a red replay:

- **Fail closed on an empty value; reserve the usage error for an argument that
  was omitted.** `scripts/write_mode.py` distinguishes them with sentinel
  defaults. Treating "passed empty" as a usage error made a correct step fail a
  replay with `--event, --input and --enabled were all empty`, which reads as a
  workflow defect when it is a replay limitation.
- **Do not "fix" it by injecting the env block.** A literal `${{ secrets.X }}`
  looks *configured* to a backend and can start real network calls with a bogus
  key — strictly worse than an obviously empty value.

### 1. `do-harness eval` SIGSEGVs on its second invocation

```console
$ rm -rf .do-harness
$ do-harness eval   # exit 0, and it creates .do-harness/agent_state.db
$ do-harness eval   # exit 139 (SIGSEGV), no stdout, no stderr
```

Any command that creates the state database first triggers it too — `seed`
does, and so does `verify --record`:

```console
$ rm -rf .do-harness && do-harness seed && do-harness eval   # seed 0, eval 139
```

It does **not** reproduce in this repository's working copy, where repeated
`eval` runs return 0. The trees were verified byte-identical (`diff -rq`,
excluding `.git`/ignored paths) between the crashing copy and the working one,
and a minimal tree (`do-harness.toml` + `.agents/`) does not crash at all, so the
trigger is not simply "outside the original directory" or "a copy". What is
established is the reproduction, which is what the workflow is designed around.

**Mitigations, both deliberate** (`.github/workflows/verify.yml`):

- `eval` runs **first**, before anything creates the database.
- `eval` is `continue-on-error: true`. It grades only `.agents/skills`, so per
  Step 6 it is supplemental; an upstream crash must not red the build, and the
  step must stay visible rather than be deleted.
- `seed` is **not** run in CI at all. On an ephemeral runner it proves nothing
  the `invariants-shape` sensor and `tests/test_harness_boundary.py` do not
  already prove, and it is one of the ways to trigger the crash.

`tests/test_harness_boundary.py` pins the ordering (`eval` before `verify`) and
the tolerance, so a later reordering cannot silently reintroduce this.

### 2. `status` exits 1 on a checkout with no recorded beats

Correct behaviour, easy to mistake for a failure. A fresh CI checkout has no
beats, so `status --set verification` reports `missing` and exits 1. Gating on it
would make the workflow red on every first run, so the step is reporting-only
and prints a notice instead.

### Related, for the record

`doctor` exits 1 outside a git repository (`error: no git repository found`).
Harmless in CI, where `actions/checkout` always provides one, but it means
`doctor` cannot be used as a smoke test in a plain directory.

## Risks and escape hatches

| Risk | Mitigation |
|---|---|
| `commit-msg` fail-closed hook blocks all commits | Step 1 before `hook install`; `do-harness hook uninstall` to recover |
| `init` scaffolds a Rust crate into this Python repo | always `--language generic` |
| `verify` green on a vacuous sensor set | Step 3 before any green claim; use `--strict` + `--evidence` |
| Two skill trees (root `SKILL.md` vs `.agents/skills/`) | Step 5 makes the split explicit in `AGENTS.md`; `do-harness eval` only ever sees the latter, which is why its green is not evidence about the former |
| Two skill graders disagree | Resolved: they grade *different* artifacts (root skill vs `.agents/skills`), so neither replaces the other. `do-harness eval` is supplemental; `check_evals` stays authoritative for `evals/evals.json` |
| Halted sensor masquerades as permanent red | `errors list` / `errors clear`; documented in Step 7 |
| CI gains a dependency on GitHub Releases | separate workflow; pinned version; checksums verified |
| Binary deleted by `cargo clean` (only if vendored) | do not vendor; never build into the shared `target/` |

## Rollback

Everything except `AGENTS.md` and `plans/invariants.json` is removable:

```bash
do-harness hook uninstall
rm do-harness.toml .github/workflows/verify.yml
rm -rf .do-harness .agents plans docs/do-harness.md
```

`scripts/validate.py` and the existing CI matrix are never modified by this
plan, so the pre-adoption contract survives a full rollback.
