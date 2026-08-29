"""The search page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from backend.app import search as finder
from backend.app.deps import RequiredUser, SessionDep
from backend.app.models import Project, TimeNote, User
from backend.app.queue import parse_filters
from backend.app.templating import render

router = APIRouter(tags=["search"])


@router.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    session: SessionDep,
    user: RequiredUser,
    q: str = "",
    project_id: str | None = None,
    person_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    filters = parse_filters(project_id, person_id, since, until)
    results = finder.search(session, user, q, filters)

    # Only offer filters over projects the searcher can already reach.
    projects = list(
        session.scalars(
            select(Project)
            .where(Project.id.in_(finder.visible_project_ids(user)))
            .order_by(Project.name)
        ).all()
    )

    return render(
        request,
        "search.html",
        user=user,
        results=results,
        filters=filters,
        query=q,
        projects=projects,
        people=_people(session, user),
        minimum=finder.MIN_QUERY_LENGTH,
        limit=finder.RESULT_LIMIT,
    )


def _people(session: SessionDep, user: User) -> list[User]:
    """Everyone whose time the searcher can already see, for the person filter.

    Derived from the notes they may read rather than from the user table, so the filter
    cannot become a staff directory.
    """
    return list(
        session.scalars(
            select(User)
            .where(
                User.id.in_(
                    select(TimeNote.user_id).where(
                        TimeNote.project_id.in_(finder.visible_project_ids(user))
                    )
                )
            )
            .order_by(User.full_name)
        ).all()
    )
