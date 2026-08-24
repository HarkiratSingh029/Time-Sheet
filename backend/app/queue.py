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
from datetime import date, datetime, timedelta
from typing import NamedTuple

from sqlalchemy import Select, and_, exists, func, select
from sqlalchemy.orm import Session, selectinload

from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    TimeNote,
    TimeNoteState,
    User,
    as_utc,
    utcnow,
)
from backend.app.workflow import can_decide_anywhere

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


def age_in_days(note: TimeNote, now: datetime | None = None) -> int:
    """How long an entry has waited, in elapsed 24-hour periods.

    Timestamp to timestamp, never date to date. `submitted_at` is stored in UTC; comparing
    it with `date.today()` — a local date — reads a day older than reality for most of the
    day in most timezones, and that same figure moves a project's health threshold (#47).
    """
    submitted = as_utc(note.submitted_at)
    if submitted is None:
        return 0
    return max(0, ((now or utcnow()) - submitted).days)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def awaiting_this_person(user: User, *, strict: bool = False) -> object:
    """SQL for "this entry's open step is theirs".

    The open step is the lowest-numbered pending approval, so the condition is: a pending
    approval exists for them, with no earlier pending approval on the same note. Expressing
    it here rather than filtering in Python is what lets the nav badge be a `COUNT` on every
    page render instead of loading the whole queue.
    """
    mine = Approval.__table__.alias("mine")
    earlier = Approval.__table__.alias("earlier")

    no_earlier_step = ~exists(
        select(earlier.c.id).where(
            and_(
                earlier.c.time_note_id == mine.c.time_note_id,
                earlier.c.decision == ApprovalDecision.PENDING.value,
                earlier.c.sequence < mine.c.sequence,
            )
        )
    )

    conditions = [
        mine.c.time_note_id == TimeNote.id,
        mine.c.decision == ApprovalDecision.PENDING.value,
        no_earlier_step,
    ]
    # `strict` asks the narrower question: is the chain waiting on *them personally*.
    # Someone holding `approval.decide_any` can act on anything, which is right for a queue
    # and wrong for a daily email — a backstop is not somebody who is being waited on.
    if strict or not can_decide_anywhere(user):
        conditions.append(mine.c.approver_id == user.id)

    return exists(select(mine.c.id).where(and_(*conditions)))


def pending_query(user: User, filters: Filters, *, strict: bool = False) -> Select[tuple[TimeNote]]:
    query = select(TimeNote).where(
        TimeNote.state == TimeNoteState.SUBMITTED,
        awaiting_this_person(user, strict=strict),
    )

    if filters.project_id is not None:
        query = query.where(TimeNote.project_id == filters.project_id)
    if filters.person_id is not None:
        query = query.where(TimeNote.user_id == filters.person_id)
    if filters.since is not None:
        query = query.where(TimeNote.work_date >= filters.since)
    if filters.until is not None:
        query = query.where(TimeNote.work_date <= filters.until)

    return query


def pending_for(
    session: Session, user: User, filters: Filters, *, strict: bool = False
) -> list[TimeNote]:
    """Entries whose open approval step belongs to this person, narrowed by the filters."""
    query = pending_query(user, filters, strict=strict).options(
        selectinload(TimeNote.approvals).selectinload(Approval.approver),
        selectinload(TimeNote.user),
        selectinload(TimeNote.project),
        selectinload(TimeNote.task),
    )
    return list(session.scalars(query.order_by(TimeNote.work_date, TimeNote.id)).all())


def pending_count(session: Session, user: User) -> int:
    """Just the number, for the nav badge — no rows loaded."""
    return (
        session.scalar(select(func.count()).select_from(pending_query(user, Filters()).subquery()))
        or 0
    )


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
