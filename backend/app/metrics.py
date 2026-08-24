"""Every number the dashboards show.

One place that answers, because computing these per screen is how two dashboards start
disagreeing about the same project. `for_project` returns everything 2.2 and 2.3 need in a
single call.

Two rules hold throughout:

* **Approved and unapproved hours are never merged.** A figure that silently mixes signed
  work with work still under review is worse than no figure, because it looks authoritative.
  Nothing here returns their sum alone.
* **Health is derived, never entered.** It falls out of schedule, budget and approval age
  against the thresholds in `docs/DATA_MODEL.md` §4. There is no setter.

Aggregates are computed in SQL. Loading a year of time notes to add them up would work at
thirty users and would be the first thing to fall over at fifty projects.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    ProjectStatus,
    Task,
    TimeNote,
    TimeNoteState,
)

MINUTES_PER_HOUR = 60


class Health(enum.StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


COMPLETED_STATUSES = {ProjectStatus.COMPLETED, ProjectStatus.CANCELLED}


@dataclass(frozen=True)
class Hours:
    """The four buckets a dashboard is allowed to show, kept apart on purpose."""

    draft: float = 0.0
    submitted: float = 0.0
    approved: float = 0.0
    rejected: float = 0.0
    billable: float = 0.0
    non_billable: float = 0.0

    @property
    def logged(self) -> float:
        """Everything recorded, whatever its state — not a substitute for `approved`."""
        return round(self.draft + self.submitted + self.approved + self.rejected, 2)

    @property
    def awaiting_decision(self) -> float:
        return self.submitted


@dataclass(frozen=True)
class ProjectMetrics:
    project: Project
    hours: Hours
    consultants: int
    budget_hours: int | None
    budget_ratio: float | None
    schedule_ratio: float | None
    days_elapsed: int
    days_planned: int | None
    cost: float | None
    cost_burn: float | None
    oldest_pending_days: int
    health: Health
    health_reasons: tuple[str, ...]

    @property
    def is_over_budget(self) -> bool:
        return self.budget_ratio is not None and self.budget_ratio > 1

    @property
    def is_overdue(self) -> bool:
        return self.schedule_ratio is not None and self.schedule_ratio > 1


def _hours(minutes_expression):
    return func.coalesce(func.sum(minutes_expression), 0) / MINUTES_PER_HOUR


def _state_sum(state: TimeNoteState):
    return _hours(case((TimeNote.state == state.value, TimeNote.duration_minutes), else_=0))


def hours_for(session: Session, project_id: int) -> Hours:
    """One query for all six figures rather than six queries for one each."""
    row = session.execute(
        select(
            _state_sum(TimeNoteState.DRAFT),
            _state_sum(TimeNoteState.SUBMITTED),
            _state_sum(TimeNoteState.APPROVED),
            _state_sum(TimeNoteState.REJECTED),
            _hours(case((Task.is_billable.is_(True), TimeNote.duration_minutes), else_=0)),
            _hours(case((Task.is_billable.is_(False), TimeNote.duration_minutes), else_=0)),
        )
        .select_from(TimeNote)
        .join(Task, Task.id == TimeNote.task_id)
        .where(TimeNote.project_id == project_id)
    ).one()

    return Hours(*(round(float(value), 2) for value in row))


def consultants_on(session: Session, project_id: int) -> int:
    """People who have actually logged time, not people assigned."""
    return (
        session.scalar(
            select(func.count(func.distinct(TimeNote.user_id))).where(
                TimeNote.project_id == project_id
            )
        )
        or 0
    )


def oldest_pending_approval_days(session: Session, project_id: int, today: date) -> int:
    oldest = session.scalar(
        select(func.min(TimeNote.submitted_at))
        .join(Approval, Approval.time_note_id == TimeNote.id)
        .where(
            TimeNote.project_id == project_id,
            TimeNote.state == TimeNoteState.SUBMITTED.value,
            Approval.decision == ApprovalDecision.PENDING.value,
        )
    )
    if oldest is None:
        return 0
    return max(0, (today - oldest.date()).days)


def _schedule(project: Project, today: date) -> tuple[float | None, int, int | None]:
    """Elapsed against planned. An open-ended project has no schedule to be behind."""
    days_elapsed = max(0, (today - project.start_date).days)
    if project.end_date is None:
        return None, days_elapsed, None

    days_planned = max(1, (project.end_date - project.start_date).days)
    return round(days_elapsed / days_planned, 4), days_elapsed, days_planned


def derive_health(
    *,
    project: Project,
    hours: Hours,
    budget_ratio: float | None,
    schedule_ratio: float | None,
    oldest_pending_days: int,
    settings: Settings,
) -> tuple[Health, tuple[str, ...]]:
    """Green, amber or red, with the reasons — a colour nobody can explain is not a signal."""
    if project.status in COMPLETED_STATUSES:
        return Health.GREEN, ("Project is closed.",)

    amber_at = settings.health_amber_ratio
    reasons_red: list[str] = []
    reasons_amber: list[str] = []

    if budget_ratio is not None:
        if budget_ratio > 1:
            reasons_red.append(f"Over the hour budget ({budget_ratio:.0%} used).")
        elif budget_ratio >= amber_at:
            reasons_amber.append(f"{budget_ratio:.0%} of the hour budget used.")

    if schedule_ratio is not None:
        if schedule_ratio > 1:
            reasons_red.append("Past the end date and not closed.")
        elif schedule_ratio >= amber_at:
            reasons_amber.append(f"{schedule_ratio:.0%} of the schedule elapsed.")

    if oldest_pending_days >= settings.health_red_approval_days:
        reasons_red.append(f"Approvals waiting {oldest_pending_days} days.")
    elif oldest_pending_days >= settings.health_amber_approval_days:
        reasons_amber.append(f"Approvals waiting {oldest_pending_days} days.")

    if reasons_red:
        return Health.RED, tuple(reasons_red + reasons_amber)
    if reasons_amber:
        return Health.AMBER, tuple(reasons_amber)
    return Health.GREEN, ()


def for_project(
    session: Session, project: Project, settings: Settings, today: date | None = None
) -> ProjectMetrics:
    today = today or date.today()

    hours = hours_for(session, project.id)
    schedule_ratio, days_elapsed, days_planned = _schedule(project, today)
    oldest_pending = oldest_pending_approval_days(session, project.id, today)

    budget_hours = project.billable_hours_budget
    budget_ratio = (
        round(hours.logged / budget_hours, 4) if budget_hours and budget_hours > 0 else None
    )

    cost = float(project.cost) if project.cost is not None else None
    cost_burn = (
        round(cost * budget_ratio, 2) if cost is not None and budget_ratio is not None else None
    )

    health, reasons = derive_health(
        project=project,
        hours=hours,
        budget_ratio=budget_ratio,
        schedule_ratio=schedule_ratio,
        oldest_pending_days=oldest_pending,
        settings=settings,
    )

    return ProjectMetrics(
        project=project,
        hours=hours,
        consultants=consultants_on(session, project.id),
        budget_hours=budget_hours,
        budget_ratio=budget_ratio,
        schedule_ratio=schedule_ratio,
        days_elapsed=days_elapsed,
        days_planned=days_planned,
        cost=cost,
        cost_burn=cost_burn,
        oldest_pending_days=oldest_pending,
        health=health,
        health_reasons=reasons,
    )


def for_projects(
    session: Session, projects: list[Project], settings: Settings, today: date | None = None
) -> list[ProjectMetrics]:
    today = today or date.today()
    return [for_project(session, project, settings, today) for project in projects]
