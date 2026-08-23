"""The approver's queue: filtering, grouping and ageing.

Right for a handful of entries, the EPIC 0 card list is wrong at month-end, when an
approver faces forty entries from six people across three projects. This module turns the
queue into something you can narrow and act on in one pass.

Grouping is by person and week because that is how the work was done and how an approver
reads it — "Priya's week to 8 February, 37.5 hours" is a decision; forty separate cards
asking the same question are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import Approval, ApprovalDecision, Project, TimeNote, TimeNoteState, User
from backend.app.workflow import can_decide, can_decide_anywhere

# Matches the amber project-health threshold in docs/DATA_MODEL.md §4: the same number
# should not mean two different things in one product.
AGEING_DAYS = 7


class Filters(NamedTuple):
    project_id: int | None = None
    person_id: int | None = None
    since: date | None = None
    until: date | None = None

    @property
    def any_applied(self) -> bool:
        return any(value is not None for value in self)

    def as_query_string(self) -> str:
        parts = []
        if self.project_id:
            parts.append(f"project_id={self.project_id}")
        if self.person_id:
            parts.append(f"person_id={self.person_id}")
        if self.since:
            parts.append(f"since={self.since.isoformat()}")
        if self.until:
            parts.append(f"until={self.until.isoformat()}")
        return "&".join(parts)


@dataclass
class Group:
    """One person's week on one project — the unit an approver actually decides."""

    person: User
    project: Project
    week_starting: date
    notes: list[TimeNote] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.person.id}-{self.project.id}-{self.week_starting.isoformat()}"

    @property
    def total_minutes(self) -> int:
        return sum(note.duration_minutes for note in self.notes)

    @property
    def hours(self) -> float:
        return round(self.total_minutes / 60, 2)

    @property
    def note_ids(self) -> list[int]:
        return [note.id for note in self.notes]

    @property
    def oldest_age_days(self) -> int:
        return max((age_in_days(note) for note in self.notes), default=0)

    @property
    def is_ageing(self) -> bool:
        return self.oldest_age_days >= AGEING_DAYS


def parse_filters(
    project_id: str | None, person_id: str | None, since: str | None, until: str | None
) -> Filters:
    """Anything unparseable is dropped rather than rejected: a bad URL should not be a wall."""
    return Filters(
        project_id=_as_int(project_id),
        person_id=_as_int(person_id),
        since=_as_date(since),
        until=_as_date(until),
    )


def age_in_days(note: TimeNote, today: date | None = None) -> int:
    if note.submitted_at is None:
        return 0
    return max(0, ((today or date.today()) - note.submitted_at.date()).days)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def pending_for(session: Session, user: User, filters: Filters) -> list[TimeNote]:
    """Entries waiting on this person, narrowed by the filters."""
    query = (
        select(TimeNote)
        .join(Approval, Approval.time_note_id == TimeNote.id)
        .options(
            selectinload(TimeNote.approvals),
            selectinload(TimeNote.user),
            selectinload(TimeNote.project),
            selectinload(TimeNote.task),
        )
        .where(
            TimeNote.state == TimeNoteState.SUBMITTED,
            Approval.decision == ApprovalDecision.PENDING,
        )
    )

    if not can_decide_anywhere(user):
        query = query.where(Approval.approver_id == user.id)
    if filters.project_id is not None:
        query = query.where(TimeNote.project_id == filters.project_id)
    if filters.person_id is not None:
        query = query.where(TimeNote.user_id == filters.person_id)
    if filters.since is not None:
        query = query.where(TimeNote.work_date >= filters.since)
    if filters.until is not None:
        query = query.where(TimeNote.work_date <= filters.until)

    notes = session.scalars(query.order_by(TimeNote.work_date, TimeNote.id).distinct()).all()

    # "It is your turn" depends on the first pending row, which the join cannot express.
    return [note for note in notes if can_decide(session, user, note)]


def group_by_person_and_week(notes: list[TimeNote]) -> list[Group]:
    groups: dict[str, Group] = {}
    for note in notes:
        starting = week_start(note.work_date)
        key = f"{note.user_id}-{note.project_id}-{starting.isoformat()}"
        group = groups.get(key)
        if group is None:
            group = Group(person=note.user, project=note.project, week_starting=starting)
            groups[key] = group
        group.notes.append(note)

    # Oldest first: the queue should push what has waited longest to the top.
    return sorted(
        groups.values(),
        key=lambda group: (-group.oldest_age_days, group.person.full_name, group.week_starting),
    )


def filter_choices(notes: list[TimeNote]) -> tuple[list[Project], list[User]]:
    """Only offer filters that would actually match something in this queue."""
    projects = {note.project.id: note.project for note in notes}
    people = {note.user.id: note.user for note in notes}
    return (
        sorted(projects.values(), key=lambda project: project.name),
        sorted(people.values(), key=lambda person: person.full_name),
    )


def _as_int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _as_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
