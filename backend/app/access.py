"""Who may see and change what.

Authorization is per object, not per page (`docs/ARCHITECTURE.md` §4). Every project route
goes through here, so there is one place to read and one place to change when EPIC 1
introduces user-defined roles.

A project a user may not see returns **404, not 403**: a 403 confirms the project exists,
which is itself a leak on a system where project codes are meaningful.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from backend.app.models import Project, ProjectMember, User


def can_view(user: User, project: Project) -> bool:
    if user.role.is_administrator:
        return True
    if project.owner_id == user.id or project.manager_id == user.id:
        return True
    return any(member.user_id == user.id for member in project.members)


def can_manage(user: User, project: Project) -> bool:
    """Edit the project, and manage its members and tasks."""
    return user.role.is_administrator or project.owner_id == user.id


def can_create_projects(user: User) -> bool:
    return user.role.is_administrator


def visible_projects_query(user: User) -> Select[tuple[Project]]:
    query = select(Project).order_by(Project.name)
    if user.role.is_administrator:
        return query
    return (
        query.outerjoin(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            or_(
                Project.owner_id == user.id,
                Project.manager_id == user.id,
                ProjectMember.user_id == user.id,
            )
        )
        .distinct()
    )


def get_visible_project(session: Session, user: User, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None or not can_view(user, project):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def get_manageable_project(session: Session, user: User, project_id: int) -> Project:
    project = get_visible_project(session, user, project_id)
    if not can_manage(user, project):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You cannot change this project"
        )
    return project


def require_project_creation(user: User) -> None:
    if not can_create_projects(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only an administrator creates projects"
        )


def require_administrator(user: User) -> None:
    if not user.role.is_administrator:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrators only")
