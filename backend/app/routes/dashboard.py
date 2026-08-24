"""The portfolio view.

`docs/DATA_MODEL.md` §4 asks for a global picture: every project owner sees every project,
so the health of the portfolio is one screen rather than a tour of twenty. It is also where
an approval queue that has stopped moving becomes visible to somebody other than the
approver sitting on it.

Every figure links to the thing that explains it. A dashboard nobody can act from is
decoration.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from backend.app import metrics
from backend.app.access import has_permission
from backend.app.deps import RequiredUser, SessionDep, SettingsDep
from backend.app.models import Project
from backend.app.permissions import PROJECT_VIEW_ANY
from backend.app.queue import AGEING_DAYS
from backend.app.templating import render

router = APIRouter(tags=["dashboard"])


def can_see_portfolio(user) -> bool:
    return has_permission(user, PROJECT_VIEW_ANY)


@router.get("/dashboard", response_class=HTMLResponse)
async def portfolio_dashboard(
    request: Request, session: SessionDep, settings: SettingsDep, user: RequiredUser
):
    if not can_see_portfolio(user):
        # 404 rather than 403: how many projects exist is itself information.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    projects = list(session.scalars(select(Project).order_by(Project.name)).all())
    view = metrics.portfolio(session, projects, settings)

    return render(
        request,
        "dashboard.html",
        user=user,
        portfolio=view,
        ageing_days=AGEING_DAYS,
    )
