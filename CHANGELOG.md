# Changelog

All notable changes to `skill-basketball-streams` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Notes

- **A sweep for settings that are read but never written found a second instance — and the
  "documented gap" it produced was the wrong answer.** The first instance was `visibility`, now
  fixed. The second was **`defaultColorId`**: `calendar_config.get_default_color_id()` existed
  with **no code path calling it**, while the same value was hardcoded as the literal 6 in four
  more places (`upsert_events`, `audit_events`, `event_ledger`, and `color_mapping`'s fallback
  rule). Measured rather than inferred: with the config set to `defaultColorId: 3`,
  `get_default_color_id()` returns 3 and a plan row for an unmapped league still carries 6 —
  **changing the field changes nothing.** It was first left open as a documented gap, on the
  reasoning that wiring it required a product decision (override the league table, or only
  stand in for a league it does not know?). That reasoning rested on a false premise: the value
  is not a preference, it is `verification.STATE_COLOR_IDS["VERIFIED"]`, and the field was
  therefore **removed rather than wired** — see the `Changed` entry below for the evidence.
  The lesson worth keeping: *reported and not fixed* is a legitimate outcome only while the
  open question is real. Here the question was the defect's own framing, and a month of
  documentation would not have found the collision that made the answer obvious. The other
  three classes checked were clean: every telemetry artifact named in `docs/runtime.md` has a
  module that writes it, every secret and variable in the docs table is used by a workflow, and
  every env var a workflow passes to a step is read by something (the shell, for the three the
  first pass flagged).

### Added

- **`scripts/link_inventory.py` — the producer `link_check.py --input` never had — and
  `scripts/stream_links.py`, the vocabulary its writer and its reader share.**
  `references/self-learning.md` lists link revalidation as one of five learning loops, and its
  input is `links.json`: a file named in that table, in `SKILL.md`, in `docs/do-harness.md` and
  in `link_check.py`'s own docstring, and **produced by nothing**. The loop could not run even
  in principle — the same shape as `telemetry/events.jsonl` and `evidence.json` before
  `event_ledger.py`, and it had a second cause one layer up (see *Fixed*). The inventory is
  built from two places because the links live in two: the stored event descriptions, read back
  out of the `FREE STREAM LINKS:` block, and `config/sources.json` — which is the half no check
  covered, since of its **22 approved domains only 4** are reachable from the recorded page
  corpus, so `fiba.basketball`, `euroleaguebasketball.net`, `sportschau.de`, `zdf.de`,
  `joyn.de`, `pluto.tv`, `zattoo.com` and 11 others had never been tested at all. Each entry
  carries a `kind` (`calendar-event` / `approved-domain` / `approved-channel` /
  `validation-account`), because the right reaction differs: a dead event link is quarantined, a
  dead domain means the tier table is stale, and a `validation-account` answering 401/403 is the
  **expected** result rather than a finding. It makes no network request, so it is deterministic
  and can gate a change, while `link_check.py` remains the probing half and deliberately gates
  nothing — a league behind a rate limit must not red a build. `events_without_links` is
  reported rather than dropped, because "the calendar has no dead links" and "no event had a
  link to check" must not read the same.

- **The rehearsal can now be filled in from one gitignored file, run with no credential at
  all, and pasted into the release PR.** `.env.example` lists every variable `build_checks()`
  names with where to get it, and `--env-file PATH` loads it — **explicitly, never
  discovered**, so a stray `.env` cannot change what a report says about the environment it was
  given, while an already-set environment variable wins over the file so a single run stays
  overridable (`GEMINI_API_KEY=other python3 scripts/rehearse.py --env-file .env`). It mutates
  `os.environ` rather than a private dict because the probes are the *runtime's own* code and
  read the environment directly; a malformed line is refused by name and line number, because
  a skipped `GEMINI_API_KEY gemini-…` reads as `missing` and sends the reader to Google
  instead of to their own typo. `--offline` probes only the credential-free checks, which is
  the half that now gates CI (`validate.yml`, both matrix versions): every workflow that files
  an issue must declare `issues: write`, or the run reads fine and the issue never appears.
  `--markdown` writes the same table to stdout for the PR, and `--json --markdown` is a usage
  error rather than an interleave — two payloads cannot share stdout. `.env` is gitignored and
  `.env.example` is not, both asserted, because the file holds a calendar API key and this
  repository is public. Six new guards, all falsified before being trusted.
