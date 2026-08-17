---
name: ts-timesheets-architecture
description: TS Timesheets stack, domain objects, approval workflow and deployment shape — FastAPI, Jinja, SQLite, SAAS-in-a-box on one host. Use when adding a backend feature, touching the data model, choosing a dependency, or working on deployment.
---

# TS Timesheets — architecture

Full text: `docs/ARCHITECTURE.md` and `docs/DATA_MODEL.md`.

## The constraint that decides everything

**~30 concurrent users, one small VPC host.** Simplicity is the ultimate sophistication;
small, quick and powerful like a honey badger. Architecture that would be right at 30,000
users is wrong here.

## Stack

| Layer | Choice |
| --- | --- |
| API + pages | FastAPI on Uvicorn — one process serves JSON and HTML |
| Templates | Jinja2, server-rendered, no bundler |
| Client JS | Vanilla ES modules from `frontend/static/` |
| Data | SQLite (WAL) through SQLAlchemy — Postgres stays a config change |
| Validation | Pydantic v2 |
| Files | Local volume `data/uploads/` — **no S3** |
| Auth | Server-side sessions, signed cookies |

**Deliberately absent:** S3, Redis, Celery, Kubernetes, microservices, SPA frameworks,
any bundler in the runtime image. Adding one of these needs a measured limit, not a
preference.

## Deployment

One `docker compose up`: the app container plus an optional `cloudflared` container that
is **on by default**. No inbound ports on the host — the tunnel is the only door. `data/`
is the single stateful directory and the single backup target.

Launchers carry a fresh flag: `./scripts/dev.sh --fresh`,
`docker compose down -v && docker compose up --build`.

## Domain objects

`User` (with role, skills, resume summary, tenure) · `Role` · `Project` (owner, manager,
status, cost, budgets, dates, `required_approvals` 1–5) · `Task` (always belongs to a
project) · `TimeNote` (one user, one project, one day) · `Approval` (one approver's
decision, sequenced 1–5) · `ProjectFile`.

## The workflow

```
draft ──submit──► submitted ──all required approvals approved──► approved
  ▲                    │
  └────reject──────────┘
```

Access to the project comes first — the calendar is per project, bounded by project dates
and the billable-hour budget. A consultant double-clicks a date, logs duration and detail,
submits; the project's rules generate 1–5 approval rows; owners read the metrics.

## Rules for backend work

- **Authorization is per object, not per page.** A consultant sees only their assigned
  projects; an approver only their queue; the administrator everything.
- Durations are stored in **minutes**, entered in hours.
- A time note's `work_date` must fall inside the project window.
- Configuration is environment variables only, documented in `.env.example`. Fail loudly
  at startup on a missing required variable rather than running on a silent default.
- Nothing stateful outside `data/`.
- Health (green/amber/red) is **derived**, never entered.

## Tests

Few and consolidated (`ts-timesheets-testing-gates`). A 30-user product does not earn a
slow suite.
