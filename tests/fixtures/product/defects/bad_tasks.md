# Task board

Source of truth: `.do-harness/agent_state.db` (harness-owned, gitignored). This
file mirrors the committed export `plans/tasks.json`, which is refreshed with
`do-harness task export`. `scripts/check_product_contract.py` fails if the two
disagree, so edit neither by hand without re-exporting first.

Totals: pending=1 in_progress=0 done=1 failed=0

| id | title | status |
| --- | --- | --- |
| 1 | Phase 5 fixture parsing | done |
| 2 | Add an iCalendar (RFC 5545) parser so fixture ground truth can come from a feed instead of a page | done |
