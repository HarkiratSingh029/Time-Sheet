"""Projects, their members and their tasks.

A time note has nowhere to go without a project: the calendar is per project and bounded
by its dates, and membership is what grants access to it.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from backend.app.access import (
    can_create_projects,
    can_manage,
    get_manageable_project,
    get_visible_project,
    require_project_creation,
    visible_projects_query,
)
from backend.app.deps import RequiredUser, SessionDep, SettingsDep
from backend.app.flash import flash
from backend.app.models import Project, ProjectMember, ProjectStatus, Task, TimeNote, User
from backend.app.templating import render

router = APIRouter(prefix="/projects", tags=["projects"])

EPIC_0_REQUIRED_APPROVALS = 1


class ProjectFormError(ValueError):
    """The submitted project details cannot be saved, with a reason a human can act on."""


@router.get("", response_class=HTMLResponse)
async def list_projects(request: Request, session: SessionDep, user: RequiredUser):
    projects = session.scalars(visible_projects_query(user)).all()
    return render(
        request,
        "projects/list.html",
        user=user,
        projects=projects,
        can_create=can_create_projects(user),
    )


@router.get("/new", response_class=HTMLResponse)
async def new_project_form(request: Request, session: SessionDep, user: RequiredUser):
    require_project_creation(user)
    return render(
        request,
        "projects/form.html",
        user=user,
        project=None,
        people=_people(session),
        statuses=list(ProjectStatus),
        error=None,
    )


@router.post("")
async def create_project(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    name: str = Form(...),
    code: str = Form(...),
    description: str = Form(""),
    comment: str = Form(""),
    owner_id: int = Form(...),
    manager_id: str = Form(""),
    status_value: str = Form(ProjectStatus.PLANNED.value, alias="status"),
    cost: str = Form(""),
    billable_hours_budget: str = Form(""),
    proposed_duration_days: str = Form(""),
    start_date: str = Form(...),
    end_date: str = Form(""),
):
    require_project_creation(user)

    project = Project(required_approvals=EPIC_0_REQUIRED_APPROVALS)
    try:
        _apply_form(
            project,
            name=name,
            code=code,
            description=description,
            comment=comment,
            owner_id=owner_id,
            manager_id=manager_id,
            status_value=status_value,
            cost=cost,
            billable_hours_budget=billable_hours_budget,
            proposed_duration_days=proposed_duration_days,
            start_date=start_date,
            end_date=end_date,
        )
        _assert_code_is_free(session, project.code, project_id=None)
    except ProjectFormError as error:
        return render(
            request,
            "projects/form.html",
            user=user,
            project=None,
            people=_people(session),
            statuses=list(ProjectStatus),
            error=str(error),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    session.add(project)
    session.commit()

    response = RedirectResponse(f"/projects/{project.id}", status_code=status.HTTP_303_SEE_OTHER)
    flash(response, settings, f"Project {project.code} created.")
    return response


@router.get("/{project_id}", response_class=HTMLResponse)
async def project_detail(
    request: Request, session: SessionDep, user: RequiredUser, project_id: int
):
    project = get_visible_project(session, user, project_id)
    logged_minutes = (
        session.scalar(select(TimeNote.duration_minutes).where(TimeNote.project_id == project.id))
        or 0
    )
    return render(
        request,
        "projects/detail.html",
        user=user,
        project=project,
        manageable=can_manage(user, project),
        assignable=_assignable_people(session, project),
        has_time_notes=bool(logged_minutes),
    )


@router.get("/{project_id}/edit", response_class=HTMLResponse)
async def edit_project_form(
    request: Request, session: SessionDep, user: RequiredUser, project_id: int
):
    project = get_manageable_project(session, user, project_id)
    return render(
        request,
        "projects/form.html",
        user=user,
        project=project,
        people=_people(session),
        statuses=list(ProjectStatus),
        error=None,
    )


@router.post("/{project_id}/edit")
async def update_project(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    name: str = Form(...),
    code: str = Form(...),
    description: str = Form(""),
    comment: str = Form(""),
    owner_id: int = Form(...),
    manager_id: str = Form(""),
    status_value: str = Form(ProjectStatus.PLANNED.value, alias="status"),
    cost: str = Form(""),
    billable_hours_budget: str = Form(""),
    proposed_duration_days: str = Form(""),
    start_date: str = Form(...),
    end_date: str = Form(""),
):
    project = get_manageable_project(session, user, project_id)

    try:
        _apply_form(
            project,
            name=name,
            code=code,
            description=description,
            comment=comment,
            owner_id=owner_id,
            manager_id=manager_id,
            status_value=status_value,
            cost=cost,
            billable_hours_budget=billable_hours_budget,
            proposed_duration_days=proposed_duration_days,
            start_date=start_date,
            end_date=end_date,
        )
        _assert_code_is_free(session, project.code, project_id=project.id)
        _assert_window_still_covers_time_notes(session, project)
    except ProjectFormError as error:
        session.rollback()
        return render(
            request,
            "projects/form.html",
            user=user,
            project=session.get(Project, project_id),
            people=_people(session),
            statuses=list(ProjectStatus),
            error=str(error),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    session.commit()

    response = RedirectResponse(f"/projects/{project.id}", status_code=status.HTTP_303_SEE_OTHER)
    flash(response, settings, "Project updated.")
    return response


# --- members --------------------------------------------------------------------------


@router.post("/{project_id}/members")
async def add_member(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    user_id: int = Form(...),
    is_approver: bool = Form(False),
):
    project = get_manageable_project(session, user, project_id)

    person = session.get(User, user_id)
    response = RedirectResponse(f"/projects/{project.id}", status_code=status.HTTP_303_SEE_OTHER)

    if person is None:
        flash(response, settings, "That person no longer exists.", "error")
        return response

    already_a_member = any(member.user_id == user_id for member in project.members)
    if already_a_member:
        flash(response, settings, f"{person.email} is already on this project.", "warning")
        return response

    session.add(ProjectMember(project_id=project.id, user_id=user_id, is_approver=is_approver))
    session.commit()
    flash(response, settings, f"{person.email} added to {project.code}.")
    return response


@router.post("/{project_id}/members/{member_id}/remove")
async def remove_member(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    member_id: int,
):
    project = get_manageable_project(session, user, project_id)
    member = session.get(ProjectMember, member_id)

    response = RedirectResponse(f"/projects/{project.id}", status_code=status.HTTP_303_SEE_OTHER)

    if member is None or member.project_id != project.id:
        flash(response, settings, "That membership no longer exists.", "warning")
        return response

    logged = session.scalar(
        select(TimeNote.id)
        .where(TimeNote.project_id == project.id, TimeNote.user_id == member.user_id)
        .limit(1)
    )
    if logged is not None:
        flash(
            response,
            settings,
            "That person has logged time on this project, so their membership stays. "
            "Removing it would orphan their time notes.",
            "error",
        )
        return response

    session.delete(member)
    session.commit()
    flash(response, settings, "Member removed.")
    return response


# --- tasks ----------------------------------------------------------------------------


@router.post("/{project_id}/tasks")
async def add_task(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    name: str = Form(...),
    description: str = Form(""),
    is_billable: bool = Form(False),
):
    project = get_manageable_project(session, user, project_id)
    response = RedirectResponse(f"/projects/{project.id}", status_code=status.HTTP_303_SEE_OTHER)

    if not name.strip():
        flash(response, settings, "A task needs a name.", "error")
        return response

    session.add(
        Task(
            project_id=project.id,
            name=name.strip(),
            description=description.strip(),
            is_billable=is_billable,
        )
    )
    session.commit()
    flash(response, settings, f"Task “{name.strip()}” added.")
    return response


@router.post("/{project_id}/tasks/{task_id}")
async def update_task(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    task_id: int,
    name: str = Form(...),
    description: str = Form(""),
    is_billable: bool = Form(False),
):
    project = get_manageable_project(session, user, project_id)
    task = _task_of(session, project, task_id)

    response = RedirectResponse(f"/projects/{project.id}", status_code=status.HTTP_303_SEE_OTHER)
    if not name.strip():
        flash(response, settings, "A task needs a name.", "error")
        return response

    task.name = name.strip()
    task.description = description.strip()
    task.is_billable = is_billable
    session.commit()
    flash(response, settings, "Task updated.")
    return response


@router.post("/{project_id}/tasks/{task_id}/archive")
async def archive_task(
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    project_id: int,
    task_id: int,
    archived: bool = Form(True),
):
    project = get_manageable_project(session, user, project_id)
    task = _task_of(session, project, task_id)

    task.is_archived = archived
    session.commit()

    response = RedirectResponse(f"/projects/{project.id}", status_code=status.HTTP_303_SEE_OTHER)
    flash(response, settings, "Task archived." if archived else "Task restored.")
    return response


# --- helpers --------------------------------------------------------------------------


def _people(session) -> list[User]:
    return list(session.scalars(select(User).where(User.is_active).order_by(User.full_name)).all())


def _assignable_people(session, project: Project) -> list[User]:
    assigned = {member.user_id for member in project.members}
    return [person for person in _people(session) if person.id not in assigned]


def _task_of(session, project: Project, task_id: int) -> Task:
    task = session.get(Task, task_id)
    if task is None or task.project_id != project.id:
        # Reached only by editing a URL: a task never leaves its project.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


def _apply_form(project: Project, **fields: object) -> None:
    project.name = _required_text(fields["name"], "Name")
    project.code = _required_text(fields["code"], "Code").upper()
    project.description = str(fields["description"]).strip()
    project.comment = str(fields["comment"]).strip()

    project.owner_id = int(fields["owner_id"])
    project.manager_id = _optional_int(fields["manager_id"])

    project.status = ProjectStatus(str(fields["status_value"]))
    project.cost = _optional_decimal(fields["cost"], "Cost")
    project.billable_hours_budget = _optional_int(fields["billable_hours_budget"])
    project.proposed_duration_days = _optional_int(fields["proposed_duration_days"])

    project.start_date = _required_date(fields["start_date"], "Start date")
    project.end_date = _optional_date(fields["end_date"], "End date")

    if project.end_date is not None and project.end_date < project.start_date:
        raise ProjectFormError("The end date cannot fall before the start date.")


def _assert_code_is_free(session, code: str, *, project_id: int | None) -> None:
    query = select(Project.id).where(Project.code == code)
    if project_id is not None:
        query = query.where(Project.id != project_id)
    if session.scalar(query.limit(1)) is not None:
        raise ProjectFormError(f"Project code {code} is already in use.")


def _assert_window_still_covers_time_notes(session, project: Project) -> None:
    """Narrowing the window under existing time notes would strand them outside it."""
    outside = select(TimeNote.work_date).where(TimeNote.project_id == project.id)
    outside = outside.where(TimeNote.work_date < project.start_date)
    if project.end_date is not None:
        stranded = session.scalar(outside.limit(1)) or session.scalar(
            select(TimeNote.work_date)
            .where(TimeNote.project_id == project.id, TimeNote.work_date > project.end_date)
            .limit(1)
        )
    else:
        stranded = session.scalar(outside.limit(1))

    if stranded is not None:
        raise ProjectFormError(
            f"Time has already been logged on {stranded:%d %b %Y}, which the new dates "
            "would exclude."
        )


def _required_text(value: object, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise ProjectFormError(f"{label} is required.")
    return text


def _required_date(value: object, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise ProjectFormError(f"{label} must be a valid date.") from error


def _optional_date(value: object, label: str) -> date | None:
    text = str(value).strip()
    return _required_date(text, label) if text else None


def _optional_int(value: object) -> int | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as error:
        raise ProjectFormError("Whole numbers only for budgets and durations.") from error


def _optional_decimal(value: object, label: str) -> float | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        amount = float(text)
    except ValueError as error:
        raise ProjectFormError(f"{label} must be a number.") from error
    if amount < 0:
        raise ProjectFormError(f"{label} cannot be negative.")
    return amount
