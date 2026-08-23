"""The approver's queue.

Time that nobody signs off is just a note. This is where a day becomes billable.

Bulk **approve** exists; bulk **reject** deliberately does not. A rejection carries a
comment, and one comment pasted across forty entries is noise rather than feedback — the
author would learn nothing they could act on.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.app.deps import RequiredUser, SessionDep, SettingsDep
from backend.app.flash import flash
from backend.app.models import TimeNote
from backend.app.queue import (
    AGEING_DAYS,
    Filters,
    age_in_days,
    filter_choices,
    group_by_person_and_week,
    parse_filters,
    pending_for,
)
from backend.app.templating import render
from backend.app.workflow import WorkflowError, approve, can_decide, reject

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_class=HTMLResponse)
async def queue(
    request: Request,
    session: SessionDep,
    user: RequiredUser,
    project_id: str | None = None,
    person_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    filters = parse_filters(project_id, person_id, since, until)

    everything = pending_for(session, user, Filters())
    projects, people = filter_choices(everything)
    notes = pending_for(session, user, filters) if filters.any_applied else everything

    return render(
        request,
        "approvals/queue.html",
        user=user,
        groups=group_by_person_and_week(notes),
        total_pending=len(everything),
        showing=len(notes),
        filters=filters,
        query_string=filters.as_query_string(),
        projects=projects,
        people=people,
        ageing_days=AGEING_DAYS,
        age_of=age_in_days,
    )


@router.post("/{note_id}/approve")
async def approve_note(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    note_id: int,
    next: str = Form(""),
):
    note = _decidable_note(session, user, note_id)
    response = _back_to_queue(next)

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
    next: str = Form(""),
):
    note = _decidable_note(session, user, note_id)
    response = _back_to_queue(next)

    try:
        reject(session, user, note, comment)
        session.commit()
    except WorkflowError as error:
        session.rollback()
        flash(response, settings, str(error), "error")
        return response

    flash(response, settings, f"Sent {note.work_date:%d %b} back to {note.user.email}.")
    return response


@router.post("/approve-group")
async def approve_group(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    note_ids: list[int] = Form(default_factory=list),
    next: str = Form(""),
):
    """Approve a whole week for one person.

    Every entry is checked individually: a bulk action is a convenience over the same rule,
    never a way around it. If any entry is refused the run stops and nothing commits, so an
    approver never has to guess which half of a group went through.
    """
    response = _back_to_queue(next)

    if not note_ids:
        flash(response, settings, "Nothing was selected.", "warning")
        return response

    try:
        for note_id in note_ids:
            note = session.get(TimeNote, note_id)
            if note is None or not can_decide(session, user, note):
                raise WorkflowError(
                    "One of those entries is not yours to decide, so none were approved."
                )
            approve(session, user, note)
        session.commit()
    except WorkflowError as error:
        session.rollback()
        flash(response, settings, str(error), "error")
        return response

    count = len(note_ids)
    flash(response, settings, f"Approved {count} entr{'y' if count == 1 else 'ies'}.")
    return response


def _back_to_queue(next_query: str) -> RedirectResponse:
    """Return to the filtered view the approver was looking at, not the top of the queue."""
    suffix = f"?{next_query}" if next_query else ""
    return RedirectResponse(f"/approvals{suffix}", status_code=status.HTTP_303_SEE_OTHER)


def _decidable_note(session: SessionDep, user, note_id: int) -> TimeNote:
    """404 rather than 403: a queue you are not on should not confirm what is in it."""
    note = session.get(TimeNote, note_id)
    if note is None or not can_decide(session, user, note):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")
    return note
