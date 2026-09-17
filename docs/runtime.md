# Runtime operations

Operator guide for the daily streams runtime. Design rationale lives in
`live-stream-runtime-spec.md`; the do-harness adoption plan lives in
`docs/do-harness.md` and is still **PLAN ONLY**.

## What runs today

| Phase | Workflow job | Calendar writes | Status |
|---|---|---|---|
| 0 — candidate ledger | `runtime-daily.yml` → `telemetry` | **never** | implemented |
| 1 — skill run, dry-run | `runtime-daily.yml` → `runtime` | gated off by default | implemented |
| 2 — writes with honest labelling | `runtime-daily.yml` → `runtime` | via `ENABLE_CALENDAR_WRITES` + `write_mode.py` | implemented |
| 3 — post-hoc audit | `runtime-daily.yml` → `audit` | read + relabel | implemented |
| 4 — self-improvement branch | `self-improve.yml` | none | implemented |
| 5 — official-fixture ground truth | manual / cron | none | implemented |
| 6 — evidence corpus stays true | `corpus-refresh.yml` | none | implemented |

All five phases are now written, and every one has an offline path, so the whole
runtime is exercisable in CI without a single credential. What is still unproven is
not code but **inputs**: real transcripts have never been captured
(`tests/fixtures/runtime_transcripts.json` is absent by design), so the grader has
never graded a real run, and the pre-fix-red property of a synthesised eval case is
asserted rather than demonstrated.

## Verification states

Every event carries one of three states (`scripts/verification.py`). The state,
not a boolean, decides the title and the colour — see
`references/calendar-setup.md` for the table.

| State | Title | `colorId` |
|---|---|---|
| `VERIFIED` | unchanged | league colour (`6`/`11`/`2`) |
| `UNVERIFIED` | `[UNVERIFIED] …` | `5` Banana |
| `WRONG` | `[WRONG] …` | `7` Peacock |

An uncertain state **overrides** the league colour, so an unconfirmed final never
looks like a confirmed one. `WRONG` is terminal, set only by
`scripts/audit_events.py`, and relabels rather than deletes.

## Writing to the calendar

The write path is four modules, each independently inspectable. Nothing writes
until `--live` is passed, and the dry run always runs first so the log contains
the intended change even when the write then fails.

```bash
# 1. transcript -> candidates. The runtime agent has no `edit` and no `bash`, so
#    the transcript is the only channel it has; this enforces the contract.
python3 scripts/extract_candidates.py --transcript .tmp/transcript.json \
  --out .tmp/candidates.json

# 2. read the calendar FIRST. `list` recovers the verification state from the
#    title prefix, which is what makes "never touch a verified event" possible.
python3 scripts/calendar_io.py list --days 7 --out .tmp/existing.json

# 3. plan in memory -- no API calls at all
python3 scripts/upsert_events.py --existing .tmp/existing.json \
  --candidates .tmp/candidates.json --json > .tmp/plan.json

# 4. dry run (no token needed), then the real writes
python3 scripts/calendar_io.py apply --plan .tmp/plan.json --out .tmp/applied.json
python3 scripts/calendar_io.py apply --plan .tmp/plan.json --live \
  --out .tmp/applied.json

# 5. record what was written. Phase 3 judges these events, and it can only judge
#    an event it can name, so this is a separate inspected step rather than a
#    side effect of the write.
python3 scripts/event_ledger.py record --plan .tmp/plan.json \
  --applied .tmp/applied.json --dest .tmp
```

Dedupe happens over the **whole window** before any write, and a `VERIFIED` event
is never modified. A re-run therefore creates zero duplicates and leaves verified
events byte-identical. `skip` rows send nothing at all — no request, not a no-op
request.

**Every event carries a `League:`/`Teams:` description.** `list_events` reads the
`Teams:` line back out and `upsert_events.events_match` matches on it, so it is
the field that decides whether next run's candidate *is* the event already on the
calendar. Nothing used to write one, every event was created with
`description: ""`, and because `events_match` skips the teams check whenever
either side is empty, a stored event matched **any** candidate inside the
30-minute window — so a different pairing at the same time was planned as an
*update* of it rather than a new event. The block is now derived from the plan
row's own `league`/`teams` (an explicit `descriptions=` entry still wins).

**`--out` records the applied result, not the plan.** The two answer different
questions — the plan says what we intended, the result says what the calendar now
holds and under which `event_id`. It is written *before* `apply` exits non-zero,
so a partially failed run is still recordable: the events that landed are exactly
the ones a subscriber can see.

**Why `--live` is a flag and not a default.** "Did this run write to a public
calendar?" should be answerable by reading the command line rather than by
trusting a code path. A bare `apply` makes no HTTP request whatsoever, and
there is a test that replaces the transport with one that raises, so a leak is a
failure rather than a comment.

## Measuring recall against real fixtures

Search-derived candidates cannot see a game **no** backend surfaced, and that is
the worst failure this system has. `scripts/fixtures.py` normalises official
league fixture lists into the ledger's `game_key` shape so the two can be joined:

```bash
# Fetch and normalise (network); or --input a saved page for offline work
python3 scripts/fixtures.py --source bbl --source bcl --out .tmp/fixtures.jsonl

# Then the number that matters: games nobody surfaced
python3 scripts/candidates.py unseen --ledger .tmp/candidates.jsonl \
  --fixtures .tmp/fixtures.jsonl

# Or fold it into the normal recall report
python3 scripts/candidates.py recall --ledger .tmp/candidates.jsonl \
  --fixtures .tmp/fixtures.jsonl
```

A fixture that fails to parse is **dropped, never guessed**: a half-parsed event
would be reported as a missed game that does not exist, which would send someone
hunting a phantom and quietly deflate the metric that is supposed to catch real
misses. Only JSON-LD and a microdata fallback are supported; adding a site means
capturing a page as a fixture, not writing a bespoke scraper.

