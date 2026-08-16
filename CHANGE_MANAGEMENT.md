# Change Management — TS Timesheets

How work enters this repository. Every non-trivial change follows it end to end.

Remote: `origin` → <https://github.com/HarkiratSingh029/Time-Sheet.git>

---

## 1. Repository topology

| Branch | Purpose |
| --- | --- |
| `release` | Released code, deployed to the production host. |
| `main` | Production-ready code. Tagged releases land here. |
| `staging` | Pre-release validation. Promoted from `develop`. |
| `develop` | Integration branch. **All feature/fix/design branches start here.** |
| `feature/<N>_<desc>` | Single-purpose feature branch. |
| `fix/<N>_<desc>` | Single-purpose bug-fix branch. |
| `design/<N>_<desc>` | Brand-toolkit branch — changes `brands/` and the guidelines, never application source (§7). |

---

## 2. The end-to-end flow

### a. Fetch latest

```bash
git fetch origin
```

### b. Branch from `origin/develop`

```bash
git checkout -b feature/<N>_<snake_case_description> origin/develop
# or fix/<N>_… , or design/<N>_…
```

`<N>` is the GitHub issue number. The description is short, lowercase and
underscore-separated: `feature/24_project_calendar`, `fix/25_login_crash_on_empty_email`,
`design/26_dark_mode_tokens`.

### c. Open the GitHub issue

- Title: **`[Feature] #<N>: <description>`**, **`[Fix] #<N>: <description>`** or
  **`[Design] #<N>: <description>`**. The prefix must match the branch prefix.
- Body follows §4. Labels from §5 are applied **at creation**, not later.

### d. Make the change

- Keep the diff inside the issue's stated scope.
- Update any affected `README.md`.
- Commit messages reference the issue: `#24: add project calendar grid`.
- Update the issue, its EPIC checklist and `docs/ROADMAP.md` as the work lands.

### e. Push the branch — automatic

```bash
git push -u origin feature/<N>_<...>
```

### f. Open a PR to `develop` — automatic

- Title mirrors the issue title.
- Body links the issue with `Closes #<N>`, plus a summary and a test plan.
- Copy the issue's labels onto the PR.

**Steps e and f close the flow; they are not a separate request.** When the work is
complete, push and open the PR without asking. The PR targets `develop`, never `main`.

**"Complete" means both of these:**

1. The issue's scope is done — not a subset, no acceptance criterion quietly dropped.
2. The gates pass: `pytest`, `ruff check .`, and `node scripts/check-frontend.mjs` for
   any frontend change.

If scope is unfinished or a gate is red, **do not open the PR**. Say what is failing and
stop. Deliberately partial work (a staged epic, a spike) says so instead of opening a PR
that claims readiness.

### g. Releasing: PR `develop` → `staging`

- Title: `Release: <date or version>`.
- Body **must contain release notes** for everything since the last promotion, grouped
  under Features / Fixes / Chores.
- Attach screenshots for UI-visible changes.

**Only on request.** Never automatic.

---

## 3. Naming conventions at a glance

| Artifact | Convention | Example |
| --- | --- | --- |
| Feature issue | `[Feature] #<N>: <description>` | `[Feature] #24: Project calendar grid` |
| Fix issue | `[Fix] #<N>: <description>` | `[Fix] #25: Login crash on empty email` |
| Design issue | `[Design] #<N>: <description>` | `[Design] #26: Dark mode token set` |
| Feature branch | `feature/<N>_<snake_case>` | `feature/24_project_calendar_grid` |
| Fix branch | `fix/<N>_<snake_case>` | `fix/25_login_crash_on_empty_email` |
| Design branch | `design/<N>_<snake_case>` | `design/26_dark_mode_tokens` |
| Commit | `#<N>: <imperative summary>` | `#24: render month grid from project dates` |
| PR to develop | Same as the issue title | `[Feature] #24: Project calendar grid` |
| PR to staging | `Release: <date or version>` | `Release: 2026-08-30` |

---

## 4. Standards every change must meet

### 4.1 The issue body

Comprehensive but **under 300 words**, in bullets. Clear, concise, valuable:

- **Context / Why** — the user-facing reason. Which files change, how, and why that is
  the right move.
- **Scope** — what this change does, specifically.
- **Out of scope** — what is intentionally excluded, including effort deemed too
  expensive to be worth it now.
- **Acceptance criteria** — a checkbox list a reviewer can verify, naming the files to
  check and the verification method.
- For bugs: **steps to reproduce**, **expected vs. actual**, and **urgency**.

### 4.2 READMEs are part of the change

If a change touches setup, tooling, public APIs or a significant feature, update the
relevant `README.md`. These documents are optimized for a human reader, not for
completeness.

### 4.3 Avoid excessive comments

Prefer expressive identifiers and small, well-named functions. Comment only when the
*why* is non-obvious: hidden constraints, workarounds, surprising invariants. Narrative
belongs in the PR description.

### 4.4 Release notes for staging promotions

See §2.g.

### 4.5 A well-done `.gitignore`

Runtime state (`data/`, uploads, SQLite files), secrets (`.env`) and generated artifacts
(`brands/dist/`) never enter git. `.env.example` is tracked as the template.

### 4.6 Design tickets change the brand, not the app

See §7.

### 4.7 Few, consolidated tests

Tests exist to make the gates a real statement, not to inflate a count. Prefer a small
number of high-value tests over exhaustive per-function coverage — long build times cost
more than they return on a 30-user application.

---

## 5. Standard labels

