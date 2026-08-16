# TS Timesheets — working agreement

A timesheet platform: consultants log time against projects, approvers sign it off,
project owners track billability and project health. ~30 concurrent users, one small
host, `docker compose up`.

## Read these before changing anything

| Document | What it governs |
| --- | --- |
| `CHANGE_MANAGEMENT.md` | Branches, issues, commits, PRs, labels, gates |
| `BRAND_GUIDELINES.md` | Palette, typography, components, accessibility |
| `docs/ARCHITECTURE.md` | Stack, deployment, what we deliberately don't build |
| `docs/DATA_MODEL.md` | Objects, relationships, the approval workflow |
| `docs/ROADMAP.md` | EPIC 0 plus three epics, and what is in each |

Skills in `.claude/skills/` carry the operating detail: `ts-timesheets-change-management`,
`ts-timesheets-ticket-writing`, `ts-timesheets-design-system`,
`ts-timesheets-architecture`, `ts-timesheets-testing-gates`.

## Non-negotiables

- **Branch from `develop`, never from `main`.** One issue → one branch → one PR, numbers matching.
- **Labels at issue creation**, not after. `type:` and `priority:` are mandatory.
- **No hard-coded colours** in `frontend/` or `backend/` — `var(--color-*)` only.
- **A `[Design]` ticket never touches `backend/`, `frontend/`, `scripts/` or `tests/`.**
- **Gates green and scope complete → push and open the PR to `develop` without asking.**
  Red gate or partial scope → say what is failing and stop.
- **Merging, tagging and the `develop` → `staging` promotion stay explicit.** Never automatic.
- **Nothing stateful outside `data/`.** No S3, no Redis, no broker, no bundler.
- **Few, consolidated tests.** Test the workflow and authorization, not getters.

## Gates

```bash
pytest
ruff check . && ruff format --check .
node scripts/check-frontend.mjs        # frontend changes
python3 brands/scripts/build_all.py    # brands changes
./scripts/sync-agent-skills.sh         # after editing .claude/skills
```

## Local development

```bash
./scripts/dev.sh            # http://127.0.0.1:8000
./scripts/dev.sh --fresh    # wipe data/, reseed, run
```
