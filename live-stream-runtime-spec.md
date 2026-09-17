# Spec — Basketball Live-Stream Runtime, Evals & Self-Improvement

**Short name:** `live-stream-runtime`
**Status:** the plan of record. Every decision below is captured from the interview
rounds; items marked **[VERIFY]** are unconfirmed facts that must be checked at
implementation time.

> **Implementation progress (2026-09-14).** Phases **0–5** are implemented and covered by
> offline tests. See §16 for the per-phase status. The spec is kept as-written rather than
> rewritten to match the code, because the decisions it records — and the research that
> produced them — are the reason the code looks the way it does. Where implementation
> diverged, it is recorded in §6 (four states ship, not five) and §16.
>
> **What is still unproven is inputs, not code.** No real transcript has ever been captured
> (`tests/fixtures/runtime_transcripts.json` is absent by design — see
> `tests/fixtures/README.md`), so §9.1's real-transcript grading and §9.3's "the new case must
> fail before its own fix" are enforced only where a transcript exists. `self-improve.yml`
> warns loudly when it does not, rather than reporting green. The ladder's rung 1 is now
> implemented (`capture_transcripts.py --runner gemini`, see §16) but has never been executed
> here either, for the same reason: no free key. What is tested is the request contract, the
> error taxonomy and the failover — not that a free provider answered.
**Repo:** `skill-basketball-streams` (agentskills.io skill, MIT, public)
**Author of record:** interview with repo owner, 2026-09-14
**Baseline:** v1.2.0 in the working tree (uncommitted) — 32 eval cases, 178 tests,
`SKILL.md` Steps 1–7 + 2.3–2.9.

---

## 1. Problem statement

The skill can *describe* how to find free basketball live streams, but nothing
**runs** it. Concretely, today:

- `scripts/runtime_eval.py` is a **canned-stub scaffold**. It compares a hand-written
  string against `expected_output`; it has never invoked a real model, never opened a
  URL, never touched the calendar. Its own docstring says so.
- There is no scheduler. Nothing runs daily; a missed matchday is only noticed by a human.
- There is no measurement of **false positives** (an event created for a stream that was
  paid, never live, or never existed) or **false negatives** (a real free stream that was
  never found). Nothing records what was *dropped*, so recall is unmeasurable.
- There is no feedback path from a real-world mistake back into `evals/evals.json`.
- Bot-blocked sources (`magenta.tv`) degrade silently: `openUrl` returns 200 on the SPA
  shell, verified 2026-09-14, and the pipeline has no way to tell that from a working stream.

**Goal:** a daily, unattended, free-of-cost runtime that maximises **recall now** while
keeping every event honestly labelled, and that converts its own mistakes into eval cases
that gate the next change to `main`.

---

## 2. Goals and non-goals

### Goals

| # | Goal |
|---|---|
| G1 | Run the full skill daily in GitHub Actions with no human in the loop |
| G2 | Never create a duplicate event; always check the calendar first |
| G3 | Never present an unconfirmed stream as confirmed — label it instead of dropping it |
| G4 | Measure recall against a persisted baseline of every candidate any backend surfaced |
| G5 | Measure precision through a post-hoc audit of created events |
| G6 | Turn every misjudgement into an eval case automatically |
| G7 | Improve tier order, gates and thresholds from observed outcomes |
| G8 | Keep `main` protected: improvements land on a branch and merge only on an all-green eval run |
| G9 | Cost $0 in API spend |

### Non-goals

- Paid broadcasters, pirate sites, highlight reels, non-basketball sports (unchanged contract).
- A web UI or dashboard. The calendar, the telemetry branch and Actions summaries are the UI.
- Real-time (<1 h) detection. The cadence is **once daily**.
- Replacing the 7-check pipeline. It stays the source of truth; the runtime only drives it.

---

## 3. Decisions captured from the interview

| Topic | Decision | Round |
|---|---|---|
| Autonomy | Write events **directly**; **check the calendar for existing events first**; **mark** anything not 100 % validated as free | 1 |
| Failure cost | **Prefer recall early, precision later** | 1 |
| Self-improvement scope | **All five**: auto-grow evals, auto-tune tier order, auto-tune gates/thresholds, distill skills from traces, rewrite own instructions | 1 |
| Unverified marking | **Title prefix + colour** (not description-only, not a separate calendar) | 2 |
| LLM path | **Gemini free (AI Studio) primary → OpenCode Zen free → OpenRouter free**, automatic failover | 3 |
| Guardrail before live | **Self-improvement branch**; `main` advances only when the eval run is green | 3 |
| Cadence | **Once daily** | 3 |
| History | **Append-only, forever** | 3 |
| Telemetry home | **Dedicated telemetry branch** (orphan), not `main` | 6 |
| Alerting | **GitHub issue + Actions job summary** | 6 |
| Executor | **`opencode` CLI headless**, loading `SKILL.md` | 4 |
| Recall baseline | **Search-derived candidates** — every candidate any backend surfaced is the baseline | 4 |
| Wrong-event marking | **Peacock `7`** colour | 5 |
| Upsert semantics | **Replace if unverified** (never touch verified events) | 5 |
| Merge bar | **All evals green** | 5 |
| magenta.tv EPG API | **Experimental rung, verify first** | 6 |
| AGPL tooling | **Allowed as an optional, documented, user-installed rung** | 7 |
| Where it runs | **Everything in GitHub Actions** | 7 |

---

## 4. Verified facts that constrain the design

These were checked during research and materially change what is possible.

### 4.1 GitHub Models no longer exists

> "As of July 30, 2026, GitHub Models has been fully retired. The playground, model
> catalog, inference API, and bring your own key (BYOK) are no longer available to any
> customer." — `docs.github.com/github-models`, read 2026-09-14.

**Consequence:** the tempting "free LLM inside Actions via `GITHUB_TOKEN`" design is dead.
A provider API key must be supplied as a secret.

### 4.2 Anti-bot: plain Playwright is detected, and *why* matters

2026 benchmark, 7 tools × 31 real anti-bot targets × 3 sweeps (residential IP):

| Tool | OK | Gated | Blocked | Engine | Licence |
|---|---|---|---|---|---|
| **nodriver** | 28 | 3 | **0** | system Chrome via CDP, no Playwright shim | **AGPL-3.0** |
| CloakBrowser | 26 | 3 | 2 | Chromium 145 fork (49 C++ patches) | MIT wrapper / custom binary |
| **curl_cffi** | 26 | 3 | 2 | no browser; TLS impersonation only | **MIT** |
| patchright | 25 | 3 | 3 | Chrome 148, `channel=chrome` | Apache-2.0 |
| camoufox | 25 | 3 | 3 | Firefox 135 fork | MPL-2.0 |
| vanilla Playwright | 24 | 2 | 5 | Chromium 147 | Apache-2.0 |
| rebrowser-playwright | 24 | 2 | 5 | Chromium 136, unmaintained | unspecified |

Two conclusions drive the ladder:

1. **The deciding signal is automation-protocol fingerprinting**, not browser claims.
   Vanilla Playwright leaks its CDP `Runtime.enable` / `Target.setAutoAttach` startup
   sequence; patching JS properties cannot fix that. `nodriver` wins only because it
   removes Playwright from the control plane.
2. **`curl_cffi` is the value pick.** A 6.4 MB MIT wheel with no JS engine ties a 130 MB
   patched Chromium fork. Most targets never need JavaScript.

### 4.3 magenta.tv: the SPA shell, and the dead backend API

- `https://www.magenta.tv/` returns **"No readable text found at URL"** via a plain GET
  (verified 2026-09-14) — the 200-on-shell trap.
- The real Telekom TV backend used by `iptv-org/epg`'s `web.magentatv.de` grabber is:
  - `POST https://api.prod.sngtv.magentatv.de/EPG/JSON/Authenticate?SID=firstup&T=Windows_chrome_118`
    with body `{"terminalid":"00:00:00:00:00:00","mac":"00:00:00:00:00:00","terminaltype":"WEBTV","utcEnable":1,"timezone":"Etc/GMT0","userType":3,"terminalvendor":"Unknown"}`
  - `https://api.prod.sngtv.magentatv.de/EPG/JSON/PlayBillList` (programme list)
  - Config: `sites/web.magentatv.de/web.magentatv.de.config.js` (`fetchCookieAndToken`, `setHeaders`)