Applied **at creation time**. A typical issue carries one label from each relevant
category: **type** (mandatory), **priority** (mandatory), plus **area**, **tech**,
**platform**, **build** and **release** where relevant.

### 5.1 Universal

| Category | Prefix | Values |
| --- | --- | --- |
| Type | `type:` | `feature`, `fix`, `design`, `chore`, `docs`, `refactor`, `perf`, `test`, `security`, `epic` |
| Priority | `priority:` | `p0-blocker`, `p1-high`, `p2-medium`, `p3-low` |
| Status | `status:` | `triage`, `in-progress`, `blocked`, `needs-review`, `needs-qa` |
| Release | `release:` | `next`, `backlog`, `hotfix` |
| Platform | `platform:` | `server`, `web`, `cli` |

### 5.2 This repo — Python / FastAPI backend, `platform:server`

| Category | Values |
| --- | --- |
| `tech:` | `python`, `fastapi`, `pydantic`, `sqlalchemy`, `jinja`, `javascript`, `css` |
| `area:` | `api`, `auth`, `db`, `ui`, `timesheet`, `projects`, `approvals`, `dashboard`, `storage`, `scripts`, `deploy` |
| `build:` | `pip`, `docker`, `ci-actions`, `ruff`, `pytest` |

### 5.3 Applying labels with `gh`

```bash
gh issue create \
  --title "[Feature] #<N>: <description>" \
  --body-file issue.md \
  --label "type:feature,area:api,tech:fastapi,platform:server,priority:p2-medium"
```

Labels are provisioned once by `scripts/bootstrap-labels.sh`.

---

## 6. Shortcuts you may say directly

| Shortcut | What happens |
| --- | --- |
| **"Create an issue and branch for feature/fix/design X"** | §2.a–§2.c: fetch, open the labelled issue, branch from `develop`, then stop. |
| **"Create a PR for merging feature X to develop"** | §2.e–§2.f. Redundant once work is complete (the PR is automatic) — still useful to open a PR early on deliberately partial work. |
| **"Create a PR for staging"** | §2.g. Populate release notes from `git log origin/staging..origin/develop`, attach screenshots, label `release:next`. **Only on request.** |

---

## 7. The Design ticket type

A **Design ticket** lets the brand system evolve without dragging an application change
along with it. It updates the **brand toolkit** and does **not** touch application source.

**In scope**

- `brands/` — tokens, palette, generated CSS, icons, research
- `BRAND_GUIDELINES.md`
- The affected skill checkpoints (`.claude/skills/ts-timesheets-*`, mirrored to
  `.agents/skills/` by `scripts/sync-agent-skills.sh`)
- `CHANGE_MANAGEMENT.md`, when the change is to the design process itself

**Out of scope — this makes it a Feature ticket instead**

- Anything under `backend/`, `frontend/`, `scripts/` or `tests/`
- Any CSS, JS or HTML the product actually serves

Adopting a new token *in the product* is a Feature ticket that references the Design
ticket that introduced it. Keeping these apart is what lets a brand change be reviewed on
whether it is right, rather than on whether it broke a test.

**Gates for a Design ticket** — `pytest` and `ruff check` are not the relevant gates when
no application code changed. These are:

```bash
python3 brands/scripts/build_all.py                                  # validates tokens, rebuilds every artifact
git diff --stat origin/develop -- backend frontend scripts tests     # must be empty
```

**Body requirements** — the §4.1 fields plus:

- **Precedence check.** Does the change conflict with a vendored or generic design skill?
  State which rule wins and why. `BRAND_GUIDELINES.md` always wins on TS Timesheets
  surfaces; the point is to record the decision so a future session does not re-litigate it.
- **What stays locked.** The approval-state color semantics (approved Mint, rejected Red,
  pending Amber), the light/dark palette contract, and the product naming in
  `BRAND_GUIDELINES.md` §1 are not negotiable. Confirm the change leaves them intact, or
  explain why the ticket is really about something else.

---

## 8. Quick agent checklist

- [ ] `git fetch origin`.
- [ ] Decide `feature/` vs `fix/` vs `design/` (§7).
- [ ] Determine the next issue number: `max(remote branch numbers, latest GitHub issue) + 1`.
- [ ] Open the labelled issue with the §4.1 body.
- [ ] Branch from `origin/develop`.
- [ ] Focused commits referencing `#<N>`.
- [ ] Update the relevant `README.md`, the EPIC checklist and `docs/ROADMAP.md`.
- [ ] Run the gates: `pytest`, `ruff check .`, `node scripts/check-frontend.mjs`. For a
      Design ticket, `python3 brands/scripts/build_all.py` and confirm no application
      source changed.
- [ ] Green and complete → push the branch and open the PR to `develop`, copying labels.
- [ ] Red or unfinished → don't open the PR; report what is failing.
- [ ] Stop there. Merging, tagging and the `develop` → `staging` promotion stay explicit.

---

## 9. Common pitfalls

- **Don't branch from `main`.** Always from `develop`.
- **Don't bundle unrelated changes.** Each `#<N>` is its own branch, issue and PR.
- **Don't let a Design ticket touch application source.** That is a second, Feature ticket.
- **Don't write excessive comments.**
- **Don't forget README updates** when the surface area changes.
- **Don't open the staging PR without release notes** and, where relevant, screenshots.
- **Don't open a PR over red gates or unfinished scope.** The PR is automatic *because*
  it means "ready for review".
- **Don't let "the PR is automatic" creep into merging it.** Merging, tagging and staging
  promotion remain explicit.