- **`scripts/rehearse.py` — one command for every credential a release needs, reported
  `missing` / `valid` / `invalid` / `unverified`.** It exists because a credential that is *set*
  is not a credential that *works*, and this repository had no single place that asked the second
  question about the whole system. `--check-rungs` and `--check-backends` are presence-based
  **by design** — the reasoning is written down in both, "a preflight that reds a correctly
  configured runner gets deleted" — so a dead key reads as configured everywhere except in a
  live probe. Measured on this checkout: `OPENROUTER_API_KEY` is set and answers
  `HTTP 401 User not found`, and `ANTHROPIC_AUTH_TOKEN` is the **same secret** while the
  `opencode` CLI it is supposed to authenticate refuses to install — two rungs reported `OK` by
  a presence check and `invalid` here. The command covers the calendar, GitHub issues, the three
  model rungs, both search backends and an optional YouTube Data API, and **imports every probe
  rather than restating it** (`probe_opencode_cli`, `probe_openrouter_key`, `list_gemini_models`,
  and the backends' own `last_error`), so it cannot green-light a rung the ladder does not read —
  pinned by two registry tests that fail when a rung or backend is added without a surface.
  **It never writes**: the calendar probe reads one day, the search probes ask for a single
  result. `missing` and `invalid` are kept apart deliberately: an unconfigured surface is a
  legitimate local state, a *rejected* one means the runtime will try it and fail at 06:00, and
  only the second is fatal (with `--require-all` extending that to required-but-missing). Only
  `assess(checks, environ, prober)` — a pure function — is tested, because the live half must not
  become a sensor: `do-harness` sensors are offline and deterministic, and a free provider having
  a bad day cannot be allowed to red the build. Wired into the release flow in
  `CONTRIBUTING.md` and `scripts/README.md` → Releasing, where the harness has no step.

- **The rehearsal grew the two surfaces a credential list cannot answer, and the search for
  them found a real gap.** Three additions, each from something that went wrong:

  - **`github-issues:grant` — the CI half of the GitHub surface.** On a runner `GITHUB_TOKEN`
    is *always* set, and `gh api user` succeeds for a token scoped read-only, so the token
    probe can only ever prove **identity**. Whether the surface works is decided by the
    workflow's `permissions:` block, which is what this check reads (offline). The live row
    now says so in its own result rather than leaving a bare `valid` to be over-read.
  - **A false-negative found by falsifying the guard, not by reading it.** The first version
    checked `"issues: write" in text` — and `corpus-refresh.yml`'s own header comment says
    "`contents: read` plus `issues: write`", so the check reported the grant as present
    **after it had been deleted from the YAML**, and the test still passed. A rule reading a
    string no block writes, which is the fourth time this repository has found that shape. It
    is now a structural scan of `permissions:` blocks, pinned by a test that asserts a comment
    mentioning the grant is not the grant. The detector also had to grow: a script-name list
    alone **misses `self-improve.yml`**, which files with `gh issue create` directly — while a
    bare `--file` would be a false positive, since `opencode` takes attachments with the same
    flag.
  - **`render:firecrawl`, and the registry test that found it missing.** `FIRECRAWL_API_KEY`
    is a real secret the workflows pass (`render rung 1`), and the per-component registry tests
    in the new file would not have noticed, because it belongs to neither the model ladder nor
    the search backends. The test is now over the **union** of every credential the three
    runtimes read, and it fails with `unrehearsed credential(s): <name>`. Its probe is
    deliberately absent (`unverified`): the ladder treats `blocked` as "climb", so a dead key
    (provider `401`) and a working ladder (target `403`) look identical, and a probe would
    spend a paid credit to say so.

- **`SETUP.md` → Production credentials (the dress rehearsal).** One row per surface with
  where each key comes from, what `OK`/`NO`/`FAIL` mean, and the `--require-all` gate, plus a
  test that every surface in `build_checks()` appears there — a surface nobody can configure
  is a `NO` row with no instructions.

- **`validate.py --check calendar-config` refuses an unaccepted `visibility`, with the smoke-test
  fixture the rule requires.** `calendar_io.py --visibility` defaults to the config value and
  sends it on every create and update, so an unaccepted one was rejected by the API *at write
  time*, naming its own parameter rather than the config file that supplied the typo. Caught
  before a run needs it. `validate.py` restates the enum rather than importing
  `calendar_config.py` — it is deliberately self-contained and its smoke-test fixtures are bare
  directories with no `scripts/` in them — so `tests/test_validate.py` asserts the two lists are
  equal; that assertion is what keeps the restatement from becoming a second truth. The
  self-test's fixture registry now accepts **more than one fixture per check**, because `_fail`
  exits on the first branch it trips: `calendar-config` has two FAIL branches and a single entry
  would have left the newer one permanently unexercised while the output still read
  `OK: smoke-test` (4 subprocess invocations became 5).
- **`tests/fixtures/composio/` + `tests/test_composio_contract.py`** (NEW) — the calendar
  transport's argument contract, checked against the **provider's own recorded schema** rather
  than against a fixture this repo wrote. `tests/test_calendar_io.py` pins how Composio's
  *responses* are parsed, and a hand-built envelope supplies whatever the test author believed
  the API sends — so nothing in it could tell you whether the argument names go anywhere. The
  recorded corpus (`GET /api/v3.1/tools/{slug}`, read-only, five tools, toolkit `20260915_00`)
  closes that: every argument `create_arguments`/`patch_arguments`/`list_events` sends is asserted
  to be a real property of the tool it is sent to, obtained by *calling* the mappers so the test
  cannot drift from the code. Two of the assertions are the reasons the transport is shaped the
  way it is — **`CREATE_EVENT` has no `color_id` property** (which is why a coloured create is two
  calls) and **`visibility` is a real property of both event tools** — and recording them found
  two things a hand-built fixture never would have: `start_datetime` is the *only* required
  property of `CREATE_EVENT`, so omitting `calendar_id` does not fail but silently targets the
  user's **primary** calendar; and the `create_meeting_room` / `exclude_organizer` descriptions
  are the provider's own statement that both default to the opposite of what a public streams
  calendar wants. Follows `tests/fixtures/pages/`'s rule — *inputs may be authored, outputs must
  be captured* — with the same provenance README and the same "residual risk, stated plainly"
  section, which records that the `CREATE_EVENT` **response** envelope is verified but not
  stored (storing it means writing to a public calendar on a schedule).
- **A live smoke test of the whole write path, run 2026-09-15.** One labelled
  `[UNVERIFIED] SMOKE TEST — ignore` event on the group calendar, created through
  `calendar_io.py apply --live`, read back raw, then deleted — with a before/after event count
  proving nothing was left behind. It confirmed seven things the offline suite cannot: the
  `visibility` value is **accepted and stored** as `public`; the colour really does need the
  follow-up `PATCH` (it came back `colorId: 5`); **no Meet link and no attendee** were added; the
  event id is recorded in `applied.json`; and the `League:`/`Teams:` description round-trips
  through `parse_event`, which is the property the dedupe planner depends on. The read path was
  smoke-tested the same day (5 real events, states recovered from title prefixes).
  One thing that run surfaced and this change does **not** fix: every event on the live calendar
  carries `teams: []`, because they were authored by hand — and `upsert_events.events_match`
  skips the teams check whenever either side is empty, so a hand-made event still matches *any*
  candidate inside the 30-minute window.
- **`models` in the capture payload — which model wrote each transcript, not just which rung.**
  `rungs` already recorded the rung per case, and that is not the same claim:
  `openrouter/free` is a *router*, so "the openrouter rung served this" leaves "which model
  produced this grading failure?" unanswerable. The answer is read from the response's own
  `model` field (`LAST_MODELS`), because for that rung the id in the request is deliberately
  not a model. `gemini` records the id it resolved, since a direct call to one id is its own
  evidence. `opencode` is deliberately **absent**: the CLI picks its own model and may fail over
  inside itself, so the id on the command line is what was *asked for* — and a wrong
  attribution is worse than none. A case that produced no output is never attributed, so a
  stale per-rung record cannot credit a failed case to the model that answered the last one.
  The top-level `model` key (the single-rung shape) is now derived from the same record rather
  than from `_GEMINI_MODEL` directly, so the two keys cannot disagree.
- **`scripts/event_ledger.py`** (NEW) — **the writer Phase 3 never had.** `§10.1` lists
  `telemetry/events.jsonl` and `docs/runtime.md` documents `telemetry/evidence.json`, the `audit`
  job reads both, and its own comment calls them "two artefacts that Phase 2 must produce" — but
  nothing produced them. Every run therefore found its inputs absent, printed a notice, and did
  nothing: Phase 3 had never run, `metrics.json` reported precision `n/a` for the life of the
  project, §17's "precision ≥ 0.95" was unmeasurable, and §9's loop had no ledger to learn from.
  `record --plan p.json --applied a.json --dest DIR --run-id ID` writes both from the **applied
  result** (what the calendar now holds, and under which id) plus the plan row, never from the
  plan alone, which only says what was intended. Four rules, each of which is a way to be wrong:
  a **dry run records nothing** (there is no event, so a verdict about it would be about
  nothing); an event with **no `event_id` is refused** rather than keyed to an empty string,
  because the audit resolves a verdict back to an event by id and such a row could never be
  acted on; **evidence is copied from explicit flags and never derived**, because a missing key
  is `INCONCLUSIVE` and an explicit `false` is `WRONG`, so a default would relabel live games as
  wrong; and the ledger is **append-only**, with the duplicate rows the audit collapses — so the
  "never pruned" promise in §10.3 stays literally true without one event being judged twice.
  `evidence.json` is derived, so a touched event's entry is **replaced** rather than merged: a
  union would let yesterday's `live_confirmed: true` outlive today's observation.
- **`scripts/evidence.py`** (NEW) — the `{live_confirmed, free_confirmed, paid}` contract in one
  place, used by `extract_candidates.py` (which must carry the flags through) and
  `event_ledger.py` (which must record them). `coerce_evidence()` has **no defaults**: only an
  unambiguous flag survives (a real bool, or `true`/`false`/`yes`/`no`/`1`/`0` — a model writes
  those), and `"unknown"`, `null` or a number means *not recorded* rather than `false`. Two
  modules disagreeing about that distinction would be enough to relabel a live game as wrong.
- **`.github/workflows/runtime-daily.yml`** — the `run-ledger` artifact handoff. The `runtime`
  job writes the ledger (it is the only job that knows what it wrote) and **uploads** it; the
  `audit` job **downloads** it into the telemetry worktree and commits it. The split exists
  because `runtime` holds the calendar credential and deliberately has no `contents: write`, and
  because an artifact adds no permission — the same producer/publisher separation `rung-health`
  and `rung-issues` use. The audit's `needs: runtime` is an **ordering** dependency only: its
  condition is `always()`, so it still runs on a day the skill run failed, which is when a false
  positive is most likely to have been created.
- **`tests/fixtures/upsert_verdict_existing.json`, `upsert_verdict_candidates.json`,
  `upsert_verdicts.jsonl`** (NEW) — the pair the `upsert-verdicts` sensor runs. Two `UNVERIFIED`
  events that both candidates would promote, one of which the audit has condemned, plus a third
  ledger row (an `INCONCLUSIVE` for the other event) proving only `WRONG` holds anything. Without
  the ledger the plan is `update=2`; with it, `update=1 skip=1` — asserted in `pytest` as well as
  exercised by the sensor, because a sensor only checks an exit code.
- **`tests/test_evidence.py`, `tests/test_event_ledger.py`, `tests/test_run_ledger_wiring.py`**
  (NEW) — 120 offline tests. The one that matters is a **round trip**: the ledger's output is fed
  to `audit_events.sweep` and the verdicts are asserted, so producer and consumer are tested
  against each other rather than against their own documentation. The rest pin the refusals, the
  evidence semantics, and the workflow handoff (artifact name matched on both sides, download
  before the inputs check, an absent artifact tolerated, no write permission added to `runtime`).
- **`scripts/rung_health.py`** (NEW) — the writer behind `live-stream-runtime-spec.md` §10
  (`rungs.json`) and §18.4 ("a rung parked for N consecutive days raises an issue"). Both were
  unimplementable as written: `RungHealth` parks a rung for the rest of one run and is then gone,
  so a retired backend — GitHub Models was withdrawn 2026-07-30 — left one `failed` line in one
  morning's log as its only trace. `probe` climbs **every** host in one invocation with one
  shared strike tracker (a tracker per host gives a dead backend one strike each and never parks
  it) and appends `rung-attempts.jsonl`; `snapshot` derives `rungs.json` from that ledger;
  `parked --days 3` reports and optionally files one deduped issue. Four semantics are
  load-bearing: rows are built from named fields so a **page body never reaches the ledger**
  (`render_ladder --probe --json` dumps whole documents, >1 MB on a YouTube page); `skipped`
  (no key / no package) **breaks** a strike streak instead of extending it, or every keyless run
  would park the hosted rungs and a later key would start from a fake history; a calendar day
  with no rows **breaks** a streak and the gap days are named, because a dead backend and a dead
  cron otherwise read identically; and a streak ends at the rung's **latest observed** day, so a
  rung fixed today stops re-reporting last week's. Only *fileable* failures are filed — a hosted
  rung failing, or a local rung that cannot connect at all — so a target's WAF tightening is
  recorded in `rungs.json` and never opened as an issue.
- **`scripts/gh_issue.py`** (NEW) — the **one** implementation of "create the issue, or comment
  on the open one", shared by `corpus_flip_issue.py` and `rung_health.py` rather than copied.
  The title marker is the dedupe key and is a required, keyword-only argument, so two features
  cannot answer "is there already an issue?" with each other's key and silently merge unrelated
  findings; an unparseable `gh` list returns `None` instead of guessing, so a `gh` format change
  cannot open a duplicate issue every week.
- **`.github/workflows/runtime-daily.yml`** — two new jobs. `rung-health` probes the ladder and
  commits the ledger; it is deliberately **not** `needs: telemetry`, because the search preflight
  reds when no search backend is configured and a dead *render* backend is a different fault that
  must still be recorded on that day. `rung-issues` files the §18.4 finding and is separated the
  way `self-improve.yml` is: it holds `issues: write`, holds no search or render key, and never
  writes the branch. All three jobs that push `telemetry` now share one job-level concurrency
  group, so writers wait instead of racing to a non-fast-forward rejection — with `queue: max`,
  which is not decoration: the default `queue: single` is documented to **cancel and replace** a
  pending job in the group, so a third writer eligible at the same moment would silently cancel
  one rather than wait, and a cancelled ledger job is indistinguishable from a quiet day.
- **`tests/test_rung_health.py`, `tests/test_gh_issue.py`** (NEW) — 79 offline tests: the ledger
  contract (no body, idempotent by `(run_id, rung, url)`), the snapshot derivation, the four
  streak rules, the workflow's privilege split and writer serialisation, and a stub `gh` on
  `PATH` proving a run with nothing parked never reaches GitHub at all.

### Changed

- **The approved-source registry's BBL row pointed at three locators that no longer answer, in
  three different files.** Found by actually running the inventory: the league renamed to
  easyCredit BBL, and every part of the Tier-1 entry had gone stale independently —
  `basketball-bundesliga.de` no longer serves the site (TLS SNI failure on both the bare and
  `www` host, while DNS still resolves), `x.com/BBLofficial` is a **404** while the other four
  approved X accounts answer 200, and `youtube.com/@bbl_basketball` is a **404** while
  `@fiba`, `@EuroLeague` and `@BasketballCL` all answer 200 — so the probes were discriminating
  rather than reporting a provider outage. The current locators are `easycredit-bbl.de`
  ("easyCredit BBL — Die offizielle Seite"), `x.com/easyCreditBBL` (also linked from the
  league's own homepage) and `youtube.com/@basketballbundesliga` (channel: "easyCredit
  Basketball Bundesliga"). Updated in lockstep in `config/sources.json`,
  `references/approved-sources.md`, `references/youtube-live-search.md`, `README.md`, `SKILL.md`
  and `scripts/youtube_live.py`'s `DEFAULT_ALLOWED_HANDLES` — the last of which matters most,
  because that tuple is what the live gate actually enforces and `tests/test_youtube_live.py`
  asserts it matches the registry. **The Facebook page is deliberately left alone, and marked
  unverified rather than "verified good":** `facebook.com` answers HTTP 400 to every user agent
  this repo sends, for every handle including a known-good control, so a probe there cannot
  distinguish a dead page from a blocked one. Two related findings are reported and **not**
  fixed, because both need a decision rather than an edit: `scripts/fixtures.py`'s BBL source
  URL (`basketball-bundesliga.de/spielplan/`) cannot simply be repointed to the new domain,
  which serves **no JSON-LD and no microdata** on either of its schedule pages, so Phase 5's
  BBL fixture fetch needs a different extraction strategy; and 18 of the 22 approved domains had
  never been checked at all before this.

- **BREAKING (config) — `defaultColorId` is removed, not wired up.** The field was reported as
  read-but-never-written and deliberately left open, on the reasoning that fixing it meant
  choosing whether it *overrides* `color_mapping`'s league table or only stands in for a league
  the table does not know. That framing was wrong, and the collision is measurable: the value is
  not a preference, it is `verification.STATE_COLOR_IDS["VERIFIED"]`, and the precedence rule —
  an uncertain state wins over the league colour — holds only while the league default stays
  distinguishable from `5` (UNVERIFIED) and `7` (WRONG). `SETUP.md` was offering the user **all
  eleven** colours for that field, both of those included, so the knob was a safety label that
  could be set to the opposite of what the calendar meant. Deleted from `config/calendar.json`,
  `calendar_config.DEFAULT_CONFIG`, `get_default_color_id()`, `validate.py`'s recommended fields
  and its three smoke fixtures, and `SETUP.md` (whose eleven-line colour list is replaced by
  what each colour *means*). The field was inert, so **a fork that set it only has a line to
  delete** — no event changes colour. In its place: one definition,
  `verification.DEFAULT_LEAGUE_COLOR_ID`, imported by all four call sites that used to carry
  their own literal `"6"`, and `verification.FORBIDDEN_LEAGUE_COLOR_IDS` (`2`, `5`, `7`, `11`),
  which `tests/test_color_mapping.py` asserts the default is never in plus a redeclaration check
  that fails if any module reintroduces a local copy. Both guards were verified to fail rather
  than assumed to pass: pointing the constant at `STATE_COLOR_IDS[STATE_WRONG]` reds the
  collision test, and a scratch `DEFAULT_LEAGUE_COLOR = "6"` in `scripts/` reds the
  redeclaration test.
- **`scripts/upsert_events.py` — a stored event with no `Teams:` line is no longer a wildcard.**
  This was the most consequential defect found, and it was found by measuring the live calendar
  rather than by reading the code. Of 102 events, **78 carried no `Teams:` line** — most predate
  the description writer. `events_match` skipped the teams check entirely whenever *either* side
  was empty, so the 30-minute window was the only gate (the league check applied only when the
  stored entry also carried a `League:` line, which 11 of the 78 did not). Every one of the 78 is
  `VERIFIED`, and a `VERIFIED` match is a **skip** — so the effect was never a duplicate event:
  it was a **silently dropped game**, a real fixture treated as already present and never
  written. With no teams recorded, the entry must now *name the candidate's clubs* in its own
  text (`team_tokens.text_names_all`, new). Verified against the live calendar both ways:
  **0 of 78** entries match a candidate for a different game, while **24/24** entries carrying a
  `Teams:` line and every club-naming entry still match their *own* game — including the four
  live entries that write the pairing `A - B`, which a `vs`-only rule would have made
  unmatchable and turned into real duplicates. The fix is deliberately not a backfill: no write
  to the live calendar was needed, and a backfill would have left the rule intact.
- **BREAKING — the calendar credential is Composio's, not Google's.** `scripts/calendar_io.py`
  no longer calls the Calendar API: it executes Composio toolkit tools
  (`POST /api/v3.1/tools/execute/{slug}`, an `x-api-key` header, `{user_id, arguments, version}`
  in the body). **A fork must set two secrets — `COMPOSIO_API_KEY` and `COMPOSIO_USER_ID` — and
  can delete `GCP_WORKLOAD_IDENTITY_PROVIDER` / `GCP_SERVICE_ACCOUNT`.** The Google Cloud
  project, the service account, the calendar-sharing step and the
  `google-github-actions/auth@v2` step in `runtime-daily.yml` are all gone. The interface is
  deliberately unchanged: same modes, same flags, same output files, same exit codes, same
  dry-run guarantee (no HTTP at all without `--live`), same `parse_event`/pagination code. What
  changed is the transport and the argument names, and what it costs is on the other side of the
  ledger — a long-lived credential that can write to a public calendar now lives with a third
  party, which is the opposite of what WIF was chosen for. The four Composio behaviours this
  depends on were **verified against a live account, not assumed**, and each is silent in a
  passing run: `CREATE_EVENT` **ignores `color_id`** (so a coloured create is *two* calls, and a
  failed colour patch is reported as a failure even though the event exists — a wrong colour is a
  wrong label on a public calendar — while the event id is still recorded, because the audit
  must not lose it); it **adds a Google Meet link and adds the connected user as an attendee**
  unless `create_meeting_room: false` / `exclude_organizer: true` are sent, which they now always
  are; it nests the created event under `data.response_data` where `EVENTS_LIST` puts the Google
  body straight under `data`, so reading only `data` finds **no id** — which is why `_payload()`
  falls back to the envelope rather than to `{}`, an empty payload being exactly what "no events
  today" looks like; and `data` is documented as a string but is a dict on v3.1, so both are
  accepted. The toolkit version is `latest`, **not a pin**: Composio records that older pinned
  versions can drop or remap `timeMin`/`timeMax` before Google sees them, and a dropped window
  filter returns the wrong window silently. `COMPOSIO_USER_ID` beats a pinned
  `COMPOSIO_CONNECTED_ACCOUNT_ID` for the same reason — it survives a reconnect — and passing
  both is refused rather than resolved, because the request would be ambiguous about whose
  calendar a write belongs to.
- **`SKILL.md`, `references/calendar-setup.md`, `references/implementation-notes.md`,
  `references/search-backends.md`, `README.md`** — the product's calendar contract now names the
  tools that exist. `googleCalendarListEvents` / `googleCalendarCreateEvent` appear nowhere in
  the skill: they are MCP names for an environment this repository never wired, and after the
  change above the repository's own credential could not serve them. In their place:
  `GOOGLECALENDAR_EVENTS_LIST`, `GOOGLECALENDAR_CREATE_EVENT` and `GOOGLECALENDAR_PATCH_EVENT`,
  with the argument names those tools actually take (`start_datetime`, not `startTime`) and the
  two mandatory arguments whose defaults are wrong for a public calendar. `tests/test_calendar_
  credential_wiring.py` enforces both directions — every `GOOGLECALENDAR_*` slug the docs name
  must be a `TOOL_*` constant `calendar_io.py` calls, and every tool it calls must be named
  somewhere — so this cannot rot back into documentation for tools that do not exist.
- **`.github/workflows/runtime-daily.yml`** — the `google-github-actions/auth@v2` step is
  **removed** from both jobs, and the two steps that run `calendar_io.py` now receive
  `COMPOSIO_API_KEY` and `COMPOSIO_USER_ID`. Removing the step rather than leaving it inert is
  the point: nothing downstream reads a Google access token any more, so it could only ever fail
  or be ignored, and it was one of the two places a fork was told to configure a GCP project it
  no longer needs. The audit job's copy went too — it was producing a token nothing consumed.
  A missing credential is a loud exit 2 naming the variable, not a quiet empty run.
  `tests/test_calendar_credential_wiring.py` (NEW, 14 tests) pins the whole substitution: no step
  reads `steps.auth`, no workflow authenticates to Google, the `GCP_*` variables are gone, both
  calendar steps carry **both** halves of the credential (a key alone scopes to nobody and fails
  as a broken key), no workflow exports a `COMPOSIO_*` name the CLI does not read — asserted in
  both directions against `os.environ.get` in the module itself — and both steps are still gated
  on `ENABLE_CALENDAR_WRITES`, so the credential never becomes the thing that decides a write.
- **`scripts/calendar_io.py`** — **`visibility` is now sent, closing a reader with no writer.**
  `config/calendar.json` carries `visibility: "public"`, `SETUP.md` and
  `references/calendar-setup.md` both documented events as public, and `calendar_config.py`
  exposed `get_visibility()` — which **no code path called**. So the promise rested entirely on a
  reader nobody invoked, and every event took whatever the calendar's own default was. It is a
  real argument of both `CREATE_EVENT` and `PATCH_EVENT` (`default` / `public` / `private` /
  `confidential`, taken from the live tool schema), and it now travels on **both** paths: a
  `PATCH` that omitted it would replace the field with nothing and let a later run silently undo
  the visibility an earlier one set. The value comes from the config file — `--visibility`
  defaults to `calendar_config.get_visibility()` rather than to a literal, so the file that
  documents the setting is the file that decides it — and an unknown value is refused at the CLI
  with exit 2 naming the enum, because forwarding a typo produces a rejection that names the
  API's parameter instead of the config file that was wrong. This is an event-level property,
  not a substitute for the calendar's sharing setting; both are needed for a reader to find an
  event, and the reference now says so.
- **`references/calendar-setup.md`** — the *Visibility* section and the tool notes now describe
  what the code does. An earlier revision of this change asserted that Composio's `CREATE_EVENT`
  has no `visibility` parameter and documented that as a permanent gap; that was **wrong** — the
  field is in the live schema. The claim was written from a remembered probe instead of a
  re-check, which is the same mistake as a unit test with a hand-built input: it described the
  fixture, not the contract.
- **`scripts/upsert_events.py`** — a new optional `--verdicts audit.jsonl`, and the **only** way
  its `WRONG` policy row is reachable on the real path. `plan_upsert` reads the stored state off
  the calendar, and the calendar recovers `WRONG` from a `[WRONG] ` title prefix that
  `audit_events` computes and nothing applies — so an event the audit had proved was paid or
  never live stayed on the calendar looking like one awaiting confirmation, and the next run's
  fresh guess was free to promote it to `VERIFIED`: exactly what §17 calls a hard requirement,
  violated on every run. The planner now honours the newest `WRONG` verdict from the ledger
  itself, joined by `event_id` (a calendar event has no `game_key`), checked **before** the
  calendar's own `VERIFIED` label because the audit outranks it. The dedupe reuses
  `audit_events.latest_per_event` — the same function the audit uses, since two implementations
  disagreeing about which row is current would invert the guarantee. A **single-row** ledger is
  read as a row rather than as a wrapper object: reading it as `{"verdicts": …}` yields *no*
  verdicts, and "no verdicts" is the state in which the wrong event gets promoted.
- **`scripts/calendar_io.py`** — two things the write path used to drop. It now returns the
  **`event_id` the API assigned** on a create (and on an update), because `audit_events` resolves
  every verdict back to an event by id and the id was being thrown away: even with a ledger
  writer, `events.jsonl` would have been unkeyable. `--out` also works for `apply` and writes the
  applied *result* — it is written before the exit-1 path, so a partially failed apply is still
  recordable, and the events that landed are exactly the ones a subscriber can see. And it
  writes the documented **`League:`/`Teams:` description** from the plan row: `list_events` reads
  that `Teams:` line back out and `upsert_events.events_match` matches on it, but `apply_plan`'s
  `descriptions=` map was never passed by anything, so every event carried `description: ""`.
  Because the teams check is **skipped** whenever either side is empty, a stored event matched
  *any* candidate inside the 30-minute window — a different pairing at the same time was planned
  as an `update` of it rather than a new event, which is a mislabel on a public calendar. An
  explicit `descriptions=` entry still wins; `league_from_description()` is the reader's
  counterpart, so a stored event now carries a league too.
- **`scripts/audit_events.py`** — the **latest row per `event_id` is the one judged**. The ledger
  is append-only forever, so an event updated on five days has five rows; judging all of them
  would append five verdicts for one event and count it five times in the precision denominator.
  Rows with no id are kept rather than dropped, because dropping one would hide a write we cannot
  name, and the number collapsed is reported on stderr so `--json` stdout stays payload-only.
- **`scripts/extract_candidates.py`** — the field whitelist now carries the **evidence flags**
  through (`evidence.py`). It dropped them, so the audit could never get past "no evidence
  recorded" regardless of what the agent observed: the Phase 3 loop was dead twice over.
- **`scripts/upsert_events.py`** — plan rows carry `league_color_id` and the `evidence` flags. The
  colour is needed because a later `VERIFIED` promotion must restore the *league* colour (the
  row's `color_id` is already the state colour), and the evidence travels with the row so the
  ledger can record an event from the plan and the apply result alone — no re-join against the
  candidate list, whose `game_key` an update row deliberately replaces with the existing event's.
- **`scripts/render_ladder.py`** — an attempt's state now has one implementation,
  `attempt_state()` (`evidence` / `ok-no-evidence` / `blocked` / `failed` / `skipped`), read by the
  probe report, the `--probe --json` payload (which gains a `state` field) and the rung ledger.
  It previously derived the label inline with `skipped` missing from the chain, so a rung with no
  key printed `failed` — the same word as a retired backend — and `rungs.json` would have kept
  that reading for months. The browserless-rung skip on a JS host now also carries `skipped`,
  so it is not counted as a failure either.

- **`live-stream-runtime-spec.md`** (NEW) — spec for the daily runtime, evals and
  self-improvement loop, derived from an interview with the repo owner. 18 sections
  covering the verification state machine, recall measurement, telemetry schemas,
  the render-ladder module contract, a Phase 0-5 plan, and the research findings
  that constrain all of it. **SPEC ONLY** — records decisions, does not implement them.
- **`plans/invariants.json`** (NEW) — 18 `DecisionHeader` invariants for the do-harness
  contract (`{invariant, rationale, sensor, category}`), encoding every non-negotiable
  gate: no event without all 7 checks, magenta.tv needs a `magentasport.de` announcement,
  YouTube needs `start > now` with no `actualEndTime`, BCL is per-game, social media is
  never a `directLink`, new sources stay quarantined, and the default render ladder
  contains zero AGPL rungs.
- **`scripts/candidates.py`** (NEW) — the Phase 0 candidate ledger and recall
  denominator. `append` / `recall` / `retry`, with recall computed over **unique games**
  rather than rows, because one game is routinely surfaced by several backends.
- **`scripts/render_ladder.py`** (NEW) — ordered render ladder (firecrawl → tinyfish →
  curl_cffi → patchright → camoufox, with `nodriver` opt-in only). Two properties are
  deliberate: `blocked` (401/403/429/451) is a distinct outcome from a real error, so
  403 climbs the ladder instead of discarding a game; and `assert_shippable()` plus a
  test guarantee no default rung is AGPL-3.0, protecting this MIT repo's licence.
- **`scripts/run_daily.py`** (NEW) — Phase 0 daily shell: search ladder (Exa → TinyFish)
  into the ledger. Has **no calendar access at all**, so a bad run cannot reach a
  subscriber. Fails loudly when no backend key is present rather than writing an empty
  ledger, because an empty denominator is a lie, not a result.
- **`scripts/capture_transcripts.py`** (NEW) — captures **real** runtime transcripts for
  grading (`opencode` / `command` / `replay`). Exits non-zero and names the cases with no
  output instead of inventing one.
- **`.github/workflows/runtime-daily.yml`** (NEW) — cron `30 8 * * *` plus
  `workflow_dispatch` and `repository_dispatch`. The telemetry job writes zero calendar
  calls; calendar writes and the audit job are gated behind `ENABLE_CALENDAR_WRITES` /
  `ENABLE_AUDIT`, both defaulting off.
- **`docs/runtime.md`** (NEW) — operator guide: secrets and variables, cadence rationale,
  telemetry layout, rung health, the AGPL note in plain language, the LLM budget, and a
  recovery runbook.
- **`tests/fixtures/README.md`** (NEW) — documents why `runtime_transcripts.json` is
  absent by design, and the rule that distinguishes the two fixture kinds: **inputs may be
  hand-written, outputs must be captured.**
- **`tests/test_candidates.py`, `tests/test_render_ladder.py`, `tests/test_transcripts.py`**
  (NEW) — 93 offline tests, bringing the suite to 271.
- **`scripts/verification.py`** (NEW) — the verification state machine
  (`VERIFIED` / `UNVERIFIED` / `WRONG`) shared by the write and audit paths, so the prefixes
  and colours cannot drift apart. One deliberate precedence rule: an uncertain state
  **overrides** the league colour, because losing a final's Tomato red to a "reserved"
  colour would hide exactly the uncertainty the state exists to expose.
- **`scripts/upsert_events.py`** (NEW) — the dedupe-then-write planner. Lists the whole
  window, matches on ±30 min **and** the team pair **and** a compatible league, and prints a
  `create`/`update`/`skip` plan **without calling the calendar API** — which is what keeps
  the write policy testable without credentials. Short and long club names count as the same
  club (`ALBA` = `ALBA Berlin`), because one missed match is a duplicate event on a public
  calendar.
- **`scripts/audit_events.py`** (NEW) — post-hoc verdicts and the promotion sweep, with
  `--out` appending `audit.jsonl` rows. Three designed-in properties: absence of evidence
  yields `INCONCLUSIVE` rather than `WRONG` (a scraper failure must never read as "was never
  live"); `WRONG` is terminal; nothing is ever deleted. `--events` accepts JSONL as well as
  JSON, because the documented input is the telemetry branch's ledger.
- **`tests/test_verification.py`, `tests/test_upsert_events.py`, `tests/test_audit_events.py`,
  `tests/test_fixture_contracts.py`** (NEW) — 122 offline tests, bringing the suite to 393.
- **`tests/fixtures/{upsert_existing,upsert_candidates,audit_events,audit_evidence}.json`,
  `tests/fixtures/candidates_ledger.jsonl`** (NEW) — hand-written *inputs* that
  `.github/workflows/validate.yml` invokes directly. `test_fixture_contracts.py` pins their
  documented outcomes, so a drifted fixture fails the suite instead of quietly turning a CI
  step vacuous.
- **`evals/evals.json` cases 33–36** — the new behaviour was untested by the eval set. The new
  cases cover the `[UNVERIFIED]` labelling concession, the `WRONG` relabel-not-delete rule,
  `INCONCLUSIVE` on missing live evidence, and the zero-duplicate re-run. Eval count 32 → 36.
- **`scripts/extract_candidates.py`** (NEW, Phase 1) — the transcript → planner seam. The
  runtime agent is denied `edit` and `bash`, so the transcript is its only channel; the
  fenced-```json``` contract is therefore enforced in code with tests rather than assumed of
  the model. It refuses `state=WRONG`, because that is an audit verdict and a run that could
  assert it would be overwriting the one label a human is meant to trust.
- **`scripts/calendar_io.py`** (NEW, Phase 1) — the only module that talks to the Calendar
  API. `list` normalises events into the planner's shape and recovers
  `VERIFIED`/`UNVERIFIED`/`WRONG` from the title prefix, which is what makes "never touch a
  verified event" enforceable at all. `apply` performs **no HTTP request** unless `--live` is
  passed: whether a run wrote to a public calendar should be answerable by reading the
  command line, not by trusting a code path.
- **`scripts/fixtures.py`** (NEW, Phase 5) — official league fixtures → the ledger's
  `game_key` shape. JSON-LD first (a standard with a stdlib parser), a microdata fallback
  second, and **no match means no fixture** — a half-parsed event would be reported as a
  missed game that does not exist.
- **`scripts/synthesise_eval_case.py`** (NEW, Phase 4) — audit misjudgement → regression eval
  case. Encodes the bug, not the fix, so it fails until the gate is tightened; refuses to
  write a case whose assertion has no needle (such a case grades nothing); and is idempotent,
  because the dedupe key deliberately excludes the audit timestamp.
- **`.github/workflows/self-improve.yml`** (NEW, Phase 4) — synthesise, push a
  `self-improve/YYYY-MM-DD` branch, gate it on `validate.py` + `runtime_eval.py` +
  `synthesise_eval_case.py --verify` + `pytest`, then open a PR only if green. Holds
  `contents: write` but **no `id-token: write`** and no Google auth step, so it can edit the
  instructions and still cannot reach a calendar.
- **`scripts/candidates.py`** gains the `unseen` class — fixture games that appear in no
  ledger row, the failure the search-derived metric structurally cannot see — plus
  `--fixtures` on `recall`. `recall_metrics` is unchanged and still reports `null` rather
  than `0.0` when there is nothing to measure.
- **`tests/test_extract_candidates.py`, `test_calendar_io.py`, `test_fixtures.py`,
  `test_synthesise_eval_case.py`** (NEW) — 187 offline tests, bringing the suite to 617.
- **Six new input fixtures** — `synthesise_verdicts.jsonl`, `league_fixtures.jsonl`, two
  synthetic league pages (one JSON-LD, one microdata-only), and an agent-transcript envelope
  sample. `tests/fixtures/README.md` records why the transcript sample is legitimate where a
  hand-written `runtime_transcripts.json` would not be: it is an input to the extractor, not
  a record of what the skill did.

- **`do-harness` adopted** (`do-harness.toml`, `AGENTS.md`, `scripts/check-commitlint.sh`,
  `.github/workflows/verify.yml`) — **14 offline sensors** plus the `feedback` / `verification`
  / `release` signal sets. Every sensor is deterministic and credential-free; the two
  network-adjacent ones (`live-gate`, `link-gate`) run against fixtures in `--dry-run` mode.
  Hooks are installed and `do-harness verify --set verification --strict` passes with
  evidence. `docs/do-harness.md` records the whole adoption, including the one step that had
  to be withdrawn.
- **`scripts/metrics.py`** (NEW) — derives `metrics.json` from the append-only telemetry
  streams. Three metrics, deliberately not collapsed: **recall** (of the games a backend saw,
  how many were captured), **fixture recall** (of the games that actually happened, how many
  any backend even *saw* — the only metric that can detect a systematically invisible
  source), and **precision** over the post-hoc audit. Two semantics are load-bearing: a
  missing input reports `None`, never `0.0`, because "we did not measure" and "we measured
  zero" are different claims; and `INCONCLUSIVE` is excluded from the precision denominator,
  so an audit that reached no verdict cannot make a quiet day look accurate.
- **`tests/test_harness_boundary.py`** (NEW) — 27 tests pinning the adoption boundary:
  every declared sensor has an `argv`, signal sets and hooks reference only declared sensors,
  `plans/invariants.json` is a bare array naming only real sensors, and the four product
  graders are still wired into `verify.yml`. See the withdrawal note below.
- **`tests/test_metrics.py`** (NEW) — the metrics contract, including that `.render()` prints
  `n/a` rather than a misleading `0.000`.
- **`scripts/metrics.py --trend`** — per-run history derived from the `run_id` on the existing
  append-only streams, so no new storage was needed. Three semantics are deliberate: per-run
  recall answers "of what *this* run saw, how much did it capture?" (the number that moves when
  a gate changes) and is not cumulative recall; a run appearing only in the audit stream still
  produces a row (`rows=0`, `recall=n/a`, `judged=1`) because dropping it would hide the day a
  false positive was found; and the sparkline scale is **absolute 0..1, not min/max**, because
  normalising to the observed range would draw `0.980` vs `0.985` as a dramatic climb — exactly
  the misreading a trend is meant to prevent. A gap renders as `·` rather than being
  interpolated. Wired into the runtime job summary and into `validate.yml`.
- **`tests/fixtures/candidates_ledger_runs.jsonl`, `tests/fixtures/audit_runs.jsonl`** (NEW) —
  three runs whose recall climbs 0.000 → 0.500 → 1.000, plus an audit run with **no** candidate
  rows, so the trend's direction and the run-id merge are pinned rather than asserted.
- **`tests/fixtures/youtube_live_candidates.json`** (NEW) — the `live-gate` sensor's input.
  It immediately caught a real gap: `@FIBAWorld` was rejected only in prose and by
  `evals/evals.json`, while no Python gate enforced it, so a channel-shaped URL for a handle
  that does not exist sailed through as `SCHEDULED`. `youtube_live.py` now reads the
  allow-list from `config/sources.json`.
- **`scripts/capture_transcripts.py`** — the ladder's **rung 1 now exists**. `--runner gemini`
  calls the free AI Studio product directly over HTTPS, so the documented primary rung no
  longer requires the opencode CLI; `--runner ladder` fails over gemini → opencode →
  openrouter and records **which rung served each case** in the output file. `--list-models`
  reports the ladder, which credentials are set, and the live model list (Lite tier first)
  without spending a generate call. The model id is **discovered at run time, not pinned** —
  free tiers are withdrawn without notice, so a hardcoded id is how a rung rots unnoticed.
- **`scripts/check_workflow_refs.py`** (NEW) — every repo path a workflow names must exist.
  Written for a step that read a fixture which is *deliberately* absent and hid the exit code
  behind `|| true`, so it no-opped on every run and still read as a pass. The check is
  deliberately narrow about what counts (URLs, globs, `${{ }}`, `.tmp/`, write targets and
  existence-guarded paths are all ignored): a check that cries wolf gets deleted rather than
  investigated.
- **`tests/test_capture_gemini.py`, `tests/test_check_workflow_refs.py`** (NEW) — 52 offline
  tests: the shared transport's error contract, distinct reasons for a missing key / 429 /
  safety block / empty candidate, Lite-first model ranking, ladder failover and total-failure
  diagnosis; and the reference gate's false-positive traps plus the pre-fix step text it must
  reject.

- **`scripts/capture_transcripts.py`** — `--list-models` now probes the `opencode` rung
  (`opencode --version`, zero-token). Found in the wild on this machine: `--check-rungs`
  reported `OK rung opencode: ANTHROPIC_AUTH_TOKEN is set` while the binary on `PATH` failed with
  `failed to install the right version of the opencode CLI for your platform`. The rung status is
  presence-based **by design**, and `--list-models` probed the OpenRouter key and the Gemini list
  but not this rung — so nothing anywhere checked whether it was usable, the CI preflight passed,
  and the capture then produced no transcript. A rung that reports `OK` and cannot serve a request
  is exactly the silent failure the preflight exists to prevent.
- **`scripts/replay_ci.py`** (NEW) — replays a workflow's `run:` steps against a **clean
  copy** of the repo. This is the technique that found every CI-only defect here, and it had
  been re-derived by hand three times. It encodes the two traps that fail *misleadingly*: the
  clean copy must exclude `.git`/`.do-harness`/`.tmp`/caches (inheriting them hides the defect
  being looked for), and it must supply `GITHUB_STEP_SUMMARY`/`GITHUB_OUTPUT`, which Actions
  always sets — without them a step dies with `: No such file or directory` and the replay
  blames the workflow for its own omission. Steps that could write outside the replay directory
  (`--live`, `git push`, Google auth, any `opencode` invocation) are never executed, and every
  `*_API_KEY`/`*_TOKEN` variable is stripped from the environment first, so a replay cannot
  spend quota or touch a real calendar.
- **`tests/test_replay_ci.py`, `tests/test_run_daily.py`** (NEW) — 74 offline tests. The replay
  harness is pinned on the properties that make it trustworthy rather than on its output: the
  clean copy really is clean, credentials really are stripped, and a denied step is **skipped
  rather than run** (verified by a marker file the step would have created).
- **`tests/test_run_daily.py`** (NEW) — 45 tests for the Phase 0 runtime, which until now had
  **no test at all** despite being the only thing that populates the recall denominator. Covers
  the plumbing filter (including that a lookalike `notgoogle.com` is *not* suppressed), de-duplication
  across queries, contributed-vs-attempted backends, the `--check-backends` gate, the `--json`
  stdout contract, and the `runtime-daily.yml` ordering — the gate must precede the ladder it
  gates, and the ladder must not be piped, since a pipe would report `tee`'s status and turn the
  tolerance into a no-op.
- **`scripts/record_pages.py`** (NEW) — records and re-checks the corpus of **real HTTP responses**
  that the stream-evidence gate is judged against. `--refresh` re-fetches every page and writes a
  manifest carrying each response's byte length, **SHA-256**, verdict and why the page is
  interesting; `--check` is the offline gate (a `validate.yml` step plus the `page-corpus` sensor)
  that the stored pages still classify as recorded **and are still the recorded bytes** — an edited
  fixture would otherwise pass while encoding a weakened gate. YouTube documents, at 1.2–1.4 MB, are stored as
  verbatim extracts of the two fields and one meta tag their verdict turns on; two pages are
  `verify-only` because 250 KB of gzip for "no media token anywhere" is not worth committing twice.
- **`tests/fixtures/pages/`** (NEW) — eight real pages with `manifest.json` and a README stating what
  each one proves and the residual risk that remains.
- **`tests/test_recorded_pages.py`** (NEW) — 34 tests over that corpus, including the one that
  matters most: **the historical rule is required to keep failing on the recorded negatives**, so the
  corpus cannot be relaxed into passing for the wrong reason. Seven more pin the game anchor against
  the same real titles.
- **`scripts/team_tokens.py`** (NEW) — the one club-name matcher, shared by `upsert_events.py` (to
  avoid creating the same game twice) and `render_ladder.py` (to decide whether a page names the
  game). Folding is case-, punctuation- and diacritic-insensitive and covers the German digraph
  spellings; matching is set equality or nesting.
- **`tests/test_team_tokens.py`** (NEW) — 14 tests for those rules, including that `vs` is never
  split into `v` + `s` and that a name with no identity (`Basketball`) never matches by nesting.
- **`.github/workflows/corpus-refresh.yml`** (NEW) — the evidence corpus is re-fetched **weekly**,
  because `record_pages.py --check` answers a question about *bytes* and cannot notice that the
  live page changed shape. It holds `contents: read` + `issues: write`, no credential, and never
  commits. **Only a verdict change files anything**: a byte change is expected (the stored YouTube
  fixtures are extracts of a 1.3 MB document, so their hash moves whenever a sidebar suggestion
  changes) and reporting it would file an issue every week until nobody read them. A persistent
  flip comments on the open issue rather than piling one up per week, and a failed fetch is
  reported as a warning naming the page — never as a flip, since that would look like a finding
  about the gate.
- **`scripts/corpus_flip_issue.py`** (NEW) — turns `record_pages.py --refresh --json` into one
  deduplicated issue. Files nothing when no verdict changed (the expected outcome on nearly every
  run), exits **1** when a flip cannot be filed rather than dropping the finding, and reports the
  two directions separately because they need different fixes: `lost-evidence` is missed streams,
  `gained-evidence` is the false positives the corpus exists to prevent. `--file` is the whole
  capability, byte-for-byte like `calendar_io`'s `--live` — and it is denied in `replay_ci.py` for
  the same reason.
- **`tests/test_corpus_flip_issue.py`** (NEW) — 26 tests. The negative ones carry the weight: a run
  with no flip must **never invoke `gh`** (proven with a stub `gh` on `PATH` that records every
  call), and the title must stay free of dates so a stuck page cannot spawn a weekly issue.
- **`scripts/write_mode.py`** (NEW) — the calendar write switch, extracted from the workflow for
  exactly one reason: a `${{ }}` expression cannot be unit-tested, so the dead-cron defect had no
  way to be caught before a subscriber noticed no events were appearing. Pure
  `resolve_dry_run(event, dispatch_input, enabled)` plus a CLI that prints the `GITHUB_OUTPUT`
  payload (`dry_run`, `reason`); stdout is the payload alone, because a stray human line would not
  merely be untidy — GitHub's parser rejects it and the step would fail. Reason text is sanitised
  before it becomes an output, since it echoes a repository variable and lands in a shell `echo`
  and a markdown table.
- **`tests/test_write_mode.py`** (NEW) — 33 tests: the decision matrix (including that the
  variable is the switch on a cron, and that a dispatch cannot widen it), the `$GITHUB_OUTPUT`
  contract, and the workflow wiring. The structural pins scan the YAML **with comments stripped**,
  because the buggy expression is quoted verbatim in the explanatory comment above its own fix —
  prose describing a defect is not the defect, a trap this repo has already paid for once.
- **`plans/invariants.json`** — a 20th invariant: the unattended runtime writes only when
  `ENABLE_CALENDAR_WRITES` is exactly `true`, and a `workflow_dispatch` input can never widen
  that. `sensor: pytest` (the sensor that actually proves it), so the do-harness contract still
  resolves every invariant to a real sensor.
- **`do-harness.toml`** — an 18th sensor, `upsert-verdicts`: the audit's verdicts honoured by the planner, run against a committed fixture pair so the §17 guarantee is exercised in CI and not only in `pytest`. Added to both the `verification` and `release` signal sets; `plans/invariants.json` gains a 23rd invariant resolving to it.
- **`do-harness.toml`** — a 17th sensor, `event-ledger`: the Phase 2 → 3 seam as one command,
  running the round trip (`tests/test_event_ledger.py`) that writes both of the audit's inputs
  and asserts the verdicts it returns from them. Added to both the `verification` and `release`
  signal sets. `plans/invariants.json` gains a 22nd invariant — the audit judges only events
  Phase 2 actually wrote, and only ones it can name — resolving to that sensor.
- **`.github/workflows/runtime-daily.yml`** — the `runtime` job gains a read-only "Read the audit
  verdict ledger" step and passes it to the planner. Deliberately a `git show` of the telemetry
  branch rather than a branch checkout or an artifact: this job holds the calendar credential and
  has no `contents: write`, and reading needs none. An absent ledger is a notice (a repository
  whose audit has not run), and the job summary names each event an audit verdict held, because
  a `skip` count on its own does not say *why* a game was left alone.
- **`.github/workflows/validate.yml`** — the write-path step now passes `--out
  .tmp/applied.json` and a new step runs `event_ledger.py record` against it. The fixture apply
  is a dry run, so the ledger records nothing and says so, which is the correct answer and the
  one worth pinning in CI.

### Fixed

- **`link_check.py --json --out` produced unparseable output.** The `--out` confirmation was
  printed to stdout in both modes, so asking for the machine-readable report *and* writing it to
  a file emitted a JSON document with a human line in front of it — a parse error for every
  reader, including the tests that assert the payload. The confirmation now goes to stderr when
  `--json` is set, which is the rule this repo has written down and then re-learned four times
  (`upsert_events`, `calendar_io`, `synthesise_eval_case`, `run_daily`); it is now pinned by a
  test that parses stdout with `--json --out` together.

- **A candidate's `directLink` was dropped at the planner boundary, so no event the runtime
  wrote recorded its link anywhere — which is why `links.json` had no producer.** The link was
  lost at *every* boundary, measured rather than inferred: `upsert_events._event_fields`
  projected `start`/`end`/`league`/`teams`/`league_color_id` (+ `evidence`) and named no link
  field at all, `calendar_io.description_for` wrote only the `League:`/`Teams:` lines out of
  the six parts `references/calendar-setup.md` specifies, and `event_ledger.ledger_row` records
  no link either. `grep` for `directLink`, `sourceReference`, `Validated:` or
  `Validation Notes:` across `scripts/` returned **zero writers** — while four readers depended
  on them: `SKILL.md` Constraint 9 ("every event description must contain `sourceReference` and
  validation timestamp"), the validation pseudocode in `references/lessons-learned.md`
  (`if (!stream.directLink) REJECT`), `references/self-learning.md`'s revalidation loop, which
  quarantines a link *by removing it from the description*, and `link_check.py`'s own docstring
  ("every calendar event carries a `sourceReference` and one or more direct stream links").
  This is the third instance of the class the repo keeps finding — a rule that reads a field
  nothing writes — and its tell was the same: a *unit test with a hand-built input*, where the
  fixture supplied the link the real pipeline never carried. The writer now exists in one place,
  `scripts/stream_links.py`: `_event_fields` carries the fields from the candidate,
  `description_for` renders the block, and `parse_event` reads it back, so a link written and
  read back still decides the same way. Only the parts a row actually has are rendered, so a row
  with no link information produces byte-identical output to before.

- **`calendar_io.parse_event` crashed on its own output, and was not idempotent.** It read
  `raw["start"]["dateTime"]`, but `calendar_io list` writes this function's *flattened* result —
  so re-reading a `list` export died with `AttributeError: 'str' object has no attribute 'get'`,
  and that export is the documented input of `link_inventory.py`. Feeding a parsed event back in
  also lost `id` (read only as `id`, never `event_id`) and `colorId` (never `league_color_id`),
  so the second pass was not equal to the first. Closed at the parser rather than special-cased
  at the caller: a normaliser that cannot read what it writes is how the round trip above stays
  broken in a way no unit test notices.

- **`scripts/rehearse.py` could not be imported on Python 3.9 — one of the two versions the CI
  matrix runs.** `Probe = Callable[[], tuple[bool | None, str]]` is an *assignment*, not an
  annotation, so its right-hand side is evaluated at import — demonstrated rather than assumed:
  `X = Callable[[], 1//0]` raises at import — and the `bool | None` inside it is PEP 604,
  Python 3.10+. On 3.9 that is `TypeError: unsupported operand type(s) for |` before a single
  test collects, and the pytest job is exactly where it would have surfaced: after the push, on
  a suite that is green on 3.12. The return type is now quoted, which leaves the annotation
  identical and evaluates nothing. Found by reading `validate.yml`'s matrix while adding the
  step below, which now makes the version explicit rather than incidental — `rehearse.py
  --list` runs on both versions, so a script that only works on the newest installed Python
  cannot reach `main`.

- **The corpus refresh's "stop rather than partially update" rule was not true — an
  aborted in-place refresh left the corpus corrupted, and unrecoverably so.** `record()`
  wrote each stored page file as it fetched it and the manifest only at the end, so a page
  that could not be reduced halfway through rewrote the files of the pages that had
  **succeeded** while the manifest kept their old hashes. Measured on a copy of the corpus:
  a refresh blocked on a YouTube `429` rewrote two of the three stored files, after which
  `--check` reported `FAIL: … is not the recorded bytes (hash mismatch) — re-record it
  rather than editing it` for both, and re-recording was unavailable for exactly as long as
  the blocking page stayed blocked. The documented way to refresh is *in-place*, so this was
  the path a maintainer would take. Two changes:

  - **The write is all-or-nothing.** Everything is buffered and written only once every page
    has been reduced; an aborted refresh now leaves the corpus byte-identical (checked: all
    four files' hashes unchanged, `--check` still exit 0).
  - **The page is collected rather than fatal.** `UnreduciblePage` carries back the flips
    among the pages that *were* reduced, `--json` adds `unusable` and `"ok": false`, and
    `corpus_flip_issue.py --markdown` renders it as a partial comparison — so a week when a
    provider rate-limits the runner is no longer a week with no verdict comparison at all,
    which read exactly like a week in which nothing flipped. Nothing is written and no issue
    is filed: that is what the exit code still decides. Live, the same run that previously
    died on page 5 of 8 now reports `0 flip(s) among 6 reachable page(s)` and names both
    `429` pages.

  The summary had the same bug in a smaller place: it read "8 pages re-recorded ⏐ no verdict
  changed" without mentioning the pages it never reached, which is an unasked question
  reported as a clean bill of health.

- **`run_daily.py` swallowed the one cause an operator can act on.** A configured but
  *rejected* search key and a query that legitimately found nothing both arrive as an empty
  result list, and the loud failure that ends a run could therefore only say "surfaced no
  candidates" — so a `401` from Exa and a quiet day were the same sentence. Found by auditing
  every network entry point in `scripts/` for the "an error response is data" assumption (the
  one `record_pages._fetch` had): six of the eight were clean, two of them *explicitly* —
  `calendar_io` already refuses a `200` carrying `successful: false` with the reasoning written
  out, and `capture_transcripts._request` documents that no caller can mistake an HTML error
  page for a model that answered with nothing. `run_daily` was the one that failed *closed but
  mute*: it exits 1 rather than writing an empty ledger, which is right, but never said why.
  Each backend now records its first transport failure (`last_error`, first rather than last so
  the message does not depend on how many queries the ladder happened to run) and the failure
  names it — `backends ['exa-mcp'] surfaced no candidates across 8 queries — exa-mcp: HTTP 401`.
  The presence-based `--check-backends` is deliberately **unchanged**: a dead key still counts
  as configured, because that gate answers a different question. Validity is now
  `rehearse.py`'s job. Five tests, four falsified: with the recording disabled the message
  reverts to the bare "surfaced no candidates".

- **A `429` from YouTube was classified as a page — the one way this corpus could report a
  false "missed games" finding.** `_fetch` returns an error's status *and body* like any other
  response, and `record()` treated only `status is None` — a connection failure — as a failure,
  so an HTTP error body passed through as content. Found by running the weekly job's own
  command against the live web rather than by reading it: `record_pages.py --refresh` died with
  an unhandled `ValueError: no ytInitialPlayerResponse and no og:video in document` on page 5
  of 8, and a `429` body was the document it choked on. Two consequences, neither hypothetical.
  On an `expect=evidence` page an error body carries no evidence, so it reads as a
  **`lost-evidence` flip** — an issue filed saying real broadcasts stopped being recognised,
  caused by nothing but a rate limit — and because the documented refresh is *in-place* it
  would have written the error body over the recorded fixture. An HTTP error status is now a
  fetch failure, alongside a dead URL and a 200 that carries no extract (the YouTube consent
  wall), and **all three name the page in a `FAIL:` line**. That last part is why the shape
  mattered: `corpus-refresh.yml` tolerates the failure with a warning telling the reader "the
  FAIL line above names it", and the failing path printed a traceback instead — so the
  instruction pointed at a line that did not exist. `--refresh` exits 1 without writing a
  manifest, so the cycle still stops rather than partially updating the corpus. Four tests,
  two falsified to prove they bite: with the status guard disabled the refresh records
  `clb-home status=429 evidence=False` and the false-flip test fails.

- **The `LLM_MODEL` default named a rung the repository might have no credential for.** Both
  workflows pinned the agent's model with `LLM_MODEL: ${{ vars.LLM_MODEL ||
  'opencode/big-pickle' }}`, and `opencode/big-pickle` is an **OpenCode Zen** id — rung 2 of
  the ladder. The ladder is credential-driven and `capture_transcripts.py --check-rungs`
  passes for a repository holding only `GEMINI_API_KEY` or only `OPENROUTER_API_KEY`; those
  repositories are correctly configured, and the run still asked the CLI for a model it had no
  credential for, produced no transcript, and read exactly like a quiet day. Which rung serves
  is a fact about the credentials that are set, so it cannot be a workflow literal. The choice
  now lives in `scripts/llm_model.py` — a pure `resolve_model(pin, environ)` with the matrix
  written down — and both steps **read** `steps.llm.outputs.model` instead of re-deriving it,
  the same move `write_mode.py` made for the dead `DRY_RUN` switch. Precedence is the ladder's
  own (`gemini` → `opencode` → `openrouter`, imported from `capture_transcripts` rather than
  restated), and `LLM_MODEL` still wins over all of it. Two boundaries are deliberate: it is a
  **selector, not a gate** (`--check-rungs` is the gate, upstream, and a resolver that refused
  to run with no credential would break a `replay_ci.py` replay, where the keys are stripped by
  design), and a runner authenticated only through the CLI's own auth store is **not**
  detected — a Zen id is meaningless without a Zen key, so that case falls through to the free
  router and is what the `LLM_MODEL` pin is for. Selections are never silent: the rung, the
  model and the reason go to the log, and each workflow's run step now carries the credential
  for the rung the resolver picked, including the `GOOGLE_API_KEY` alias.
- **The agent run's transcript was not always a transcript.** Both `opencode run` steps
  persisted it with `... | tee FILE`, and a pipeline's exit status is the *last* command's: the
  CLI could fail and the step would still be green, with every step below it working from a file
  that was never written. The repo had already learned this once — `runtime-daily.yml`'s Phase 0
  ladder carries the comment "Do not pipe this through `tee`: the pipe would mask the exit
  status", pinned by `tests/test_run_daily.py` — and the ladder avoids the pipe. Here `tee` *is*
  the writer, so the pipe is armed with `set -o pipefail` instead, in both workflows.
- **`Run the skill` could not open its own output file.** It was the earliest step in its job to
  write into `.tmp`, and nothing before it created the directory, so `tee .tmp/transcript.json`
  failed on ENOENT — and the artifact upload's `if-no-files-found: ignore` hid both that failure
  and the missing transcript. The step now runs `mkdir -p .tmp` first, and
  `tests/test_agent_run_transcript.py` pins that it is still the first writer (a future earlier
  `.tmp` writer must not be allowed to make the `mkdir` look redundant).
- **`--runner ladder` sent the CLI rung's model id to OpenRouter.** `run_openrouter` used
  `args.model or OPENROUTER_DEFAULT_MODEL`, and in ladder mode `--model` names the *CLI* rung's
  model (`opencode/…`) — never an OpenRouter slug. The rung answered `no choices in response`
  and read as dead for a reason that had nothing to do with it; `resolve_gemini_model` already
  applied the same guard on the other side of the ladder. `openrouter_model_for` now does.
- **§17's hard requirement could not be enforced.** A `WRONG` event was never shown as
  `VERIFIED` on paper and always could be in practice: the planner's `WRONG` row reads a
  `[WRONG] ` title prefix from the calendar, and nothing wrote one — `audit_events` computes the
  relabelled title and stores it in `audit.jsonl`, and no component consumed it. So the audit's
  terminal verdict existed only as a telemetry row, and a later run's fresh guess was free to
  promote the condemned event. The runtime job now reads the ledger read-only (a `git show` of
  the telemetry branch, needing no `contents: write`) and passes it to the planner, which holds
  the event whatever the candidate claims. No new permission, no new writer: the *same*
  `latest_per_event` decides which verdict is current on both sides.
- **Phase 3 had never run.** `telemetry/events.jsonl` and `telemetry/evidence.json` are the
  audit's documented inputs and the `audit` job reads them, but nothing wrote them: it found them
  absent, printed a notice, and did nothing on **every** run. No verdict was ever recorded,
  `precision` was `n/a` for the life of the project, and the self-improvement loop had no ledger
  to learn from — while every workflow, sensor and gate read green. `scripts/event_ledger.py` is
  the writer; the chain needed three more links repaired before its output was usable at all
  (the discarded event id, the dropped evidence flags, the non-deduping audit). Verified end to
  end offline: candidates → plan → applied result → ledger → audit gives `verified=1 wrong=1
  inconclusive=1`, `metrics.py` reports `precision=0.500 (1/2 judged, 1 inconclusive)` where it
  used to report `n/a`, and `synthesise_eval_case.py` turns the paid-access misjudgement into a
  regression case.
- **A stored event with no description matched any candidate within ±30 minutes.**
  `upsert_events.events_match` compares the `Teams:` line of the event description — the format
  `references/calendar-setup.md` specifies and `list_events` parses back out — and
  `calendar_io.apply_plan`'s `descriptions=` map was never passed by anything, so every event the
  runtime created carried `description: ""`. The teams check is **skipped** whenever either side
  is empty, so a different pairing at the same time was planned as an `update` of the existing
  event: the wrong game written onto a subscriber's event instead of a new one. The block is now
  derived from the plan row's own `league`/`teams`, and `parse_event` reads the `League:` line
  back so the league check is usable on stored events too.

- **The daily cron could never write to the calendar.** `runtime-daily.yml` resolved dry-run
  with `DRY_RUN: ${{ inputs.dry_run || 'true' }}` in *both* the agent step and the apply step.
  On a `schedule` event there is no `inputs` object, so that expression is **always** `'true'`
  and `--live` was unreachable: with `ENABLE_CALENDAR_WRITES=true` the job planned every event,
  wrote nothing, and ran green every morning. `docs/runtime.md` names that variable as *the*
  switch, so the documented control was dead on the only path nobody watches — the same
  "green and empty" shape as the Phase 0 ledger with no backend configured. The decision now
  lives in `scripts/write_mode.py`, and a workflow step **reads** the resolved value instead of
  recomputing it; `tests/test_write_mode.py` reds if any workflow re-derives the mode (checked
  by restoring the expression, which fails three pins).
- **The write mode is now fail-closed about the master switch.** On a `workflow_dispatch` the
  `dry_run` input can only ever *add* dry-run: `--input false --enabled ''` is still dry-run, so
  poking the button cannot widen what the repository variable allows. An empty event name or an
  unrecognised input value fails closed and names the reason.

### Changed

- **The resolved write mode is printed in the `runtime` job summary**, with the input that
  decided it. "No events were created" means something quite different in dry-run than with
  writes permitted, and the old `::notice::` inside the apply step was only visible to somebody
  already reading the log.
- **`validate.yml` asserts both directions of the switch.** The `--enabled true` case had no
  fixture and no step, which is why a permanently-dry-run cron went unnoticed; the step also
  pins that a dispatch cannot grant a write the variable did not.
- **Workflow context reaches shells through `env`, not `${{ }}` interpolation**, in the resolve
  step and both job summaries. That is GitHub's script-injection guidance, and it is what makes
  the steps replayable at all: a `${{ }}` inside a `run:` block arrives at the shell unexpanded,
  where it is a bash *bad substitution*, so the replay `runtime-daily.yml` showed two permanent
  FAILs that had nothing to do with the workflow. `replay_ci.py` and `docs/do-harness.md` now
  record the limitation (a step's `env:` block is **not** injected) and why injecting it is the
  wrong fix — a literal `${{ secrets.X }}` reads as *configured* to a backend and could start
  real network calls with a bogus key.

- **`record_pages.py --refresh` now reports verdict flips** (`--baseline`, default `--out`, read
  *before* anything is written, so an in-place refresh can see what it is replacing). `--json`
  carries the same as `pages`/`flips` and remains the payload alone on stdout, since the workflow
  redirects it straight into a file the next step parses. A page absent from the baseline is new,
  not a change, so a first run reports nothing.
- **`replay_ci.py` denies GitHub writes.** `gh issue create|comment|edit|…` and
  `corpus_flip_issue.py --file` are skipped, never executed: a replay cannot delete what it
  created, and replaying `self-improve.yml` would otherwise open a real PR on the public repo. The
  denial is on the **capability** (the flag) rather than the step name, matching how `--live` is
  handled, and it is matched with `re.DOTALL` because the flag is nearly always on the next line
  of a wrapped command — exactly where a line-anchored regex steps quietly over it. The read-only
  halves stay replayable.
- **A page must name the game before its evidence may become a calendar link.** `--expect-teams`
  adds `render_ladder.anchor_game()`, which matches the expected clubs against the document's
  **own name** (`<title>`, `og:title`, `twitter:title`, YouTube `videoDetails.title`) and never
  against the body — a body links to every club it mentions, the same trap as a body-wide "live"
  marker. Two behaviours come from real recorded pages: a title that advertises **more than one
  pairing** (`"… USA vs France live | Germany vs Spain"`) is reported `ambiguous` rather than
  attributed to one of them, and a competition name (`"Tauihi Basketball Aotearoa"`) is no longer
  mistaken for a club. Both are now regression-tested against the recorded titles.
- **The render ladder can no longer be empty, and an unavailable rung no longer ends the climb.**
  `curl_cffi not installed` was classified as a *real error*, so the ladder stopped at its first
  rung: in a stdlib-only environment — which is what `requirements-dev.txt` promises and what CI
  installs — no rung could ever run, and every game would have become `UNVERIFIED` for a reason
  unrelated to the game. `skipped` is now a distinct outcome that climbs, and a keyless
  `urllib` rung leads the server-rendered ladders (and trails the `magenta.tv` SPA ladder, where a
  browserless GET cannot succeed but does prove the gate posture). `--probe` now prints **every**
  rung's reason instead of one line for whichever rung happened to be first.
- **Diacritics fold in club names, because a real recorded page proved they did not.** `Kāhu`
  tokenised to `{k, hu}`, so the page titled `LIVE - Tauranga Whai v Northern Kāhu` could not be
  attributed to that game. `München`, `Munchen` and `Muenchen` now match, as do `Straße` and
  `Strasse` — which also stops the dedupe planner creating the same game twice from two spellings.
- **The stream-evidence gate now meets the real web, and it was wrong (2026-09-15).**
  `FetchResult.has_stream_evidence()` is the last check before a rendered page may become a link in
  the calendar. It required a "player marker" **and** a "live marker", but both sets were bare words
  (`player`, `dash`, `live`, `läuft`, `hls`) matched against the whole document — and every modern
  page embeds its JavaScript runtime inline, where all of those words always appear. Run against
  eight recorded real responses, the old rule accepted:

  | Recorded page | Why it streams nothing |
  |---|---|
  | a **recorded YouTube video** | `isLiveContent:false`; `player`/`dash`/`live`/`livestream` are bundle strings |
  | the **MagentaSport announce home page** | it announces games, `magenta.tv` streams them; bundle has `.mpd` and `läuft` |
  | the **Basketball Champions League home page** | a league homepage; prose says "live now", bundle says `player` |
  | the **YouTube home page**, and the Live-filtered **results** page | neither is a stream |

  Only the 942-byte `magenta.tv` app shell was correctly rejected. The gate is now a conjunction of
  a **media** marker (`<video`, `.m3u8`, `.mpd`, a stream content type, `og:video`) and a
  **live-state** marker (`Jetzt live`, or a structured YouTube field such as `"isLiveContent":true`),
  with media markers matched after `<script>` blocks are stripped and the structured fields matched
  against the raw document — a script block is the only place they occur. All eight recorded pages
  now classify correctly, including both real live pages. `LIVE` as page *text* is no longer
  evidence; that is a deliberate narrowing, recorded in `references/magenta-tv.md`, `docs/runtime.md`
  and the corpus README rather than left as a surprise.
- **The self-improvement loop now closes end to end, offline.** `tests/test_self_improve_loop.py`
  runs audit verdict → synthesised case → capture → graded transcript, with the `replay` runner
  standing in for a model. Three real defects it caught, none of which the existing tests could
  see because each link was tested alone:
  1. **`synthesise_eval_case.py --json` was not parseable.** The summary line was printed
     *before* the payload, so `json.load(stdout)` raised — the same defect already fixed in
     `upsert_events.py` and `calendar_io.py`. The payload is now alone on stdout and the human
     line moved to stderr. `tests/test_synthesise_eval_case.py` had a test **named**
     `test_json_mode_is_parseable` that read `stdout.split("\n", 1)[1]`: it encoded the
     workaround, not the contract, so it asserted the opposite of its name.
  2. **The self-improve gate could never grade a synthesised case.** It ran
     `--transcripts tests/fixtures/runtime_transcripts.json`, which covers ids 1..N, while a
     synthesised case is N+1 — so the gate failed with "no transcript supplied" for a
     legitimate PR, for a reason unrelated to the PR. The workflow now captures transcripts for
     **exactly the new ids** and grades those.
  3. **A scoped capture could not be graded at all.** Grading a 3-case file against the 36-case
     eval set fails on the other 33, so `runtime_eval.py` gained `--allow-partial`: grade the
     cases the file covers and **report how many were skipped**. The strict default is
     unchanged (a missing transcript is a FAIL), the concession is a named flag at the call
     site rather than a silent behaviour, and two new rules make it safe — a transcript for an
     id that is not in `evals.json` is an error (stale, not extra evidence), and a file that
     grades nothing fails even with the flag.
- **`scripts/runtime_eval.py`** — `--transcripts` now grades a real capture instead of
  demanding equality with `expected_output`. The two regimes are kept apart on purpose:
  `--stubs` (canned, we author them) still requires byte-equality, because a stub drifting
  means the canonical answer changed; `--transcripts` grades the `assertions[]` needles and
  *reports* whether the canonical text matched, because a live model cannot reproduce it.
  Requiring equality made the real-transcript path unable to pass anything, which is exactly
  why the workflow's grading step could only ever print a warning. Both regimes now also
  require a `Decision=` line and at least one assertion, so neither passes vacuously.
- **`scripts/capture_transcripts.py`** — the capture prompt no longer appends
  `expected_output[:200]`. For a short case that is not a shape hint, it is the answer, and
  since grading collects `name=PASS|FAIL` needles by substring, a model echoing the prompt
  back would have scored 100%. The prompt now states the *form* and points at
  `references/validation-workflow.md` for the check names — the vocabulary is fair to give,
  the verdict is not.
- **`plans/invariants.json`** — three changes. The wrapper object was replaced by a **bare
  array** (a shape `do-harness init`/`seed` actually accepts); the set grew from 13 to 18
  invariants; and three invariants that named sensors existing nowhere (`eval-integrity`,
  `write-path`, `fixture-recall`) were remapped to the sensors that really do guard them.
  An invariant pointing at a sensor that does not exist is guarded by nothing while looking
  guarded.
- **`scripts/fixtures.py`** — `--out` is now **idempotent**: a `game_key` already in the file
  is skipped. The telemetry job re-observes the same 7-day window daily, so a blind append
  wrote every game once per day forever; the file grew without bound and `fixture_recall`
  would have measured file length instead of coverage.
- **`scripts/candidates.py`** — `fixture_recall` now measures **distinct games**, not rows,
  via the new `distinct_fixtures()`. It also reports `dropped` (no usable `game_key` — a
  parser regression) and `duplicates` (a re-observation — harmless) separately, because those
  two numbers mean opposite things and were previously conflated into one `dropped` count.
- **`.github/workflows/runtime-daily.yml`** — the telemetry job now fetches official league
  fixtures into `telemetry/fixtures.jsonl` (tolerated, not required: a markup change must
  degrade `fixture_recall` to `n/a`, not red the run) and writes the `metrics.json` snapshot.
  The job summary reuses `metrics.py`'s renderer rather than reformatting the snapshot.
- **`.github/workflows/validate.yml`** — adds a `metrics.py` step on fixture inputs, so the
  metric contract is enforced offline in the Python matrix as well.
- **`scripts/bump_version.py`** — adds a non-mutating `--check` mode comparing the `SKILL.md`
  frontmatter version against the newest dated `CHANGELOG.md` heading. Needed by the
  `version-sync` sensor, and it exits `0`/`1` without writing anything.
- **`docs/do-harness.md`** — **Step 6 is withdrawn**. It instructed making `do-harness eval`
  the sole grading authority and demoting `scripts/validate.py --check evals`. Measured,
  `do-harness eval` resolves skills **only** under `.agents/skills` (the path is hardcoded; it
  reports `error: skill '...' not found under .agents/skills`) and never sees the root skill.
  Both skills it does grade are do-harness's own tooling. Following the step would have left
  `evals/evals.json` ungraded behind a green tick reading `structure=ok evals=8/8
  pass_rate=1.00` about an unrelated skill. The four product graders stay; `do-harness eval`
  runs in addition.
- **`AGENTS.md`** — extended from the generated routing contract with this repository's
  specifics: the product is the **root** skill (`.agents/skills/*` is development tooling),
  which commands are the product gate, the release flow, and the pre-completion gate. It also
  documents the strike-halt behaviour (a sensor that fails 3 times consecutively is *halted*
  and reported failed without being run — which reads as a permanent, unexplained red) and
  the `do-harness errors list` / `errors clear` escape hatch.
- **`.github/workflows/verify.yml`** — adds a `Harness trends` step (`do-harness metrics`:
  per-sensor runs/failures, open strikes, eval pass-rate history) marked `if: always()`, so
  the strike report survives the very failure that makes it interesting.
- **`CONTRIBUTING.md`** — corrected two drift claims that would have misled a contributor:
  the CI matrix runs Python **3.9 and 3.12** (not 3.8–3.12), and the pytest suite takes
  ~**15s** (not ~1s). Also documents the `do-harness verify` gate, points at `tests/` and
  `docs/runtime.md` from the edit list, and states explicitly that `do-harness eval` is not
  part of the product gate.
- **`scripts/source_learning.py`** — adds `--dest DIR` so the daily runtime can write
  `run-log.jsonl` into a telemetry worktree without a second writer implementation, plus
  optional `--game-key / --backend / --rung / --llm / --state / --color-id / --event-id`
  keys. Optional keys are emitted **only when supplied**, so pre-existing rows keep their
  exact shape and the original 18 tests pass unchanged.
- **`scripts/runtime_eval.py`** — adds `--transcripts PATH`, accepting the rich
  `{"transcripts": [{"id", "output"}]}` shape from `capture_transcripts.py` as well as the
  flat `{id: output}` shape. `--stubs` keeps its exact semantics, so the canned mode
  survives as an offline smoke test rather than being replaced.
- **`scripts/color_mapping.py`** — `get_color_id(league, event_type, state=None)`. The
  verification-state dimension is **added**, not substituted: the league rules stay
  orthogonal, so FIBA remains Sage and a confirmed final remains Tomato, and omitting
  `state` preserves the previous behaviour exactly.
- **`SKILL.md`** — Step 5 now states dedupe-before-write over the whole window; Step 6
  documents the three states and reconciles constraint 10 (free access that is merely
  *unconfirmed* is labelled `[UNVERIFIED]` rather than rejected); a new Step 6.5 documents
  the audit and its three safety rules; constraints 17–19 and four new red flags enforce
  the state machine.
- **`.github/workflows/runtime-daily.yml`** — the audit job now runs the real
  `scripts/audit_events.py` against the telemetry branch instead of echoing a notice. It is
  deliberately self-contained (`needs: telemetry`, not `needs: runtime`) so it still runs
  on a day the skill run failed — which is exactly when a false positive is most likely to
  have been created. The runtime job's write path is now four ordered steps — extract,
  list, plan, apply — instead of leaving the calendar call to the workflow shell, and the
  dry run always runs before the live write so the log contains the intended change even
  when the write then fails.
- **`.github/workflows/validate.yml`** — the previously documented-but-absent offline
  contract checks are now actually executed: verification, the upsert fixture plan, the
  audit fixture audit, the candidates fixture ledger, the render-ladder registry, an
  offline link check, the YouTube URL builders and source-learning discovery. Phases 1–5 add
  six more, including the whole Phase 1 write path (transcript → candidates → plan → dry-run
  apply) with **no credentials at all**.
- **`scripts/upsert_events.py`** — plan rows now carry `start`, `end`, `league` and `teams`,
  so a plan is executable without the caller re-deriving the event body. On an update the
  *existing* event's start time wins, because a source re-announcing a game an hour later
  must not silently move an event subscribers already have.
- **`scripts/verification.py`** — adds `state_from_title`, the inverse of `title_for`. An
  unprefixed title reads as `VERIFIED` deliberately: events created before the state machine
  existed carry no prefix, and reading them as `UNVERIFIED` would repaint a whole calendar
  amber on the first run.
- **`references/calendar-setup.md`** — colour coding documented as two orthogonal
  dimensions with the precedence rule; duplicate detection rewritten around
  dedupe-first ordering plus the replace-if-unverified table.
- **`references/self-learning.md`** — the audit promoted to a first-class loop with its
  verdict table and three safety rules; the eval-loop section now states that a new case
  must pass against its own fix before that fix is accepted.
- **`docs/runtime.md`** — status table updated (Phases 2 and 3 implemented), plus new
  sections on verification states, the write path and auditing, and three new recovery rows.
- **`scripts/README.md`** — contents table, test list and CI table extended with the three
  new scripts and five new test files, plus an explicit rule: do not add a script that can
  only be tested with credentials. The CI table was also corrected to match the workflow it
  describes — it listed steps that did not exist and claimed a five-entry Python matrix
  where the workflow has two.
- **`README.md`** — file layout and key features cover the state machine, the
  dedupe-before-write rule and the audit path; test count 271 → 393; two new checklist items
  for the fixture contract runs.
- **`README.md`** — file layout now covers `docs/`, `plans/`, the workflows and
  `tests/fixtures/`; key features gain the in-code guardrails and recall measurement;
  test count 178 → 271.
- **`.gitignore`** — ignores `.tmp/` (workflow scratch), `logs/` (local run logs),
  `.do-harness/` and `.opencode/`.
- **`scripts/capture_transcripts.py`** — `--check-rungs` added: the offline, presence-based
  **gate** that fails when no ladder rung is configured at all, as distinct from
  `--list-models`, which is a **report** and exits 0 whenever it managed to report (even when
  the report is "every rung is down"). `--list-models` also gained a zero-token proof of the
  OpenRouter key (`GET /api/v1/key`), written after finding a key that was *present* and
  returned `401 User not found`. The distinction matters: a run with no LLM configured cannot
  apply the 7 checks, yet its summary looks exactly like "no free streams found today".
- **`.github/workflows/runtime-daily.yml`** — a `Preflight — at least one LLM rung is
  configured` step, placed **after** the CLI install (a runner whose only credential is the
  CLI's own auth store is configured, and checking earlier would red it) and before the
  backend boot. It is deliberately not `continue-on-error`: the failure it catches is a
  configuration fault, not a quiet day.
- **`.github/workflows/runtime-daily.yml`** — the two transcript-grading steps were replaced by
  one honest step. They previously ran a `--from tests/fixtures/runtime_transcripts.json`
  replay (the file is deliberately absent) and fed the **raw run stream** to
  `runtime_eval.py --transcripts`, which requires one output per eval case — a single daily run
  is not 36 cases. Both tolerated failure, so neither could ever grade anything. The step now
  distinguishes the two states that matter: no captured set (a notice, with the exact capture
  command) versus a captured set (**grade it, and fail hard**). A new step reports which LLM
  rungs the run actually had, because "found nothing" and "every rung was dead" look identical
  without it.
- **`.github/workflows/validate.yml`, `do-harness.toml`** — `check_workflow_refs` added as a CI
  step and as a `workflow-refs` sensor in the `verification` and `release` sets, so the class of
  bug cannot come back through a different workflow.
- **`scripts/capture_transcripts.py`** — a capture that fails for **any** case now writes 
  **no file at all** (previously it wrote the cases that worked and exited 1). The grading gate
  keys on the file *existing*, so a partial write would satisfy the gate while covering fewer
  cases than the eval set. `--request` is now the module's only network call, shared by both
  HTTP rungs and monkeypatchable in tests.
- **`scripts/run_daily.py`** — `--check-backends` added: the offline, presence-based **gate** on
  there being at least one configured search backend, and `runtime-daily.yml` now runs it
  *before* the ladder and deliberately does **not** tolerate it. Phase 0 is the only producer of
  the recall denominator, so a run with no backend configured writes no rows while staying green
  — and absence looks exactly like a quiet day. This is the same split already used on the LLM
  ladder (a gate that reds on configuration, a report for everything else), and the ladder step
  still tolerates a *content* failure, so a transient 429 cannot take the telemetry commit down
  with it.
- **`scripts/run_daily.py`** — `--json` now leaves stdout as the payload **alone**; every human
  line moved to stderr. This is the fourth instance of the same defect in this repo
  (`upsert_events.py`, `calendar_io.py`, `synthesise_eval_case.py`, and here), and the first time
  the contract is pinned by a test at the point of introduction. `--json` also gained
  `attempted` alongside `backends`: the two are now kept apart, because a configured backend that
  returned nothing was previously reported as a *source of candidates* — so "5 candidates from
  [exa-mcp, tinyfish]" could not be told from the case where exa contributed all five. The
  run summary prints both (`N candidates from [...] of [...] attempted`).

### Notes

- Corrected a false premise during research: **GitHub Models was fully retired on
  2026-07-30**, so no free LLM is reachable from Actions via `GITHUB_TOKEN`. Also recorded
  that a GCP project and the Calendar API require **no billing**, and that Gemini's free
  tier is a separate AI Studio product — keyless WIF does not force any spend.
- The daily cron was moved from 06:30 to **08:30 UTC** because Gemini free-tier RPD resets
  at midnight **Pacific**, so an earlier run would spend the previous day's quota.
- **Two real bugs were caught by the new tests, both of the kind that reach a public
  calendar.** (1) `--json` mode printed a human summary line after the JSON payload,
  breaking machine parsing; both writers now emit the payload alone and send progress to
  stderr. (2) The dedupe matcher compared club names exactly, so a source writing `ALBA` and
  another `ALBA Berlin` would have produced **two events for one game**; matching is now
  token-nested (both teams must still match, within ±30 min, with compatible leagues, so the
  looseness stays bounded).
- The audit job deliberately records a **notice and does nothing** when
  `telemetry/events.jsonl` or `evidence.json` is absent. Inventing a verdict for an event it
  cannot see would be worse than having no audit at all.
- **Phases 1–5 are all written now, and all of them have an offline mode**, so the whole
  runtime is exercisable in CI without a single credential. What remains unproven is not code
  but *inputs*: real transcripts have never been captured (`tests/fixtures/runtime_transcripts.json`
  is absent by design), so the grader has never graded a real run, and the pre-fix-red
  property of a synthesised eval case is asserted rather than demonstrated. That gap is
  stated in `docs/runtime.md` and in the workflow itself — `self-improve.yml` emits a warning
  rather than a green tick when no transcript is present.
- Two more bugs were caught by the new tests. `calendar_io`'s summary line unpacked a result
  dict containing a `failed` list into `.format(failed=…)`, raising `TypeError` on every
  successful dry run; and the microdata fallback's `div`-based block tracking closed on the
  wrong element, so it silently produced **zero** fixtures for any page whose container was
  not a `div`. Depth-based tracking replaced it.
- **`do-harness eval` does not grade this repository's skill**, and that finding changed an
  adopted plan. It resolves skills only under `.agents/skills` — a hardcoded path with no
  config key to widen it — so it grades do-harness's own tooling and never the root
  `SKILL.md`. The plan's Step 6 assumed otherwise and would have demoted
  `scripts/validate.py --check evals` on the strength of a green tick about an unrelated
  skill; it is withdrawn, and `tests/test_harness_boundary.py` asserts the product graders
  stay so a later edit cannot perform the swap while reading this file as an instruction.
  The general lesson is in `docs/do-harness.md`: "at least as strict" is a claim about
  *coverage*, and the only way to settle it was to run the tool against this repo's actual
  layout.
- **Two real defects in the harness gate, both invisible locally and both fatal in CI.**
  Found by replaying `verify.yml` against a *fresh* checkout rather than trusting a green
  local run. (1) **`do-harness eval` SIGSEGVs (exit 139) on its second invocation** once
  `.do-harness/agent_state.db` exists — `rm -rf .do-harness; do-harness eval` (0) then
  `do-harness eval` (139), and `do-harness seed && do-harness eval` reproduces it. It never
  happens in this working copy. (2) **`do-harness status` exits 1** on a checkout with no
  recorded beats, which is every fresh CI run. The workflow would therefore have gone red on
  every first run for two independent reasons. Fixes: `eval` now runs **first** (before
  anything creates the database) and is `continue-on-error` as the supplemental check it is;
  `status` is reporting-only; and `seed` is removed from CI entirely, since seeding an
  ephemeral database proves nothing the `invariants-shape` sensor and
  `tests/test_harness_boundary.py` do not already prove — and it is one of the triggers for
  the crash. Both defects and the reasoning are recorded in `docs/do-harness.md`, and six new
  boundary tests pin the ordering and the tolerance so a later reorder cannot reintroduce
  them.
- **`do-harness` Step 9 was exercised rather than assumed, and three claims about it were
  wrong.** Running the documented loop against two real bugs found in this repo showed that
  (a) `--task` must be a *numeric* id and the task must exist (`task add` allocates it);
  (b) `distill` refuses the root skill outright —
  `error: unknown skill 'skill-basketball-streams': no SKILL.md under .agents/skills` — the
  same hardcoded-`.agents/skills` boundary as Step 6, so distilling a heuristic from a
  root-skill bug lands it in the **tooling** skill (the experiment was reverted for that
  reason); and (c) **`--to-fixture` does not write a fixture and does not add an eval case.**
  It appends the heuristic to the target skill's `references/heuristics.md` and ratchets that
  skill's pass-rate floor (`floor now 0.95`). An earlier draft of `docs/do-harness.md` claimed
  the flag *enforces* the "every new failure mode becomes an eval case" rule. It does not — it
  only makes the bar stricter, which on a skill with no case for that failure is a stricter
  bar over unchanged coverage. The rule that creates the case for this repo is
  `scripts/synthesise_eval_case.py` + `self-improve.yml`.
- **The `live-gate` sensor earned its place immediately.** It caught `@FIBAWorld` being
  rejected only in prose — the rule lived in `SKILL.md` and `evals/evals.json` but in no
  Python gate, so a channel-shaped URL for a handle that does not exist was promoted as
  `SCHEDULED`. A sensor asserting a real outcome, rather than an arbitrary exit code, found
  it on the first run.
- **One deliberate deviation, pinned rather than assumed.** `scripts/check-commitlint.sh`
  **fails open** when its `--message` file cannot be read (exit `0` with a warning). A hook
  must not block a commit because it could not read its own input; the fail-closed case is
  the script or repository being *missing*, which do-harness enforces. `test_harness_boundary.py`
  pins this so it stays a decision instead of becoming an accident.
- The telemetry job's fixture write needed the same care as the derived snapshot. Fetching
  is tolerated (a league markup change degrades one metric to `n/a`), the write is
  idempotent, and the metric is computed over distinct games — three separate guards against
  `fixture_recall` drifting into a number that measures the file rather than the coverage.
- The Phase 4 loop is now *executed* in CI rather than described: the end-to-end test runs the
  whole chain with the `replay` runner, including the red direction. What is still simulated is
  the model — `replay` proves the plumbing, not that a free provider answers.
- **Still unproven, and it is inputs rather than code.** `--runner gemini` cannot be exercised
  here: this machine has no live `GEMINI_API_KEY` (the `OPENROUTER_API_KEY` present returns
  `401 User not found`), so no real transcript has been captured and the grader has still never
  graded a real run. What the tests prove is the *contract* — request shape, error taxonomy,
  ranking and failover — not that a free rung answered. Run
  `python3 scripts/capture_transcripts.py --list-models` first; it is the one command that
  answers "which free provider can this runner actually use?" without spending a call.

## [1.2.0] - 2026-09-14

### Added

- **`references/magenta-tv.md`** (NEW) — magenta.tv playbook: why a plain fetch
  fails (verified: `openUrl https://www.magenta.tv/` returns "No readable text
  found at URL"), the five-rung fetch ladder (Firecrawl → TinyFish Fetch → Exa →
  Tavily → headless browser), the `player + live marker` acceptance rule, URL
  shapes, and worked accept/reject validation logs.
- **`references/youtube-live-search.md`** (NEW) — live-only, future-only
  contract: both search entry points (`sp=EgJAAQ%3D%3D` HTML filter and Data API
  `eventType=live`, 100 units per call against a 10,000/day free quota), the
  raw-payload → candidate-schema field mapping table, the five-part gate, the
  channel allow-list, and three worked examples.
- **`references/search-backends.md`** (NEW) — free search/search-MCP backends
  (TinyFish, Firecrawl, Tavily, Exa, Brave, YouTube Data API) with allowances and
  JS-render support, free LLM IDs on OpenCode Zen (`big-pickle`,
  `mimo-v2.5-free`, `ling-3.0-flash-fin-free`, `nemotron-3-ultra-free`,
  `nemotron-3.5-lightning-free`, `muse-spark-1.3-contributor-free`), MCP wiring,
  the Google Calendar v3 path, and a list of the gates that must **not** be
  delegated to an LLM.
- **`references/self-learning.md`** (NEW) — run log → source scoring →
  quarantined new-source discovery → link revalidation cadence → eval feedback
  loop, including the four conditions a source must meet before a human may
  promote it.
- **`scripts/youtube_live.py`** (NEW) — stdlib live-only/future-only gate:
  `classify_stream` returns `LIVE`/`SCHEDULED`/`REJECT`, `filter_candidates`
  promotes survivors, `build_live_search_url`/`build_api_live_search_url` emit
  the two search entry points. Rejects regular uploads, fixed-duration videos,
  ended broadcasts, past `scheduled_start`, and non-live URL shapes.
- **`scripts/link_check.py`** (NEW) — link revalidation classifying
  `OK`/`BROKEN`/`BLOCKED`/`ERROR`/`UNREACHABLE`/`INVALID`; `--dry-run` performs
  structural checks with no network I/O; `--out` writes a JSON report with a
  summary and an ISO `checked_at` stamp.
- **`scripts/source_learning.py`** (NEW) — three modes: `record` (append a JSONL
  run-log row), `score` (per-source attempts/creates/hit_rate, sorted), and
  `candidates` (discover unapproved domains, write them `quarantined` to
  `logs/source-candidates.json`). Never promotes a source.
- **`config/sources.json`** (NEW) — machine-readable approved-source registry
  (tiers, domains, social handles, exclusions) consumed by
  `source_learning.py` and kept in sync with `references/approved-sources.md`.
- **`docs/do-harness.md`** (NEW) — adoption plan for the external `do-harness`
  CLI (sensors, signal sets, grading authority, risks). PLAN ONLY; nothing in it
  is executed.
- **`tests/test_youtube_live.py`, `tests/test_link_check.py`,
  `tests/test_source_learning.py`** (NEW) — 95 tests covering the three new
  scripts (units + CLI contracts), bringing the suite to 178 tests.
- **BCL as an approved source** — `championsleague.basketball` added to Tier 1
  with the **selected-games** rule: free access is decided *per game* via the
  site plus `@BasketballCL` and `facebook.com/BasketballCL`; silence is not
  consent. Added to `references/approved-sources.md`, `config/sources.json`,
  `SKILL.md` Step 2.6, and four eval cases.
- **Social media validation table** — `@BasketballCL`, `@MagentaSport`,
  `@EuroLeague`, `@BBLofficial` documented as Check 1 evidence only, never as a
  `directLink`.
- **Eval cases 21–32** — YouTube live accept / regular-upload reject /
  ended-broadcast reject / past-`scheduled_start` reject, magenta.tv
  SPA-shell reject / Firecrawl-render accept, BCL free accept / paid reject /
  silence reject, link 404 quarantine, link 403 retry, quarantined new source.

### Changed

- **`SKILL.md` version** bumped `1.1.2` → `1.2.0`; `description` rewritten (and
  kept under the 1024-char frontmatter limit); `allowed-tools` extended with
  `firecrawlScrape tinyfishFetch youtubeLiveSearch linkCheck`.
- **`SKILL.md` — new Steps 2.6–2.9**, placed after the existing 2.5 so no prior
  step number moved: 2.6 BCL selected-games handling, 2.7 YouTube live-only
  search, 2.8 the dynamic-page fetch ladder, 2.9 link revalidation + run log +
  new-source discovery.
- **`SKILL.md` — Check 2/5/6 definitions tightened**: Check 6 now requires a
  *readable* body (an empty SPA shell is UNVERIFIED, not a pass) and 401/403/429/451
  are documented as *blocked*, not *broken*; Check 2 rejects YouTube items with
  `liveBroadcastContent == none`, a set `actualEndTime`, or a fixed duration.
- **`SKILL.md` — Constraints 12–16** added (magenta render rule, BCL per-game
  rule, YouTube live-only rule, social-media-is-evidence rule, quarantine-never-delete).
- **`SKILL.md` — 5 new Rationalizations rows and 7 new Red Flags items** covering
  the SPA-shell 200, the 403-misread-as-paywall, "BCL lists it so it's free",
  `/live` URLs surviving after a broadcast ends, and deleting events on link failure.
- **`SKILL.md` — `## Available scripts`** now documents all seven scripts with
  the shared exit-code convention.
- **`references/approved-sources.md`** — Tier 1 gains BCL; Tier 4 renamed to
  MagentaSport / MagentaTV; the YouTube table gains `/live/<id>`,
  live/upcoming `watch?v=`, and `/playlist|/results|/shorts` rejections; new
  "Social Media as Validation Sources" section.
- **`references/validation-workflow.md`** — Check 6 gains the readable-body and
  anti-bot clauses; Check 2 gains the YouTube VOD clauses; new BCL special case;
  the Magenta stream-search step now points at `references/magenta-tv.md`.
- **`README.md`** — description, file layout (now including `config/`, `docs/`),
  key features, approved-source categories, and the self-validation checklist
  updated; eval count 12 → 32; test count recorded as 178.

### Notes

- All new scripts remain **stdlib-only on Python 3.8+**; the only test dependency
  is still `pytest` in `requirements-dev.txt`.
- No network access is required to validate the repo: every new test runs offline
  (dry-run mode, monkeypatched HTTP errors, or pure functions).

## [1.1.2] - 2026-07-02

### Added

- **`SKILL.md` — `> IMPORTANT MAGENTA NOTE` blockquote** under `## Approved Sources` — explains the two-domain split: `magentasport.de` (content/announcement provider) vs `magenta.tv` (streaming platform), dynamic URL problem, and the mandatory both-domain check requirement.
- **`SKILL.md` — `### Step 2.3 — MagentaSport/MagentaTV Special Handling`** — new sub-step between Step 2 and Step 2.5 with: domain separation, mandatory two-step announcement + stream search strategy, free stream indicators (PASS), paid indicators (FAIL → REJECT), and cross-reference rule (no announcement = REJECT).
- **`SKILL.md` — Constraint #11** — `Magenta two-domain rule`: a `magenta.tv` stream URL requires a matching official free-access announcement on `magentasport.de` or MagentaSport social media; without it → REJECT.
- **`SKILL.md` — Rationalizations row** — `"The magenta.tv URL looks valid so no announcement needed"` → why it's wrong.
- **`SKILL.md` — two new Red Flag checklist items** — (1) used `magenta.tv` stream URL without a matching free-access announcement on `magentasport.de`; (2) searched only one Magenta domain and skipped the other.
- **`references/validation-workflow.md` — expanded `### MagentaSport/MagentaTV Special Case`** — replaced the thin 3-bullet `### MagentaSport Free Game` section with full coverage: domain separation, free stream policy (one game per matchday), mandatory two-step search strategy, free/paid indicators, and a Validation Note for Check 1 (HTTP 200 alone is insufficient without a matching announcement).

### Changed

- **`SKILL.md` version** bumped `1.1.1` → `1.1.2`.
- **`SKILL.md` References section** — `references/validation-workflow.md` description updated to mention `MagentaSport two-domain rule + 1-game rule`.


## [1.1.1] - 2026-06-26

### Added

- **`CONTRIBUTING.md`** (NEW at repo root) — top-level contributor doc
  with quick-start, eval-case schema (required vs recommended fields),
  release-workflow pointer, broken-link issue format, and a
  Conventional Commits prefix note.
- **`scripts/README.md` → Releasing`** — canonical release workflow
  section covering pre-v1.1.0 (legacy GH013 + admin-bypass) vs
  post-v1.1.0 (direct push) flows, the `gh pr merge --rebase --admin`
  command, the tag + tag-push step, and the `delete_branch_on_merge`
  note.
- **`.github/workflows/codeql.yml`** (NEW) — CodeQL analysis on
  every push + PR + weekly cron (`0 6 * * 1`) on the default branch.
  Security-extended query suite over Python, SARIF upload via
  `github/codeql-action/analyze@v3`. Augments the validator matrix
  with security scanning.
- **`.gitignore`** — created with `__pycache__/`, `*.pyc`, `*.pyo`,
  `*.pyd`, and `.pytest_cache/` patterns. pytest bytecode cache is no
  longer tracked.

### Changed

- **Ruleset 18142708 (`Main`)** — removed the `code_scanning` rule.
  CodeQL default-setup was `not-configured`; the rule was checking
  against absent results. Direct `git push origin main` now succeeds
  for any contributor with admin role. Other rules (`deletion`,
  `non_fast_forward`, `required_linear_history`, `code_quality`)
  preserved.
- **Repo setting `delete_branch_on_merge: true`** — feat branches
  auto-cleanup after a successful PR merge (was `false`).
- **`scripts/validate.py:smoke_test`** — driven by `CHECKS.keys()`
  iteration over a per-check `fail_fixtures` registry. Adding a 4th
  check now produces a typed error ("no FAIL-fixture builder
  registered") if its fixture is not also registered.
- **`tests/test_validate.py`** — added
  `@pytest.mark.parametrize`-decorated
  `test_each_check_fail_branch_via_parametrize` for the 3 CHECKS
  entries, plus module-level `_setup_*_fail` helpers. Adding a 4th
  check requires one tuple entry.

### Cleanup

- **`feat/test-suite-and-eval-expansion`** branch REFERENCE —
  deleted locally + on remote after PR #1 (v1.1.0 release) merged at
  commit `21e7bcc`. The merged commits themselves remain on `main`
  (post-rebase at `21e7bcc`); only the branch ref is gone. `git log
  main` still shows the contribution.

## [1.1.0] - 2026-06-26

### Added

- **Test suite (`tests/`)** — pytest suite covering both `scripts/validate.py`
  (per-check PASS + targeted FAIL branches via tmp fixtures + CLI / USAGE
  contracts) and `scripts/runtime_eval.py` (structural pass + runtime pass +
  `.strip()` normalization + non-string stub rejection + missing-stub
  detection + malformed JSON + non-coercible int keys). Run with
  `pytest tests/` from the project root. Stdlib-only except for
  `pytest` itself; see `requirements-dev.txt`.
- **`requirements-dev.txt`** — declares `pytest>=8.0` as the only test
  dependency (lower bound keeps Python 3.8 supported in the CI matrix —
  pytest 9 dropped 3.8). Production scripts remain stdlib-only on Python 3.8+.
- **`tests/pytest.ini`** — pytest configuration (testpaths, default options,
  warning filter).
- **CI step: pytest** — `.github/workflows/validate.yml` now installs
  `requirements-dev.txt` and runs `pytest tests/` per matrix entry, so
  the full unit-test suite runs alongside the validator + smoke-test +
  runtime_eval on every push/PR (5 Python versions × `fail-fast: false`).
- **CI step: runtime_eval structural pass** — `.github/workflows/validate.yml`
  now runs `python3 scripts/runtime_eval.py --root .` as an additional
  step, so the per-case structural coverage runs alongside the validator
  on every push/PR.
- **CI Python matrix** — `.github/workflows/validate.yml` runs the validator
  + smoke-test + runtime_eval + pytest over
  `python-version: ['3.8', '3.9', '3.10', '3.11', '3.12']` so every
  documented stdlib compatibility range is covered.
- **Eval cases 9–12** — `evals/evals.json` extended from 8 to 12
  standard-schema cases. New coverage: positive (BBL club website on
  `albaberlin.de` passes all 7 checks; Dyn Sport Mix free tier via
  Pluto TV) and negative (past event violates Check 5; future event >+7d
  violates Check 5).
- **`scripts/runtime_eval.py`** — stdlib-only scaffold runtime-evaluator
  with two modes — **structural pass** (`--root .` walks every case
  and asserts `id:int` + `expected_output:str` + `assertions:list`) and
  **runtime pass** (`--stubs stubs.json` asserts each stub string equals
  the case's `expected_output` AND every evaluation-assertion needle
  parses out of the corpus).
- **`scripts/README.md`** — maintenance doc covering: which scripts exist,
  how to add a new check, how to add a new eval case (JSON-aware heredoc
  edits), how to update `runtime_eval.py`, exit codes + CLI conventions,
  and CI integration.
- **`CHANGELOG.md`** — Keep-a-Changelog format with `[Unreleased]` and
  dated release sections.

### Changed

- **`check_evals` type-coverage** — extended with per-case type assertions:
  `isinstance(expected_output, str)` and `isinstance(assertions[i], str)`
  per entry. Loose by design: empty strings / empty lists / whitespace
  remain legitimate (a case can carry "no PASS/FAIL signal" via
  `assertions: []` or "no output" via `expected_output: ""`). Catches
  type-shape regressions like `expected_output: 42` without rejecting
  legitimately-empty cases.
- **`scripts/validate.py --check evals` exit code** — moved from exit 1
  on first failure to "print all failures, then exit 1" so a single run
  surfaces every regression in one go instead of forcing an
  edit-fail-fix loop.
- **Eval case assertions populated** — cases id=3, id=7, id=8 (which had
  `assertions: []` after the initial `expected.checks` migration) now
  carry a single `officialSource expected PASS|FAIL` assertion derived
  from each case's prompt + expected_output, with matching
  `; checks: officialSource=...` tokens appended so the assertion
  needle parses out of the corpus.

### Fixed

- **Format consistency for case id=3 / id=7 / id=8 expected_output** —
  replaced the double-semicolon + empty `checks:` field produced by the
  initial assertion-population edit with the comma-separated token
  pattern used by cases id=1, 2, 4, 5, 6.

## [1.0.0] - 2026-06-26

### Added

- Initial release of `skill-basketball-streams`.
- `SKILL.md` — frontmatter + 7-step pipeline (date range → webSearch →
  validateStreamUrl → 7 checks → extract → duplicate check → create
  event → output table), strict YouTube URL rules, 7 mandatory checks,
  `## Rationalizations` + `## Red Flags` sections.
- `references/approved-sources.md` — broadcast platforms (Dyn Sport
  Mix, MagentaSport, Sportschau/ARD), regional broadcasters (MDR, BR24,
  RBB24), BBL club website allow-list, YouTube allow/reject table.
- `references/validation-workflow.md` — 7-check pipeline with PASS/FAIL
  criteria per check + decision logic + special cases (MagentaSport
  1-game rule, Dyn Sport Mix free tier, YouTube live-URL-only, duplicate
  handling).
- `references/calendar-setup.md` — Google Calendar event schema, color
  codes, duplicate detection parameters.
- `references/implementation-notes.md` — query templates, time/team/vocab
  helpers, full TypeScript `validateStreamUrl` implementation.
- `references/lessons-learned.md` — incident post-mortem (broken
  `/user/FIBA` events), root causes, prevention checklist.
- `evals/evals.json` — 8 standard-schema eval cases (cases 1–8) covering:
  valid FIBA, `@FIBAWorld` reject, `/user/FIBA` reject, MagentaSport
  free EuroLeague pass, Sky paid reject, highlights reject,
  `/user/TheDBBTV` accept, `/channel/UC…` reject.
- `scripts/validate.py` — stdlib-only validator running `check_evals`
  (v1.0.0 schema), `check_skill` (frontmatter + body ≤250 lines +
  mandatory sections), `check_references` (backtick-wrapped `.md` path
  resolution), plus `--check smoke-test` regression guard with three
  target-fixture subprocess invocations.
- `.github/workflows/validate.yml` — initial CI with concurrency group,
  cancel-in-progress, 5-min timeout.
- `README.md` — overview + Mermaid dataflow + self-validation
  checklist.
