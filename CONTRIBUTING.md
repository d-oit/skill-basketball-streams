# Contributing

Frontmatter-driven ([Specification](https://agentskills.io/specification)) for finding and
validating free basketball live streams in Germany. See [`SKILL.md`](SKILL.md)
for the contract, [`README.md`](README.md) for the dataflow.

Commit prefix follows [Conventional Commits](https://www.conventionalcommits.org/):
`feat:` / `fix:` / `chore:` / `docs:` / `test:` / `refactor:` / `perf:` /
`build:` / `ci:` / `style:` / `revert:`.

## Quick start

1. Fork & clone.
2. Branch off `main`:
   ```bash
   git checkout main
   git pull origin main
   git checkout -b feat/<topic>
   ```
3. Edit files. Relevant places:
   - [`SKILL.md`](SKILL.md) — frontmatter + 7-step process.
   - [`references/`](references/) — domain references
     (approved sources, validation workflow, calendar setup,
     lessons-learned, implementation notes).
   - [`evals/evals.json`](evals/evals.json) — eval cases.
   - [`scripts/`](scripts/) — validators + maintenance doc.
   - [`tests/`](tests/) — pytest suite; fixtures and their rules live in
     [`tests/fixtures/README.md`](tests/fixtures/README.md).
   - [`docs/runtime.md`](docs/runtime.md) — the runtime operator guide.
   - [`.github/workflows/validate.yml`](.github/workflows/validate.yml) — CI.
4. Validate locally (every check must stay green):
   ```bash
   python3 scripts/validate.py --root . --check all
   python3 scripts/validate.py --root . --check smoke-test
   python3 scripts/runtime_eval.py --root .
   pip install -r requirements-dev.txt     # one-time
   pytest tests/                           # ~15s
   ```

   Or run all of it through the harness (`do-harness` on `PATH` or
   `DO_HARNESS_BIN` set — see [`AGENTS.md`](AGENTS.md)):
   ```bash
   do-harness verify --set verification --strict
   ```

   `do-harness eval` is **not** part of the product gate: it resolves skills
   only under `.agents/skills` and never sees the root `SKILL.md`. See
   [`docs/do-harness.md`](docs/do-harness.md) → Step 6 (withdrawn).
5. Commit + push:
   ```bash
   git add -A
   git commit -m "feat: <concise description>"
   git push origin feat/<topic>
   ```
6. Open a PR via `gh pr create --base main`. The CI matrix runs the
   validators on Python 3.9 and 3.12 (`validate.yml`), and the harness gate
   runs on 3.12 (`verify.yml`); wait for green. The maintainer then
   rebase-merges via `gh pr merge --rebase --admin` (admin-bypass;
   see [scripts/README.md → Releasing](scripts/README.md#releasing)).
   `delete_branch_on_merge: true` auto-cleans the feat branch.

## Schema for an eval case

Append to [`evals/evals.json`](evals/evals.json). Required by
`scripts/validate.py` (see `EVALS_REQUIRED_KEYS`):

| Field | Type | Required? | Notes |
| --- | --- | --- | --- |
| `id` | int | ✓ | Monotonically increasing, unique |
| `prompt` | str | ✓ | The skill's input narrative + URL parameters |
| `expected_output` | str | ✓ | Exact `Decision=...` line the skill should emit |
| `assertions` | list[str] | ✓ | At least one: `"<name> expected PASS\|FAIL"` |

Recommended but **not enforced**:

| Field | Type | Notes |
| --- | --- | --- |
| `files` | list[str] | Reference docs the agent should consult (signal only) |

Every assertion `"<name> expected PASS|FAIL"` must yield a needle
`"<name>=PASS|FAIL"` that appears in `expected_output` (typically inside
the `; checks: ...` block). The runtime runner parses the needle.

See [scripts/README.md → Adding a new eval case](scripts/README.md#adding-a-new-eval-case)
for a Python heredoc that edits the file safely.

## Releases

See [scripts/README.md → Releasing](scripts/README.md#releasing) for the
canonical workflow:

```
feature branch → PR → rebase-merge (`gh pr merge --rebase --admin`)
                → rehearsal (`python3 scripts/rehearse.py --env-file .env --require-all`)
                → annotated tag (`git tag -a v<X.Y.Z>`)
                → push tag (`git push origin v<X.Y.Z>`)
```

The rehearsal is the one step the harness cannot take. `do-harness verify` proves
the change; it says nothing about whether the credentials the runtime will use are
configured *and accepted* — sensors are offline and deterministic by design, and a
free provider having a bad day must not red the build. Release only once it reports
no `invalid` (a rejected credential) and no `missing` required one.

If your credentials are not already exported, put them in one gitignored file:
`cp .env.example .env` and fill it in. `--env-file` is explicit, never discovered,
and an already-set environment variable wins over the file. Then paste
`python3 scripts/rehearse.py --env-file .env --markdown` into the PR — a rehearsal
whose evidence is in the PR is reviewable, and one that is merely *reported* to have
run is not.

The rehearsal's offline half (`--list` + `--offline`) also runs in `validate.yml`,
on both 3.9 and 3.12, because it needs no credential; the live half deliberately
does not run anywhere unattended.

## Reporting broken calendar links

If a basketball calendar event lost its stream (404 / 500 / paid
promotion), open an issue with: event ID, broken stream URL, which
7-check failed, `sourceReference` URL, validation timestamp. Format
template lives in [`references/lessons-learned.md`](references/lessons-learned.md).

## License

MIT, see `SKILL.md` frontmatter.