The telemetry job writes to `telemetry/fixtures.jsonl`, and that write is
**idempotent**: a `game_key` already in the file is skipped. The job re-observes
the same 7-day window every day, so a blind append would write each game once per
day forever — the file would grow without bound and `fixture_recall` would end up
measuring file length instead of coverage. The recall denominator is distinct
games, computed at read time by `distinct_fixtures()`, so a duplicated row cannot
inflate it either way. `fixture_recall` reports `dropped` (no usable `game_key`
— a parser regression) and `duplicates` (a re-observation — harmless) separately,
because those two numbers mean opposite things.

## Self-improvement

`.github/workflows/self-improve.yml` runs after the daily run. It reads the audit
ledger from the telemetry branch, turns each confirmed misjudgement into a
regression eval case, pushes a `self-improve/YYYY-MM-DD` branch, and gates it on
`validate.py` + `runtime_eval.py` + `synthesise_eval_case.py --verify` + `pytest`.
Only a green gate opens a PR; a red gate keeps the branch and opens an issue.

```bash
# Locally: what would it file?
python3 scripts/synthesise_eval_case.py --verdicts .tmp/audit.jsonl --dry-run

# And the invariant that stops a vacuous case ever being written
python3 scripts/synthesise_eval_case.py --verify
```

**The case encodes the bug, not the fix.** It asserts what the pipeline *should*
have produced, so it fails until the responsible gate is tightened. A case that
passed immediately would mean the loop "learned" by weakening the gate that
caught the misjudgement — which is why `--bless`-style re-baselining is banned
and `assert_gradeable` refuses to write a case whose assertion has no needle.

**Why this workflow may edit the repo at all.** It has `contents: write` but
**no `id-token: write`** and no Google authentication step: it cannot reach a
calendar. Writing the calendar and rewriting the instructions are different
privileges, held by different workflows on purpose.

`docs/do-harness.md` plans to make `do-harness eval` the grader here instead.
That plan is still PLAN ONLY, so the gate above uses the repo's own scripts.

## Auditing

The audit runs over events that have **finished**, so it can only conclude after
the broadcast ends:

```bash
python3 scripts/audit_events.py \
  --events telemetry/events.jsonl \
  --evidence telemetry/evidence.json \
  --out telemetry/audit.jsonl --run-id "$(date -u +%Y-%m-%dT%H:%MZ)"
```

`--events` accepts JSONL as well as JSON. `evidence.json` is
`{event_id: {live_confirmed, free_confirmed, paid}}`. A missing key is
`INCONCLUSIVE`, never `WRONG` — a scraper failure must not read as "was never
live".

**Both inputs come from the run that wrote the events.** `scripts/event_ledger.py`
produces them from the applied result and the plan row; the `runtime` job uploads
them as the `run-ledger` artifact and the audit job downloads and commits them,
because `runtime` holds the calendar credential and deliberately has no
`contents: write`. Before this existed, the audit's inputs were documented and
read but *never written by anything*: the job found them absent, printed a
notice, and did nothing on every single run. Phase 3 had never run, `precision`
was `n/a` for the life of the project (§17 gates it at ≥ 0.95), and the
self-improvement loop had no ledger to learn from.

Three rules keep the ledger honest:

* **A dry run records nothing.** Nothing was written, so a verdict about it would
  be a verdict about nothing.
* **A written event with no `event_id` is refused, not recorded with an empty
  key.** The audit resolves a verdict back to an event *by id*, so that row could
  never be applied to anything. An unnameable write is worse than a red step.
* **Evidence is copied from explicit flags and never derived.** There is no
  default, because a missing key is INCONCLUSIVE and an explicit `false` is
  `WRONG`: a default would relabel live games as wrong.

`events.jsonl` stays append-only forever, so an event updated on five days has
five rows; the audit takes the **last row per `event_id`** and says how many it
collapsed, so one event cannot be judged — and counted in the precision
denominator — five times. `evidence.json` is derived, so an event this run
touched has its entry *replaced* rather than merged: a union would let
yesterday's `live_confirmed: true` outlive today's observation.

### The verdicts come back to the planner

§17's hard requirement is that a `WRONG` event is **never shown as `VERIFIED`**.
`upsert_events` enforced it by reading a stored `WRONG` off the calendar — and the
calendar recovers that state from a `[WRONG] ` title prefix. `audit_events`
*computes* that relabelled title (`new_title` / `new_color_id`) and writes it to
`audit.jsonl`, and **no component applied it**. So the rule was unreachable on the
real path: a game the audit had proved was paid or never live stayed on the
subscriber's calendar looking exactly like one awaiting confirmation, and the next
run's fresh guess was free to promote it to `VERIFIED`.

So the planner reads the record itself:

```bash
# Read-only: the same `git show` of the branch `self-improve.yml` uses, which
# needs no `contents: write` — and this job must not have it.
git fetch --depth=1 origin telemetry
# ...then, only if the file exists:
python3 scripts/upsert_events.py --existing .tmp/existing.json \
  --candidates .tmp/candidates.json --verdicts .tmp/audit.jsonl --json
```

An event whose **newest** verdict is `WRONG` is skipped regardless of its title,
with the recording timestamp in the reason so a human can find the row that
decided it. The join is by `event_id` — the only identity the two sides share, as
a calendar event parsed by `calendar_io` has no `game_key` — and the dedupe reuses
`audit_events.latest_per_event`, deliberately the *same* function the audit uses,
because two implementations could disagree about which row is current and invert
the guarantee. `--verdicts` on a missing file is a usage error rather than an
empty ledger: silently treating it as "no verdicts" is exactly the state in which
a condemned event gets promoted.

**Still open on this seam.** The relabel itself (`[WRONG]`, colour 7, and the
prefix drop on a promotion) is computed but never written to the calendar, so a
condemned event is held rather than visibly marked. Applying it is the next step;
until then the guarantee is "never promoted again", not "shown as wrong".