- **Status: broken.** `iptv-org/epg` issue #2966 (2025-12-21) shows
  `getaddrinfo EAI_AGAIN api.prod.sngtv.magentatv.de` — the hostname no longer resolves —
  and the guide was **closed as not planned**. Issue #2367 (2024-05-05) had already flagged
  the `.DE` site as broken while `.AT` still worked.

**Consequence:** treat the EPG-JSON path as an **experimental rung behind a flag**, not a
dependency. The only reliable magenta.tv strategy today is browser-rendering via hosted
backends, and honest labelling when that fails.

### 4.4 Free LLM inventory (2026)

| Provider | Free allowance | Notes |
|---|---|---|
| **Google AI Studio (Gemini)** | Free tier key, large context | **No GCP billing required** — separate product from the paid Gemini API |
| **OpenCode Zen** | Free model IDs: `big-pickle`, `mimo-v2.5-free`, `ling-3.0-flash-fin-free`, `nemotron-3-ultra-free`, `nemotron-3.5-lightning-free`, `muse-spark-1.3-contributor-free` | OpenAI-compatible `POST /zen/v1/chat/completions`; config id format `opencode/<model-id>`; other Zen models are pay-as-you-go |
| **OpenRouter** | 20 req/min, 50 req/day (1,000/day after a one-time $10 deposit); `openrouter/free` router | Last-resort rung |
| ~~GitHub Models~~ | **Retired 2026-07-30** | Not an option |

### 4.5 Free search/render inventory

| Backend | Free allowance | JS render | Role |
|---|---|---|---|
| **Exa MCP (free tier)** | $20 sign-up credits (~2,800 searches) + $10/month | `search_and_contents` returns page text | Rung 1 |
| **TinyFish** | Search + Fetch free at any balance; 5,000 req/month, 30 req/min Search, 150 URLs/min Fetch | Fetch | Rung 2 |
| **Firecrawl** | 1,000 credits/month, no card; keyless mode available | **Yes** (`waitFor`, `onlyMainContent`) | Rung 3 (the magenta.tv workhorse) |
| YouTube Data API v3 | 10,000 units/day; `search.list` = 100 units | n/a | ~100 live searches/day |

### 4.6 Google auth is free

**Correcting a false premise from the interview:** creating a GCP project is free, and the
**Google Calendar API, IAM and STS/Workload Identity Federation require no billing
account**. The "paid Gemini API key" is a *different* product; the free Gemini tier comes
from AI Studio with its own key. Choosing WIF therefore does **not** force any spend.
Still, both auth options are $0, so the choice is about setup friction and secret hygiene,
not cost.

**Superseded:** neither option was kept. The calendar credential is now Composio's, which
removes the GCP project altogether — see the recorded correction in §12. This section is left
standing because the billing fact in it is still true and was the reason the WIF choice was
defensible at the time.

---

## 5. Architecture — the daily runtime

```
cron (daily) ─┐
workflow_dispatch ─┤
repository_dispatch ─┴─► job: daily-run
                          │
                          ├─ 1. auth (Composio API key — replaced WIF; see §12 correction)
                          ├─ 2. install opencode CLI + SKILL.md context
                          ├─ 3. list existing calendar events (today … today+7d)   ← dedupe FIRST
                          ├─ 4. search ladder:  Exa MCP free → TinyFish → Firecrawl
                          ├─ 5. gate ladder:    youtube_live.py, link_check.py, 7-check pipeline
                          ├─ 6. render ladder:  hosted backends → (flagged) self-hosted stealth rung
                          ├─ 7. LLM adjudication (only for ambiguous free-access text)
                          ├─ 8. upsert: replace-if-unverified, never touch verified
                          ├─ 9. write candidate ledger + run-log row (telemetry branch)
                          ├─ 10. post-hoc audit of yesterday's events
                          ├─ 11. eval run → self-improvement branch if green-diff exists
                          └─ 12. job summary + issue on any anomaly
```

### 5.1 Trigger

- `schedule: cron` — **once daily, `30 8 * * *` (08:30 UTC / 10:30 CEST).** The earlier
  06:30 UTC proposal is **withdrawn**: Gemini free-tier RPD resets at **midnight Pacific**
  (07:00-08:00 UTC), so a 06:30 run would spend the tail of the previous day's quota. 08:30 UTC
  starts on a fresh quota and still precedes typical 14:00+ tip-offs. Full reasoning in §15.3.
- `workflow_dispatch` for ad-hoc runs.
- `repository_dispatch` (`streams-refresh`) so the run can be kicked externally.

### 5.2 Executor — `opencode` CLI headless (verified)

Chosen in round 4. Verified against `opencode.ai/docs/cli` on 2026-09-14:

| Need | Verified mechanism |
|---|---|
| Non-interactive run | **`opencode run [message..]`** — "Run opencode in non-interactive mode by passing a prompt directly… useful for scripting, automation" |
| Pin the model | **`--model` / `-m`** in `provider/model` form, e.g. `--model opencode/big-pickle`; `opencode models [provider]` lists valid ids |
| Machine-readable output | **`--format json`** (raw JSON events) — parse this for the transcript, not the human-formatted default |
| Avoid MCP cold-boot per run | `opencode serve` once, then **`opencode run --attach http://localhost:4096`** |
| Credentials | `opencode auth login` writes `~/.local/share/opencode/auth.json`; it also loads provider keys from **env vars or a project `.env`** |
| Permissions | `opencode agent create --permissions <list>`; available: `bash, read, edit, glob, grep, webfetch, task, todowrite, websearch, lsp, skill` — **anything omitted is denied** |

**Recommended CI invocation (Phase 1):**

```bash
opencode serve --port 4096 --hostname 127.0.0.1 &      # one MCP boot for the whole job
opencode run --attach http://localhost:4096 \
  --model "$LLM_MODEL" --format json \
  --file SKILL.md --file references/ \
  "Execute Steps 1-7 for today. Emit the Step 7 table." | tee transcript.json
```

**Permission hardening — this is a security control, not a nicety.** The runtime must use a
*restricted* agent that omits `edit` and `bash`, so a calendar-writing run cannot rewrite
`SKILL.md`, `references/` or the gate scripts mid-run:

```bash
opencode agent create --path .opencode/agent --mode primary \
  --description "Basketball streams runtime: read-only research, calendar writer" \
  --permissions read,glob,grep,webfetch,websearch,skill
# note: 'edit' and 'bash' deliberately absent => denied
```

The self-improvement workflow (§9) uses a **different, explicitly-scoped agent** that is
allowed `edit`, and it never has calendar credentials. Separation of authority between the two
workflows is what makes "rewrite its own instructions" safe.

**Still to verify (behavioural, not documented):**

1. Exit-code semantics when a tool call fails mid-run, so a partial run is detectable rather
   than silently reported green.
2. Whether `--attach` surfaces a distinct exit code when the server died.
3. Whether `allowed-tools` in `SKILL.md` frontmatter constrains tool availability, or whether
   the agent `--permissions` list is the only real control (assume the latter until proven).
4. Whether `--file references/` attaches a directory or needs each file listed.

**Fallback if headless opencode proves unsuitable:** a thin Python runner
(`scripts/run_daily.py`) implementing the tool layer directly against the provider ladder over
plain HTTPS. The gate scripts (`youtube_live.py`, `link_check.py`, the 7 checks) are already
deterministic and would be reused either way — only orchestration and text judgement change.
This is why the spec keeps **all gates in Python**, never in prompts.

### 5.3 Provider ladder (failover order)

```
LLM:    Gemini free (AI Studio)  →  OpenCode Zen free  →  OpenRouter free
Search: Exa MCP (free)           →  TinyFish           →  Firecrawl
Render: Firecrawl (waitFor)      →  TinyFish Fetch     →  [flagged] self-hosted stealth rung
```

