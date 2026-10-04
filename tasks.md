# Task board

Source of truth: `.do-harness/agent_state.db`, owned by do-harness and
gitignored — the database is authoritative and `tasks.md` is only its readable
mirror. This file is derived from the committed export `plans/tasks.json`, which
`do-harness task export` refreshes; `do-harness task import` validates that
export back against the database.

To update the board: run `do-harness task export`, then edit only the table and
the `Totals:` line below to match. `scripts/check_product_contract.py` (the
`product-contract` sensor) fails if any id, title or status disagrees with
`plans/tasks.json`, or if the totals disagree with the export's `summary`.

Totals: pending=2 in_progress=0 done=1 failed=2

| id | title | status |
| --- | --- | --- |
| 1 | Phase 5 fixture parsing | pending |
| 2 | Add an iCalendar (RFC 5545) parser so fixture ground truth can come from a feed instead of a page | pending |
| 3 | schema probe | failed |
| 4 | schema probe | failed |
| 5 | Discover the official BBL iCalendar feed URL and pin it so BBL contributes fixtures | done |