**Default posture is safe.** The `telemetry` job makes zero calendar API calls. The `audit`
job is off unless `ENABLE_AUDIT` is `true`, and it now `needs: runtime` for
*ordering* only — its condition is `always()`, so it still runs on a day the skill
run failed, which is exactly when a false positive is most likely to have been
created.

The `runtime` job decides its write mode **once**, in `scripts/write_mode.py`, and every
step reads that one value (`steps.mode.outputs.dry_run`). The precedence, fail-closed:

| Event | `ENABLE_CALENDAR_WRITES` | `dry_run` input | Result |
|---|---|---|---|
| `schedule` / `repository_dispatch` | `true` | — (a cron has no inputs) | **writes** |
| `schedule` / `repository_dispatch` | anything else | — | dry-run |
| `workflow_dispatch` | `true` | `false` | **writes** |
| `workflow_dispatch` | `true` | `true` (the default) | dry-run |
| `workflow_dispatch` | anything else | `false` | dry-run — the input cannot widen the switch |
| anything | — | unrecognised | dry-run, and says so |

The resolved mode and its reason are printed in the job summary, because
"no events were created" means something quite different in dry-run than it does with
writes permitted.

> **Not an inline `${{ }}`.** This used to be `DRY_RUN: ${{ inputs.dry_run || 'true' }}` in
the agent step and again in the apply step. On a `schedule` event `inputs` is an empty
object, so that expression is *always* `'true'`: with `ENABLE_CALENDAR_WRITES=true` the daily
cron planned every event, wrote nothing, and ran green every morning — the switch was dead
on the only path nobody watches. An Actions expression cannot be unit-tested, so the
decision now lives in a tested function. **Read the resolved output; never recompute it**:
a second inline expression is how the two halves of the apply step drifted apart.

## Cadence

`30 8 * * *` — 08:30 UTC (10:30 CEST), not earlier. Gemini's free-tier RPD resets at
**midnight Pacific** (07:00–08:00 UTC depending on DST), so a 06:30 run would burn the
previous day's quota. The run covers today…today+7d, so it still precedes typical 14:00+
tip-offs by hours.

Also triggerable via `workflow_dispatch` (with a `dry_run` input) and
`repository_dispatch` of type `streams-refresh`.

## Secrets and variables

| Kind | Name | Used for |
|---|---|---|
| Secret | `EXA_API_KEY` | search rung 1 |
| Secret | `TINYFISH_API_KEY` | search rung 2, render rung 2 |
| Secret | `FIRECRAWL_API_KEY` | render rung 1 (magenta.tv) |
| Secret | `GEMINI_API_KEY` | LLM rung 1 (AI Studio free tier) |
| Secret | `OPENCODE_ZEN_API_KEY` | LLM rung 2 |
| Secret | `OPENROUTER_API_KEY` | LLM rung 3 |
| Secret | `COMPOSIO_API_KEY` | the calendar credential (Composio tool execution) |
| Secret | `COMPOSIO_USER_ID` | whose connected account the call acts as |
| Variable | `LLM_MODEL` | pins the agent's model id (e.g. `opencode/big-pickle`); unset, `scripts/llm_model.py` picks it from the rung that has a credential |
| Variable | `ENABLE_CALENDAR_WRITES` | `true` enables Phase 2 |
| Variable | `ENABLE_AUDIT` | `true` enables Phase 3 |
| Variable | `ENABLE_SELF_IMPROVE` | `true` enables Phase 4 (`.github/workflows/self-improve.yml`) |
| Variable | `BASKETBALL_CALENDAR_ID` | calendar id for `scripts/calendar_io.py` (falls back to `config/calendar.json`) |

**Auth.** The calendar credential is Composio's. There is no Google Cloud project, no
service account and no OAuth token in this repository: `scripts/calendar_io.py` calls
Composio's tool-execution endpoint with `COMPOSIO_API_KEY`, and Composio holds the Google
grant for a **connected account**. `COMPOSIO_USER_ID` says whose grant to use; a
`COMPOSIO_CONNECTED_ACCOUNT_ID` can be given instead and takes precedence, at the cost of
breaking whenever the account is reconnected. Passing both is refused rather than resolved,
because the request would be ambiguous about whose calendar a write belongs to.

The trade is explicit: the credential that reaches a public calendar now lives with a third
party instead of being minted per run. What it buys is the whole WIF setup — OIDC provider,
service account, impersonation policy, calendar sharing — disappearing, and it is why the
`google-github-actions/auth@v2` step is gone rather than merely unused.

**Where the write gate lives is unchanged.** `calendar_io.py apply` performs no HTTP at all
without `--live`, `scripts/write_mode.py` decides whether `--live` is passed, and the job
summary states which mode ran — see *The calendar write switch* above.

**Three Composio behaviours are load-bearing and are not visible in a success path.**
`CREATE_EVENT` ignores `color_id`, so the colour is applied by a follow-up `PATCH_EVENT`
(a create is two calls, and a failed colour patch fails the run while still recording the
event id); it adds a Google Meet link and the connected user as an attendee unless
`create_meeting_room: false` / `exclude_organizer: true` are sent, which they always are;
and `visibility` — an event-level property, distinct from the calendar's sharing setting — is
sent on both creates and updates, from `--visibility`, whose default is
`calendar_config.get_visibility()`. That default is not a literal on purpose: the config file
declared this setting and **nothing ever applied it**, so which visibility an event got
depended on the calendar's own default rather than on the file that documents it.
`COMPOSIO_TOOLKIT_VERSION` is deliberately `latest`, not a pin: Composio's own guide records
that older pinned toolkit versions can drop or remap `timeMin`/`timeMax` before Google sees
them, and a dropped window filter returns the wrong window silently.

**Fallback for local runs:** export `COMPOSIO_API_KEY` and `COMPOSIO_USER_ID` and use the
same scripts. A **dry run needs neither** — `apply` without `--live` is inspectable offline,
with no credentials at all.

