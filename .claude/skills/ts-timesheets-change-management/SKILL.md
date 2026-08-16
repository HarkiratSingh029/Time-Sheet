---
name: ts-timesheets-change-management
description: The branch, issue, commit and PR workflow for the TS Timesheets repo. Use when starting any change, creating a branch or issue, deciding feature vs fix vs design, committing, or opening a PR to develop or staging.
---

# TS Timesheets — change management

Canonical reference: `CHANGE_MANAGEMENT.md`. This skill is the operating summary.

## Before touching anything

```bash
git fetch origin
```

Decide the ticket type:

| Type | When |
| --- | --- |
| `feature/` `[Feature]` | Adds a capability |
| `fix/` `[Fix]` | Corrects defective behaviour |
| `design/` `[Design]` | Changes `brands/`, `BRAND_GUIDELINES.md` or the skills — **never** application source |

Next issue number: `max(remote feature/fix/design branch numbers, latest GitHub issue) + 1`.

```bash
gh issue list --state all --limit 1 --json number --jq '.[0].number'
git ls-remote --heads origin | grep -oE '(feature|fix|design)/[0-9]+' | grep -oE '[0-9]+' | sort -n | tail -1
```

## The flow

1. **Open the issue first** — the number is the branch name.
   Use the `ts-timesheets-ticket-writing` skill for the body and labels.
2. **Branch from `origin/develop`. Never from `main`.**
   ```bash
   git checkout -b feature/<N>_<snake_case> origin/develop
   ```
3. **Commit** referencing the issue: `#<N>: <imperative summary>`.
   Keep the diff inside the issue's scope. One issue, one branch, one PR.
4. **Update the docs the change touches** — `README.md`, the EPIC checklist,
   `docs/ROADMAP.md`.
5. **Run the gates** (`ts-timesheets-testing-gates` skill).
6. **Green and complete → push and open the PR to `develop` without asking.**
   ```bash
   git push -u origin feature/<N>_<snake_case>
   gh pr create --base develop --title "[Feature] #<N>: <description>" \
     --body-file pr.md --label "<the issue's labels>"
   ```
   The PR body links the issue with `Closes #<N>`, plus summary and test plan.
7. **Red gate or unfinished scope → do not open the PR.** Say exactly what is failing
   and stop.

Then stop. Merging, tagging and the `develop` → `staging` promotion stay explicit.

## Staging promotion — only on request

```bash
git log origin/staging..origin/develop --oneline
gh pr create --base staging --head develop --title "Release: <date>" --label release:next
```

Body must carry release notes grouped under Features / Fixes / Chores, plus screenshots
for UI-visible changes.

## Pitfalls

- Don't branch from `main`.
- Don't bundle unrelated changes into one branch.
- Don't let a `[Design]` ticket touch `backend/`, `frontend/`, `scripts/` or `tests/`.
- Don't open a PR over red gates — the automation only means "ready for review" if that
  is always true.
- Don't merge, tag or promote without being asked.
