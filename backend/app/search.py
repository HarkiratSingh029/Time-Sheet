"""One search across projects, people and time notes.

Two things decide the shape of this module.

**Search must never become the way to see a project you cannot open.** Every query is
scoped through the same `visible_projects_query` the project list uses, so a non-member
searching an exact project code gets nothing — not the name, not a count, not the fact it
exists. That is the difference between a search box and an enumeration oracle.

**`LIKE` has wildcards, and a user typed this.** `%` and `_` are not literal to SQL, so a
search for `%` would otherwise match every row in the database. They are escaped here, and
the escape character is declared, because a search that silently means something else is a
bug waiting for the one person who types a percent sign.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from backend.app.access import has_permission
from backend.app.models import Project, Task, TimeNote, User
from backend.app.permissions import PROJECT_VIEW_ANY, USER_MANAGE
from backend.app.queue import Filters

# Backslash, because it is the one character a person is least likely to type into a
# timesheet search and then be surprised by.
LIKE_ESCAPE = "\\"

MIN_QUERY_LENGTH = 2
RESULT_LIMIT = 50


def escape_like(term: str) -> str:
    """Make a user's text mean itself.

    `%` and `_` are SQL wildcards; unescaped, a search for `%` matches everything, which is
    both wrong and a way to dump the table.
    """
    return (
        term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def contains(column, term: str):
    return column.ilike(f"%{escape_like(term)}%", escape=LIKE_ESCAPE)


@dataclass
class Results:
    query: str
    projects: list[Project] = field(default_factory=list)
    people: list[User] = field(default_factory=list)
    notes: list[TimeNote] = field(default_factory=list)
    truncated: bool = False

    @property
    def total(self) -> int:
        return len(self.projects) + len(self.people) + len(self.notes)

    @property
    def is_empty(self) -> bool:
        return self.total == 0


def visible_project_ids(user: User):
    """The subquery every result set is scoped through."""
    from backend.app.access import visible_projects_query

    return select(
        visible_projects_query(user).with_only_columns(Project.id).order_by(None).subquery().c.id
    )


def find_projects(session: Session, user: User, term: str) -> list[Project]:
    query = (
        select(Project)
        .where(
            Project.id.in_(visible_project_ids(user)),
            or_(contains(Project.code, term), contains(Project.name, term)),
        )
        .order_by(Project.name)
        .limit(RESULT_LIMIT)
    )
    return list(session.scalars(query).all())


def find_people(session: Session, user: User, term: str) -> list[User]:
    """People are searchable by anyone who could already meet them.

    Someone who manages users sees everybody. Everybody else sees only people they share a
    project with — a directory of the whole company is a different feature, and not one this
    search should quietly grant.
    """
    query = select(User).where(or_(contains(User.full_name, term), contains(User.email, term)))

    if not has_permission(user, USER_MANAGE) and not has_permission(user, PROJECT_VIEW_ANY):
        shared = select(TimeNote.user_id).where(TimeNote.project_id.in_(visible_project_ids(user)))
        from backend.app.models import ProjectMember

        member_of = select(ProjectMember.user_id).where(
            ProjectMember.project_id.in_(visible_project_ids(user))
        )
        query = query.where(or_(User.id.in_(shared), User.id.in_(member_of), User.id == user.id))

    return list(session.scalars(query.order_by(User.full_name).limit(RESULT_LIMIT)).all())


def find_notes(session: Session, user: User, term: str, filters: Filters) -> list[TimeNote]:
    query = (
        select(TimeNote)
        .join(Task, Task.id == TimeNote.task_id)
        .where(
            TimeNote.project_id.in_(visible_project_ids(user)),
            or_(contains(TimeNote.detail, term), contains(Task.name, term)),
        )
        .options(
            selectinload(TimeNote.project),
            selectinload(TimeNote.task),
            selectinload(TimeNote.user),
        )
        .order_by(TimeNote.work_date.desc(), TimeNote.id.desc())
        .limit(RESULT_LIMIT)
    )

    if filters.project_id is not None:
        query = query.where(TimeNote.project_id == filters.project_id)
    if filters.person_id is not None:
        query = query.where(TimeNote.user_id == filters.person_id)
    if filters.since is not None:
        query = query.where(TimeNote.work_date >= filters.since)
    if filters.until is not None:
        query = query.where(TimeNote.work_date <= filters.until)

    return list(session.scalars(query).all())


def search(session: Session, user: User, term: str, filters: Filters | None = None) -> Results:
    """Everything the searcher may see that matches, grouped by kind.

    A short or empty term returns nothing rather than everything: a blank search box is a
    question nobody asked, and answering it with the whole database is both useless and a
    way to page through data one row at a time.
    """
    term = (term or "").strip()
    if len(term) < MIN_QUERY_LENGTH:
        return Results(query=term)

    filters = filters or Filters()
    results = Results(
        query=term,
        projects=find_projects(session, user, term),
        people=find_people(session, user, term),
        notes=find_notes(session, user, term, filters),
    )
    results.truncated = any(
        len(group) == RESULT_LIMIT for group in (results.projects, results.people, results.notes)
    )
    return results