## Telemetry

Lives on the orphan **`telemetry`** branch, never on `main`. One squashed commit per run.

```
telemetry/
  run-log.jsonl        one row per candidate decision (append-only forever)
  candidates.jsonl     every surfaced candidate — the recall denominator
  audit.jsonl          post-hoc verdicts
  events.jsonl         created/updated events by event_id, one row per write
  evidence.json        {event_id: {live_confirmed, free_confirmed, paid}}
  metrics.json         derived snapshot, regenerable
  sources.json         per-source score snapshot
  rung-attempts.jsonl  one row per rung per host per run (append-only)
  rungs.json           per-rung health, DERIVED from rung-attempts.jsonl
```

`events.jsonl` and `evidence.json` are *produced by the `runtime` job* (which is
the only job that knows what it wrote) and *published by the `audit` job* (which
is the only one of the two with `contents: write`). The handoff is the
`run-ledger` artifact, and the split is the same one `rung-health`/`rung-issues`
uses. Their `action` field is `create` or `update` as the planner emitted it; a
`WRONG` relabel arrives as a later update from the audit, which is what the
"quarantined" case looks like in practice — the file never carries an invented
third action.

Append-only files are **never** pruned. Derived snapshots are rewritten each run and are safe
to regenerate. Growth control is an **annual gzip roll-up**: on the first run after 1 January,
the previous year's JSONL files move to `telemetry/archive/<year>/*.jsonl.gz`.

### Reading it

```bash
# The one-line summary of all three metrics
python3 scripts/metrics.py --dest telemetry --dry-run

# The per-run view: is this getting better?
python3 scripts/metrics.py --dest telemetry --trend --dry-run

# How good is recall right now?
python3 scripts/candidates.py --ledger candidates.jsonl recall

# What should we retry — cheap recall wins already in hand?
python3 scripts/candidates.py --ledger candidates.jsonl retry

# Which sources earn their keep?
python3 scripts/source_learning.py score --dest telemetry

# New source domains awaiting a human decision
python3 scripts/source_learning.py candidates --dest telemetry --dry-run

# Which render backends are parked right now, and for how many days?
python3 scripts/rung_health.py parked --dest telemetry --days 3 --markdown
python3 scripts/rung_health.py snapshot --dest telemetry --dry-run
```

`metrics.py` derives `metrics.json` and reports three different failures, kept
separate on purpose:

| Metric | Question it answers | Reads |
|---|---|---|
| `recall` | Of the games a backend saw, how many did we capture? | `candidates.jsonl` |
| `fixture_recall` | Of the games that actually happened, how many did any backend even *see*? | `fixtures.jsonl` + `candidates.jsonl` |
| `precision` | Of the events the audit could judge, how many were right? | `audit.jsonl` |

Two semantics are load-bearing. A missing input reports **`None`, never `0.0`**
— `0.0` would read as "we measured this and it was bad", which is a different
claim from "we have no data". And `INCONCLUSIVE` is **excluded from the precision
denominator**: an audit that reached no verdict is silence, and counting silence
as correctness is how a precision number starts flattering the thing it measures.

### The trend

`--trend` groups the same streams by `run_id` (an ISO-8601 UTC stamp, so lexical
order is chronological) and prints a per-run table plus two sparklines:

```
trend (4 run(s), oldest first):
run                    rows  games  elig  cap  recall judged  wrong  precis
2026-09-14T08:30Z         2      2     2    0   0.000      0      0     n/a
2026-09-15T08:30Z         2      2     2    1   0.500      0      0     n/a
2026-09-16T08:30Z         2      2     2    2   1.000      2      1   0.500
2026-09-17T08:30Z         0      0     0    0     n/a      1      0   1.000
recall    ▁▅█·
precision ··▅█
```

Three details are deliberate rather than cosmetic:

- **Per-run recall is not cumulative recall.** It answers "of the games *this*
  run saw, how many did *this* run capture?" — the number that actually moves
  when a gate is tightened. The same game is counted again on any day it is
  re-observed, which is what the ledger's append-per-observation shape means.
  Read it alongside the cumulative figure, never instead of it: a run that
  captures 1 of its own 1 candidate scores 1.000 while failing to exist for the
  other nine games that were played.
- **A run appearing only in the audit stream is still a row** (`2026-09-17`
  above: `rows=0`, `recall=n/a`, `judged=1`). Dropping it would hide the day a
  false positive was found.
- **The sparkline scale is absolute 0..1, not min/max.** Normalising to the
  observed range would draw `0.980` vs `0.985` as a dramatic climb, which is
  exactly the misreading a trend is supposed to prevent. `·` marks a run with no
  judgement, so a gap is visible instead of interpolated.

## Rung health and degradation

`scripts/render_ladder.py` climbs rungs cheapest-first and treats `blocked`
(`401/403/429/451`) as *keep climbing*, never *reject the game*. `skipped` — a rung with no
key, or a package that is not installed — climbs too: a rung that was never available is not
the target refusing us. Three consecutive failures park a rung for the rest of the run.

The state of an attempt has **one** name, `render_ladder.attempt_state()`: `evidence`,
`ok-no-evidence`, `blocked`, `failed`, `skipped`. The probe report, the `--probe --json`
payload and the rung ledger all read it from there, because a transcript that calls a
missing credential `failed` makes it indistinguishable from a retired backend — and the
ledger keeps that reading for months.

```bash
python3 scripts/render_ladder.py --list
python3 scripts/render_ladder.py --url https://www.magenta.tv/tv/live-... --plan
python3 scripts/render_ladder.py --url https://www.magenta.tv/tv/live-... --probe \
    --expect-teams "ALBA Berlin vs FC Bayern"

# Judge a page you already have (no fetch) — the only route for YouTube, which
# the ladder never renders, and how the recorded corpus is checked.
python3 scripts/render_ladder.py --body tests/fixtures/pages/youtube-channel-live.html.gz \
    --expect-teams "Tauranga Whai vs Northern Kāhu"
```

