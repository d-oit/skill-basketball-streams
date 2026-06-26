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
   - [`.github/workflows/validate.yml`](.github/workflows/validate.yml) — CI.
4. Validate locally (every check must stay green):
   ```bash
   python3 scripts/validate.py --root . --check all
   python3 scripts/validate.py --root . --check smoke-test
   python3 scripts/runtime_eval.py --root .
   # Optional full pytest suite (~1s once installed):
   pip install -r requirements-dev.txt && pytest tests/
   ```
5. Commit + push:
   ```bash
   git add -A
   git commit -m "feat: <concise description>"
   git push origin feat/<topic>
   ```
6. Open a PR via `gh pr create --base main`. The CI matrix runs the
   validators on Python 3.8–3.12; wait for green. The maintainer then
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
                → annotated tag (`git tag -a v<X.Y.Z>`)
                → push tag (`git push origin v<X.Y.Z>`)
```

## Reporting broken calendar links

If a basketball calendar event lost its stream (404 / 500 / paid
promotion), open an issue with: event ID, broken stream URL, which
7-check failed, `sourceReference` URL, validation timestamp. Format
template lives in [`references/lessons-learned.md`](references/lessons-learned.md).

## License

MIT, see `SKILL.md` frontmatter.
