"""The month grid behind the project calendar.

Building the grid here rather than in the template keeps the Jinja readable and, more
usefully, makes the rules testable: which days fall inside the project window, which are
weekends, and what has already been logged on each.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from backend.app.models import Project, TimeNote

WEEK_STARTS_ON_MONDAY = 0


@dataclass
class Day:
    date: date
    in_month: bool
    in_window: bool
    is_weekend: bool
    is_today: bool
    notes: list[TimeNote] = field(default_factory=list)

    @property
    def loggable(self) -> bool:
        """A day that can hold a time note. Weekends can: people do work them."""
        return self.in_window

    @property
    def total_minutes(self) -> int:
        return sum(note.duration_minutes for note in self.notes)

    @property
    def hours_label(self) -> str:
        if not self.notes:
            return ""
        hours = self.total_minutes / 60
        return f"{hours:g}h"

    @property
    def state(self) -> str | None:
        """The least settled state on the day: what still needs the user's attention."""
        if not self.notes:
            return None
        precedence = ["rejected", "draft", "submitted", "approved"]
        states = {note.state.value for note in self.notes}
        for candidate in precedence:
            if candidate in states:
                return candidate
        return None


@dataclass
class Month:
    year: int
    month: int
    weeks: list[list[Day]]
    previous: str | None
    next: str | None

    @property
    def label(self) -> str:
        return date(self.year, self.month, 1).strftime("%B %Y")

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def total_minutes(self) -> int:
        return sum(day.total_minutes for week in self.weeks for day in week if day.in_month)


def parse_month(value: str | None, fallback: date) -> tuple[int, int]:
    if value:
        try:
            parsed = date.fromisoformat(f"{value}-01")
        except ValueError:
            return fallback.year, fallback.month
        return parsed.year, parsed.month
    return fallback.year, fallback.month


def default_month(project: Project, today: date) -> date:
    """Open on today when the project covers it, otherwise on the project's first month."""
    if today < project.start_date:
        return project.start_date
    if project.end_date is not None and today > project.end_date:
        return project.end_date
    return today


def _shift(year: int, month: int, delta: int) -> tuple[int, int]:
    index = (year * 12 + (month - 1)) + delta
    return index // 12, index % 12 + 1


def within_window(project: Project, day: date) -> bool:
    """Shared with the week grid: one answer to which days a project can hold."""
    if day < project.start_date:
        return False
    return project.end_date is None or day <= project.end_date


def _navigable(project: Project, year: int, month: int) -> str | None:
    """Only offer a month the project actually reaches."""
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    if last < project.start_date:
        return None
    if project.end_date is not None and first > project.end_date:
        return None
    return f"{year:04d}-{month:02d}"


def build_month(
    project: Project, year: int, month: int, notes: Iterable[TimeNote], today: date
) -> Month:
    by_date: dict[date, list[TimeNote]] = {}
    for note in notes:
        by_date.setdefault(note.work_date, []).append(note)

    grid = calendar.Calendar(firstweekday=WEEK_STARTS_ON_MONDAY)
    weeks = [
        [
            Day(
                date=day,
                in_month=day.month == month,
                in_window=within_window(project, day),
                is_weekend=day.weekday() >= 5,
                is_today=day == today,
                notes=sorted(by_date.get(day, []), key=lambda note: note.id or 0),
            )
            for day in week
        ]
        for week in grid.monthdatescalendar(year, month)
    ]

    previous_year, previous_month = _shift(year, month, -1)
    next_year, next_month = _shift(year, month, 1)

    return Month(
        year=year,
        month=month,
        weeks=weeks,
        previous=_navigable(project, previous_year, previous_month),
        next=_navigable(project, next_year, next_month),
    )