Every rung used must be recorded per candidate, so cost pressure and dead rungs are visible
in the telemetry.

### 5.4 Rendering and the anti-bot ladder

Because the decision is **"run all as GitHub Actions"**, the self-hosted stack runs in CI
too, with **datacenter-IP blocking expected and logged** rather than treated as an error.

Recommended order (coverage-per-line-changed first, MIT-clean by default):

| Rung | Tool | Licence | Why here |
|---|---|---|---|
| 0 | hosted: Firecrawl | commercial free tier | Rendering without a browser; the magenta.tv workhorse |
| 0 | hosted: TinyFish Fetch | commercial free tier | Free at any balance; good fallback |
| 1 | **`curl_cffi`** | **MIT** | 6.4 MB, no JS engine, ties a 130 MB patched Chromium (26/31). Biggest impact for the smallest change |
| 2 | `patchright` | Apache-2.0 | Adds real JS execution with the CDP-leak patches; `channel=chrome` |
| 3 | `camoufox` | MPL-2.0 | Different (Firefox) TLS shape for targets that block Chrome shapes |
| 4 | `CloakBrowser` | MIT wrapper | Patched Chromium fork, Playwright-compatible API |
| 5 *(opt-in, documented only)* | `nodriver` | **AGPL-3.0** | The only zero-blocked tool, but AGPL — see §6 |

**Licensing rule (round 7):** AGPL tooling is allowed **only as an optional, documented rung
the user installs themselves**. It is never a dependency of the repo, never bundled, never
required by any workflow, and never imported by shipped code. MIT-compatible alternatives
must always exist above it. `nodriver` sits at the bottom of the ladder behind an explicit
opt-in flag, and `docs/` must state the AGPL implication plainly.

### 5.5 Calendar interaction

Because the run writes directly (round 1), the ordering is a safety property:

0. **Decide the write mode once, outside the workflow.** `scripts/write_mode.py` resolves
   dry-run from the event name, the `workflow_dispatch` input and `ENABLE_CALENDAR_WRITES`,
   fail-closed, and every step reads that single value. The first implementation inlined
   `${{ inputs.dry_run || 'true' }}`, which is permanently `'true'` on a `schedule` event
   (`inputs` does not exist on a cron) — so Phase 2's switch did nothing on the only
   unattended path, which is precisely the false-negative this spec exists to prevent.
   See `docs/runtime.md` for the precedence table.

1. **Dedupe first.** `GOOGLECALENDAR_EVENTS_LIST` over `today 00:00 … today+7d 23:59`, then an
   in-memory match on ±30 min **and** team pair **and** league substring, before any write.
2. **Then upsert** per the round-5 rule:
   - existing **verified** event → **never touch**; log `skip (verified exists)`.
   - existing **unverified** event → **replace** with better data, promote the colour if
     free access is now confirmed, append a revision note.
   - no existing event → create.

---

## 6. Verification state machine (the heart of false-positive control)

Rather than a boolean, every event carries a state. This is what makes "prefer recall early,
precision later" honest: uncertain games are still surfaced, but they *look* uncertain.

| State | Meaning | Title | `colorId` | Colour |
|---|---|---|---|---|
| `VERIFIED` | Free access + live stream both confirmed by ≥1 approved source and Check 7 | normal title | `6` | Tangerine |
| `UNVERIFIED` | Live stream plausible, **free access not 100 % confirmed** | `[UNVERIFIED] <league> <t1> vs <t2>` | `5` | Banana (amber = caution) |
| `WRONG` | Post-hoc audit proved it was never live, or was paid | `[WRONG] <league> <t1> vs <t2>` | `7` | Peacock |
**Resolved — no fifth state.** Round 5 offered `LIVE_NOW` (Basil `10`) as an alternative and the
owner chose "Wrong = Peacock (7)" instead, so the state machine ships with **four** states. A
live-now game is simply `VERIFIED`; the event description records `Access: live at run time`.
Adding a fifth state later is backward-compatible (a new colour and prefix), so this is cheap
to revisit and deliberately deferred.

Unchanged: FIBA internationals stay `2` (Sage), finals stay `11` (Tomato). The existing
`get_color_id(league, event_type)` in `scripts/color_mapping.py` must therefore gain a
**verification-state parameter** rather than being replaced — the league rules and the state
rules are orthogonal.

**Never delete on failure.** An event whose link dies keeps its state history; the dead link
is quarantined and the reason is appended to `Validation Notes`. `WRONG` is applied by the
audit, not by a human.

### 6.1 Promotion sweep

Because the cadence is daily, every run must also **re-check yesterday's `UNVERIFIED`
events** and promote them to `VERIFIED` (colour 5 → 6) once free access is confirmed, or
mark them `WRONG` if the game turned out to be paid / not broadcast. Without this sweep the
system accumulates amber events forever.

---

## 7. False-positive prevention

1. **All deterministic gates stay in Python** (never in a prompt): `youtube_live.py`
   (live + future-only), `link_check.py` (BROKEN vs BLOCKED), the Check 1/2/3/5 keyword sets,
   duplicate detection, colour mapping.
2. **LLM is limited to judgement on ambiguous text** — free-access phrasing, query generation,
   German description copy. It may never invent a URL, override a failed check, or decide
   magenta.tv is free without the `magentasport.de` announcement.
3. **Never create without a live stream** — the v1.1.x incident rule stands.
4. **`UNVERIFIED` labelling** — an event may be created while free access is unconfirmed, but
   only with the prefix + colour-5 marking. This is the recall-preferring concession, and it is
   visible.
5. **Post-hoc audit (round 4, refined):** the next day's run re-checks events that have ended.
   Failure modes → `WRONG` (colour 7) + an appended note + an eval case + an issue line. The
   audit does **not** silently tighten a gate in place; tightening happens on the
   self-improvement branch (§9).
6. **BLOCKED ≠ broken.** `401/403/429/451` must climb the ladder, never reject the game.
   Shipping a false negative because a datacenter IP got a 403 is the mistake this rule exists
   to prevent.

## 8. False-negative prevention (recall)

The baseline is **search-derived candidates** (round 4), which makes recall computable today
without waiting for an official fixture feed.

1. **Candidate ledger.** Every candidate *any* backend surfaces is appended to the telemetry
   branch, with: url, backend/rung, discovered-at, normalised teams/league/datetime, and a
   `disposition` of `created | skipped_duplicate | rejected_<check> | unverifiable`. This file
   is the recall denominator.
2. **Recall metric** = `created ∪ promoted` ÷ `candidates with a valid live signal`.
   Reported per run and charted over the append-only history.
3. **Dropped-candidate re-check.** Candidates rejected as `unverifiable` (all render rungs
   failed) are re-tried on later runs — the cheapest available recall win, since the URL is
   already known.
4. **Sparse-source escalation.** If a candidate is rejected only at Check 6/7 and the source
   tier is ≥5, the run escalates one rung rather than giving up.
5. **[VERIFY] official-fixture upgrade path.** "Search-derived candidates" cannot see a game
   that *no* backend surfaced. A future phase should diff against official league fixture
   lists (`basketball-bundesliga.de`, `euroleaguebasketball.net`,
   `championsleague.basketball`) to catch that class. Recorded as Phase 4, explicitly out of
   scope for Phase 1.

---

## 9. Eval and self-improvement loop

Round 1 selected **all five** self-improvement capabilities. Round 3 set the brake:
**everything lands on a self-improvement branch; `main` advances only when the eval run is
green** (round 5). Round 7 (`do-harness only` from the earlier plan) means the repo-side
grader is `do-harness eval`, not the ad-hoc `--check evals`.

### 9.1 Replace the canned stubs with real transcripts

`scripts/runtime_eval.py` compares hand-written strings. The runtime must instead emit a
**real transcript** per eval case (the actual tool calls + final `Decision=…; checks: …`
line), and `runtime_eval.py --stubs <real-transcripts.json>` becomes a genuine grading run.
The canned stubs are retained only as an offline smoke-test fixture.

