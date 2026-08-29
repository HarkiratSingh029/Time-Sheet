"""Getting approved time out of the system.

Two formats, because two people want it: a CSV somebody reconciles in a spreadsheet, and a
per-project PDF somebody sends to a client.

Both are generated on request and never written to `data/` — an export is a view of the
database at a moment, and a stale copy on disk is worse than no copy.

**A CSV is a document that spreadsheets execute.** A cell beginning `=`, `+`, `-` or `@` is
a formula to Excel, Sheets and LibreOffice alike, so every field is neutralised on the way
out. The data here comes from a text box a user typed into; treating it as inert would be
the same mistake as trusting a filename (`storage.py`).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date

from fpdf import FPDF
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from backend.app.config import Settings
from backend.app.metrics import ProjectMetrics
from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    TimeNote,
    TimeNoteState,
    as_utc,
)

# Excel, Sheets and LibreOffice all treat a cell starting with one of these as a formula.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

CSV_COLUMNS = (
    "Project code",
    "Project",
    "Person",
    "Email",
    "Task",
    "Billable",
    "Date",
    "Hours",
    "State",
    "Detail",
    "Submitted",
    "Decided by",
)

MINUTES_PER_HOUR = 60


def neutralise(value: object) -> str:
    """Make a cell inert.

    Prefixing with an apostrophe is what a spreadsheet reads as "this is text", and it is
    the recommendation from every write-up of CSV injection. The alternative — stripping the
    character — silently changes somebody's data.
    """
    text = "" if value is None else str(value)
    if text.startswith(FORMULA_TRIGGERS):
        return "'" + text
    return text


def hours_of(note: TimeNote) -> str:
    """Decimal hours, from the minutes stored, matching what every screen shows."""
    return f"{note.duration_minutes / MINUTES_PER_HOUR:g}"


def decided_by(note: TimeNote) -> str:
    settled = [
        approval for approval in note.approvals if approval.decision is not ApprovalDecision.PENDING
    ]
    if not settled:
        return ""
    latest = max(settled, key=lambda approval: approval.sequence)
    return latest.approver.email if latest.approver else ""


def timesheet_query(
    visible: Select[tuple[Project]],
    project_id: int | None = None,
    person_id: int | None = None,
    since: date | None = None,
    until: date | None = None,
    state: TimeNoteState | None = None,
) -> Select[tuple[TimeNote]]:
    """Rows the caller may see, narrowed by the same filters the queue offers.

    Scoped against the viewer's own visible-projects query, so an export can never become a
    way to read a project you cannot open.
    """
    allowed = visible.with_only_columns(Project.id).order_by(None).subquery()

    query = (
        select(TimeNote)
        .where(TimeNote.project_id.in_(select(allowed.c.id)))
        .options(
            selectinload(TimeNote.project),
            selectinload(TimeNote.task),
            selectinload(TimeNote.user),
            selectinload(TimeNote.approvals).selectinload(Approval.approver),
        )
        .order_by(TimeNote.work_date, TimeNote.project_id, TimeNote.id)
    )

    if project_id is not None:
        query = query.where(TimeNote.project_id == project_id)
    if person_id is not None:
        query = query.where(TimeNote.user_id == person_id)
    if since is not None:
        query = query.where(TimeNote.work_date >= since)
    if until is not None:
        query = query.where(TimeNote.work_date <= until)
    if state is not None:
        query = query.where(TimeNote.state == state)

    return query


def row_for(note: TimeNote) -> list[str]:
    submitted = as_utc(note.submitted_at)
    return [
        neutralise(note.project.code),
        neutralise(note.project.name),
        neutralise(note.user.full_name or note.user.email),
        neutralise(note.user.email),
        neutralise(note.task.name),
        "yes" if note.task.is_billable else "no",
        note.work_date.isoformat(),
        hours_of(note),
        note.state.value,
        neutralise(note.detail),
        submitted.date().isoformat() if submitted else "",
        neutralise(decided_by(note)),
    ]


def stream_csv(session: Session, query: Select[tuple[TimeNote]]) -> Iterator[str]:
    """Yield the file a row at a time.

    `yield_per` keeps the result set out of memory, and the buffer is truncated after every
    row, so exporting a year for fifty projects costs one row of memory rather than all of
    them.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")

    # A BOM is what makes Excel open UTF-8 correctly; without it, accented names arrive
    # mangled and everybody blames the timesheet.
    yield "﻿"

    writer.writerow(CSV_COLUMNS)
    yield _drain(buffer)

    for note in session.scalars(query).yield_per(200):
        writer.writerow(row_for(note))
        yield _drain(buffer)


