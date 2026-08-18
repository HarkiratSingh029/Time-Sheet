"""The approver's queue.

Time that nobody signs off is just a note. This is where a day becomes billable.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.app.deps import RequiredUser, SessionDep, SettingsDep
from backend.app.flash import flash
from backend.app.models import TimeNote
from backend.app.templating import render
from backend.app.workflow import (
    WorkflowError,
    approve,
    can_decide,
    pending_notes_for,
    reject,
)

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_class=HTMLResponse)
async def queue(request: Request, session: SessionDep, user: RequiredUser):
    return render(
        request,
        "approvals/queue.html",
        user=user,
        notes=pending_notes_for(session, user),
    )


@router.post("/{note_id}/approve")
async def approve_note(
    session: SessionDep, settings: SettingsDep, user: RequiredUser, note_id: int
):
    note = _decidable_note(session, user, note_id)
    response = RedirectResponse("/approvals", status_code=status.HTTP_303_SEE_OTHER)

    try:
        approve(session, user, note)
        session.commit()
    except WorkflowError as error:
        session.rollback()
        flash(response, settings, str(error), "error")
        return response

    flash(response, settings, f"Approved {note.work_date:%d %b} for {note.user.email}.")
    return response


@router.post("/{note_id}/reject")
async def reject_note(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    note_id: int,
    comment: str = Form(""),
):
    note = _decidable_note(session, user, note_id)
    response = RedirectResponse("/approvals", status_code=status.HTTP_303_SEE_OTHER)

    try:
        reject(session, user, note, comment)
        session.commit()
    except WorkflowError as error:
        session.rollback()
        flash(response, settings, str(error), "error")
        return response

    flash(response, settings, f"Sent {note.work_date:%d %b} back to {note.user.email}.")
    return response


def _decidable_note(session: SessionDep, user, note_id: int) -> TimeNote:
    """404 rather than 403: a queue you are not on should not confirm what is in it."""
    note = session.get(TimeNote, note_id)
    if note is None or not can_decide(session, user, note):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    return note
