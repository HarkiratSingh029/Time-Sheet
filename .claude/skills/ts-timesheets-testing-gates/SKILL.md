---
name: ts-timesheets-testing-gates
description: The TS Timesheets quality gates and testing philosophy — pytest, ruff, the frontend checker and the brand build. Use before opening any PR, when adding tests, or when a gate fails.
---

# TS Timesheets — gates and testing

## The gates

```bash
pytest                               # backend behaviour
ruff check .                         # lint
ruff format --check .                # formatting
node scripts/check-frontend.mjs      # any change under frontend/
python3 brands/scripts/build_all.py  # any [Design] / brands change
./scripts/sync-agent-skills.sh       # after editing .claude/skills
```

All relevant gates green **and** scope complete → push and open the PR to `develop`
without asking. Any gate red → do not open the PR; say exactly what is failing.

## Testing philosophy — few, consolidated, meaningful

Tests exist to make the gates a real statement, not to inflate a count. On a 30-user
product, a slow suite costs more than it returns.

- **Test the workflow, not the getters.** One test that walks
  draft → submit → approve → appears in metrics is worth twenty per-function tests.
- **One test module per domain area**, not per source file: `tests/test_auth.py`,
  `tests/test_projects.py`, `tests/test_timesheet_workflow.py`,
  `tests/test_permissions.py`.
- **Always cover authorization.** Per-object access is the one thing a bug makes
  genuinely dangerous: a consultant must not read another project, an approver must not
  approve outside their queue.
- **Fixtures over factories over mocks.** Use a real SQLite database in a temp directory;
  it is fast and it exercises the code we ship.
- Test what the user can observe — HTTP status, response body, resulting state — not
  private helpers.
- No test needs the network. If something reaches outside the process, that boundary is
  what gets faked.

## What the frontend checker enforces

1. Every JS module parses.
2. No hard-coded colours anywhere in `frontend/` — tokens only.
3. Jinja block tags balance.
4. Page templates extend a layout (partials and layouts exempt).
5. `brands/dist/tokens.css` exists.

## What the brand build enforces

Palette parses, every required token exists in both themes, every colour is valid
`#RRGGBB`, and every declared contrast pair meets its WCAG ratio.
`--check` additionally fails when the committed `brands/dist/` is stale.

## When a gate fails

Read the actual failure before changing anything — these gates are specific on purpose.
Fix the cause; do not loosen the gate to make it pass. Loosening one is its own ticket
with its own argument.
