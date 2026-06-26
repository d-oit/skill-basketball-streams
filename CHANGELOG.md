# Changelog

All notable changes to `skill-basketball-streams` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