### 9.2 The five loops

| Loop | Mechanism | Blast radius |
|---|---|---|
| **Auto-grow evals** | Any `WRONG` event, any audit mismatch, any `BROKEN` link on a created event → synthesise a new `evals/evals.json` case (id monotonically increasing, `expected_output` carrying `checks: <name>=PASS\|FAIL` needles) | `evals/` only |
| **Auto-tune tier order** | `source_learning.py score` hit rates reorder `references/approved-sources.md` + `config/sources.json`; requires ≥10 attempts per source before reordering | `config/`, `references/` |
| **Auto-tune gates/thresholds** | Date window, keyword sets, confidence cutoffs nudged from audit outcomes | `SKILL.md`, references |
| **Distill skills from traces** | `do-harness trace add` → `distill --skill skill-basketball-streams --pattern <p> --from-trace <id> --to-fixture` (refuses without resolution steps; `--to-fixture` raises the pass-rate bar) | new `.agents/skills/` entries |
| **Rewrite own instructions** | The agent edits `SKILL.md` / `references/` from its own failure analysis | `SKILL.md`, `references/` |

### 9.3 The gate

```
daily run finds a misjudgement
   → synthesise eval case / proposed edit
   → commit to branch: self-improve/YYYY-MM-DD
   → run do-harness eval + pytest + validate.py on the full case set
        green → PR opened (and, per round 3, may auto-merge when the diff is data-only)
        red   → branch kept, issue opened, main untouched
```

Non-negotiable: **the newly generated eval case must pass before its own fix is accepted**,
otherwise the system can "learn" by weakening the gate that caught it. `--bless` must never be
run to re-baseline a red run.

---

## 10. Telemetry

**Home:** a dedicated orphan **telemetry branch** (round 6). `main` stays clean; the history is
still fully diffable and append-only forever (round 3).

```
telemetry/                      # orphan branch, never merged to main
  run-log.jsonl                 one row per candidate decision, append-only forever
  candidates.jsonl              the recall denominator (§8 item 1) — every surfaced candidate
  audit.jsonl                   post-hoc audit verdicts (§7 item 5)
  events.jsonl                  created/updated/quarantined calendar events, keyed by event_id
  metrics.json                  rolling aggregates (rewritten each run, current-state snapshot)
  sources.json                  per-source score snapshot from source_learning.py
  rung-attempts.jsonl           one row per rung per host per run (append-only)
  rungs.json                    per-rung health: attempts, successes, consecutive failures
  README.md                     schema doc + retention policy, rendered from this spec
```

### 10.1 Row schemas

The telemetry format is **JSONL, one object per line, append-only**. Every row carries `ts`
and `run_id` first so a `grep`/`jq` audit needs no schema knowledge.

`run-log.jsonl` — extends the existing `source_learning.py record` shape, so the 18 existing
tests keep passing:

```json
{"ts":"2026-09-14T08:32:11+00:00","run_id":"2026-09-14T08:30Z","source":"magenta.tv","tier":4,
 "url":"https://www.magenta.tv/tv/live-basketball-euroleague-88213","backend":"firecrawl","rung":"render-0",
 "llm":"gemini-3.5-flash-lite","outcome":"create","state":"UNVERIFIED","color_id":"5",
 "reason":"free access not confirmed on magentasport.de","event_id":"abc123"}
```

`candidates.jsonl` — one row per candidate per backend that surfaced it:

```json
{"ts":"2026-09-14T08:31:02+00:00","run_id":"2026-09-14T08:30Z","candidate_id":"c-0007",
 "url":"https://www.championsleague.basketball/live/tenerife-vs-bonn","backend":"exa-mcp",
 "game_key":"BCL|Lenovo Tenerife|Telekom Baskets Bonn|2026-09-17T20:00+02:00",
 "live_signal":true,"disposition":"created","state":"VERIFIED","first_seen_run":"2026-09-14T08:30Z"}
```

`disposition` ∈ `created | skipped_duplicate | rejected_<check> | unverifiable`. Rows with
`unverifiable` are **re-tried on later runs** (§8 item 3) — this is why the ledger must be queryable
by URL and by `game_key`, not just appended to.

`audit.jsonl`:

```json
{"ts":"2026-09-15T08:33:40+00:00","run_id":"2026-09-15T08:30Z","event_id":"abc123",
 "game_key":"BCL|Lenovo Tenerife|Telekom Baskets Bonn|2026-09-17T20:00+02:00",
 "prior_state":"UNVERIFIED","verdict":"VERIFIED","evidence":"championsleague.basketball free marker + render markers",
 "new_color_id":"6","eval_case_created":null}
```

`verdict` ∈ `VERIFIED | WRONG | INCONCLUSIVE`. Only `WRONG` sets colour `7` and the `[WRONG]`
prefix. `INCONCLUSIVE` leaves the event untouched and records why.

### 10.2 Write protocol

GitHub Actions cannot push to another branch without checking it out. The runner therefore:

```bash
# 1. main stays checked out for the code under test
git worktree add /tmp/telemetry telemetry   # or a second checkout step
# 2. append rows, then a single squashed commit per run
cd /tmp/telemetry && git add -A && git commit -m "telemetry: run 2026-09-14T08:30Z" && git push origin telemetry
```

One commit per run, never per row. The workflow needs `contents: write` and a
`concurrency` group so two runs cannot interleave a push.

### 10.3 Retention (resolving "append-only forever")

"Forever" was chosen for **audit value**, but a JSONL of every candidate across seasons grows
unbounded. Resolution:

| File | Policy |
|---|---|
| `run-log.jsonl`, `candidates.jsonl`, `events.jsonl`, `audit.jsonl`, `rung-attempts.jsonl` | **Never pruned.** Append-only forever — this is the evidence trail and it is what makes precision/recall chartable across seasons |
| `metrics.json`, `sources.json`, `rungs.json` | **Rewritten** each run from the JSONL (current-state snapshots, not history) |
| Growth control | **Annual roll-up**: on the first run after 1 January, full-year files are gzipped to `telemetry/archive/<year>/*.jsonl.gz` and the active file is truncated to the current year. Roll-ups keep the raw rows while bounding the hot file size |
| Size guard | If any active JSONL exceeds ~5 MB, open an issue recommending a roll-up rather than failing the run |

`metrics.json` is derived, so it is always safe to delete and regenerate — useful for testing
without touching history.

### 10.4 Where the writer lives

`scripts/source_learning.py` currently writes to `logs/run-log.jsonl` (gitignored). Decision:
**extend it with a `--dest <dir>` flag rather than add a second writer**, so there is exactly one
implementation of the row shape and one set of tests. Schema additions (`game_key`, `backend`,
`rung`, `llm`, `state`, `color_id`, `event_id`) must be added as **optional** keys so the
existing 18 tests continue to pass unchanged.

Run-log row (extends the existing `source_learning.py record` shape so nothing is rewritten):

```json
{"ts":"2026-09-14T06:32:11+00:00","run_id":"2026-09-14T06:30Z","game_key":"BBL|ALBA Berlin|FC Bayern Muenchen|2026-09-16T19:00+02:00",
 "source":"magenta.tv","tier":4,"url":"https://www.magenta.tv/tv/live-...","backend":"firecrawl","rung":"render-0",
 "llm":"gemini-free","decision":"create","state":"UNVERIFIED","color_id":"5","outcome":"create",
 "reason":"free access not confirmed on magentasport.de","event_id":"abc123"}
```

`scripts/source_learning.py` currently writes to `logs/run-log.jsonl` and is gitignored. The
runtime must instead write to the telemetry branch — **[VERIFY]** whether to extend
`source_learning.py` with a `--dest` mode or add a separate writer; the existing 18 tests must
keep passing either way.

---

## 11. Alerting

Round 6: **GitHub issue + Actions job summary.**

- **Job summary**: always emitted — counts by state, candidates seen, recall/precision,
  which rungs were used, which rungs failed, ladder fallbacks, spend-free confirmations.
