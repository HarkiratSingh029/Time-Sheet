"""The project calendar and time-note capture.

This is the interaction the product exists for: open a project, double-click a day, log it.
A consultant uses it every day, so it is the one screen where friction compounds.

Two rules hold on every route here, not just in the UI:
  * a note belongs to the signed-in user — `user_id` never comes from the request;
  * a submitted note is frozen to its author.
"""

from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from backend.app.access import can_manage, get_visible_project
from backend.app.calendar_month import build_month, default_month, parse_month
from backend.app.db import InvariantError
from backend.app.deps import RequiredUser, SessionDep, SettingsDep
from backend.app.flash import flash
from backend.app.models import Project, Task, TimeNote, TimeNoteState, utcnow
from backend.app.templating import render

router = APIRouter(prefix="/projects/{project_id}", tags=["timesheet"])

MINUTES_PER_HOUR = 60
MAX_HOURS_PER_DAY = 24


class EntryError(ValueError):
    """The submitted time note cannot be saved, with a reason the user can act on."""


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_view(
    request: Request,
    session: SessionDep,
    user: RequiredUser,
    project_id: int,
    month: str | None = None,
):
    project = get_visible_project(session, user, project_id)
    today = date.today()

    year, month_number = parse_month(month, default_month(project, today))
    notes = session.scalars(
        select(TimeNote).where(TimeNote.project_id == project.id, TimeNote.user_id == user.id)
    ).all()

    return render(
        request,
        "timesheet/calendar.html",
        user=user,
        project=project,
        month=build_month(project, year, month_number, notes, today),
        notes_json=_notes_as_json(notes),
        tasks=_open_tasks(session, project),
        manageable=can_manage(user, project),
    )


@router.post("/notes")
async def create_note(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    work_date: str = Form(...),
    task_id: int = Form(...),
    hours: str = Form(...),
    detail: str = Form(""),
    month: str = Form(""),
):
    project = get_visible_project(session, user, project_id)
    response = _back_to_calendar(project.id, month)

    try:
        note = TimeNote(
            project_id=project.id,
            task_id=_task_on_project(session, project, task_id).id,
            user_id=user.id,
            work_date=_parse_date(work_date),
            duration_minutes=_minutes_from_hours(hours),
            detail=detail.strip(),
            state=TimeNoteState.DRAFT,
        )
        session.add(note)
        session.commit()
    except (EntryError, InvariantError) as error:
        session.rollback()
        flash(response, settings, str(error), "error")
        return response

    flash(response, settings, f"Logged {hours}h on {note.work_date:%d %b}.")
    return response


# Declared before /notes/{note_id}: routes match in order, so otherwise "submit" would be
# read as a note id and never reach here.
@router.post("/notes/submit")
async def submit_notes(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    from_date: str = Form(...),
    to_date: str = Form(""),
    month: str = Form(""),
):
    project = get_visible_project(session, user, project_id)
    response = _back_to_calendar(project.id, month)

    try:
        first = _parse_date(from_date)
        last = _parse_date(to_date) if to_date.strip() else first
    except EntryError as error:
        flash(response, settings, str(error), "error")
        return response

    if last < first:
        flash(response, settings, "That range ends before it starts.", "error")
        return response

    drafts = session.scalars(
        select(TimeNote).where(
            TimeNote.project_id == project.id,
            TimeNote.user_id == user.id,
            TimeNote.state == TimeNoteState.DRAFT,
            TimeNote.work_date >= first,
            TimeNote.work_date <= last,
        )
    ).all()

    if not drafts:
        flash(response, settings, "Nothing to submit — no drafts in that range.", "warning")
        return response

    submitted_at = utcnow()
    for note in drafts:
        note.state = TimeNoteState.SUBMITTED
        note.submitted_at = submitted_at
    session.commit()

    # Approvals are generated in story 0.7 (#12); submitting is the state change alone.
    flash(
        response,
        settings,
        f"Submitted {len(drafts)} " + ("entry." if len(drafts) == 1 else "entries."),
    )
    return response


@router.post("/notes/{note_id}")
async def update_note(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    note_id: int,
    task_id: int = Form(...),
    hours: str = Form(...),
    detail: str = Form(""),
    month: str = Form(""),
):
    project = get_visible_project(session, user, project_id)
    note = _own_note(session, project, user, note_id)
    response = _back_to_calendar(project.id, month)

    if note.state is not TimeNoteState.DRAFT:
        flash(response, settings, _frozen_message(note), "error")
        return response

    try:
        note.task_id = _task_on_project(session, project, task_id).id
        note.duration_minutes = _minutes_from_hours(hours)
        note.detail = detail.strip()
        session.commit()
    except (EntryError, InvariantError) as error:
        session.rollback()
        flash(response, settings, str(error), "error")
        return response

    flash(response, settings, f"Updated {note.work_date:%d %b}.")
    return response


@router.post("/notes/{note_id}/delete")
async def delete_note(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    note_id: int,
    month: str = Form(""),
):
    project = get_visible_project(session, user, project_id)
    note = _own_note(session, project, user, note_id)
    response = _back_to_calendar(project.id, month)

    if note.state is not TimeNoteState.DRAFT:
        flash(response, settings, _frozen_message(note), "error")
        return response

    session.delete(note)
    session.commit()
    flash(response, settings, "Entry removed.")
    return response


# --- helpers --------------------------------------------------------------------------


def _back_to_calendar(project_id: int, month: str) -> RedirectResponse:
    suffix = f"?month={month}" if month else ""
    return RedirectResponse(
        f"/projects/{project_id}/calendar{suffix}", status_code=status.HTTP_303_SEE_OTHER
    )


def _notes_as_json(notes) -> str:
    """The month's notes, keyed by id, so the dialog opens without a round trip."""
    return json.dumps(
        {
            str(note.id): {
                "day": note.work_date.isoformat(),
                "taskId": note.task_id,
                "hours": round(note.duration_minutes / MINUTES_PER_HOUR, 2),
                "detail": note.detail,
                "state": note.state.value,
            }
            for note in notes
        }
    )


def _open_tasks(session, project: Project) -> list[Task]:
    return list(
        session.scalars(
            select(Task)
            .where(Task.project_id == project.id, Task.is_archived.is_(False))
            .order_by(Task.name)
        ).all()
    )


def _task_on_project(session, project: Project, task_id: int) -> Task:
    task = session.get(Task, task_id)
    if task is None or task.project_id != project.id or task.is_archived:
        raise EntryError("Pick a task that belongs to this project.")
    return task


def _own_note(session, project: Project, user, note_id: int) -> TimeNote:
    """A note is reachable only by its author: nobody logs or edits time for someone else."""
    note = session.get(TimeNote, note_id)
    if note is None or note.project_id != project.id or note.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    return note


def _frozen_message(note: TimeNote) -> str:
    if note.state is TimeNoteState.APPROVED:
        return "That day is approved. Ask an administrator to reopen it."
    return "That day is submitted and waiting on approval, so it is no longer editable."


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise EntryError("That is not a valid date.") from error


def _minutes_from_hours(value: str) -> int:
    try:
        hours = float(value.strip())
    except ValueError as error:
        raise EntryError("Enter the duration in hours, for example 7.5.") from error

    if hours <= 0:
        raise EntryError("A duration has to be more than zero.")
    if hours > MAX_HOURS_PER_DAY:
        raise EntryError("A single day cannot hold more than 24 hours.")

    minutes = round(hours * MINUTES_PER_HOUR)
    if minutes <= 0:
        raise EntryError("A duration has to be more than zero.")
    return minutes
