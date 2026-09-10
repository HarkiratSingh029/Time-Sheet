"""Populate a local database with an organisation worth clicking through.

`seed()` creates the bootstrap administrator and stops there, which is right for a first
production start and useless for a demo: an empty calendar, an empty approval queue and
dashboards with no numbers in them.

This module fills that gap. Everything here goes through `workflow.submit/approve/reject`
rather than writing states straight onto the rows, so the approval chains, the audit trail
and the rejection comments are the ones the product actually produces. Data that only
looks right in the tables but could never have been reached by a user teaches you nothing.

    python -m backend.app.demo
    ./scripts/dev.sh --fresh --demo

It refuses to touch a database that already has projects — losing real local work to a
convenience script is a bad trade.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from random import Random

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import permissions as vocabulary
from backend.app import workflow
from backend.app.config import ConfigurationError, Settings, load_settings
from backend.app.db import init_database, session_scope
from backend.app.models import (
    Permission,
    Project,
    ProjectMember,
    ProjectStatus,
    Role,
    Task,
    TimeNote,
    TimeNoteState,
    User,
)
from backend.app.security import hash_password
from backend.app.seed import MEMBER_ROLE, seed

# Everyone the demo creates shares one password. It is printed on the way out, and the
# whole dataset is disposable — `./scripts/dev.sh --fresh` removes it.
DEMO_PASSWORD = "demo-1234"

OWNER_ROLE = "project owner"

# Fixed, so two runs on the same day produce the same database and a screenshot taken
# today can be reproduced tomorrow.
RNG_SEED = 20260910

WEEKDAY_LOGGED_ODDS = 0.78


class DemoDataError(RuntimeError):
    """The database is not in a state the demo seed should overwrite."""


@dataclass(frozen=True)
class PersonSpec:
    email: str
    full_name: str
    title: str
    owner: bool = False


@dataclass(frozen=True)
class TaskSpec:
    name: str
    billable: bool
    details: tuple[str, ...]


@dataclass(frozen=True)
class ProjectSpec:
    code: str
    name: str
    description: str
    status: ProjectStatus
    cost: float
    billable_hours_budget: int
    proposed_duration_days: int
    starts_days_ago: int
    ends_days_ago: int | None
    required_approvals: int
    approvers: tuple[str, ...]
    consultants: tuple[str, ...]
    tasks: tuple[TaskSpec, ...]


PEOPLE: tuple[PersonSpec, ...] = (
    PersonSpec("priya@example.com", "Priya Raman", "Delivery Director", owner=True),
    PersonSpec("daniel@example.com", "Daniel Okoye", "Engagement Manager"),
    PersonSpec("mei@example.com", "Mei Lin", "Principal Consultant"),
    PersonSpec("alex@example.com", "Alex Turner", "Senior Consultant"),
    PersonSpec("sofia@example.com", "Sofia Marchetti", "Consultant"),
    PersonSpec("tom@example.com", "Tom Baxter", "Consultant"),
    PersonSpec("ana@example.com", "Ana Duarte", "Analyst"),
)

PROJECTS: tuple[ProjectSpec, ...] = (
    ProjectSpec(
        code="ATLAS",
        name="Atlas platform migration",
        description="Lift the legacy billing platform onto the new service topology.",
        status=ProjectStatus.ACTIVE,
        cost=180_000.00,
        billable_hours_budget=1_200,
        proposed_duration_days=180,
        starts_days_ago=84,
        ends_days_ago=None,
        # The one project that needs two signatures, so the sequenced chain is visible.
        required_approvals=2,
        approvers=("daniel@example.com", "mei@example.com"),
        consultants=("alex@example.com", "sofia@example.com", "tom@example.com"),
        tasks=(
            TaskSpec(
                "Discovery & assessment",
                True,
                (
                    "Mapped the billing service's outbound dependencies.",
                    "Interviewed the platform team on the cutover constraints.",
                    "Wrote up the data retention findings.",
                ),
            ),
            TaskSpec(
                "Migration engineering",
                True,
                (
                    "Ported the invoice batch job to the new scheduler.",
                    "Backfilled ledger rows and reconciled the totals.",
                    "Fixed the timezone drift in the settlement export.",
                ),
            ),
            TaskSpec(
                "Client workshops",
                True,
                (
                    "Ran the cutover dry-run workshop with the client team.",
                    "Walked finance through the new reconciliation report.",
                ),
            ),
            TaskSpec(
                "Internal / admin",
                False,
                (
                    "Weekly delivery sync and status write-up.",
                    "Onboarding paperwork and access requests.",
                ),
            ),
        ),
    ),
    ProjectSpec(
        code="HELIX",
        name="Helix data warehouse",
        description="Consolidate three reporting databases into one warehouse.",
        status=ProjectStatus.ACTIVE,
        cost=90_000.00,
        billable_hours_budget=600,
        proposed_duration_days=120,
        starts_days_ago=56,
        ends_days_ago=None,
        required_approvals=1,
        approvers=("mei@example.com",),
        consultants=("sofia@example.com", "ana@example.com"),
        tasks=(
            TaskSpec(
                "Schema design",
                True,
                (
                    "Modelled the conformed date and customer dimensions.",
                    "Reviewed grain decisions with the reporting team.",
                ),
            ),
            TaskSpec(
                "Pipeline build",
                True,
                (
                    "Built the incremental load for the orders fact table.",
                    "Added row-count reconciliation to the nightly run.",
                    "Chased down duplicate keys in the CRM extract.",
                ),
            ),
            TaskSpec(
                "Internal / admin",
                False,
                ("Sprint planning and estimate refresh.",),
            ),
        ),
    ),
    ProjectSpec(
        code="NOVA",
        name="Nova brand refresh",
        description="Refresh the customer-facing brand system ahead of the spring launch.",
        # Finished and signed off — the dashboard needs a closed project to compare against.
        status=ProjectStatus.COMPLETED,
        cost=40_000.00,
        billable_hours_budget=300,
        proposed_duration_days=60,
        starts_days_ago=70,
        ends_days_ago=21,
        required_approvals=1,
        approvers=("daniel@example.com",),
        consultants=("tom@example.com", "ana@example.com"),
        tasks=(
            TaskSpec(
                "Identity system",
                True,
                (
                    "Drafted the tone-of-voice guidance.",
                    "Reworked the logo lockups for small sizes.",
                ),
            ),
            TaskSpec(
                "Rollout collateral",
                True,
                (
                    "Produced the launch deck and one-pager.",
                    "Adapted the templates for the partner channel.",
                ),
            ),
        ),
    ),
)

DURATION_CHOICES: tuple[int, ...] = (240, 300, 330, 360, 420, 450, 480)

REJECTION_COMMENTS: tuple[str, ...] = (
    "Please split this across the two tasks it actually covers.",
    "This looks like it belongs on the following week — can you check the date?",
    "Nine hours needs a line on what ran long. Add the detail and resubmit.",
    "Client workshop time is billable to the workshop task, not to admin.",
)


def _ensure_owner_role(session: Session) -> Role:
    """A non-system role, so the demo also shows that roles are user-defined."""
    role = session.scalar(select(Role).where(Role.name == OWNER_ROLE))
    if role is not None:
        return role

    grants = (
        vocabulary.PROJECT_CREATE,
        vocabulary.PROJECT_MANAGE_ANY,
        vocabulary.PROJECT_VIEW_ANY,
        vocabulary.TIMESHEET_LOG,
    )
    role = Role(
        name=OWNER_ROLE,
        description="Creates projects, sets the approval rules, reads the dashboards.",
        is_administrator=False,
        is_system=False,
    )
    role.permissions = list(
        session.scalars(select(Permission).where(Permission.code.in_(grants))).all()
    )
    session.add(role)
    session.flush()
    return role


def _weekdays(first: date, last: date) -> list[date]:
    days: list[date] = []
    cursor = first
    while cursor <= last:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _outcome(rng: Random, age_days: int, *, closed_project: bool) -> str:
    """What happened to a day that old.

    Old work is settled, recent work is still moving. A demo where everything is approved
    shows an empty approval queue, which is the screen people most want to look at.
    """
    if closed_project:
        return "approved"
    if age_days > 21:
        return "approved"
    if age_days > 7:
        roll = rng.random()
        if roll < 0.74:
            return "approved"
        if roll < 0.90:
            return "submitted"
        return "rejected"
    roll = rng.random()
    if roll < 0.42:
        return "submitted"
    if roll < 0.84:
        return "draft"
    return "rejected"


def _drive(
    session: Session,
    note: TimeNote,
    author: User,
    approvers: list[User],
    outcome: str,
    rng: Random,
) -> None:
    """Walk one note to its outcome using the real transitions."""
    if outcome == "draft":
        return

    workflow.submit(session, note, author)
    # submit() inserts the approval rows by foreign key, so the collection this note
    # already loaded does not know about them. A request-per-transition never notices —
    # each one gets a fresh session — but this script drives the whole chain in one.
    session.expire(note, ["approvals"])
    if outcome == "submitted":
        return

    if outcome == "rejected":
        # A rejection lands the note back in draft with the reason attached — that is the
        # product's behaviour, not a `rejected` row sitting in the table.
        workflow.reject(session, approvers[0], note, rng.choice(REJECTION_COMMENTS))
        return

    for approver in approvers:
        workflow.approve(session, approver, note)


def build_demo(session: Session, settings: Settings, *, as_of: date) -> dict[str, int]:
    """Create the whole dataset. Returns a count per object, for the closing summary."""
    if session.scalar(select(Project).limit(1)) is not None:
        raise DemoDataError(
            "This database already has projects. The demo seed only runs on a clean one — "
            "use `./scripts/dev.sh --fresh --demo` if you are happy to lose what is there."
        )

    seed(session, settings)

    rng = Random(RNG_SEED)
    owner_role = _ensure_owner_role(session)
    member_role = session.scalar(select(Role).where(Role.name == MEMBER_ROLE))
    if member_role is None:  # pragma: no cover - seed() guarantees it
        raise DemoDataError("The member role is missing; the database was not seeded.")

    people: dict[str, User] = {}
    for spec in PEOPLE:
        user = User(
            email=spec.email,
            password_hash=hash_password(DEMO_PASSWORD),
            full_name=spec.full_name,
            title=spec.title,
            role_id=owner_role.id if spec.owner else member_role.id,
        )
        session.add(user)
        people[spec.email] = user
    session.flush()

    counts = {"users": len(people), "projects": 0, "tasks": 0, "time_notes": 0}

    for spec in PROJECTS:
        start = as_of - timedelta(days=spec.starts_days_ago)
        end = None if spec.ends_days_ago is None else as_of - timedelta(days=spec.ends_days_ago)
        owner = people["priya@example.com"]

        project = Project(
            name=spec.name,
            code=spec.code,
            description=spec.description,
            owner_id=owner.id,
            manager_id=people[spec.approvers[0]].id,
            status=spec.status,
            cost=spec.cost,
            billable_hours_budget=spec.billable_hours_budget,
            proposed_duration_days=spec.proposed_duration_days,
            current_duration_days=(min(end or as_of, as_of) - start).days,
            start_date=start,
            end_date=end,
            required_approvals=spec.required_approvals,
        )
        session.add(project)
        session.flush()
        counts["projects"] += 1

        for order, email in enumerate(spec.approvers, start=1):
            session.add(
                ProjectMember(
                    project_id=project.id,
                    user_id=people[email].id,
                    is_approver=True,
                    approval_order=order,
                )
            )
        for email in spec.consultants:
            session.add(
                ProjectMember(project_id=project.id, user_id=people[email].id, is_approver=False)
            )

        tasks: list[Task] = []
        for task_spec in spec.tasks:
            task = Task(
                project_id=project.id,
                name=task_spec.name,
                is_billable=task_spec.billable,
            )
            session.add(task)
            tasks.append(task)
        session.flush()
        counts["tasks"] += len(tasks)

        approvers = [people[email] for email in spec.approvers][: spec.required_approvals]
        # A note may never fall outside its project's window — `db.py` enforces it.
        last_day = min(end, as_of) if end is not None else as_of
        closed = spec.status is ProjectStatus.COMPLETED

        for email in spec.consultants:
            author = people[email]
            for day in _weekdays(start, last_day):
                if rng.random() > WEEKDAY_LOGGED_ODDS:
                    continue

                task_spec, task = rng.choice(list(zip(spec.tasks, tasks, strict=True)))
                note = TimeNote(
                    project_id=project.id,
                    task_id=task.id,
                    user_id=author.id,
                    work_date=day,
                    duration_minutes=rng.choice(DURATION_CHOICES),
                    detail=rng.choice(task_spec.details),
                    state=TimeNoteState.DRAFT,
                )
                session.add(note)
                session.flush()
                counts["time_notes"] += 1

                _drive(
                    session,
                    note,
                    author,
                    approvers,
                    _outcome(rng, (as_of - day).days, closed_project=closed),
                    rng,
                )

    session.flush()
    return counts


def _summarise(session: Session, counts: dict[str, int]) -> str:
    states = dict.fromkeys(TimeNoteState, 0)
    for note in session.scalars(select(TimeNote)).all():
        states[note.state] += 1

    lines = [
        "",
        "Demo data created:",
        f"  {counts['users']} people, {counts['projects']} projects, {counts['tasks']} tasks",
        f"  {counts['time_notes']} time notes — "
        + ", ".join(f"{count} {state.value}" for state, count in states.items() if count),
        "",
        "Sign in at http://127.0.0.1:8000",
        "",
        f"  {'email':<24} {'password':<12} what they see",
        f"  {'-' * 24} {'-' * 12} {'-' * 40}",
    ]
    for spec in PEOPLE:
        if spec.owner:
            role = "owns every project, reads the dashboards"
        elif any(spec.email in project.approvers for project in PROJECTS):
            role = "approval queue"
        else:
            role = "logs time on a project calendar"
        lines.append(f"  {spec.email:<24} {DEMO_PASSWORD:<12} {role}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.app.demo",
        description="Fill a local database with demo people, projects and time notes.",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Anchor the generated history to this day instead of today.",
    )
    arguments = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigurationError as error:
        print(f"TS Timesheets cannot start: {error}", file=sys.stderr)
        return 1

    _engine, factory = init_database(settings)
    try:
        for session in session_scope(factory):
            counts = build_demo(session, settings, as_of=arguments.as_of or date.today())
            summary = _summarise(session, counts)
    except DemoDataError as error:
        print(f"{error}", file=sys.stderr)
        return 1

    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
