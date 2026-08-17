# Data model

The objects the product is made of, and the workflow that connects them.

---

## 1. Objects

### User

A person with access to the platform.

| Field | Notes |
| --- | --- |
| `id`, `email`, `password_hash`, `is_active` | Identity and login. |
| `full_name`, `title` | Display. |
| `role_id` | The role that decides what they may see and do. |
| `skills` | Free-form skill list — what this consultant can be staffed on. |
| `resume_summary` | Short professional brief. |
| `tenure_started_on` | Start date, for tenure reporting. |

The **administrator** is the license holder and default super user: unrestricted CRUD on
every object. Administrators add users and assign their roles.

### Role

Named permission set. At launch there is exactly one role — `administrator` — and roles
are user-definable from there. A role decides access to timesheet objects and a user's
place in the approval workflow.

### Project

| Field | Notes |
| --- | --- |
| `id`, `name`, `code`, `description` | Identity. |
| `owner_id`, `manager_id` | The owner reads the metrics; the manager runs the work. |
| `status` | `planned`, `active`, `on_hold`, `completed`, `cancelled`. |
| `cost`, `billable_hours_budget` | Commercials and the hour budget the calendar is bounded by. |
| `proposed_duration_days`, `current_duration_days` | Planned vs. actual. |
| `start_date`, `end_date` | Bounds the project calendar. |
| `comment` | Owner's running note. |
| `required_approvals` | 1–5. How many approvals a time note needs on this project. |

A project owns its **tasks**, its **calendar**, its **approval rules** and its **files**.

### Task

A unit of work a consultant may log against. Tasks belong to a project — there is no such
thing as a task without one.

| Field | Notes |
| --- | --- |
| `id`, `project_id`, `name`, `description` | Identity. |
| `is_billable` | Whether logged time counts toward billability. |

### Time note

A consultant's entry for one day on one project. This is the unit the product exists to
capture.

| Field | Notes |
| --- | --- |
| `id`, `project_id`, `task_id`, `user_id` | Who logged what, where. |
| `work_date` | The calendar day. Must fall inside the project window. |
| `duration_minutes` | Stored in minutes; entered in hours. |
| `detail` | What was actually done. |
| `state` | `draft`, `submitted`, `approved`, `rejected`. |

### Approval

One approver's decision on one time note. A project needing three approvals produces
three approval rows.

| Field | Notes |
| --- | --- |
| `id`, `time_note_id`, `approver_id`, `sequence` | Which approval slot this is (1–5). |
| `decision` | `pending`, `approved`, `rejected`. |
| `decided_at`, `comment` | Audit trail; a rejection requires a comment. |

### Project file

An attachment on a project — a statement of work, a signed timesheet export, a report.
Stored on the local volume under `data/uploads/<project_id>/`, with metadata in the
database.

---

## 2. Relationships

```
Role ──< User ──< TimeNote >── Task >── Project >── ProjectFile
                     │                     │
                     └──< Approval         └──< ProjectMember (user ↔ project, with role)
```

- A user has one role; a role has many users.
- A user is assigned to many projects through `ProjectMember`; a project has many members.
- A project has many tasks; a task belongs to exactly one project.
- A time note belongs to one project, one task and one user.
- A time note has 1–5 approvals, determined by the project's `required_approvals`.

---

## 3. The workflow

1. A consultant opens a project they are assigned to. **Access to the project comes
   first** — the calendar is per project, bounded by the project's dates and its billable
   hour budget.
2. They **double-click a date** on the calendar. A popup captures task, duration and
   detail. The note is created in `draft`.
3. They **submit** the day, or a range of days. State becomes `submitted` and the
   project's approval rules generate the approval rows.
4. Each **approver** sees a queue of submitted notes and approves or rejects. A rejection
   carries a comment and returns the note to `draft` for correction.
5. When every required approval is `approved`, the note becomes `approved` and counts
   toward billability.
6. **Project owners** read the dashboard: their own projects in detail, and every project
   in aggregate.

### State machine

```
draft ──submit──► submitted ──all approvals approved──► approved
  ▲                    │
  └────reject──────────┘
```

`approved` is terminal for a consultant; only an administrator may reopen one, and the
reopen is recorded.

---

## 4. Metrics

**Per project** — status, completion against `proposed_duration_days`, hours logged vs.
`billable_hours_budget`, approved vs. unapproved hours, consultants staffed, cost burn,
start/end dates, owner, manager, comment, attached files.

**Global** — every project owner sees every project:

- Upcoming deadlines, nearest first.
- Health distribution: green / amber / red.
- Unapproved hours sitting in the queue, and how long they have waited.
- Utilisation across consultants.

Health is derived, not entered:

| Health | Condition |
| --- | --- |
| 🟢 Green | On or ahead of schedule, and hours logged within budget. |
| 🟡 Amber | Schedule or budget between 90% and 100% consumed, or approvals older than 7 days. |
| 🔴 Red | Past `end_date` while incomplete, over budget, or approvals older than 14 days. |

Exact thresholds are configurable per deployment; the semantics are not.