The ladder is **never empty**: the keyless stdlib `urllib` rung is in every ladder, first on a
server-rendered host and last on a JS host. Before it existed, a stdlib-only environment — which
is what `requirements-dev.txt` promises and what CI installs — had no rung that could run at all,
and the ladder *stopped* at its first unavailable rung, so every game became `UNVERIFIED` for a
reason that had nothing to do with the game.

### Attaching the game (not just the stream)

`has_stream_evidence()` proves the page is playing a live stream, not that it is playing *this*
game. `--expect-teams "A vs B"` adds `anchor_game()`, which matches the expected clubs against
the document's **own name** — `<title>`, `og:title`, `twitter:title`, YouTube's
`videoDetails.title` — and never against the body, because a body links to every club it
mentions. Two consequences worth knowing:

- A title that advertises **more than one pairing** (`"... USA vs France live | Germany vs
  Spain"`, a real recorded page) is reported `ambiguous` and does **not** pass: it satisfies
  "both teams appear" for every pairing it lists, so attributing it would put an arbitrary game
  in the calendar.
- The club-name rules are shared with the dedupe planner (`scripts/team_tokens.py`), so `Kāhu` =
  `Kahu` = `Northern Kāhu`, `München` = `Munchen` = `Muenchen`, and `ALBA` = `ALBA Berlin`.

Corpus and provenance: `tests/fixtures/pages/README.md`.

**Expected CI degradation.** GitHub runners use datacenter IPs, so `magenta.tv` will be blocked
harder than from a residential connection. The correct response is to mark the game
**UNVERIFIED** (recall preserved, uncertainty visible) — not to drop it.

### Rung health across runs ("is a backend dead, or did I forget a key?")

The in-run tracker parks a rung for the rest of *one* run and is then gone, so a backend can
be retired — as GitHub Models was on 2026-07-30 — with one `failed` line in one morning's log
as the only trace. The daily `rung-health` job probes three hosts per run and
`scripts/rung_health.py` records the outcome:

```bash
python3 scripts/rung_health.py probe --dest .tmp/telemetry \
    --url https://www.magentasport.de/ --url https://www.championsleague.basketball/ \
    --url https://www.magenta.tv/
python3 scripts/rung_health.py snapshot --dest .tmp/telemetry
python3 scripts/rung_health.py parked --dest .tmp/telemetry --days 3 --markdown
```

`rung-attempts.jsonl` is append-only (a `(run_id, rung, url)` row is never written twice, so a
retried workflow does not inflate its own history). `rungs.json` is derived from it — delete it
and it regenerates. The three hosts go through **one** invocation with **one** strike tracker:
three separate probes would give a dead backend one strike each and never park it.

The finding is deliberately narrow, because a tracker that cries wolf is a tracker nobody reads:

| Case | Recorded | Files an issue |
|---|---|---|
| A hosted rung fails, any status | yes | **yes** — a provider rejecting the key is the retired-backend case |
| A local rung cannot connect at all | yes | **yes** — it is broken, not refused |
| A local rung refused (`403`) by the target | yes | no — that is the refusal the ladder is built to climb past |
| A rung with no key, or a package not installed | yes | no — `skipped` **breaks** a strike streak |
| A day with no rows at all | — | no, and it **breaks** the streak; the gap days are named on the issue |
| A rung that worked on the latest observed day | yes | no — a streak ends at the rung's latest observed day |

The last two are the ones that decide whether this is trustworthy. A gap is not evidence of
anything, so a dead backend and a dead cron must not produce the same report; and a rung that was
fixed today must not keep re-reporting last week's streak, or the issue can never close.

Filing is a separate job from producing, the way `self-improve.yml` holds no calendar credential:
the job that probes and commits holds no issue permission, and the job that files holds
`issues: write`, no search or render key, and never writes the branch. A run with nothing parked
never invokes `gh` at all.

### What counts as stream evidence (the gate before a calendar write)

`FetchResult.has_stream_evidence()` decides whether a rung's response is usable
at all, and it is the last thing standing between a rendered page and a link in
the calendar. It requires **both** a media marker (`<video`, `.m3u8`, `.mpd`, a
stream content type, `og:video`) and a live-state marker (`Jetzt live`, or a
structured YouTube field such as `"isLiveContent":true`). Media markers are
matched with `<script>` blocks stripped; the structured fields are matched
against the raw document, since that is the only place they occur.

Bare vocabulary is deliberately excluded. The rule until 2026-09-15 accepted
`player`, `dash`, `live`, `läuft` anywhere in the document, and every modern page
ships its JS runtime inline — so on the recorded corpus it accepted a **recorded
YouTube video**, the **YouTube home page**, the **MagentaSport announce home
page** and the **Basketball Champions League home page**. All four are stored as
fixtures under `tests/fixtures/pages/`, and a test requires the old rule to keep
failing on them.

```bash
python3 scripts/record_pages.py --check      # offline gate (validate.yml + page-corpus sensor)
python3 scripts/record_pages.py --list       # what the corpus covers
python3 scripts/record_pages.py --refresh    # re-fetch, then read the verdict diff
python3 scripts/render_ladder.py --url <u> --probe   # per-rung evidence report
```

Adding a marker means adding a recorded page that proves it discriminates. The
corpus is a screen, not a proof: Check 1 (free-access announcement, matched on
*teams*) and Check 4 (basketball-specific) still have to hold.

### The corpus is re-fetched weekly, because the gate can age

`--check` is offline and answers a question about **bytes**: do the stored pages
still classify the way the manifest says? That cannot notice a page that changed
shape — a re-branded player, a moved `isLiveContent` field — which is precisely
the drift that silently turns a working gate into a blind one.

`.github/workflows/corpus-refresh.yml` runs Mondays 06:00 UTC, re-records every
page into a throwaway directory, and compares the gate's verdict with the
recorded one:

