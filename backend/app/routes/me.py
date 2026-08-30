"""The consultant's own view.

Every other screen answers somebody else's question: the approver's queue, the owner's
dashboard. This one answers "what did I do, and what still needs me" — the calendar is per
project by design, so this is the only place the picture comes together.

There is no user parameter. The page is always the signed-in person's own time, so no
permission can widen it into somebody else's timesheet.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from backend.app import metrics
from backend.app.deps import RequiredUser, SessionDep
from backend.app.queue import pending_count
from backend.app.templating import render
from backend.app.workflow import latest_rejection

router = APIRouter(tags=["me"])


@router.get("/me", response_class=HTMLResponse)
async def my_time(request: Request, session: SessionDep, user: RequiredUser):
    mine = metrics.own_time(session, user)

    return render(
        request,
        "me.html",
        user=user,
        mine=mine,
        rejection_for=latest_rejection,
        # If they approve for anybody, link to the queue rather than repeating it here.
        awaiting_them=pending_count(session, user),
    )