- **Issue**: opened (or appended to) when any of: the run failed; a provider ladder was
  exhausted; a `WRONG` verdict was recorded; `do-harness eval` went red; a render rung
  regressed vs the previous run.
- **[VERIFY]** issue dedupe strategy — one rolling issue titled `[runtime] daily stream run
  anomalies` appended per day is the proposal, so 30 issues/month are not created.

---

## 12. Auth, secrets and cost

| What | How | Cost |
|---|---|---|
| Google Calendar writes | **Composio tool execution** — `COMPOSIO_API_KEY` + `COMPOSIO_USER_ID`, Google grant held by the connected account | $0 (Hobby tier, 100K calls/month) |
| ~~WIF/OIDC~~ | Superseded — see the correction below | — |
| LLM | `GEMINI_API_KEY` (AI Studio free) → `OPENCODE_ZEN_API_KEY` → `OPENROUTER_API_KEY` | $0 |
| Search/render | `EXA_API_KEY`, `TINYFISH_API_KEY`, `FIRECRAWL_API_KEY` | $0 |
| Actions minutes | Public repo → free standard-runner minutes | $0 |

Recorded correction: **WIF does not require billing**, and it does not require a paid Gemini
key — Gemini's free tier is a separate AI Studio product. If GCP project creation is
unwanted friction, the service-account-JSON path is equally free; WIF is preferred purely for
secret hygiene (no long-lived credential to leak).

Recorded correction (**transport superseded**): the WIF/OIDC path above was implemented and
then replaced. The credential is now **Composio's**: `scripts/calendar_io.py` executes
toolkit tools over HTTP and Composio holds the Google grant for a connected account. There is
no GCP project, no service account, no impersonation policy, no calendar-sharing step, and no
`google-github-actions/auth@v2` step in `runtime-daily.yml`. The trade is the opposite of the
one WIF was chosen for — a long-lived credential now reaches a public calendar and it lives
with a third party — and it was taken deliberately to remove the setup entirely. Nothing
above about *billing* is affected; the free Gemini tier and §4.6 still hold.

The properties this spec actually protects are unchanged: dedupe-before-write, no HTTP at all
without `--live`, one decision in tested Python about whether a run may write (`--live` is
**read** from `write_mode.py`, never recomputed in YAML), and a `WRONG` event that is never
shown as `VERIFIED`. Four Composio behaviours are now load-bearing and are recorded in
`docs/runtime.md` and `scripts/README.md`: `CREATE_EVENT` ignores `color_id` (colour is a
follow-up `PATCH_EVENT`), it adds a Meet link and an attendee unless told not to, it nests its
result under `data.response_data` where `EVENTS_LIST` does not, and `version` must be `latest`
because a pinned toolkit version can silently drop the `timeMin`/`timeMax` window filter.

**Guardrails:** hard caps per run — max searches per tier, max render rungs per candidate,
and a bail-out that stops climbing the ladder once a rung has failed 3 consecutive times
(mirrors the do-harness strike-halting idea). Exhausting every rung → **fail closed** and log,
per round 4.

---

## 13. Files to add or change

```
NEW  .github/workflows/runtime-daily.yml        cron + workflow_dispatch + repository_dispatch
NEW  .github/workflows/self-improve.yml         branch creation, eval gate, PR
NEW  scripts/run_daily.py                       orchestration IF opencode headless proves unusable
NEW  scripts/upsert_events.py                   dedupe-then-replace-if-unverified logic
NEW  scripts/audit_events.py                    post-hoc VERIFIED/WRONG verdicts
NEW  scripts/candidates.py                      candidate ledger writer + recall metric
NEW  scripts/render_ladder.py                   hosted rungs + curl_cffi/patchright/camoufox (MIT-clean)
NEW  tests/fixtures/runtime_transcripts.json    real transcripts replacing canned stubs
EDIT scripts/color_mapping.py                   add verification-state dimension (keep league rules)
EDIT scripts/link_check.py                      add ledger/telemetry emission hooks
EDIT scripts/source_learning.py                 write to telemetry branch; keep 18 tests green
EDIT scripts/runtime_eval.py                    grade real transcripts; keep stub mode as smoke-test
EDIT SKILL.md                                   new state machine, labelling rule, audit + sweep steps
EDIT references/self-learning.md                the five loops, branch gate, telemetry schema
EDIT references/search-backends.md              provider ladders, curl_cffi-first anti-bot table, licences
NEW  docs/runtime.md                            operations doc: secrets, cadence, recovery, AGPL note
EDIT README.md, CHANGELOG.md, config/*.json
```

Note: `docs/do-harness.md` already exists and is **PLAN ONLY**. This spec assumes do-harness
becomes the repo-side grader for the self-improvement gate; executing that plan remains a
separate, explicitly-marked step.

---

## 14. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Headless `opencode` cannot be pinned to a free model in CI | Runtime cost or failure | Fallback to `scripts/run_daily.py` + direct HTTPS; gates already in Python |
| Datacenter IPs blocked harder than residential | magenta.tv recall collapses in CI | Mark `UNVERIFIED` rather than drop; log rung failure; optional proxied rung **[VERIFY]** |
| Free tiers rotate / are withdrawn (GitHub Models already retired 2026-07-30) | Provider ladder rots | Per-rung health recorded in telemetry; `metrics.json` exposes dead rungs |
| Auto-grew eval case encodes the wrong expectation | System "learns" a bug in | New case must pass against the fix *and* the fix must be reviewable; `--bless` banned on red |
| Auto-tuned thresholds loosen a gate | Silent precision loss | Threshold changes are instruction edits → branch + eval gate, never data-only auto-merge |
| `nodriver` AGPL contamination | Licence breach | Opt-in, user-installed, never imported by shipped code; documented in `docs/runtime.md` |
| Daily commits bloat the repo | Noisy history | Orphan telemetry branch, never `main` |
| Duplicate events from overlapping runs | Calendar spam | Dedupe-first ordering; `concurrency` group on the workflow |
| gemini.tv calendar is public | Unverified guesses embarrass | `UNVERIFIED` prefix + colour make uncertainty visible to subscribers |

---

## 15. Open questions

### 15.1 Resolved during follow-up research (2026-09-14)

| # | Question | Resolution |
|---|---|---|
| 1 | Headless `opencode` invocation + model pinning | **Resolved.** `opencode run --model provider/model --format json`, `--attach` to a `serve` instance, `agent create --permissions` for tool control. See §5.2 |
| 2 | Extend `source_learning.py` or add a writer | **Resolved: extend** with `--dest`; new row keys must be optional so the 18 existing tests stay green. See §10.4 |
| 3 | Is the 5th state `LIVE_NOW` wanted? | **Resolved: no.** Round 5 chose "Wrong = Peacock" over "add a fifth state". Four states ship. See §6 |
| 4 | Exact free Gemini model IDs | **Resolved as a policy, not a literal.** Quotas are volatile and **per-project**, reset at **midnight Pacific**, and documented as "not guaranteed". Do **not** hardcode an RPD budget: read `ai.google.dev/gemini-api/docs/rate-limits` + AI Studio at build time and treat the Lite tier (`3.5 Flash-Lite` / `3.1 Flash Lite`, which currently carry the most generous free RPD) as primary. See §15.3 |
| 7 | Telemetry retention | **Resolved: annual gzip roll-up.** See §10.3 |

### 15.2 Still open (must be verified with the binary/console in hand)

