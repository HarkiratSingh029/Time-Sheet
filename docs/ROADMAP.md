# Roadmap

Four epics. **EPIC 0** stands outside the main three: it is the initial running version of
the application — the smallest thing that is genuinely TS Timesheets rather than a
scaffold. EPICs 1–3 build the product out from there, each with **no more than seven
stories**.

| Epic | Theme | Issue | Status |
| --- | --- | --- | --- |
| **EPIC 0** | Foundation — a running timesheet | _#TBD_ | Planned |
| **EPIC 1** | Roles, permissions & the approval workflow | _#TBD_ | Planned |
| **EPIC 2** | Insight — dashboards, metrics & files | _#TBD_ | Planned |
| **EPIC 3** | Operations — hardening, deployment & CI | _#TBD_ | Planned |

Stories for EPIC 0 exist as issues now, because it is next. Stories for EPICs 1–3 live as
checklists on their epic issue and are promoted to issues when that epic starts — an issue
opened four months before anyone reads it is a stale issue.

---

## EPIC 0 — Foundation

**Goal:** an administrator can log in, create a project, assign a consultant, and that
consultant can log time on a calendar and have it approved. Deployable with
`docker compose up`.

**Done when** the end-to-end path — login → create project → assign → log time → submit →
approve → see it approved — works in a fresh container, with green gates.

| # | Story | Why it is in EPIC 0 |
| --- | --- | --- |
| 0.1 | Application skeleton, configuration and gates | Nothing else can be verified without `pytest`, `ruff` and a launchable app. |
| 0.2 | Data layer: models, SQLite/WAL, seed | The objects in `docs/DATA_MODEL.md` made real. |
| 0.3 | Authentication, sessions and the bootstrap administrator | Every screen is behind a login; the admin is the first user. |
| 0.4 | UI shell, brand tokens and theme toggle | The design system enters the product once, at the shell, not per screen. |
| 0.5 | Projects, members and tasks | A time note has nowhere to go without a project. |
| 0.6 | Project calendar and time-note capture | The core interaction: double-click a date, log the day, submit. |
| 0.7 | Single-approval flow, end to end | Makes the workflow real; multi-approver chains wait for EPIC 1. |

**Explicitly not in EPIC 0:** user-defined roles, 2–5 approver chains, dashboards and
metrics, file uploads, exports, notifications, CI. Each has an epic of its own.

---

## EPIC 1 — Roles, permissions & the approval workflow

**Goal:** the workflow described in `docs/DATA_MODEL.md` §3, in full — user-defined roles,
up to five approvers per project, and a time-note lifecycle a team can actually operate.

| # | Story |
| --- | --- |
| 1.1 | Role and permission model — user-defined roles beyond `administrator` |
| 1.2 | Administrator user management: invite, edit, deactivate, assign roles |
| 1.3 | Per-project approval rules with 1–5 approvers, sequenced |
| 1.4 | Approver queue: filter, bulk approve, mandatory rejection comment |
| 1.5 | Time-note lifecycle: edit, withdraw, reopen, with an audit trail |
| 1.6 | Notifications: in-app badges and a daily email digest for pending approvals |
| 1.7 | Week view and bulk entry: copy last week, fill a range |

---

## EPIC 2 — Insight

**Goal:** project owners see billability and project health without asking anyone.

| # | Story |
| --- | --- |
| 2.1 | Metrics engine: hours, billability, burn, derived health |
| 2.2 | Per-project dashboard: status, completion, consultants, timesheets, commercials |
| 2.3 | Global dashboard: upcoming deadlines, health distribution, utilisation |
| 2.4 | Project files: upload, list, download from the local volume |
| 2.5 | Exports: timesheet CSV and a per-project PDF report |
| 2.6 | Search and filtering across projects, users and time notes |
| 2.7 | Consultant self-view: my hours, my submissions, my approvals |

---

## EPIC 3 — Operations

**Goal:** running this on a small VPC is boring, which is the highest compliment a
deployment can receive.

| # | Story |
| --- | --- |
| 3.1 | Production compose and Cloudflare Tunnel, on by default |
| 3.2 | Backup and restore of `data/` — one command each way |
| 3.3 | Observability: structured logs, health and readiness endpoints, real error pages |
| 3.4 | Security hardening: CSRF, rate limiting, session policy, password reset |
| 3.5 | CI on GitHub Actions running every gate on each PR |
| 3.6 | Performance pass: indexes, query review, calendar and dashboard load times |
| 3.7 | Admin console: configuration, licence details, audit log viewer |

---

## Sequencing

```
EPIC 0 ──► EPIC 1 ──► EPIC 2
   │                    │
   └────────────────────┴──► EPIC 3
```

EPIC 3 stories may be pulled forward when they unblock something: CI (3.1 aside, 3.5) is
worth doing early, and security hardening lands before anything is exposed through a
tunnel to the public internet.
