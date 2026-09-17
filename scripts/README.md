# `scripts/`

Maintenance scripts for the `skill-basketball-streams` project. Stdlib-only
on Python 3.8+; the only external dependency lives in `requirements-dev.txt`
for the test suite (`pytest>=9.0`).

## Contents

| Script | Purpose |
|---|---|
| [`validate.py`](validate.py) | Schema + reference validator. Three checks: `evals` (evals/evals.json conforms to v1.0.0 schema), `skill` (SKILL.md frontmatter + body ≤250 lines + mandatory sections), `references` (every backtick-wrapped `.md` path in SKILL.md / README.md resolves). Plus a `smoke-test` self-test that re-invokes itself against three target-fixture repos to confirm every FAIL branch correctly rejects. Wire into CI. |
| [`replay_ci.py`](replay_ci.py) | **Debugging tool.** Replays a workflow's `run:` steps against a **clean copy** of the repo — the technique that found every CI-only defect here (`do-harness` segfaulting on a second invocation, `status` exiting 1 with no recorded beats), neither of which reproduces in a working copy whose state is already recorded. Encodes the two details that are easy to get wrong: the copy must exclude `.git`/`.do-harness`/`.tmp`/caches (inheriting them hides the very bug being looked for), and it must supply `GITHUB_STEP_SUMMARY`/`GITHUB_OUTPUT`, which Actions always sets and a local shell never does (without them a step dies with `: No such file or directory`, which reads as a broken workflow). Requires an explicit `--workflow` — there is no "run everything" mode — and steps that could write outside the replay directory (`--live`, `git push`, Google auth, any `opencode` invocation) are **never executed**, at any flag setting: a false skip is loud and cheap, a false execution is neither. Every `*_API_KEY`/`*_TOKEN` variable in the parent environment is dropped before a step runs, so a replay cannot spend quota or touch a real calendar. |
| [`check_workflow_refs.py`](check_workflow_refs.py) | Every repo path a workflow names must exist. Catches the *class*, not one instance: a step reading a file that was never committed can only no-op, however it hides the exit code. `runtime-daily.yml` ran `capture_transcripts.py --from tests/fixtures/runtime_transcripts.json --check \|\| true` for weeks — a fixture that is deliberately absent — so the step was a silent no-op that read as a pass. Ignore rules are deliberately narrow (URLs, globs, `${{ }}`, `.tmp/`, write targets via `--out`/`--dest`/`>`, and paths guarded by an existence test in the same file) because a check that cries wolf gets deleted. |
| [`runtime_eval.py`](runtime_eval.py) | Runtime evaluator. Three modes: **structural pass** (`--root .`, walks every case and reports coverage); **runtime pass over canned stubs** (`--stubs stubs.json`), which asserts the stub *equals* `expected_output` **and** that every `assertions[]` needle holds — we author the stubs, so a drift there is a real regression; and **runtime pass over a real capture** (`--transcripts file.json`), which grades the `assertions[]` needles and *reports* whether the canonical text matched, because a live model cannot reproduce `expected_output` byte-for-byte. Equality would have made that path unable to pass anything. Both regimes require a `Decision=` line and at least one assertion, so neither can pass vacuously. Three further rules: a transcript for a case id that is not in `evals.json` is an error (**stale**, not extra evidence), a file that grades nothing fails even with `--allow-partial`, and a file covering only some cases fails unless `--allow-partial` names that concession at the call site (which then reports how many cases it left out). Operates on captured strings only — does NOT invoke any real skill. |
| [`youtube_live.py`](youtube_live.py) | Live-only, future-only YouTube gate. `classify_stream(candidate, now)` → `LIVE`/`SCHEDULED`/`REJECT`; `filter_candidates` promotes survivors; `build_live_search_url` / `build_api_live_search_url` emit the two search entry points. CLI: `--input candidates.json [--now ISO] [--json]`, `--print-urls QUERY`. Exits 1 when nothing is promotable. |
| [`link_check.py`](link_check.py) | Link revalidation. Classifies `OK`/`BROKEN`/`BLOCKED`/`ERROR`/`UNREACHABLE`/`INVALID`; `--dry-run` does structural checks only (no network); `--out` writes a JSON report with a `summary` and an ISO `checked_at`. Exits 1 when any link is not `OK`. Under `--json` the `--out` confirmation goes to **stderr**, so stdout stays the payload alone. |
| [`link_inventory.py`](link_inventory.py) | **The producer `link_check.py --input` never had.** `references/self-learning.md` lists link revalidation as one of five learning loops and its input is `links.json` — a file named in that table, in `SKILL.md` and in `link_check.py`'s own docstring, which **nothing produced**. The loop could not run even in principle, for a reason one layer further up: until `description_for` rendered the `FREE STREAM LINKS:` block, the runtime recorded a link *nowhere*, so there was nothing to inventory. Two sources, because the links live in two places: the stored event descriptions (the links that **rot** — a YouTube `/live` URL 404s the moment the broadcast ends) and `config/sources.json` (the links we *search*, and the half no check covered — of its 22 domains only 4 are reachable from the recorded page corpus). Each entry carries a `kind` (`calendar-event`/`approved-domain`/`approved-channel`/`validation-account`) because the right reaction differs: a dead event link is quarantined, a dead domain means the tier table is stale, and a `validation-account` answering 401/403 is the **expected** result rather than a finding. Makes no network request, so it is offline and deterministic and can gate a change; `link_check.py` is the probing half and deliberately gates nothing. `events_without_links` is reported rather than dropped, because "the calendar has no dead links" and "no event had a link to check" must not read the same. |
| [`stream_links.py`](stream_links.py) | **The link half of an event description, in one place.** `references/calendar-setup.md` specifies six parts of the description; only `League:` and `Teams:` were ever written, and `upsert_events._event_fields` carried no link at all — so `SKILL.md` Constraint 9, the revalidation loop in `references/self-learning.md` and `link_check.py`'s "every calendar event carries a `sourceReference`" all read a field **nothing wrote**. This module is the vocabulary the planner (which carries the link), the writer (which renders it) and the reader (which parses it back) share, so a rename cannot land on one side only. `normalise_links()` accepts the documented camelCase (`directLink`, `sourceReference`) and the runtime's snake_case, and returns only the keys present, so a row with no link renders byte-identically to before. `render_block()` emits the template's `Date/Time:` / `FREE STREAM LINKS:` / `Access:` / `SOURCE REFERENCE:` sections verbatim; `links_from_description()` reads them back, locating the URL by pattern rather than splitting on `:` — a bare bullet would otherwise record the source as `https`. |
| [`source_learning.py`](source_learning.py) | Self-learning. Modes: `record` (append a JSONL row to `logs/run-log.jsonl`), `score` (per-source attempts/creates/hit_rate), `candidates` (write unapproved domains as `quarantined` to `logs/source-candidates.json`). Never promotes a source. |
| [`calendar_config.py`](calendar_config.py) | Reads `config/calendar.json` with the `BASKETBALL_CALENDAR_ID` environment override. |
| [`color_mapping.py`](color_mapping.py) | `get_color_id(league, event_type, state=None)` → Google Calendar `colorId`. Two orthogonal dimensions: the league/event-type rules (FIBA Sage, finals Tomato) and the verification state. A non-`VERIFIED` state overrides the league colour, so an unconfirmed stream always looks unconfirmed. |
| [`verification.py`](verification.py) | **Phase 2 runtime.** The verification state machine: `VERIFIED`/`UNVERIFIED`/`WRONG`, their title prefixes and colour precedence. Single source of truth shared by `upsert_events.py` and `audit_events.py`, so the states cannot drift apart. Prefixing is idempotent. |
| [`evidence.py`](evidence.py) | **The free-access/live evidence contract, in one place.** `docs/runtime.md` documents `evidence.json` as `{event_id: {live_confirmed, free_confirmed, paid}}` and `audit_events.py` is its only consumer, drawing one hard line: a **missing key is `INCONCLUSIVE` and an explicit `false` is a `WRONG` verdict**. So the *presence* of a key is the claim, and two modules disagreeing about it would relabel live games as wrong. `coerce_evidence()` has **no defaults**: an absent key stays absent, only an unambiguous flag survives (a real bool, or `true`/`false`/`yes`/`no`/`1`/`0` — a model writes those), and anything else — `"unknown"`, `null`, a number — means *not recorded*, never `false`. A nested `evidence` mapping is read too and wins over a flat key of the same name, because it is the more specific statement. Used by `extract_candidates.py` (which used to drop the fields) and `event_ledger.py` (which records them). |
| [`upsert_events.py`](upsert_events.py) | **Phase 2 runtime.** Dedupe-then-write planner. `--existing existing.json --candidates candidates.json [--json]` prints `create`/`update`/`skip` rows and **never calls the calendar API**, which is what keeps the write policy testable without credentials. Dedupe matches on ±30 min **and** team pair **and** compatible league; short and long club names (`ALBA` = `ALBA Berlin`) are the same club, because one missed match is a duplicate event on a public calendar. `--verdicts audit.jsonl` hands it the audit ledger, and that is the **only way its `WRONG` policy row is reachable on the real path**: the stored state it reads comes from the calendar, which recovers `WRONG` from a `[WRONG] ` title prefix that `audit_events` computes and nothing applies. An event whose newest verdict is `WRONG` is skipped whatever its title says, so a game the audit proved was paid or never live can no longer be promoted back to `VERIFIED` by a later run (§17's hard requirement). The join is by `event_id` (a calendar event has no `game_key`), the dedupe reuses `audit_events.latest_per_event` so the two cannot disagree about which row is current, and a single-row ledger is read as *a row* rather than as a wrapper object — reading it otherwise yields **no** verdicts, which is the state in which the wrong event gets promoted. Plan rows also carry `league_color_id` and the `evidence` flags: the colour is needed because a later `VERIFIED` promotion must restore the *league* colour (the row's `color_id` is already the state colour), and the evidence travels with the row so `event_ledger.py` can record an event from the plan and the apply result alone, with no re-join against the candidate list — whose `game_key` an update row deliberately replaces with the existing event's. |
| [`event_ledger.py`](event_ledger.py) | **Phase 2 -> 3, the missing writer.** `telemetry/events.jsonl` and `evidence.json` are the audit's documented inputs and the audit job reads them — but *nothing wrote them*, so the audit found them absent, printed a notice, and did nothing on **every** run: no verdict was ever recorded, `precision` stayed `n/a` for the life of the project, and `self-improve` had no ledger to learn from. `record --plan p.json --applied a.json --dest DIR --run-id ID` writes both from the **applied result** (what the calendar holds) plus the plan row, rather than from the plan alone, which only says what was intended. Four properties: a **dry run records nothing** (nothing was written, so a verdict about it would be about nothing); a written event with **no `event_id` is refused**, not recorded with an empty key, because the audit resolves a verdict back to an event by id and such a row could never be acted on; **evidence is copied from explicit flags and never derived** (a default would turn "not recorded" into a `WRONG` verdict); and the ledger is **append-only**, with the audit taking the latest row per `event_id` — so history is kept and one event is still judged once. `evidence.json` is the exception and is replaced per touched event, so yesterday's `live_confirmed: true` cannot outlive today's observation. The run's own `--out` result is what feeds it, so `calendar_io apply`'s ids survive the exit-1 path of a partially failed apply. |
| [`audit_events.py`](audit_events.py) | **Phase 3 runtime.** Post-hoc verdicts and the promotion sweep. `--events events.json --evidence evidence.json [--now ISO] [--out audit.jsonl] [--dry-run]`. Three designed-in properties: absence of evidence yields `INCONCLUSIVE` (never `WRONG`), `WRONG` is terminal, and nothing is ever deleted. `--events` accepts JSONL as well as JSON. **The latest row per `event_id` is the one judged** — the ledger is append-only forever, so an event updated on five days has five rows, and judging all of them would append five verdicts for one event and inflate the precision denominator with copies of one finding. The count it collapsed is printed on stderr, so `--json` stdout stays payload-only. |
| [`bump_version.py`](bump_version.py) | Keeps the `SKILL.md` frontmatter version and the newest `CHANGELOG.md` heading in sync. |
| [`write_mode.py`](write_mode.py) | **The calendar write switch.** `--event NAME [--input DISPATCH_INPUT] [--enabled VAR]` prints the `GITHUB_OUTPUT` payload (`dry_run`, `reason`) for one run, fail-closed: `ENABLE_CALENDAR_WRITES` must be exactly `true`, and on a dispatch the `dry_run` input can only ever **add** dry-run — it can never grant a write the variable did not allow. It exists because the decision used to be the inline `DRY_RUN: ${{ inputs.dry_run || 'true' }}`, which is *always* `'true'` on a `schedule` event (`inputs` does not exist on a cron): with the variable true, the daily job planned every event, wrote none, and ran green — the one failure that is invisible without reading the log. An Actions expression cannot be unit-tested; a function can. stdout is the payload only, so `>> "$GITHUB_OUTPUT"` cannot be corrupted by a human line. |
| [`llm_model.py`](llm_model.py) | **Which model the agent runs.** Both workflows pinned it with `LLM_MODEL: ${{ vars.LLM_MODEL || 'opencode/big-pickle' }}`, and `opencode/big-pickle` is an **OpenCode Zen** id — rung 2. The ladder is credential-driven and `capture_transcripts.py --check-rungs` passes for a repo holding only `GEMINI_API_KEY` or only `OPENROUTER_API_KEY`, so those repositories asked the CLI for a model they had no credential for, produced no transcript, and read exactly like a quiet day. Which rung serves is a fact about the credentials that are set, so it cannot be a workflow literal — and a decision written as a `${{ }}` expression cannot be unit-tested, the same lesson `write_mode.py` records for `DRY_RUN`. `resolve_model(pin, environ)` is pure and follows the ladder's own order (`gemini` → `opencode` → `openrouter`, imported from `capture_transcripts` rather than restated), with `LLM_MODEL` winning over all of it; the steps then **read** `steps.llm.outputs.model`. Two boundaries are deliberate: it is a **selector, not a gate** (`--check-rungs` is the gate; refusing to run with no credential would break a `replay_ci.py` replay, which strips every `*_API_KEY` by design), and a runner authenticated only through `opencode auth login` is not detected, because a Zen id is meaningless without a Zen key and probing `auth.json` is not this module's job — that case is what the pin is for. stdout is the payload only, so `>> "$GITHUB_OUTPUT"` cannot be corrupted by a human line. |
| [`candidates.py`](candidates.py) | **Phase 0 runtime.** Candidate ledger + recall denominator. Modes: `append` (validate and append rows), `recall` (recall over *unique games*, not rows — the same game is surfaced by several backends), `retry` (games every render rung failed on and that were never captured — the cheapest recall win, since the URL is already known). |
| [`render_ladder.py`](render_ladder.py) | **Phase 0/1 runtime.** Ordered render ladder. Three designed-in properties: `blocked` (401/403/429/451) is distinct from a real error, so 403 means *climb* rather than *reject the game*; `skipped` (no key / package not installed) also climbs, because a rung that was never available is not the target refusing us — without that, a stdlib-only environment gave up on its first rung and no game could ever be evidence; and `assert_shippable()` guarantees no default rung is AGPL-3.0, so `nodriver` can only be opt-in. The keyless `urllib` rung means the ladder is **never empty**. `--expect-teams "A vs B"` adds the **game anchor**: `anchor_game()` matches the expected clubs against the document's *own name* (`<title>`, `og:title`, `twitter:title`, YouTube `videoDetails.title`), never the body, and reports a keyword-stuffed multi-game title as `ambiguous` rather than attributing one at random. `--body FILE` judges a saved page (a `.gz` file is decompressed) with no fetch at all — the only way to get a verdict for a host the ladder never renders (YouTube) and how the recorded corpus is judged. Modes: `--list`, `--plan`, `--probe`, `--body`. |
| [`team_tokens.py`](team_tokens.py) | **Shared club-name matching**, used by `upsert_events.py` (to avoid creating the same game twice) and `render_ladder.py` (to decide whether a page names the game). Folding is case-, punctuation- and diacritic-insensitive (`Kāhu` = `Kahu`, `München` = `Munchen` = `Muenchen`), matching is set equality or nesting (`ALBA` = `ALBA Berlin`), and a name made only of generic words (`Basketball`) carries no identity, so it never matches by nesting. Extracted to one module after the recorded live page proved the copy in `upsert_events` could not match `Northern Kāhu`. |
| [`record_pages.py`](record_pages.py) | **Evidence-gate corpus.** Records and re-checks the real pages that `render_ladder.py`'s stream-evidence gate is judged against (`tests/fixtures/pages/`, see its README). `--refresh` re-fetches and rewrites the corpus with a manifest carrying each response's byte length, SHA-256, verdict and *why the page is interesting*; `--check` is the offline gate (`validate.yml` step + `page-corpus` sensor) that the stored pages still classify as recorded. The rule this replaced matched bare words (`player`, `dash`, `live`, `läuft`) against the whole document, so it accepted a **recorded YouTube video**, the **YouTube home page**, the **MagentaSport announce home page** and the **Basketball Champions League home page** — recorded, not asserted: `test_the_old_vocabulary_fooled_by_real_pages` requires the old rule to keep failing on those fixtures. `--refresh` is a **report** (exit 0 when it completed) and prints a `FLIP:` line for any page whose *verdict* changed against the previous manifest (`--baseline`, default `--out`, read before anything is written) — never for a byte change, because the stored YouTube extracts hash differently whenever the live page moves and that would be weekly noise. `--json` carries the same under `pages`/`flips` for `.github/workflows/corpus-refresh.yml`. It exits 1 and writes **nothing at all** when a page does not yield the response the corpus is a statement about. Three things can do that, and all three **name the page in a `FAIL:` line** — the weekly workflow tolerates the failure with a warning telling the reader to look for exactly that line, so a bare traceback would point them at a message that does not exist: a connection failure; **an HTTP error status**, because `_fetch` returns an error's status and body like any other response and a 429 body classified as a page carries no evidence, so on an `expect=evidence` page it reads as a `lost-evidence` flip — a rate limit filing an issue about missed games — and an in-place refresh would write the error body over the recorded fixture; and a document that fetched but carries no extract (the YouTube consent wall). A page that cannot be reduced is **collected rather than fatal**, so the run still answers what it was asked — did a verdict flip? — for every page it *did* reach, and `--json` carries the partial comparison with `"ok": false` and an `unusable` list that `corpus_flip_issue.py --markdown` renders as partial. The WRITE is all-or-nothing. That ordering is the point: the previous version wrote each stored file as it went and the manifest last, so an abort in the middle of an in-place refresh rewrote the pages that **succeeded** and kept the old manifest — leaving the corpus failing its own `--check` with a hash mismatch that could not be re-recorded away on the very day re-recording was impossible. |
| [`rung_health.py`](rung_health.py) | **Rung health across runs — spec §10 `rungs.json` and §18.4.** `RungHealth` parks a rung after three consecutive failures, but it lives in memory, so the parking died with the process and "a rung parked for N consecutive days raises an issue (the GitHub Models lesson)" could never be observed: a retired backend left one `failed` line in one morning's log. `probe` climbs the ladder for **every** host in ONE invocation with ONE shared tracker — a tracker per host gives a dead backend one strike each and never parks it — and appends `rung-attempts.jsonl`; `snapshot` derives `rungs.json` from that ledger (rewritten each run, regenerable); `parked --days 3` reports the rungs parked for three consecutive **observed** days, and `--file` turns that into one deduped issue. Four semantics are load-bearing. Rows are built from named fields, so a **page body never reaches the ledger** (`render_ladder --probe --json` dumps whole documents). `skipped` (no key / no package) **breaks** a strike streak instead of extending it, or every run in a keyless repository would park the hosted rungs and installing a key later would start from a fake history. A calendar day with no rows **breaks** a streak rather than extending it, and the gap days are named, because a dead backend and a dead cron look identical in a table of parked days. And a streak ends at the rung's **latest observed** day, so a rung fixed today stops re-reporting last week's. Only `fileable` failures reach an issue: a hosted rung failing (a provider rejecting the key is the retired-backend case) or a local rung that cannot connect at all — a local rung *refused* by a target is the 403 the ladder is built to climb past, so it stays in `rungs.json` and is never filed. |
| [`gh_issue.py`](gh_issue.py) | **The one place that opens or comments on a dedupe-keyed GitHub issue**, shared by `corpus_flip_issue.py` and `rung_health.py` instead of copied. One issue per finding: the title marker is the dedupe key and is a **required, keyword-only** argument, so two callers cannot answer "is there already an issue?" with each other's key and silently merge unrelated findings. A repeat comments on the open issue rather than opening another; an unparseable `gh` list returns `None` rather than guessing, so a `gh` format change cannot open a duplicate every week; and a failing `gh` raises, so the caller can exit non-zero — a green workflow that reported nothing is the failure this exists to prevent. Library only, no CLI. |
| [`corpus_flip_issue.py`](corpus_flip_issue.py) | **Turns one verdict flip into one GitHub issue.** Reads the `--refresh --json` payload and files nothing when no verdict changed (the expected outcome on almost every weekly run), so a flip is the only thing that can open an issue. The title is the dedupe key and carries no date or page name, so a persistent flip comments on the open issue instead of piling one up per week — while an issue that *cannot* be filed exits 1 rather than dropping the finding. Reports both directions separately because they need different fixes: `lost-evidence` is missed streams, `gained-evidence` is the false positives the corpus exists to prevent. `--file` is the whole capability (byte-for-byte like `calendar_io`'s `--live`, and denied in `replay_ci.py` for it). |
| [`run_daily.py`](run_daily.py) | **Phase 0 runtime.** Search ladder (Exa → free keyless Exa MCP → TinyFish) → candidate ledger. Deliberately has **no calendar access at all**: its only job is to populate the recall denominator. `--check-backends` is the offline, presence-based **gate** on there being at least one configured backend (a run with none would be green and write nothing, which is indistinguishable from a quiet day) — and the free keyless Exa MCP rung (`exa-mcp-keyless`, no API key; steps aside when `EXA_API_KEY` is set) means the gate passes with zero secrets; `--json` leaves stdout as the payload **alone**, with every human line on stderr. The report keeps `backends` (that contributed a row) apart from `attempted` (that answered with nothing) — crediting the latter would overstate the coverage that produced a recall figure. Fails loudly rather than writing an empty ledger when no backend key is present. |
| [`rehearse.py`](rehearse.py) | **Pre-release rehearsal.** One command for every surface that leaves this machine — `calendar`, `github-issues`, the three model rungs, both search backends, and an optional YouTube Data API — each reported `missing` / `valid` / `invalid` / `unverified`, because a credential that is **set** is not a credential that **works** and only one of those four states should stop a release. `--check-rungs` and `--check-backends` are presence-based **by design** ("a preflight that reds a correctly configured runner gets deleted"), so a dead key reads as configured everywhere except here — this command exists because a present `OPENROUTER_API_KEY` answering `401 User not found` went unnoticed for a month. Probes are **imported, not restated** (`capture_transcripts.probe_opencode_cli` / `probe_openrouter_key` / `list_gemini_models`, and the backends' own `last_error`), so the rehearsal cannot green-light a rung the ladder does not read. **It never writes**: the calendar probe is a read of one day and the search probes ask for a single result. `--require-all` additionally fails on a `missing` required credential. Two surfaces are structural rather than credential-based and say so: `github-issues:grant` reads whether the workflows that file an issue declare `issues: write` (static, because a **read-only** `GITHUB_TOKEN` authenticates perfectly, so the token probe can only prove identity), and `render:firecrawl` reports presence with `unverified` because the ladder's climb-on-`blocked` semantics make a status uninterpretable as a credential verdict. Only `assess(checks, environ, prober)` — the classification — is tested, because the live half is deliberately **not** a sensor: `do-harness` sensors are offline and deterministic, and a free provider having a bad day must not red the build. Three flags exist for the operator rather than the report. `--env-file PATH` loads a dotenv file — [`.env.example`](../.env.example) is the tracked template and `.env` is gitignored — **explicitly and never discovered**, so a stray `.env` cannot change what a report says about the environment it was given, and an already-set environment variable wins over the file. It mutates `os.environ` rather than a private dict because the probes are the *runtime's own* code and read the environment directly; a malformed line is refused by name and line number, because a skipped `GEMINI_API_KEY gemini-…` reads as `missing` and sends the reader to Google instead of to their own typo. `--offline` probes **only** the credential-free checks, which is what makes the issue-grant half usable as a CI gate on a runner holding no secrets (`validate.yml`). `--markdown` prints the same table to stdout for the release PR, and `--json --markdown` is a usage error rather than an interleave: two payloads cannot share stdout. |
| [`capture_transcripts.py`](capture_transcripts.py) | Captures **real** runtime transcripts for grading. Runners: `gemini` (rung 1 — the free AI Studio product, direct HTTPS, no CLI), `opencode` (production CLI), `openrouter` (rung 3), `ladder` (failover gemini → opencode → openrouter, recording which rung served each case), `command` (any CLI), `replay` (offline, no credentials). `--list-models` **reports** the ladder, which credentials are set, whether the `opencode` CLI actually starts (`--version`, zero-token — added after a rung reported `OK` while the binary was a shim that could not run), whether the OpenRouter key is *accepted*, and the live Gemini model list (Lite tier first) — free tiers are withdrawn without notice, so the id is discovered at run time rather than pinned. `--check-rungs` **gates** on there being at least one configured rung: presence-based and offline, so it cannot red a run for a network reason. The capture prompt carries **no answer** (`expected_output` used to be appended, which is the answer for a short case and would let a model score 100% by echoing it). Refuses to write a file at all when any case produced nothing: the downstream gate keys on the file *existing*, so a partial write would present an incomplete capture as a complete one. The payload records provenance twice: `rungs` (which rung served each case) and `models` (which *model* did), the latter recorded from the response's own `model` field — `openrouter/free` is a router, so the id in the request is not a model, which is exactly why the answer has to be read back rather than assumed. `opencode` is deliberately absent from `models`: the CLI may fail over inside itself, so the id passed on the command line is what was *asked for*, and a wrong attribution is worse than none. `--runner ladder` also no longer hands the CLI rung's `--model` to OpenRouter (`openrouter_model_for`), which turned a working rung into `no choices in response`. |
| [`extract_candidates.py`](extract_candidates.py) | **Phase 1 runtime.** The transcript → planner seam. The runtime agent is denied `edit` and `bash`, so the transcript is the only channel it has; the fenced-```json``` contract is therefore enforced in code rather than assumed. Validates exactly two teams, a parseable start, and a *proposable* state — `WRONG` is an audit verdict and is refused, because a run that could assert it would overwrite the label a human trusts. It also **carries the evidence flags through** (`evidence.py`): the field whitelist used to drop them, so the audit could never get past "no evidence recorded" no matter what the agent observed. |
| [`calendar_io.py`](calendar_io.py) | **Phase 1 runtime.** The only module that reads or writes the calendar. The transport is **Composio tool execution**, not the Calendar API: `POST /api/v3.1/tools/execute/{slug}` with an `x-api-key` header (`COMPOSIO_API_KEY`) and `{user_id, arguments, version}` in the body. That removes the Google Cloud project, the service account and the WIF auth step entirely — the trade is that the credential reaching a public calendar lives with a third party. Five behaviours of the toolkit were **verified against a live account** rather than assumed, and all five are silent in a passing run: `CREATE_EVENT` ignores `color_id` (so the colour is a follow-up `PATCH_EVENT`, and a failed colour patch fails the run while still recording the event id); it adds a Google Meet link and the connected user as an attendee unless `create_meeting_room: false` / `exclude_organizer: true` are sent, which they always are; it nests the created event under `data.response_data` while `EVENTS_LIST` puts the Google body straight under `data`  (`_payload` handles both, and falls back to the envelope rather than to `{}`, because an empty payload is exactly what "no events today" looks like); `data` is a dict on v3.1 although it is documented as a string; and **`visibility` is a real argument of both event tools** (`default`/`public`/`private`/`confidential`), which is now sent from `--visibility`, whose default is `calendar_config.get_visibility()` rather than a literal. That last one closed a reader with no writer: `config/calendar.json` declared the setting, `SETUP.md` documented it, `get_visibility()` existed, and **nothing called it**, so every event took the calendar's own default. It travels on `PATCH` too — a partial update that omitted it would clear what a previous run set. `COMPOSIO_TOOLKIT_VERSION` is `latest`, not a pin, because Composio records that older pinned toolkit versions can drop or remap `timeMin`/`timeMax` — and a dropped window filter returns the wrong window silently. `list` returns events normalised into the planner's shape, recovering `VERIFIED`/`UNVERIFIED`/`WRONG` from the title prefix; `apply` performs **no HTTP at all** unless `--live` is passed, so a bare invocation cannot write to a public calendar. Two things it now records that it used to drop. It returns the **`event_id` the API assigned** on a create (and on an update), because Phase 3 resolves every verdict back to an event by id and a discarded id left the ledger unkeyable. And it writes the documented **`League:`/`Teams:` description** from the plan row: `list_events` reads that `Teams:` line back out and `events_match` matches on it, but nothing ever wrote one — every event carried `description: ""`, and since the teams check is skipped whenever either side is empty, a stored event matched **any** candidate inside the 30-minute window, so a different pairing at the same time was planned as an *update* of it rather than a new event. `--out` writes the applied result (what the calendar holds, and under which ids) *before* the exit-1 path, so a partially failed apply is still recordable. |
| [`fixtures.py`](fixtures.py) | **Phase 5 runtime.** League fixtures → the same `game_key` shape as the ledger, so the two can be joined. JSON-LD first (a standard with a stdlib parser), a microdata fallback second, and **no match means no fixture** — a half-parsed event would be counted as a missed game that does not exist. `--ledger/--fixtures` reports the games no backend surfaced. `--out` is **idempotent**: a `game_key` already in the file is skipped, because the daily job re-observes the same window and a blind append would write every game once per day forever. |
| [`metrics.py`](metrics.py) | **Telemetry.** Derives `metrics.json` from the append-only JSONL streams (`candidates.jsonl`, `fixtures.jsonl`, `audit.jsonl`). Reports three failures that are deliberately not collapsed: **recall** (of the games a backend saw, how many were captured), **fixture recall** (of the games that actually happened, how many any backend even *saw* — the only metric that can detect a systematically invisible source), and **precision** over the post-hoc audit. Missing inputs report `None`, never `0.0`: "we did not measure" and "we measured zero" are different claims. `INCONCLUSIVE` is excluded from the precision denominator, so a quiet day cannot look accurate. `--trend` groups the same streams by `run_id` for a per-run table + sparklines; the sparkline scale is absolute 0..1 rather than min/max, so a tiny real move cannot be drawn as a dramatic climb. |
| [`synthesise_eval_case.py`](synthesise_eval_case.py) | **Phase 4 runtime.** Turns an audit misjudgement into a regression eval case. Encodes the *bug*, not the fix, so it fails until the gate is tightened; refuses to write a case whose assertion has no needle (such a case grades nothing); and is idempotent, because the dedupe key excludes the audit timestamp. `--verify` checks every case in the file is gradeable. `--json` emits the payload **alone** on stdout (the human summary moves to stderr), because `self-improve.yml` parses it to learn which ids to capture. |

### Tests

Pytest suite lives under [`../tests/`](../tests/):

- `../tests/test_validate.py` — covers `validate.py` (per-check PASS + FAIL
  branches via tmp fixtures + CLI / USAGE contracts).
- `../tests/test_runtime_eval.py` — covers `runtime_eval.py` (structural
  + runtime + `.strip()` normalization + non-string stub rejection +
  missing-stub detection + malformed JSON + non-coercible int keys).
- `../tests/test_youtube_live.py` — covers the live-only/future-only gate
  (URL shapes, VOD/ended-broadcast rejection, datetime-greater-than-now) + CLI.
- `../tests/test_link_check.py` — covers link classification (`BROKEN` vs
  `BLOCKED` vs `ERROR` vs `UNREACHABLE`) via monkeypatched HTTP errors +
  `--dry-run` CLI contracts.
- `../tests/test_source_learning.py` — covers scoring, candidate discovery,
  quarantine writes, and the three CLI modes against tmp workspaces.
- `../tests/test_color_mapping.py`, `../tests/test_bump_version.py` — unit
  coverage for the colour table and the version helpers.
- `../tests/test_candidates.py` — recall arithmetic over unique games, row
  validation, retry selection, ledger IO, CLI contracts.
- `../tests/test_replay_ci.py` — the fresh-checkout replay harness: that the
  copy really excludes machine-local state, that credentials are stripped, that
  dangerous steps are skipped rather than run, that `GITHUB_STEP_SUMMARY` is
  supplied, and that `--json` stays parseable.
- `../tests/test_run_daily.py` — the Phase 0 runtime: plumbing filtering,
  de-duplication, contributed-vs-attempted backends, the `--check-backends`
  gate, and the `--json` stdout contract (payload alone). Also pins the
  `runtime-daily.yml` ordering — the gate must precede the ladder it gates, and
  the ladder must not be piped, since a pipe would mask the exit status.
- `../tests/test_render_ladder.py` — licence guards (including the "default
  ladder contains zero AGPL rungs" test), host ordering, blocked-climbs vs
  error-stops, JS-host skipping and strike-halting — all with fake rungs, so no
  network or browser is needed.
- `../tests/test_recorded_pages.py` — the stream-evidence gate against the
  **real** web (`tests/fixtures/pages/`): every stored page classifies as the
  manifest says, the historical vocabulary provably fails on the recorded
  negatives, the real live pages keep their evidence, the corpus stays small
  enough to commit, and `--check`'s failure modes (a page that changes, a
  manifest that contradicts itself, a missing file) each red. The same corpus
  pins the game anchor: the real single-game title is attributed, a different
  game on the same page is not, the real competition name is rejected, and the
  real keyword-stuffed title is reported **ambiguous** rather than matched.
- `../tests/test_team_tokens.py` — the shared club-name rules: diacritic and
  German-digraph folding (`Kāhu`/`Kahu`, `München`/`Muenchen`), nesting, the
  city-name looseness stated as a decision rather than a surprise, `vs` never
  splitting into `v` + `s`, and `A vs. B` / `A v B` / `A gegen B` all splitting.
- `../tests/test_transcripts.py` — transcript capture shapes plus grading real
  transcripts through `runtime_eval.py --transcripts`.
- `../tests/test_extract_candidates.py` — the transcript/envelope contract,
  including that `WRONG` is not a proposable state, and that the evidence flags
  survive the whitelist (they used to be dropped, which is why Phase 3 could
  never get past "no evidence recorded").
- `../tests/test_evidence.py` — the evidence contract, and specifically the
  **absence** of a default: a decisive yes/no survives, and `"unknown"`,
  `null`, numbers and objects mean *not recorded* rather than `false`, because
  `false` is a `WRONG` verdict on a live game.
- `../tests/test_event_ledger.py` — the writer, and the **round trip**: the last
  class feeds what the ledger produced into `audit_events.sweep` and asserts the
  verdicts, so producer and consumer are tested against each other rather than
  against their own documentation. Also pins the refusals — a dry run records
  nothing, an event with no id is refused instead of keyed to nothing, and
  evidence is copied rather than derived.
- `../tests/test_run_ledger_wiring.py` — the `runtime` → `audit` handoff in
  `runtime-daily.yml`: the artifact name matches on both sides, it lands where
  the audit looks, an absent artifact is tolerated, the download precedes the
  inputs check, the audit is *ordered* after the run but still runs with
  `always()`, and the runtime job gains no write permission.
- `../tests/test_calendar_io.py` — event normalisation, pagination, and the
  "a dry run makes no HTTP request" property (the transport is replaced with one
  that raises, so a leak is a failure rather than a comment).
- `../tests/test_fixtures.py` — JSON-LD and microdata parsing, the window filter,
  and the "no match means no fixture" rule.
- `../tests/test_synthesise_eval_case.py` — the three misjudgement classes,
  needle integrity, and idempotency across a changing audit timestamp.
- `../tests/test_verification.py` — the state model: prefix idempotency,
  state-switch replacement, and the "uncertainty wins over the league colour"
  precedence rule.
- `../tests/test_upsert_events.py` — dedupe matching (window, team pair, league,
  short-vs-long club names), the replace-if-unverified table, the Phase 2
  acceptance test that a re-run plans zero creates, and the audit-verdict input:
  a `WRONG` verdict holds an event whatever the candidate claims, only `WRONG`
  does, and a ledger for another event is not applied.
- `../tests/test_audit_events.py` — verdicts (`VERIFIED`/`WRONG`/`INCONCLUSIVE`),
  the terminal-`WRONG` rule, idempotent sweeps, JSONL loading, and the
  `audit.jsonl` telemetry writer.
- `../tests/test_metrics.py` — the metrics contract: `None` (never `0.0`) for a
  metric that was never measured, `INCONCLUSIVE` excluded from the precision
  denominator, and `metrics.json` being rewritable while the JSONL streams are
  not.
- `../tests/test_capture_gemini.py` — the ladder's rung 1: the shared transport's
  error contract (a non-JSON body is never a body), distinct reasons for a
  missing key / 429 / safety block / empty candidate, Lite-tier-first model
  ranking, and that failover moves to the next rung while a total failure names
  every rung that was tried.
- `../tests/test_check_workflow_refs.py` — the workflow-reference gate, including
  the false-positive traps (URLs, comments, trailing punctuation, globs, write
  targets) and the pre-fix step text that must be rejected.
- `../tests/test_self_improve_loop.py` — the Phase 4 loop end to end and offline:
  audit verdict → synthesised case → capture (via `replay`) → graded, plus the id
  arithmetic that makes a per-id capture necessary and the red direction
  (one missing needle must fail).
- `../tests/test_harness_boundary.py` — the `do-harness` adoption boundary:
  every declared sensor has an `argv`, signal sets and hooks reference only
  declared sensors, invariants are a bare array naming only real sensors, and
  the four product graders are still wired into `verify.yml` (`do-harness eval`
  does **not** grade the root skill — see `../docs/do-harness.md`).

Run from project root:

```bash
pip install -r requirements-dev.txt   # one-time
pytest tests/                         # ~15s
```

## Exit codes (canonical)

All scripts in this directory share the same exit-code convention:

| Code | Meaning |
|---|---|
| `0`  | PASS — every requested check succeeded. |
| `1`  | FAIL — one or more diagnostics printed to stderr. |
| `2`  | USAGE — bad arguments, missing required input file, or `--root` does not point to a directory. |

Per agentskills.io `using-scripts.md`:

- `OK: …` lines go to stdout when each check passes.
- `FAIL: …` lines go to stderr on failure.
- Scripts are idempotent — re-running has no side effects.

## Adding a new check to `validate.py`

1. Add a new function `def check_<name>(root: Path) -> None` that exits 0 on
   success and prints `FAIL: …` lines + calls `sys.exit(1)` on failure.
   Mirror the codestyle of `check_evals` / `check_skill` /
   `check_references`.
2. Register the new check in the `CHECKS` dict at module level.
3. Add `"<name>"` to `argparse`'s `--check` choices tuple.
4. Add a fixture builder to `smoke_test()` so the FAIL branch is exercised
   by a dedicated subprocess invocation. The smoke-test is the canonical
   regression guard for "did the FAIL branch silently go vacuous".
5. Add pytest coverage: at minimum one PASS test (against the current
   repo) + one FAIL test (against a tmp fixture).
6. Run `pytest tests/` and `python3 scripts/validate.py --check all`
   locally before committing.

## Adding a new eval case

`evals/evals.json` is a structured schema — edit it JSON-aware, never by
eye. Use a Python heredoc like the following so the JSON shape is preserved
on re-run:

```bash
python3 << 'PYEOF'
import json
from pathlib import Path

p = Path("evals/evals.json")
d = json.loads(p.read_text(encoding="utf-8"))

# example: append a new case
d["evals"].append({
    "id": len(d["evals"]) + 1,
    "prompt": "...",
    "expected_output": "Decision=...; checks: foo=PASS, bar=PASS",
    "assertions": ["foo expected PASS", "bar expected PASS"],
    "files": ["references/validation-workflow.md"],
})

p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PYEOF
```

**Consistency rule**: every assertion string `"<name> expected
PASS|FAIL"` must yield a needle `"<name>=PASS|FAIL"` that appears in the
case's `expected_output` (typically inside the `"checks: ..."` block).
The `runtime_eval.py` runner uses this needle for its check.

After adding a case:

- `python3 scripts/validate.py --check evals` should still PASS.
- `python3 scripts/runtime_eval.py --root .` should emit one new
  `OK: structural: Case N: …` line.
- `python3 scripts/runtime_eval.py --root . --stubs <stubs>` should
  PASS if your stub JSON contains a key for the new id with the case's
  `expected_output` as its value.

## Updating `runtime_eval.py`

The script has two modes:

- `structural_pass(cases)` — no stubs; reports per-case shape and exits 0
  if every case has well-formed `id` / `expected_output` / `assertions`
  fields.
- `runtime_pass(cases, stubs)` — with stubs; asserts equality between
  stub and `expected_output` (after `.strip()`) AND per-assertion needle
  search.

`evaluate_assertion(assertion, stub_output)` translates `"<name>
expected PASS|FAIL"` into a needle search of `"<name>=PASS|FAIL"`.

If you change the assertion grammar (e.g., add new keywords beyond
`PASS|FAIL`), update:

- the regex in `evaluate_assertion`,
- the `expected_output` strings in `evals/evals.json` (must contain the
  new needles),
- pytest cases in `../tests/test_runtime_eval.py` that exercise the
  grammar.

## CI integration

`.github/workflows/validate.yml` runs the following on every push/PR:

| Step | Command | Purpose |
|---|---|---|
| Run validator | `python3 scripts/validate.py --root . --check all` | Schema + reference contract |
| Run validator (smoke-test regression guard) | `python3 scripts/validate.py --check smoke-test` | Validates `validate.py`'s FAIL-branch logic on tmp fixtures |
| Run runtime_eval (structural pass) | `python3 scripts/runtime_eval.py --root .` | Per-case structural coverage |
| Run verification | `python3 scripts/verification.py` | State model + colour precedence |
| Run upsert_events (fixture plan) | `python3 scripts/upsert_events.py --existing tests/fixtures/upsert_existing.json --candidates tests/fixtures/upsert_candidates.json --json` | Dedupe planner: one create, one skip, no calendar access |
| Run audit_events (fixture audit) | `python3 scripts/audit_events.py --events tests/fixtures/audit_events.json --evidence tests/fixtures/audit_evidence.json --now 2026-09-15T08:30:00Z` | Verdict contract, no network |
| Run candidates (fixture ledger) | `python3 scripts/candidates.py recall --ledger tests/fixtures/candidates_ledger.jsonl` | Recall denominator contract |
| Run candidates unseen (fixture join) | `python3 scripts/candidates.py unseen --ledger … --fixtures tests/fixtures/league_fixtures.jsonl` | Phase 5 fixture recall is non-zero |
| Run metrics (fixture inputs) | `python3 scripts/metrics.py --candidates … --fixtures … --audit tests/fixtures/synthesise_verdicts.jsonl --run-id ci --dry-run` | The derived snapshot's shape + `n/a` semantics, offline |
| Run render_ladder (rung list) | `python3 scripts/render_ladder.py --list` | Rung registry + licence compliance |
| Run link_check (offline dry-run) | `python3 scripts/link_check.py --url https://www.youtube.com/@fiba/live --dry-run` | Link-gate contract, no network |
| Run link_inventory (fixture events) | `python3 scripts/link_inventory.py --events tests/fixtures/link_inventory_events.json --root . --now 2026-09-17T09:00:00Z --json` | The `links.json` producer: stored event links + all 22 approved domains, no network |
| Run youtube_live (URL builders) | `python3 scripts/youtube_live.py --print-urls "EuroLeague live"` | Live-filter URL construction |
| Run source_learning (dry-run) | `python3 scripts/source_learning.py candidates --root . --dry-run` | Source-registry discovery, no writes |
| Run synthesise_eval_case (--verify) | `python3 scripts/synthesise_eval_case.py --verify` | Every eval assertion needle resolves |
| Run fixtures (both page shapes) | `python3 scripts/fixtures.py --input tests/fixtures/fixtures_page_bbl.html --source bbl --now 2026-09-14T08:30:00Z` | JSON-LD **and** microdata parsing stay working |
| Run extract_candidates (envelope sample) | `python3 scripts/extract_candidates.py --transcript tests/fixtures/agent_transcript_sample.json --out .tmp/candidates.json` | The transcript/planner seam, no model needed |
| Run the write path (plan + dry-run apply) | `upsert_events --json > plan.json` then `calendar_io apply --plan plan.json` | The Phase 1 chain end to end, with **no credentials at all** |
| Run check_workflow_refs | `python3 scripts/check_workflow_refs.py --root .` | No workflow names a file that does not exist |
| Run record_pages (corpus gate) | `python3 scripts/record_pages.py --check` | The evidence gate still classifies real recorded pages correctly |
| Run pytest | `python3 -m pytest tests/ -v --tb=short` | Unit + CLI coverage, including the fixture contracts |

Three further workflows exist and are deliberately kept out of this one:

- **`.github/workflows/runtime-daily.yml`** — the scheduled runtime (cron `30 8 * * *`).
  Calendar writes are gated behind `ENABLE_CALENDAR_WRITES`, and the audit job behind
  `ENABLE_AUDIT`. It is separate so a runtime regression can never red the schema contract.
  It is also the only workflow that runs `capture_transcripts.py --check-rungs`, because it
  is the only one where a credential is expected to exist: in `validate.yml` that step would
  correctly **fail**, since no LLM is configured there. Do not "fix" that by adding it to
  the CI table above.

  Its `rung-health` job is the only producer of the rung ledger (`scripts/rung_health.py`),
  and it is deliberately **not** `needs: telemetry`: the search preflight reds when no search
  backend is configured, and a dead *render* backend is a different fault that must still be
  recorded that day. Producing and filing are split across two jobs the same way
  `self-improve.yml` holds no calendar credential — `rung-health` commits the ledger and
  holds no issue permission; `rung-issues` holds `issues: write`, holds no search or render
  key, and never writes the branch. All three jobs that push `telemetry` share one job-level
  concurrency group (`telemetry-branch`, `queue: max`), so writers wait instead of racing to a
  non-fast-forward rejection. `queue: max` is required rather than decoration: the default
  `queue: single` is documented to **cancel and replace** a pending job in the group, so a third
  writer eligible at the same moment would silently cancel one instead of waiting — and a
  cancelled ledger job looks exactly like a quiet day.
- **  `.github/workflows/self-improve.yml`** — Phase 4. Turns an audit misjudgement into a
  regression eval case on a branch, captures transcripts for **exactly the new ids** (the
  committed capture covers ids 1..N, and a synthesised case is N+1, so reusing it would fail
  the gate for every new case), grades those with `--allow-partial`, then gates on the graders
  above. It holds **no
  calendar credential** (`id-token: write` is absent), which is what makes it safe to give
  it the authority to edit the instructions: writing the calendar and rewriting the
  instructions are different privileges, held by different workflows.
- **`.github/workflows/verify.yml`** — the `do-harness` gate (pinned `v0.1.0`, checksum-verified
  installer). Two jobs: `do-harness verify --set verification --strict --evidence`, and **the
  same four product graders again**. The duplication is deliberate — `do-harness eval` resolves
  skills only under `.agents/skills` and never sees the root `SKILL.md`, so it cannot stand in
  for `validate.py` / `runtime_eval.py` / `synthesise_eval_case.py` / `pytest`. See
  [`../docs/do-harness.md`](../docs/do-harness.md) → Step 6 (withdrawn).

**Every runtime path has an offline mode** (`--dry-run`, `--list`, `--plan`, `replay`), which
is what keeps this suite runnable in CI without secrets. Do not add a script that can only be
tested with credentials.

All commands run on Python `['3.9', '3.12']` via
`strategy.matrix.python-version` (`fail-fast: false`).

If you add a step, **append** it to the list (do NOT replace existing steps) and
keep the matrix intact. A new runtime step must have an offline mode — see the
table above — because the scheduled workflow
(`.github/workflows/runtime-daily.yml`) is the only place credentials exist, and
its failures must not be able to red the schema contract.

## Releasing

**Workflow**: feature branch → PR → rebase-merge → annotated tag → tag push.

### The rehearsal comes first

```bash
cp .env.example .env                          # optional: every key in one gitignored file
python3 scripts/rehearse.py --env-file .env --require-all
python3 scripts/rehearse.py --env-file .env --markdown   # paste into the release PR
```

`do-harness verify --set release --strict` proves the *change*; it cannot prove the
*credentials*, because sensors here are offline and deterministic on purpose. So the
release flow has a step before the tag that the harness cannot take: this one. It
exits `0` only when no credential was **rejected**, and with `--require-all` only when
none required is **missing** either. A `missing` row on a bare checkout is not a
defect — it means the rehearsal was incomplete, which is a different statement from
`invalid` and the reason the two are reported apart.

Put the `--markdown` table in the PR. A rehearsal that ran and a rehearsal that is
*claimed* to have run look identical in a release note otherwise, and the renderer is
shared so the pasted table cannot disagree with the printed one. Its offline half
(`--list` + `--offline`) runs in CI on both matrix versions; the live half cannot,
and that asymmetry is the whole reason this step is a human one.

### Pre-v1.1.0 (legacy)

Ruleset 18142708 had a `code_scanning` rule that required CodeQL
results. Since CodeQL was not configured, direct `git push origin main`
was rejected with `GH013: Code Scanning not configured for main`. The
workaround was to open a PR and merge with admin bypass:

```bash
gh pr merge <PR-number> --rebase --admin
```

The `--admin` flag invokes `bypass_actors[pull_request]` for the
maintainer, overriding the `code_scanning` + `code_quality` rules.

### Post-v1.1.0

The `code_scanning` rule was removed from 18142708 (default-branch
commit: `21e7bcc`). CodeQL default-setup was `not-configured`; the rule
was checking against absent results. With that rule gone, direct
`git push origin main` is the expected path for maintainers — this
commit is the first direct push since the ruleset change, so its
success verifies the fix end-to-end.

Caveat: `code_quality` (severity: errors) remains in 18142708 and
**has not been tested**. If a code-quality tool is added later and trips
on lint errors, direct push may be rejected; fall back to the PR
rebase-merge path in that case.

### Concrete release commands

```bash
# 1. Land feature work on a feat branch
git checkout -b feat/release-prep
# (edit, commit, push the branch)
git push origin feat/release-prep

# 2. Open the PR
gh pr create --base main --head feat/release-prep \
  --title "feat: ..." --body "..."

# 3. After CI is green, rebase-merge with admin bypass
#    `--rebase` preserves commits + satisfies `required_linear_history`
gh pr merge <PR-number> --rebase --admin

# 4. Tag + push tag (ruleset targets branch refs only, not tag refs)
git fetch origin
git checkout main
git tag -a v<X.Y.Z> -m "Release v<X.Y.Z>: <summary>"
git push origin v<X.Y.Z>

# 5. (optional) GitHub Release with notes from CHANGELOG.md
gh release create v<X.Y.Z> --notes-file CHANGELOG.md
```

`--squash` works too (`gh pr merge <PR-number> --squash --admin`); it
also satisfies `required_linear_history`. Only the legacy `--merge`
(merge commit) is blocked by the ruleset.

The `delete_branch_on_merge` repo setting is `true`, so the feat branch
is auto-cleaned after a successful merge.

## See also

- [`../SKILL.md`](../SKILL.md) — frontmatter + 7-step pipeline.
- [`../README.md`](../README.md) — overview + Mermaid dataflow +
  self-validation checklist.
- [`../CHANGELOG.md`](../CHANGELOG.md) — release history.