1. `opencode` **exit-code semantics** on partial tool failure, and whether `--attach` errors are distinguishable (§5.2).
2. Whether `SKILL.md` `allowed-tools` constrains tool availability, or only `agent --permissions` does — **assume only the latter** until proven (§5.2).
3. Whether `--file references/` attaches a directory or needs each file enumerated (§5.2).
4. **`distill --to-fixture` fixture location.** Not documented in `do-harness`'s `docs/adoption.md` (checked 2026-09-14). Proposed: keep do-harness fixtures **separate** from `evals/evals.json`, because that file is pinned to the agentskills.io schema and is already validated by `scripts/validate.py` + `runtime_eval.py`. Use `evals/do-harness/` and confirm with `do-harness distill --help` once the pinned binary is installed.
5. **Proxied render rung** — still unspecified. Given "run all as GitHub Actions", decide whether datacenter-IP blocking for `magenta.tv` is acceptable degradation or warrants a proxy.
6. Whether `plans/invariants.json` accepts our gate invariants verbatim: the schema is a `DecisionHeader` array of `{invariant, rationale, sensor, category}` and **unknown fields inside a header are rejected**, so each invariant needs a real `sensor` name. See §19.

### 15.3 LLM call budget (new constraint from the Gemini research)

This materially changes the runtime design and is worth stating explicitly:

- Gemini free-tier quotas are enforced **per project**, across RPM / TPM / RPD, and "specified rate limits are not guaranteed and actual capacity may vary". Community reports of the Flash free tier during 2025-2026 range from **~20 RPD** to **~1,000 RPD** after repeated cuts.
- **RPD resets at midnight Pacific** (07:00-08:00 UTC depending on DST), not midnight UTC.

Consequences baked into the spec:

1. **Schedule the cron at or after 08:15 UTC** so the run starts on a fresh daily quota. The
   earlier 06:30 UTC proposal is **withdrawn** — it would spend the tail of the previous day's
   quota. 08:30 UTC (10:30 CEST) still precedes typical 14:00+ tip-offs and the run covers
   today+7d, so nothing time-critical is lost.
2. **Budget LLM calls per run** assuming the worst credible case (~20 RPD). The design must
   therefore **batch** judgement: one call per game's free-access evidence bundle, not one call
   per candidate, and a hard cap that stops LLM use and falls back to deterministic-only
   labelling once the cap is hit.
3. **Lite models first.** Primary = the Lite tier; escalate to a larger Flash/Pro model only for
   a genuinely ambiguous case, and only if the budget allows.
4. Quota exhaustion must **not** fail the run: it degrades to deterministic-only (creating
   `UNVERIFIED` events instead of dropping games), which is exactly the recall-preferring
   behaviour chosen in round 1.

---

## 16. Phase 0-5 implementation plan

Each phase is independently shippable and each ends with an acceptance test that a machine can
check. Phases 0-1 touch nothing that can embarrass a calendar subscriber.

**Status:** Phase 0 **done** · Phase 1 **done** — `scripts/extract_candidates.py` (the
agent/planner seam) and `scripts/calendar_io.py` (the only API caller; `apply` writes nothing
without `--live`) make the write path a script chain rather than workflow shell · Phase 2
**done** — logic and state machine, exercised by `tests/test_upsert_events.py` and
`tests/test_verification.py` · Phase 3 **done** — `tests/test_audit_events.py` plus a JSONL
telemetry writer · Phase 4 **done** — `scripts/synthesise_eval_case.py` +
`.github/workflows/self-improve.yml`, with the calendar credential deliberately absent ·
Phase 5 **done** — `scripts/fixtures.py` and the `unseen` class in `scripts/candidates.py`.

**§17's hard requirement was unenforceable on the real path (2026-09-15).** §6's table and §17
both promise that a `WRONG` event is **never shown as `VERIFIED`**, and `upsert_events` implemented
it: a stored `WRONG` is skipped, so "an audit verdict outranks a fresh guess". But the stored
state comes from the calendar, and the calendar recovers it from the **title prefix**
(`[WRONG] …`). `audit_events` computes that relabelled title and puts it in `audit.jsonl` — and
nothing applied it, so on the real path the row was unreachable: a game the audit had proved was
paid or never live stayed on the subscriber's calendar looking exactly like one awaiting
confirmation, and the next run's fresh guess was free to promote it to `VERIFIED`, which is the
false positive the whole pipeline exists to prevent. The planner now takes the audit ledger
(`--verdicts`) and honours the newest `WRONG` for an event from the record itself, joined by
`event_id`. Two follow-ons are still open and named here rather than implied: the *relabel*
(`[WRONG]`, colour 7; prefix dropped on a promotion) is computed but never written to the
calendar, so a condemned event is held rather than visibly marked, and the alert that would tell
a human it happened (§11) still has no producer for that condition.

**Phase 3 was wired end to end and had never run (2026-09-15).** §10.1 lists
`events.jsonl` (created/updated events by `event_id`) and `docs/runtime.md` documents
`evidence.json` (`{event_id: {live_confirmed, free_confirmed, paid}}`). The `audit` job reads
both, and its own comment in `runtime-daily.yml` named them as "two artefacts that Phase 2 must
produce" — but **nothing produced them**. So every run found its inputs absent, printed a
notice, and did nothing: no verdict was ever recorded, `metrics.json` reported precision `n/a`
for the life of the project, §17's "precision ≥ 0.95" was unmeasurable, and §9's loop had no
ledger to learn from. `scripts/event_ledger.py` is the writer, and three links in that chain
had to be rebuilt to make its output usable: `calendar_io.apply_plan` discarded the `id` the
API returns (so no verdict could be resolved back to an event), `extract_candidates`' field
whitelist dropped the evidence flags (so the audit could never get past "no evidence
recorded"), and `audit_events` judged every row (so an append-only ledger would have appended
one verdict per write for a single event). The handoff is a `run-ledger` artifact rather than a
direct write, because the job that knows what it wrote is the job that holds the calendar
credential and has no `contents: write`.

**A field the reader depends on was never written (2026-09-15).** `§6`'s `events_match`
decides whether a candidate *is* the event already on the calendar by comparing the `Teams:`
line of the description — the format §4 and `references/calendar-setup.md` specify, and the one
`list_events` parses back out. `apply_plan` takes a `descriptions=` map and **nothing ever
passed one**, so every event the runtime created carried `description: ""`. The teams check is
skipped whenever either side is empty, so a stored event matched *any* candidate inside the
30-minute window: a different pairing at the same time was planned as an `update` of it rather
than a new event, which is a mislabel on a public calendar. The block is now derived from the
plan row's own `league`/`teams`; an explicit `descriptions=` entry still wins.

**Added after Phase 5 (2026-09-14), both recorded here rather than rewritten into §4/§9.**
The ladder's rung 1 existed only in this document: `capture_transcripts.py` shipped `opencode`,
`openrouter`, `command` and `replay`, so the **free** primary rung had no implementation and
the capture path always needed the CLI. It now has `--runner gemini` (direct HTTPS to
`generativelanguage.googleapis.com`, no CLI, no billing), `--runner ladder` (gemini →
opencode → openrouter, recording which rung served each case) and `--list-models`, which
resolves the model id from a live `models.list` instead of trusting a pin. §9.1 is unchanged
in substance: real-transcript grading still requires a captured file, and one has still never
been captured — rung 1 has never run here for lack of a free key.
`scripts/check_workflow_refs.py` was added for a different reason: `runtime-daily.yml` ran a
grading step against a fixture that is deliberately absent, behind `|| true`, so it could only
ever no-op. The check treats a workflow path as a claim that must hold, which is the class fix
rather than the instance fix.

**§9.3 is now wired, and one of its links had to be rebuilt.** The branch captures transcripts
for **exactly the newly synthesised ids** and grades those with `--allow-partial`. Reusing the
committed capture was not an option: it covers ids 1..N, a synthesised case is N+1, and the gate
fails with "no transcript supplied" — a legitimate PR refused for a reason unrelated to it. The
partial flag is named at the call site and reports how many cases it skipped, which is the
concession §9.3's "non-negotiable" needs to stay checkable. `tests/test_self_improve_loop.py`
executes the whole chain in CI with the `replay` runner, including the red direction.

