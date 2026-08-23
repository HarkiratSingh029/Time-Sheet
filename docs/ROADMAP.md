# Roadmap

Four epics. **EPIC 0** stands outside the main three: it is the initial running version of
the application — the smallest thing that is genuinely TS Timesheets rather than a
scaffold. EPICs 1–3 build the product out from there, each with **no more than seven
stories**.

| Epic | Theme | Issue | Status |
| --- | --- | --- | --- |
| **EPIC 0** | Foundation — a running timesheet | [#2](https://github.com/HarkiratSingh029/Time-Sheet/issues/2) | **Complete** |
| **EPIC 1** | Roles, permissions & the approval workflow | [#3](https://github.com/HarkiratSingh029/Time-Sheet/issues/3) | **Complete** |
| **EPIC 2** | Insight — dashboards, metrics & files | [#4](https://github.com/HarkiratSingh029/Time-Sheet/issues/4) | **In progress** |
| **EPIC 3** | Operations — hardening, deployment & CI | [#5](https://github.com/HarkiratSingh029/Time-Sheet/issues/5) | Planned |

Stories are promoted from a checklist on their epic to real issues when that epic starts —
an issue opened four months before anyone reads it is a stale issue. EPIC 0's stories
(#6–#12) and EPIC 1's (#21–#27) are merged; EPIC 2's are open as #38–#44. EPIC 3 remains a
checklist.

---

## EPIC 0 — Foundation

**Goal:** an administrator can log in, create a project, assign a consultant, and that
consultant can log time on a calendar and have it approved. Deployable with
`docker compose up`.

**Done.** The end-to-end path — login → create project → assign → log time → submit →
approve → see it approved — works in a fresh container, with green gates. Covered by
`tests/test_approvals.py::test_the_full_epic_0_path`.

One addition beyond the original seven stories: an administrator can add a person
(`/users/new`). EPIC 0 could not staff a project otherwise. The full invite, edit and
deactivate lifecycle remains story 1.2.

| # | Issue | Story | Why it is in EPIC 0 |
| --- | --- | --- | --- |
| 0.1 | [#6](https://github.com/HarkiratSingh029/Time-Sheet/issues/6) | Application skeleton, configuration and container | Nothing else can be verified without `pytest`, `ruff` and a launchable app. |
| 0.2 | [#7](https://github.com/HarkiratSingh029/Time-Sheet/issues/7) | Data layer: models, SQLite/WAL, seed | The objects in `docs/DATA_MODEL.md` made real. |
| 0.3 | [#8](https://github.com/HarkiratSingh029/Time-Sheet/issues/8) | Authentication, sessions and the bootstrap administrator | Every screen is behind a login; the admin is the first user. |
| 0.4 | [#9](https://github.com/HarkiratSingh029/Time-Sheet/issues/9) | UI shell, brand tokens and theme toggle | The design system enters the product once, at the shell, not per screen. |
| 0.5 | [#10](https://github.com/HarkiratSingh029/Time-Sheet/issues/10) | Projects, members and tasks | A time note has nowhere to go without a project. |
| 0.6 | [#11](https://github.com/HarkiratSingh029/Time-Sheet/issues/11) | Project calendar and time-note capture | The core interaction: double-click a date, log the day, submit. |
| 0.7 | [#12](https://github.com/HarkiratSingh029/Time-Sheet/issues/12) | Single-approval flow, end to end | Makes the workflow real; multi-approver chains wait for EPIC 1. |

**Explicitly not in EPIC 0:** user-defined roles, 2–5 approver chains, dashboards and
metrics, file uploads, exports, notifications, CI. Each has an epic of its own.

---

## EPIC 1 — Roles, permissions & the approval workflow

**Goal:** the workflow described in `docs/DATA_MODEL.md` §3, in full — user-defined roles,
up to five approvers per project, and a time-note lifecycle a team can actually operate.

**Done.** 1.5 was taken before 1.4 so the bulk approve actions were audited by construction
rather than retrofitted. Alembic (#21) and the Python 3.14 container image (#31) were pulled
forward from EPIC 3 along the way, each on its own ticket.

| # | Issue | Story |
| --- | --- | --- |
| 1.1 | [#21](https://github.com/HarkiratSingh029/Time-Sheet/issues/21) | Role and permission model — user-defined roles beyond `administrator` |
| 1.2 | [#22](https://github.com/HarkiratSingh029/Time-Sheet/issues/22) | Administrator user management: invite, edit, deactivate, assign roles |
| 1.3 | [#23](https://github.com/HarkiratSingh029/Time-Sheet/issues/23) | Per-project approval rules with 1–5 approvers, sequenced |
| 1.4 | [#24](https://github.com/HarkiratSingh029/Time-Sheet/issues/24) | Approver queue: filter, bulk approve, mandatory rejection comment |
| 1.5 | [#25](https://github.com/HarkiratSingh029/Time-Sheet/issues/25) | Time-note lifecycle: edit, withdraw, reopen, with an audit trail |
| 1.6 | [#26](https://github.com/HarkiratSingh029/Time-Sheet/issues/26) | Notifications: in-app badges and a daily email digest for pending approvals |
| 1.7 | [#27](https://github.com/HarkiratSingh029/Time-Sheet/issues/27) | Week view and bulk entry: copy last week, fill a range |

---

## EPIC 2 — Insight

**Goal:** project owners see billability and project health without asking anyone.

| # | Issue | Story |
| --- | --- | --- |
| 2.1 | [#38](https://github.com/HarkiratSingh029/Time-Sheet/issues/38) | Metrics engine: hours, billability, burn, derived health |
| 2.2 | [#39](https://github.com/HarkiratSingh029/Time-Sheet/issues/39) | Per-project dashboard: status, completion, consultants, timesheets, commercials |
| 2.3 | [#40](https://github.com/HarkiratSingh029/Time-Sheet/issues/40) | Global dashboard: upcoming deadlines, health distribution, utilisation |
| 2.4 | [#41](https://github.com/HarkiratSingh029/Time-Sheet/issues/41) | Project files: upload, list, download from the local volume |
| 2.5 | [#42](https://github.com/HarkiratSingh029/Time-Sheet/issues/42) | Exports: timesheet CSV and a per-project PDF report |
| 2.6 | [#43](https://github.com/HarkiratSingh029/Time-Sheet/issues/43) | Search and filtering across projects, users and time notes |
| 2.7 | [#44](https://github.com/HarkiratSingh029/Time-Sheet/issues/44) | Consultant self-view: my hours, my submissions, my approvals |

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