```bash
# What the weekly job does, locally (needs network); the FLIP: lines are the finding
python3 scripts/record_pages.py --refresh --out .tmp/corpus --baseline tests/fixtures/pages

# What it would file, with no GitHub access
python3 scripts/corpus_flip_issue.py --report .tmp/refresh.json --dry-run
```

**Only a verdict change files anything.** A byte change is expected — the stored
YouTube fixtures are extracts of a 1.3 MB document whose hash moves whenever a
sidebar suggestion is edited — so reporting it would file an issue every week
until nobody read the issues. A persistent flip comments on the open issue rather
than opening another, and a run in which no verdict changed touches GitHub not at
all.

| Flip | Means | Fix |
|---|---|---|
| `lost-evidence` | a page playing a real stream is no longer recognised → **missed games** | if the source changed, update `PAGES`/`expect` and re-record; if the gate broke, fix `render_ladder.py` and leave the corpus alone |
| `gained-evidence` | a page playing nothing is accepted → **false positives** | same two questions, opposite urgency: this is the failure the corpus exists to prevent |

Neither fix is "edit the fixture": `--check` refuses a stored file whose hash is
not the recorded bytes, so a corpus edited into agreement would be an assertion
about a file we wrote rather than about the web. The refreshed corpus is a build
artifact; anything committed goes through a PR, where `validate.yml` re-runs the
offline gate.

A **failed fetch is not a flip.** `record_pages.py --refresh` stops rather than
partially updating the corpus (an in-place refresh would delete the stored files
of the pages that failed), so the workflow warns, names the page in the log, and
files nothing — one dead URL must not look like a finding about the gate.

## Licensing — read before adding a rung

This repository is MIT. `nodriver` is the strongest anti-bot tool (zero blocked targets in the
2026 benchmark) and is **AGPL-3.0**, so:

- it ships **only** as an opt-in rung, never in the default ladder;
- shipped code never imports it — invocation is subprocess- or extra-based and fails with an
  actionable message when absent;
- it is never listed in `requirements-dev.txt` or any workflow;
- a test asserts the default ladder contains zero AGPL entries.

If you install it yourself, AGPL obligations attach to **you** if you distribute a service
built on it. `assert_shippable()` enforces the first point mechanically; the rest is documented
because no code can enforce it for you.

## LLM budget

Free-tier quotas are volatile, enforced **per project**, and explicitly "not guaranteed" — a
daily run must be designed for the worst credible case (~20 requests/day). Therefore:

- batch judgement to **one call per game's evidence bundle**, never one call per candidate;
- cap total calls (~20) and fall back to deterministic-only labelling when the cap is hit;
- prefer the Lite tier; escalate to a larger model only for a genuinely ambiguous case;
- quota exhaustion **degrades**, it never fails the run — games become `UNVERIFIED` rather than
  being dropped.

### The ladder, and how to check it

The runtime ladder is **Gemini free (AI Studio) → OpenCode Zen free → OpenRouter free**, with
failover. All three rungs are **$0**: the AI Studio free tier is a separate product from the
paid Gemini API and needs no GCP billing.

```bash
# Report: which rungs exist, which credentials are set, whether the OpenRouter
# key is *accepted* (a free GET /api/v1/key), and which models the free Gemini
# key can reach (Lite tier first). Exits 0 whenever it managed to report, even if
# the report is "every rung is down". Spends no generate call.
python3 scripts/capture_transcripts.py --list-models

# Gate: fail when NO rung is configured at all. Offline and deterministic, so
# the scheduled run preflights on it (and fails in seconds, not after a search
# ladder has run).
python3 scripts/capture_transcripts.py --check-rungs

# Capture the whole eval set, failing over across the rungs.
python3 scripts/capture_transcripts.py --runner ladder \
  --out tests/fixtures/runtime_transcripts.json
```

`--check-rungs` deliberately tests **configuration, not validity**: it is
presence-based so it cannot red a run for a network reason, and it accepts the
CLI's own providers (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) as well as the
documented ladder's, because a preflight that reds a correctly configured runner
gets deleted. What `--list-models` shows is validity, and it probes **each rung
that can be probed**: `GET /api/v1/key` for OpenRouter and `opencode --version`
for the CLI. Both exist because this repo hit exactly these failures —
`OPENROUTER_API_KEY` set and returning `401 User not found`, and `ANTHROPIC_AUTH_TOKEN`
set next to an `opencode` shim on `PATH` that cannot start. In both cases the
*configuration* report says `OK` while the rung cannot serve a request, so
`--check-rungs` alone would pass the preflight and the run would then capture
nothing — the silent failure the preflight exists to prevent.

Two more rules are worth knowing before you touch this:

- **The model id is discovered, not pinned.** `--list-models` reads `models.list` and ranks the
  Lite tier first, because the Lite tier carries the free RPD budget. `GEMINI_DEFAULT_MODEL` is
  only reached when that probe fails, and the run prints which source it used. A pinned id is
  how a rung rots unnoticed (GitHub Models was retired 2026-07-30 and nothing noticed).
- **A partial capture writes no file at all.** The grading gate keys on
  `tests/fixtures/runtime_transcripts.json` *existing*, so writing the 30 cases that worked
  would present an incomplete capture as a complete one. The script exits 1 and names the
  missing cases instead.
- **Which model answered is recorded, not assumed.** `rungs` says which *rung* served each
  case; `models` says which *model* did, where the rung can support the claim. `openrouter` is
  the case that forces the key to exist — `openrouter/free` is a router, so the id in the
  request is deliberately not a model, and the response's own `model` field is the only
  evidence. `gemini` is a direct call to the id it resolved, so that is recorded too.
  `opencode` is deliberately **absent** from `models`: the CLI picks its own model and may fail
  over inside itself, so the id on the command line is what was *asked for* rather than what
  answered — and a wrong attribution is worse than none. A case that produced no output is
  never attributed at all.
