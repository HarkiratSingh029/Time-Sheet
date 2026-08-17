---
name: ts-timesheets-ticket-writing
description: How to write TS Timesheets GitHub issues, epics and PR bodies, and which labels to apply. Use when creating or updating any issue, epic or pull request in this repo.
---

# TS Timesheets — writing tickets

## The body

**Under 300 words. Bullets, not prose.** Comprehensive means every field is answered,
not that the ticket is long.

```markdown
## Context / Why
- The user-facing reason this exists.
- Which files change, how, and why that is the right move.

## Scope
- Specifically what this change does.

## Out of scope
- What is intentionally excluded, and effort deliberately phased out.

## Acceptance criteria
- [ ] Verifiable statement, naming the file to check.
- [ ] `pytest`, `ruff check .` green.
- [ ] `node scripts/check-frontend.mjs` green (frontend changes).
```

For a **bug**, add: steps to reproduce, expected vs. actual, and urgency.

For a **`[Design]` ticket**, add:
- **Precedence check** — does this conflict with a generic or vendored design skill?
  `BRAND_GUIDELINES.md` wins on TS Timesheets surfaces; record the decision.
- **What stays locked** — semantic colour meanings, the light/dark contract, product
  naming. Confirm untouched, or explain why the ticket is really about something else.
- Gates are `python3 brands/scripts/build_all.py` plus an empty
  `git diff --stat origin/develop -- backend frontend scripts tests`.

## Titles

`[Feature] #<N>: <description>` · `[Fix] #<N>: <description>` · `[Design] #<N>: <description>`

The prefix matches the branch prefix. The PR title mirrors the issue title.

## Epics

An epic issue is `type:epic`, carries the goal, the "done when", a **checklist of its
stories** and an explicit "not in this epic" section. No epic exceeds **seven stories**.
Story issues are opened when the epic starts, not months ahead — link them back to the
epic and tick the checklist as they land.

## Labels — applied at creation, never after

Mandatory: one `type:` and one `priority:`. Add `area:`, `tech:`, `platform:`, `build:`
and `release:` where they apply.

```bash
gh issue create --title "[Feature] #<N>: <description>" --body-file issue.md \
  --label "type:feature,area:timesheet,tech:fastapi,platform:server,priority:p2-medium"
```

Full taxonomy: `CHANGE_MANAGEMENT.md` §5. Provision with `./scripts/bootstrap-labels.sh`.

## PR bodies

```markdown
Closes #<N>

## Summary
- What changed, in the reviewer's terms.

## Test plan
- pytest — <result>
- ruff check . — <result>
- node scripts/check-frontend.mjs — <result>
- Manual: <the path a human should click>
```

Copy the issue's labels onto the PR.
