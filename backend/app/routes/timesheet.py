"""The project calendar and time-note capture.

This is the interaction the product exists for: open a project, double-click a day, log it.
A consultant uses it every day, so it is the one screen where friction compounds.

Two rules hold on every route here, not just in the UI:
  * a note belongs to the signed-in user — `user_id` never comes from the request;
  * a submitted note is frozen to its author.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from backend.app.access import can_decide_anywhere, can_manage, get_visible_project
from backend.app.calendar_month import build_month, default_month, parse_month, within_window
from backend.app.calendar_week import (
    DAYS_IN_WEEK,
    build_week,
    days_already_logged,
    notes_in,
    parse_week,
    week_start,
)
from backend.app.db import InvariantError
from backend.app.deps import RequiredUser, SessionDep, SettingsDep
from backend.app.flash import flash
from backend.app.models import Project, Task, TimeNote, TimeNoteState
from backend.app.templating import render
from backend.app.workflow import (
    WorkflowError,
    can_read_history,
    history_of,
    latest_rejection,
)
from backend.app.workflow import reopen as reopen_note
from backend.app.workflow import submit as submit_note
from backend.app.workflow import withdraw as withdraw_note

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

    response = render(
        request,
        "timesheet/calendar.html",
        user=user,
        project=project,
        month=build_month(project, year, month_number, notes, today),
        notes_json=_notes_as_json(notes),
        rejections=_rejections(notes),
        submitted_notes=[note for note in notes if note.state is TimeNoteState.SUBMITTED],
        approved_notes=[note for note in notes if note.state is TimeNoteState.APPROVED],
        tasks=_open_tasks(session, project),
        manageable=can_manage(user, project),
    )
    response.set_cookie(VIEW_COOKIE, "month", max_age=31_536_000, samesite="lax", path="/")
    return response


VIEW_COOKIE = "ts_calendar_view"


@router.get("/week", response_class=HTMLResponse)
async def week_view(
    request: Request,
    session: SessionDep,
    user: RequiredUser,
    project_id: int,
    week: str | None = None,
):
    project = get_visible_project(session, user, project_id)
    today = date.today()
    starting = parse_week(week, default_month(project, today))
    ending = starting + timedelta(days=DAYS_IN_WEEK - 1)

    tasks = _open_tasks(session, project)
    notes = notes_in(session, project, user.id, starting, ending)

    response = render(
        request,
        "timesheet/week.html",
        user=user,
        project=project,
        week=build_week(project, starting, tasks, notes, today),
        tasks=tasks,
        has_drafts=any(note.state is TimeNoteState.DRAFT for note in notes),
    )
    # Remembered per user, in their own browser: a view is a preference, not data.
    response.set_cookie(VIEW_COOKIE, "week", max_age=31_536_000, samesite="lax", path="/")
    return response


@router.post("/week/cells")
async def save_week_cell(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    work_date: str = Form(...),
    task_id: int = Form(...),
    hours: str = Form(""),
    week: str = Form(""),
):
    """One cell of the grid. Empty clears the entry; anything else writes it.

    Both branches go through the same helpers the month view uses, so the project window,
    the duration limits and the frozen-once-submitted rule need no restating here.
    """
    project = get_visible_project(session, user, project_id)
    response = _back_to_week(project.id, week)

    try:
        day = _parse_date(work_date)
        task = _task_on_project(session, project, task_id)
        existing = _cell_note(session, project, user, task.id, day)

        if not hours.strip():
            if existing is None:
                return response
            if existing.state is not TimeNoteState.DRAFT:
                flash(response, settings, _frozen_message(existing), "error")
                return response
            session.delete(existing)
            session.commit()
            flash(response, settings, f"Cleared {day:%d %b}.")
            return response

        minutes = _minutes_from_hours(hours)

        if existing is None:
            session.add(
                TimeNote(
                    project_id=project.id,
                    task_id=task.id,
                    user_id=user.id,
                    work_date=day,
                    duration_minutes=minutes,
                    state=TimeNoteState.DRAFT,
                )
            )
        elif existing.state is not TimeNoteState.DRAFT:
            flash(response, settings, _frozen_message(existing), "error")
            return response
        else:
            existing.duration_minutes = minutes

        session.commit()
    except (EntryError, InvariantError) as error:
        session.rollback()
        flash(response, settings, str(error), "error")
        return response

    flash(response, settings, f"Saved {day:%d %b}.")
    return response


@router.post("/week/copy-last-week")
async def copy_last_week(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    week: str = Form(...),
):
    """Reproduce last week's drafts into this one, shifted seven days.

    Only drafts: a submitted or approved day is a statement somebody has acted on, and
    copying it forward would quietly claim the same work twice.
    """
    project = get_visible_project(session, user, project_id)
    response = _back_to_week(project.id, week)

    starting = week_start(_parse_date(week))
    previous = starting - timedelta(days=DAYS_IN_WEEK)

    source = notes_in(
        session, project, user.id, previous, previous + timedelta(days=DAYS_IN_WEEK - 1)
    )
    drafts = [note for note in source if note.state is TimeNoteState.DRAFT]
    if not drafts:
        flash(response, settings, "There are no drafts in the previous week to copy.", "warning")
        return response

    occupied = days_already_logged(
        notes_in(session, project, user.id, starting, starting + timedelta(days=DAYS_IN_WEEK - 1))
    )

    created, skipped, outside = 0, 0, 0
    for note in drafts:
        target = note.work_date + timedelta(days=DAYS_IN_WEEK)
        if not within_window(project, target):
            outside += 1
            continue
        if target in occupied:
            skipped += 1
            continue
        session.add(
            TimeNote(
                project_id=project.id,
                task_id=note.task_id,
                user_id=user.id,
                work_date=target,
                duration_minutes=note.duration_minutes,
                detail=note.detail,
                state=TimeNoteState.DRAFT,
            )
        )
        occupied.add(target)
        created += 1

    session.commit()
    flash(response, settings, _bulk_message(created, skipped, outside))
    return response


@router.post("/week/fill")
async def fill_range(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    task_id: int = Form(...),
    hours: str = Form(...),
    from_date: str = Form(...),
    to_date: str = Form(...),
    include_weekends: bool = Form(False),
    detail: str = Form(""),
    week: str = Form(""),
):
    """One task and one duration across a span, skipping days that already carry an entry."""
    project = get_visible_project(session, user, project_id)
    response = _back_to_week(project.id, week)

    try:
        first = _parse_date(from_date)
        last = _parse_date(to_date)
        minutes = _minutes_from_hours(hours)
        task = _task_on_project(session, project, task_id)
    except EntryError as error:
        flash(response, settings, str(error), "error")
        return response

    if last < first:
        flash(response, settings, "That range ends before it starts.", "error")
        return response

    occupied = days_already_logged(notes_in(session, project, user.id, first, last))

    created, skipped, outside = 0, 0, 0
    day = first
    while day <= last:
        if day.weekday() >= 5 and not include_weekends:
            day += timedelta(days=1)
            continue
        if not within_window(project, day):
            outside += 1
        elif day in occupied:
            skipped += 1
        else:
            session.add(
                TimeNote(
                    project_id=project.id,
                    task_id=task.id,
                    user_id=user.id,
                    work_date=day,
                    duration_minutes=minutes,
                    detail=detail.strip(),
                    state=TimeNoteState.DRAFT,
                )
            )
            created += 1
        day += timedelta(days=1)

    session.commit()
    flash(response, settings, _bulk_message(created, skipped, outside))
    return response


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

    try:
        for note in drafts:
            submit_note(session, note, actor=user)
        session.commit()
    except WorkflowError as error:
        session.rollback()
        flash(response, settings, str(error), "error")
        return response

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


@router.post("/notes/{note_id}/withdraw")
async def withdraw_entry(
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

    try:
        withdraw_note(session, user, note)
        session.commit()
    except WorkflowError as error:
        session.rollback()
        flash(response, settings, str(error), "error")
        return response

    flash(response, settings, f"Pulled {note.work_date:%d %b} back to draft.")
    return response


@router.post("/notes/{note_id}/reopen")
async def reopen_entry(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    note_id: int,
    reason: str = Form(""),
    month: str = Form(""),
):
    project = get_visible_project(session, user, project_id)
    note = session.get(TimeNote, note_id)
    if note is None or note.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    response = _back_to_calendar(project.id, month)
    try:
        reopen_note(session, user, note, reason)
        session.commit()
    except WorkflowError as error:
        session.rollback()
        flash(response, settings, str(error), "error")
        return response

    flash(response, settings, f"Reopened {note.work_date:%d %b} for {note.user.email}.")
    return response


@router.get("/notes/{note_id}/history", response_class=HTMLResponse)
async def entry_history(
    request: Request,
    session: SessionDep,
    user: RequiredUser,
    project_id: int,
    note_id: int,
):
    project = get_visible_project(session, user, project_id)
    note = session.get(TimeNote, note_id)
    if note is None or note.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    if not can_read_history(session, user, note):
        # 404 rather than 403: whose day it was is itself information.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    return render(
        request,
        "timesheet/history.html",
        user=user,
        project=project,
        note=note,
        events=history_of(note),
        can_reopen=can_decide_anywhere(user),
    )


def _back_to_week(project_id: int, week: str) -> RedirectResponse:
    suffix = f"?week={week}" if week else ""
    return RedirectResponse(
        f"/projects/{project_id}/week{suffix}", status_code=status.HTTP_303_SEE_OTHER
    )


def _cell_note(session, project: Project, user, task_id: int, day: date) -> TimeNote | None:
    return session.scalar(
        select(TimeNote).where(
            TimeNote.project_id == project.id,
            TimeNote.user_id == user.id,
            TimeNote.task_id == task_id,
            TimeNote.work_date == day,
        )
    )


def _bulk_message(created: int, skipped: int, outside: int) -> str:
    """Always say what happened, including what was left alone."""
    parts = [f"Created {created} entr{'y' if created == 1 else 'ies'}"]
    if skipped:
        parts.append(f"{skipped} day{'' if skipped == 1 else 's'} already had time logged")
    if outside:
        parts.append(f"{outside} fell outside the project")
    return ", ".join(parts) + "."


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


def _rejections(notes) -> list[dict[str, str]]:
    """Why a day came back, shown to its author — a rejection nobody reads is a dead end."""
    sent_back = []
    for note in notes:
        if note.state is not TimeNoteState.DRAFT:
            continue
        rejection = latest_rejection(note)
        if rejection is not None:
            sent_back.append(
                {
                    "day": note.work_date.strftime("%d %b"),
                    "comment": rejection.comment,
                    "by": rejection.approver.email,
                }
            )
    return sent_back


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