- **The capture prompt carries no answer.** It states the output *form* and points at
  `references/validation-workflow.md` for the check names. It used to append
  `expected_output[:200]`, which for a short case is the answer itself — and because grading
  collects `name=PASS|FAIL` needles by substring, a model that echoed the prompt back would
  have scored 100%.

`GEMINI_MODEL` pins the id for the Gemini rung only; `--model` belongs to the CLI rung
(`opencode/…`) and is deliberately **not** passed to either HTTP rung in `--runner ladder`
(`resolve_gemini_model` and `openrouter_model_for` apply the same guard).

### Which model the agent run uses

`capture_transcripts.py` chooses its model by credential at capture time, and the agent run
has to choose the same way — a Zen model id as the unconditional default asks the CLI for
something a Gemini-only or OpenRouter-only repository has no credential for, and the run then
produces no transcript, which is indistinguishable from a quiet day. `scripts/llm_model.py`
resolves it in ordinary Python, following the ladder's own order, and both workflow steps read
`steps.llm.outputs.model` rather than re-deriving it:

| Situation | Model |
|---|---|
| `vars.LLM_MODEL` set | used verbatim — the pin wins over everything below |
| `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | `google/<gemini id>` — rung 1 |
| `OPENCODE_ZEN_API_KEY` | `opencode/big-pickle` — rung 2 |
| `OPENROUTER_API_KEY` | `openrouter/openrouter/free` — rung 3, the router that selects free models |

```bash
# What would this repository actually run? Reports rung, model and reason.
python3 scripts/llm_model.py
```

Two boundaries are deliberate. It is a **selector, not a gate**: `--check-rungs` is the gate on
"at least one rung is configured", and duplicating that here would both give the repo two
answers to one question and break `replay_ci.py`, whose replays strip every `*_API_KEY` by
design. And a runner authenticated only through `opencode auth login` is **not** detected — it
reads the environment, not `~/.local/share/opencode/auth.json` — so that case is what the
`LLM_MODEL` pin is for.

**What is verified about those ids, and what is not.** `opencode` composes a model reference as
`provider_id/model_id`, and a model whose own id contains a slash therefore produces a
three-segment reference (`edenai/anthropic/claude-sonnet-5` is the documented example), so
`openrouter/openrouter/free` is the correct shape for the free router: provider `openrouter`
(confirmed in OpenCode's provider directory, which also documents adding models under
`provider.openrouter.models`) plus the slug `openrouter/free` (confirmed in OpenRouter's own
docs). `opencode/big-pickle` was already the repo's rung-2 id. `google/<id>` for rung 1 is the
one **unverified** value: OpenCode's provider directory documents *Google Vertex AI* but no
Google AI Studio entry, and models.dev — which supplies the provider ids — was not reachable
from here. It is a loud failure rather than a silent one (an unknown provider aborts the CLI,
and the step also prints the rung, the model and the reason), and `LLM_MODEL` overrides it.
Confirm with `opencode models` before relying on it.

## Recovery runbook

| Symptom | Action |
|---|---|
| `telemetry` job red, no rows written | Check the API keys; verify with `--dry-run` locally. An empty ledger is honest, not a bug |
| Every rung blocked for `magenta.tv` | Expected from datacenter IPs. Games should be `UNVERIFIED`; do not "fix" by loosening Check 6 |
| `recall=0.0` with a non-zero denominator | Correct for Phase 0/1 — nothing is `created` yet, so `captured` is 0 by construction |
| The run plans events but the calendar never changes | Read the `Write mode` row of the job summary. `dry-run` with `ENABLE_CALENDAR_WRITES=true` means the mode resolver disagreed with you, and the `Decided by` row names the input that did it. This is the exact failure the inline `${{ }}` used to hide — it was dry-run forever, on every cron, with no way to tell |
| Telemetry job is green but the ledger did not grow | Check the ladder step first: it tolerates a *content* failure (reachable backend, no results, or a transient HTTP error) so a 429 cannot take the commit down with it. A run with **no backend configured at all** is a different thing and reds at the `--check-backends` preflight, which is deliberately not tolerated — a green run that recorded nothing looks exactly like a quiet day |
| Every game is `UNVERIFIED` and every rung says "not installed" / "not set" | Expected in a bare environment, and the *reason* is printed per rung now. Install a rung from `render_ladder.py --list`, or accept that only the keyless `urllib` rung runs: it can carry evidence on a server-rendered host (`championsleague.basketball`, `magentasport.de`) and only reveals the app shell on `magenta.tv` |
| A live page passes the evidence gate but no event is created | Check the anchor: the page's own title must name the expected clubs. A keyword-stuffed title (`"A vs B \| C vs D"`) is deliberately `ambiguous` — read the job summary rather than relaxing the rule |
| `run_daily` reports `0 candidates from [...] of [...] attempted` | The first list is what contributed a row, the second what answered. If a backend is in the second and not the first it is reachable and returning nothing usable |
| A `[rungs] render rung parked` issue, and every game `UNVERIFIED` | Backends die permanently (GitHub Models was retired 2026-07-30). The issue names the rung, the consecutive days, the last error and any gap days. Replace the rung, or restore its credential if the error is a `blocked`/401 |
| `rung-health` job green but `rungs.json` unchanged | It only changed when nothing new was recorded — check `rung-attempts.jsonl` first. Every run appends rows for a new `run_id`, so a run that added none did not reach the probe step |
| A rung is parked but no issue appeared | Read the `fileable` rules above. A local rung refused by a target (403) is recorded and deliberately never filed, and a `skipped` rung is not a failure |
| Event marked `WRONG` | Working as intended. Fix the gate on the self-improvement branch, never by editing the event |
| LLM quota exhausted mid-run | Expected. Confirm the run degraded to `UNVERIFIED` instead of failing |
| The run fails in seconds at **Preflight — at least one LLM rung is configured** | No credential at all: set `GEMINI_API_KEY` (a free AI Studio key needs no billing) or one of the CLI providers. This is a misconfiguration, and it is deliberately not tolerated — without it the run cannot apply the 7 checks, and the summary would look like a quiet day |
| `--list-models` shows `NO openrouter key: HTTP 401` | The key is set but rejected. A present-but-dead credential is why the probe exists; replace the secret |
| `--list-models` shows `NO opencode cli: exit 1: ...` while `--check-rungs` says `OK rung opencode` | The rung is *configured* (a token is set) but the CLI cannot start — e.g. a package-manager shim that never installed. Presence and usability are different questions; the capture will fail with `no output for case(s) [...]` |
| `audit` job notices missing inputs | Phase 2 has not written any events yet. It records a notice and does nothing rather than inventing a verdict |
| `INCONCLUSIVE` every day | Evidence is not being produced. Check the gate scripts emit `live_confirmed`/`free_confirmed`, not just a link status |
| Duplicate events appear | `scripts/upsert_events.py --existing … --candidates …` will show why. If a club's short and long names differ beyond one being a subset of the other, extend `_team_matches`, not the window |
| `unseen` is non-zero | Expected, and the point of Phase 5: games no backend surfaced. Feed them into the search queries rather than lowering a gate |
| `fixtures.py` returns nothing | A site changed its markup, or carries no JSON-LD. Capture the page as a fixture and add a parser; do **not** loosen the "no match means no fixture" rule |
| `extract_candidates` rejects everything | The agent did not emit a fenced ```json block. Check the transcript before suspecting the parser |
| Job says "the per-case grader did not run" | Correct and not a failure: this job makes one run for today, not one per eval case, so per-case grading needs a captured set (`--runner ladder`, see above). The step grades — and fails hard — the moment that file exists |
| A workflow step no-ops with no error | It may be naming a file that does not exist. `python3 scripts/check_workflow_refs.py --root .` names the path and the workflow |
| `self-improve` gate red | Working as intended — the system refused its own fix. Fix the gate; never re-baseline the eval set to clear it |
| `self-improve` warns "the new case(s) were NOT graded against real output" | The per-id capture found no working rung. Set `GEMINI_API_KEY` (free AI Studio); until then the synthesised case's pre-fix-red property is asserted, not demonstrated |
| `runtime_eval` says `names case ids not in evals.json` | The transcript file is stale — it covers a case that has since been renumbered or removed. Re-capture; do not hand-edit it |
| `corpus-refresh` opened a `[corpus] page verdict flip` issue | Not noise by construction — a byte change files nothing, so a verdict moved. Read the direction: `lost-evidence` is missed streams, `gained-evidence` is the false positives the corpus exists for. Decide whether the source or the gate moved; the issue lists both routes and the commands |
| `corpus-refresh` warns it could not re-fetch every page | The log names the page. A URL that has gone (an aged VOD, a moved channel tab) is a corpus problem, not a gate finding, and nothing was filed. Update `PAGES` with a replacement page rather than deleting the fixture |

