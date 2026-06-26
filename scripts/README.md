# `scripts/`

Maintenance scripts for the `skill-basketball-streams` project. Stdlib-only
on Python 3.8+; the only external dependency lives in `requirements-dev.txt`
for the test suite (`pytest>=9.0`).

## Contents

| Script | Purpose |
|---|---|
| [`validate.py`](validate.py) | Schema + reference validator. Three checks: `evals` (evals/evals.json conforms to v1.0.0 schema), `skill` (SKILL.md frontmatter + body ≤250 lines + mandatory sections), `references` (every backtick-wrapped `.md` path in SKILL.md / README.md resolves). Plus a `smoke-test` self-test that re-invokes itself against three target-fixture repos to confirm every FAIL branch correctly rejects. Wire into CI. |
| [`runtime_eval.py`](runtime_eval.py) | Scaffold runtime skill-evaluator. Two modes: **structural pass** (`--root .`, walks every case and reports coverage) and **runtime pass** (`--stubs stubs.json`, asserts every case's `expected_output` + `assertions[]` against canned stub outputs). Operates on canned strings only — does NOT invoke any real skill. |

### Tests

Pytest suite lives under [`../tests/`](../tests/):

- `../tests/test_validate.py` — covers `validate.py` (per-check PASS + FAIL
  branches via tmp fixtures + CLI / USAGE contracts).
- `../tests/test_runtime_eval.py` — covers `runtime_eval.py` (structural
  + runtime + `.strip()` normalization + non-string stub rejection +
  missing-stub detection + malformed JSON + non-coercible int keys).

Run from project root:

```bash
pip install -r requirements-dev.txt   # one-time
pytest tests/                         # ~1s
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

All three commands run on Python `[3.8, 3.9, 3.10, 3.11, 3.12]` via
`strategy.matrix.python-version` (5 entries × `fail-fast: false`).

If you add a fourth step, append it under the existing three (do NOT
replace them) and keep the matrix intact.

## Releasing

**Workflow**: feature branch → PR → rebase-merge → annotated tag → tag push.

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