def _drain(buffer: io.StringIO) -> str:
    value = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return value


def filename_for(project: Project | None, since: date | None, until: date | None) -> str:
    parts = ["timesheet"]
    if project is not None:
        parts.append(project.code.lower())
    if since:
        parts.append(since.isoformat())
    if until:
        parts.append(until.isoformat())
    return "-".join(parts) + ".csv"


# --- the report -----------------------------------------------------------------------


class ProjectReport(FPDF):
    """A page somebody can send to a client without editing it first."""

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self.set_auto_page_break(auto=True, margin=18)

    def header(self) -> None:
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 8, f"{self.project.name}", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", size=9)
        self.set_text_color(90, 100, 120)
        self.cell(0, 5, f"{self.project.code} - TS Timesheets", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font("Helvetica", size=8)
        self.set_text_color(120, 130, 150)
        self.cell(0, 8, f"Page {self.page_no()} of {{nb}}", align="C")


def _pdf_text(value: str) -> str:
    """The built-in fonts are Latin-1; anything outside it becomes a question mark rather
    than an exception in the middle of somebody's month-end."""
    return value.encode("latin-1", "replace").decode("latin-1")


def project_report(
    metrics: ProjectMetrics,
    notes: list[TimeNote],
    settings: Settings,
    generated_on: date,
    since: date | None = None,
    until: date | None = None,
) -> bytes:
    project = metrics.project
    pdf = ProjectReport(project)
    pdf.alias_nb_pages()
    pdf.add_page()

    period = "the whole project"
    if since or until:
        period = f"{since or project.start_date} to {until or generated_on}"

    pdf.set_font("Helvetica", size=10)
    pdf.multi_cell(
        0,
        5,
        _pdf_text(
            f"Approved time for {period}. "
            f"Generated {generated_on.isoformat()}. "
            f"Health: {metrics.health.value}."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(3)

    figures = [
        ("Approved hours", f"{metrics.hours.approved:g}"),
        ("Awaiting approval", f"{metrics.hours.awaiting_decision:g}"),
        ("Draft", f"{metrics.hours.draft:g}"),
        ("Logged in total", f"{metrics.hours.logged:g}"),
        ("Billable", f"{metrics.hours.billable:g}"),
        ("Consultants", str(metrics.consultants)),
    ]
    if metrics.budget_hours:
        figures.append(("Hour budget", f"{metrics.hours.logged:g} of {metrics.budget_hours}"))
    if metrics.cost_burn is not None:
        figures.append(("Cost burn", f"{metrics.cost_burn:,.2f}"))

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    for label, value in figures:
        pdf.cell(60, 5, _pdf_text(label))
        pdf.cell(0, 5, _pdf_text(value), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Approved entries", new_x="LMARGIN", new_y="NEXT")

    widths = (24, 46, 46, 18, 40)
    headings = ("Date", "Person", "Task", "Hours", "Detail")
    pdf.set_font("Helvetica", "B", 8)
    for width, heading in zip(widths, headings, strict=True):
        pdf.cell(width, 6, heading, border="B")
    pdf.ln()

    pdf.set_font("Helvetica", size=8)
    total = 0.0
    for note in notes:
        total += note.duration_minutes / MINUTES_PER_HOUR
        cells = (
            note.work_date.isoformat(),
            (note.user.full_name or note.user.email)[:28],
            note.task.name[:28],
            hours_of(note),
            (note.detail or "")[:24],
        )
        for width, value in zip(widths, cells, strict=True):
            pdf.cell(width, 5, _pdf_text(value))
        pdf.ln()

    if not notes:
        pdf.cell(0, 5, "No approved entries in this period.", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "B", 9)
    pdf.ln(2)
    pdf.cell(sum(widths[:3]), 6, "Total", border="T")
    pdf.cell(widths[3], 6, f"{total:g}", border="T")
    pdf.cell(widths[4], 6, "", border="T")

    return bytes(pdf.output())