## Verifying the runtime locally

```bash
python3 scripts/run_daily.py --check-backends   # gate: is any backend configured?
python3 scripts/run_daily.py --dest .tmp/telemetry --dry-run
python3 scripts/candidates.py --ledger .tmp/telemetry/candidates.jsonl recall || true
python3 scripts/render_ladder.py --list
python3 scripts/rung_health.py probe --dest .tmp/telemetry \
  --url https://www.magentasport.de/ --dry-run
python3 scripts/rung_health.py parked --dest .tmp/telemetry --days 3 --markdown
python3 scripts/verification.py
python3 scripts/upsert_events.py --existing existing.json \
  --candidates candidates.json --json
python3 scripts/audit_events.py --events events.jsonl --evidence evidence.json \
  --out .tmp/telemetry/audit.jsonl --dry-run
python3 scripts/extract_candidates.py \
  --transcript tests/fixtures/agent_transcript_sample.json --out .tmp/candidates.json
python3 scripts/calendar_io.py apply --plan .tmp/plan.json
python3 scripts/fixtures.py --input tests/fixtures/fixtures_page_bbl.html \
  --source bbl --now 2026-09-14T08:30:00Z
python3 scripts/candidates.py unseen --ledger .tmp/candidates.jsonl \
  --fixtures tests/fixtures/league_fixtures.jsonl
python3 scripts/synthesise_eval_case.py --verify
python3 scripts/check_workflow_refs.py --root .
python3 scripts/capture_transcripts.py --check-rungs
python3 scripts/source_learning.py record --source exa-mcp --outcome reject \
  --url https://example.com/live --dest .tmp/telemetry --dry-run
python3 -m pytest tests/
```

No credentials are needed for any of the above — every path has an offline mode, which is what
keeps this repo's test suite runnable in CI without secrets.

To reproduce **CI itself** rather than the individual scripts — the only way to see a failure
that appears on a fresh checkout and nowhere else — replay a workflow's shell steps against a
clean copy:

```bash
python3 scripts/replay_ci.py --workflow .github/workflows/validate.yml
python3 scripts/replay_ci.py --workflow .github/workflows/verify.yml --job product-graders
python3 scripts/replay_ci.py --workflow .github/workflows/runtime-daily.yml --json
python3 scripts/replay_ci.py --workflow .github/workflows/corpus-refresh.yml
```

Steps that could write outside the replay directory are skipped, never executed, and the parent
environment's `*_API_KEY`/`*_TOKEN` variables are stripped — so a replay cannot spend quota,
touch the calendar, or open an issue: `--live`, `git push`, Google auth, the runtime agent and
GitHub writes (`gh issue create`, `corpus_flip_issue.py --file`) are all denied, and the denial is
on the *capability* (the flag) rather than the step name, since a replay cannot delete what it
created on GitHub. The read-only halves (`gh issue list`, `--markdown`) stay replayable. A `FAIL`
in a job that needs secrets (`runtime-daily`'s backend preflight) is the replay reporting that CI
would need them, not a defect.