**A second divergence, recorded rather than rewritten.** §9.1 expected real transcripts to be
graded by "`runtime_eval.py --stubs <real-transcripts.json>`". They are graded by
`--transcripts` on the `assertions[]` needles instead, and `--stubs` keeps byte-equality for the
canned fixtures. The reason is that equality with `expected_output` is not a strict gate when
the output comes from a model — it is a **closed** gate: a live model never reproduces the
canonical string, so that path could not pass anything, which is precisely why the workflow
step it fed could only ever print a warning. The capture prompt was hardened at the same time:
it no longer quotes `expected_output`, because a prompt that carries the answer plus
substring grading is a grade that measures nothing.

**The stream-evidence gate was the one predicate still judged against strings we invented,
and it was wrong (2026-09-15).** §5.4 and the magenta.tv reference both require "player marker
**and** live marker", and both listed bare words (`player`, `dash`, `live`, `läuft`, `hls`, a
play-button label). That rule matched against the whole document, including the page's inline
JavaScript runtime, so on real recorded responses it accepted a **recorded YouTube video**, the
**YouTube home page**, the **MagentaSport announce home page** and the **Basketball Champions
League home page** — every page that streams nothing, and the exact failure §1 exists to
prevent. The markers are now structural (a media asset *and* a live-state badge or structured
field), media markers are matched with `<script>` blocks stripped, and the judgement is pinned
against real responses in `tests/fixtures/pages/` (`scripts/record_pages.py --check`, a
`validate.yml` step and the `page-corpus` sensor). This is the §5.2 discipline applied to the
last gate that did not have it: the fixtures are the ground truth, and the rule that failed on
them is kept as a test so it cannot come back.

**A fixture is not a permanent guarantee, so the corpus is re-checked against the live web
(2026-09-15).** `record_pages.py --check` is offline and can only prove that the stored **bytes**
still classify as recorded; a source that re-brands its player or moves a structured field would
leave the gate blind while everything stayed green. `.github/workflows/corpus-refresh.yml`
re-records every page weekly and reports a page whose **verdict** — not whose bytes — changed,
filing one deduplicated issue. `lost-evidence` is the failure §1 cares about most (a real stream no
longer recognised is a **missed game**); `gained-evidence` is the false positive the narrowed marker
set exists to prevent. Neither is fixed by editing a fixture: the corpus is a statement about the
web, and `--check` refusing an edited file is what keeps it one.

**The render ladder was empty in a stdlib-only environment (2026-09-15).** §5.4's ladder begins
with a hosted backend, and every local rung is an optional package. Nothing installs them (the
repo is stdlib-only on purpose, and `validate.yml` installs pytest and nothing else), so every
rung was either a key we may not have or a package we deliberately do not require — and because
an unavailable rung was classified as a *real error*, the climb **stopped at the first one**. The
practical effect: no game could ever carry render evidence, and the failure looked like "no rung
produced evidence for this URL" for every URL. `skipped` is now a distinct outcome that climbs,
and a keyless stdlib `urllib` rung is in every ladder — first where the host is server-rendered
(`championsleague.basketball`, `magentasport.de`), last on the `magenta.tv` SPA where it can only
prove the gate posture. `--probe` reports every rung's reason rather than one.

**And evidence is not attribution.** `has_stream_evidence()` answers "is this page playing a live
stream?", not "is it playing *this* game" — a page streaming something else passed it. §6's
Check 7 and Check 4 therefore have a code-level counterpart now: `anchor_game()` matches the
expected clubs against the document's **own name** (never the body, which links to every club it
mentions), refuses a title advertising more than one pairing as `ambiguous`, and shares its
club-name folding with the dedupe planner (`team_tokens.py`) so `Kāhu` = `Kahu` and `München` =
`Muenchen`. Both hard cases are real recorded titles; see `tests/fixtures/pages/README.md`.

Each phase's acceptance criterion is now machine-checked in
`.github/workflows/validate.yml`, which is why the runtime survives a schema change to any
single source page.

### Phase 0 — Telemetry and the candidate ledger (no calendar writes)

| File | Change |
|---|---|
| `scripts/source_learning.py` | Add `--dest <dir>`; add optional keys `game_key`, `backend`, `rung`, `llm`, `state`, `color_id`, `event_id` to the row; keep every existing key |
| `scripts/candidates.py` (new) | Ledger writer + `recall` subcommand computing `(created ∪ promoted) ÷ live_signal` |
| `scripts/run_daily.py` (new) | Phase-0 shell: search ladder only, writes `candidates.jsonl`, no calendar access |
| `.github/workflows/runtime-daily.yml` (new) | cron + `workflow_dispatch` + `repository_dispatch`; creates the telemetry worktree, pushes one commit per run |
| `tests/test_candidates.py` (new) | Recall arithmetic, `disposition` classification, `unverifiable` re-try selection |
| `tests/test_run_daily.py` (new) | Plumbing filtering, contributed-vs-attempted backends, the `--check-backends` gate, the `--json` stdout contract, and the `runtime-daily.yml` ordering |
| `docs/runtime.md` (new) | Secrets, telemetry schema, recovery runbook, AGPL note |

**Acceptance:** five consecutive daily runs populate `candidates.jsonl`; `scripts/candidates.py recall` prints a non-zero denominator; **zero** calendar API calls (assert by scoping the credential to read-only for this phase).

**Status:** Phase 0 is implemented. Two properties are load-bearing and were added after the fact, because a green run that records nothing looks exactly like a quiet day:

- `run_daily.py --check-backends` is the offline, presence-based **gate** on there being at least one configured backend. It runs before the ladder, and is deliberately **not** tolerated. The ladder step itself tolerates only *content* outcomes (reachable backend, no results, or a transient HTTP error), so a 429 cannot take the telemetry commit down with it — the same split as `--check-rungs` (gate) vs `--list-models` (report) on the LLM ladder.
- `--json` leaves stdout as the payload **alone**; every human line goes to stderr. And `backends` (contributed a row) is reported separately from `attempted` (answered with nothing), so a contributing backend cannot be confused with a silent one.

### Phase 1 — Daily run in dry-run

| File | Change |
|---|---|
| `scripts/run_daily.py` | Wire the full 7-check pipeline; emit the Step 7 table + a JSON run report |
| `.github/workflows/runtime-daily.yml` | Add the provider ladders; restricted agent permissions (no `edit`, no `bash`); job summary |
| `.opencode/agent/runtime.md` (new) | The permission-restricted agent definition (§5.2) |
| `tests/fixtures/runtime_transcripts.json` (new) | One captured real transcript per eval case |
| `scripts/runtime_eval.py` | Accept real transcripts; keep `--stubs` canned mode as an offline smoke test |

**Acceptance:** 7 consecutive runs where the job summary shows candidates, per-rung health, and the LLM call count; `runtime_eval.py` grades the captured transcripts and passes; every existing test still green.

### Phase 2 — Enable writes with honest labelling

| File | Change |
|---|---|
| `scripts/upsert_events.py` (new) | Dedupe-first ordering; replace-if-unverified; never touch verified |
| `scripts/color_mapping.py` | Add the verification-state dimension, keeping the league rules orthogonal |
| `SKILL.md` | New §state machine, labelling rule, and the `[UNVERIFIED]` prefix convention |
| `references/calendar-setup.md` | Document the four states and their colour ids |

**Acceptance:** a game with confirmed free access is created `VERIFIED` (colour `6`); a game with a plausible live stream but no free confirmation is created `UNVERIFIED` (`[UNVERIFIED]` prefix, colour `5`); a re-run over the same window creates **zero** duplicates; verified events are byte-identical across re-runs.

### Phase 3 — Audit and promotion sweep

| File | Change |
|---|---|
| `scripts/audit_events.py` (new) | Post-hoc verdicts, promotion sweep, `audit.jsonl` writer |
| `scripts/run_daily.py` | Run the sweep before the search so states are fresh |
| `references/self-learning.md` | Documentation for the sweep and verdict rules |

**Acceptance:** yesterday's `UNVERIFIED` events resolve to `VERIFIED` (colour 5→6) or `WRONG` (`[WRONG]` prefix, colour `7`) within one run; `INCONCLUSIVE` events are provably untouched; a synthetic never-live fixture is correctly marked `WRONG`.

### Phase 4 — Self-improvement branch and eval gate

