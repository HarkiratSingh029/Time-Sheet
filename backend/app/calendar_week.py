"""The week grid, and the two bulk moves that fill it.

The month grid is right for reviewing a month and tedious for filling one: a consultant on
the same project every day logs the same entry five times, one dialog at a time. This is
the difference between a timesheet people keep up to date and one they reconstruct on the
last Friday of the month.

Nothing here validates an entry. Creating a note still goes through the same rules as the
month view — the project window, the duration limits, the task-belongs-to-project
invariant — because a second code path is a second set of bugs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.calendar_month import Day, within_window
from backend.app.models import Project, Task, TimeNote, TimeNoteState

DAYS_IN_WEEK = 7


@dataclass
class Cell:
    """One task on one day: the entry that exists there, if any."""

    day: Day
    task: Task
    note: TimeNote | None = None

    @property
    def hours(self) -> str:
        return "" if self.note is None else f"{self.note.duration_minutes / 60:g}"

    @property
    def editable(self) -> bool:
        return self.day.in_window and (self.note is None or self.note.state is TimeNoteState.DRAFT)

    @property
    def state(self) -> str | None:
        return None if self.note is None else self.note.state.value


@dataclass
class Row:
    task: Task
    cells: list[Cell] = field(default_factory=list)

    @property
    def total_minutes(self) -> int:
        return sum(cell.note.duration_minutes for cell in self.cells if cell.note)


@dataclass
class Week:
    starting: date
    days: list[Day]
    rows: list[Row]
    previous: str | None
    next: str | None

    @property
    def key(self) -> str:
        return self.starting.isoformat()

    @property
    def label(self) -> str:
        ending = self.starting + timedelta(days=DAYS_IN_WEEK - 1)
        if self.starting.month == ending.month:
            return f"{self.starting:%d} – {ending:%d %B %Y}"
        return f"{self.starting:%d %b} – {ending:%d %b %Y}"

    @property
    def total_minutes(self) -> int:
        return sum(row.total_minutes for row in self.rows)

    def daily_minutes(self, day: date) -> int:
        return sum(
            cell.note.duration_minutes
            for row in self.rows
            for cell in row.cells
            if cell.note and cell.day.date == day
        )


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def parse_week(value: str | None, fallback: date) -> date:
    if value:
        try:
            return week_start(date.fromisoformat(value))
        except ValueError:
            return week_start(fallback)
    return week_start(fallback)


def _navigable(project: Project, starting: date) -> str | None:
    ending = starting + timedelta(days=DAYS_IN_WEEK - 1)
    if ending < project.start_date:
        return None
    if project.end_date is not None and starting > project.end_date:
        return None
    return starting.isoformat()


def build_week(
    project: Project, starting: date, tasks: list[Task], notes: list[TimeNote], today: date
) -> Week:
    starting = week_start(starting)
    days = [
        Day(
            date=starting + timedelta(days=offset),
            in_month=True,
            in_window=within_window(project, starting + timedelta(days=offset)),
            is_weekend=(starting + timedelta(days=offset)).weekday() >= 5,
            is_today=(starting + timedelta(days=offset)) == today,
        )
        for offset in range(DAYS_IN_WEEK)
    ]

    by_task_and_day = {(note.task_id, note.work_date): note for note in notes}
    rows = [
        Row(
            task=task,
            cells=[
                Cell(day=day, task=task, note=by_task_and_day.get((task.id, day.date)))
                for day in days
            ],
        )
        for task in tasks
    ]

    return Week(
        starting=starting,
        days=days,
        rows=rows,
        previous=_navigable(project, starting - timedelta(days=DAYS_IN_WEEK)),
        next=_navigable(project, starting + timedelta(days=DAYS_IN_WEEK)),
    )


def notes_in(session: Session, project: Project, user_id: int, first: date, last: date) -> list:
    return list(
        session.scalars(
            select(TimeNote).where(
                TimeNote.project_id == project.id,
                TimeNote.user_id == user_id,
                TimeNote.work_date >= first,
                TimeNote.work_date <= last,
            )
        ).all()
    )


def days_already_logged(notes: list[TimeNote]) -> set[date]:
    """A day that already carries an entry is never overwritten by a bulk action."""
    return {note.work_date for note in notes}
