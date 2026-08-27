"""Export routes.

Both formats are streamed or generated on request and scoped to what the caller may
already see — an export must never become a way to read a project you cannot open.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response, StreamingResponse

from backend.app import exports, metrics
from backend.app.access import get_visible_project, visible_projects_query
from backend.app.deps import RequiredUser, SessionDep, SettingsDep
from backend.app.models import Project, TimeNoteState
from backend.app.queue import parse_filters

router = APIRouter(prefix="/exports", tags=["exports"])


def _state(value: str | None) -> TimeNoteState | None:
    try:
        return TimeNoteState(value) if value else None
    except ValueError:
        return None


@router.get("/timesheet.csv")
async def timesheet_csv(
    session: SessionDep,
    user: RequiredUser,
    project_id: str | None = None,
    person_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    state: str | None = None,
):
    filters = parse_filters(project_id, person_id, since, until)

    query = exports.timesheet_query(
        visible_projects_query(user),
        project_id=filters.project_id,
        person_id=filters.person_id,
        since=filters.since,
        until=filters.until,
        state=_state(state),
    )

    project = session.get(Project, filters.project_id) if filters.project_id else None
    filename = exports.filename_for(project, filters.since, filters.until)

    return StreamingResponse(
        exports.stream_csv(session, query),
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/projects/{project_id}/report.pdf")
async def project_report(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    since: str | None = None,
    until: str | None = None,
):
    project = get_visible_project(session, user, project_id)
    filters = parse_filters(None, None, since, until)

    figures = metrics.for_project(session, project, settings)

    query = exports.timesheet_query(
        visible_projects_query(user),
        project_id=project.id,
        since=filters.since,
        until=filters.until,
        state=TimeNoteState.APPROVED,
    )
    notes = list(session.scalars(query).all())

    try:
        document = exports.project_report(
            figures, notes, settings, date.today(), filters.since, filters.until
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The report could not be produced",
        ) from error

    return Response(
        content=document,
        media_type="application/pdf",
        headers={
            "content-disposition": f'attachment; filename="{project.code.lower()}-report.pdf"'
        },
    )