| File | Change |
|---|---|
| `.github/workflows/self-improve.yml` (new) | Branch creation, eval gate, PR; separate agent with `edit` but **no** calendar credential |
| `scripts/synthesise_eval_case.py` (new) | Turn a `WRONG`/audit mismatch into a schema-valid `evals/evals.json` case |
| `do-harness.toml`, `AGENTS.md`, `plans/invariants.json` | Execute the existing `docs/do-harness.md` plan (still PLAN ONLY) so `do-harness eval` becomes the repo-side grader |

**Acceptance:** a deliberately seeded misjudgement produces a new eval case, a branch, and a red-then-green eval cycle; `main` is provably untouched while red; the synthesised case's needles parse through `runtime_eval.py`.

### Phase 5 — Official-fixture ground truth

| File | Change |
|---|---|
| `scripts/fixtures.py` (new) | Fetch/normalise league fixtures (`basketball-bundesliga.de`, `euroleaguebasketball.net`, `championsleague.basketball`) into `game_key`s |
| `scripts/candidates.py` | Add the `unseen` class: fixtures with no candidate at all |

**Acceptance:** recall is reported against league fixtures **and** against search-derived candidates; the unseen-game count is non-zero and tracked; the metric is chartable over the append-only history.

### Critical path

```
Phase 0 (ledger) ──► Phase 1 (dry-run) ──► Phase 2 (writes) ──┬─► Phase 3 (audit)
                                                              └─► Phase 4 (self-improve)
                                                                       │
                                                                       └─► Phase 5 (fixtures)
```

Phase 0 is the only true blocker: without the ledger there is no recall denominator, and without
recall there is no way to tell whether any later change made things better or merely quieter.

---

## 17. Success metrics

| Metric | Definition | Phase-2 target |
|---|---|---|
| Recall (search-derived) | `created ∪ promoted` ÷ candidates with a valid live signal | ≥ 0.80 |
| Precision (verified events) | verified events that survive audit ÷ all `VERIFIED` events | ≥ 0.95 |
| Mislabel rate | events whose state was wrong ÷ all created events | ≤ 0.05 |
| Mislabelled-but-hidden | `WRONG` events never shown as `VERIFIED` before audit | 100 % (hard requirement) |
| Rung health | % of runs where rung 0 (hosted render) sufficed | tracked, not gated |
| Cost | API spend per run | **$0** |
| Eval growth | misjudgements converted to eval cases | ≥ 1 per incident |
| LLM calls per run | total model calls, from `metrics.json` | ≤ 20 (worst-case free RPD) |
| Duplicate events | events created for an already-covered game | 0 |
| Unverified backlog age | oldest unresolved `UNVERIFIED` event | ≤ 2 days |

---

## 18. Render-ladder module specification

Implements §5.4 as one module with an explicit rung registry, so adding or removing a backend is
a data change and licence compliance is machine-checkable.

### 18.1 Rung interface

```python
# scripts/render_ladder.py (sketch)

class Rung:
    name: str            # "firecrawl" | "tinyfish" | "curl_cffi" | "patchright" | "camoufox" | "nodriver"
    kind: str            # "hosted" | "local"
    license: str         # "MIT" | "Apache-2.0" | "MPL-2.0" | "AGPL-3.0" | "proprietary"
    requires_js: bool    # hint for selection
    opt_in: bool         # True => never enabled by default, never a declared dependency

    def fetch(self, url: str, *, timeout: float) -> FetchResult: ...

@dataclass
class FetchResult:
    ok: bool
    status: int | None          # HTTP status when known
    text: str | None            # rendered body for marker scanning
    html: str | None
    blocked: bool               # 401/403/429/451 or a detectable gate
    rung: str
    elapsed_ms: int
```

`blocked=True` is a **distinct outcome from `ok=False`** and is what makes "403 means climb the
ladder, not reject the game" enforceable in code rather than in prose (§7 item 6).

### 18.2 Licence guards

The repo is MIT, so the ladder enforces licensing mechanically:

```python
FORBIDDEN_IN_SHIPPED_CODE = {"AGPL-3.0"}

class LicenceError(RuntimeError): ...

def assert_shippable(rungs: list[Rung]) -> None:
    """A default (non-opt-in) rung may never be AGPL."""
    for rung in rungs:
        if rung.opt_in:
            continue
        if rung.license in FORBIDDEN_IN_SHIPPED_CODE:
            raise LicenceError(
                f"{rung.name} is {rung.license}; it may only ship as an opt-in rung"
            )
```

Plus, per round 7 ("AGPL ok as an optional, documented rung"):

1. An AGPL rung is **never** imported by shipped code — invocation is via subprocess or an
   optional extra, and `import` of it must fail cleanly with an actionable message.
2. It is **never** in `requirements-dev.txt`, any workflow, or any dependency file.
3. `docs/runtime.md` states the implication in plain language: the user installs it themselves
   and, if they *distribute* a service built on it, AGPL obligations attach to them.
4. A unit test asserts that the default rung list contains zero AGPL entries, so a future edit
   cannot quietly promote `nodriver`.

### 18.3 Selection algorithm

Selection is **evidence-driven, cheapest-first, and sticky**:

```
for game in candidates_needing_render:
    hint = js_hint(game)              # True when SPA markers or prior runs needed JS
    for rung in ladder(enabled=True, host=game.host):
        if hint and not rung.requires_js and rung.kind == "local":
            continue                  # skip browserless rungs when JS is provably required
        result = rung.fetch(game.url)
        record(rung, result)          # -> rungs.json telemetry
        if result.ok:
            return result
        if result.blocked:
            continue                  # climb, never reject (spec rule 7.6)
        break                         # real error: no point trying stronger rungs
```

`host`-scoped overrides keep the ladder honest per domain:

| Host | Ladder | Rationale |
|---|---|---|
| `magenta.tv` | firecrawl → tinyfish → `curl_cffi` → `patchright` | SPA; JS required; `curl_cffi` only to detect gate posture |
| `championsleague.basketball` | hosted → `curl_cffi` | Server-rendered; no browser needed |
| `youtube.com` | none | Uses the YouTube API / live filter, never the render ladder |
| default | `curl_cffi` → hosted | Cheapest first (26/31 targets need no JS at all) |

### 18.4 Strike-halting interaction

`rungs.json` tracks consecutive failures per rung. Three consecutive failures parks a rung for
the rest of the run and records the reason — mirroring do-harness's strike-halting so a dead
backend does not burn the whole ladder on every candidate. Parked rungs are retried on the next
daily run, and a rung parked for N consecutive days raises an issue (the GitHub Models lesson:
backends die permanently).

**Implemented (2026-09-15).** `scripts/rung_health.py` is the writer this section needs:
`probe` climbs every host in one invocation with one shared tracker and appends
`rung-attempts.jsonl`; `snapshot` derives `rungs.json` from it; `parked --days 3` files one
deduped issue. Three decisions were settled while implementing it, and they narrow the promise
rather than widen it:

- **`skipped` is not a strike.** A rung with no key breaks a streak instead of extending it.
  Without that, every run in a keyless repository parks the hosted rungs and installing a key
  later starts from a fake history.
- **A gap breaks the streak, and the gap days are named.** A dead backend and a dead cron are
  otherwise the same report; the issue lists the missing days so the distinction is checkable.
- **Only *fileable* failures are filed**: a hosted rung failing (a rejected key is the
  retired-backend case) or a local rung that cannot connect at all. A local rung *refused* by a
  target is a statement about the target's WAF — the 403 rule 7.6 exists to climb past — so it
  is recorded in `rungs.json` and never filed.

A streak also ends at the rung's latest **observed** day, so a rung fixed today stops
re-reporting last week's streak and the issue can close.

### 18.5 Why this module is testable without network

Every rung implements `fetch` behind the same interface, so `tests/test_render_ladder.py` uses
fake rungs to assert: blocked-climbs-ladder, real-error-stops, opt-in-only-for-AGPL,
strike-halting after three failures, and host-scoped ordering — all offline.
