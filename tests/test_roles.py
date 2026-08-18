"""Roles as data: the permission vocabulary, the migrations, and a custom role's reach."""

from __future__ import annotations

import ast
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from backend.app import permissions as vocabulary
from backend.app.access import can_create_projects, can_manage, can_view, has_permission
from backend.app.db import EPIC_0_BASELINE, alembic_config, migrate
from backend.app.models import Permission, Project, Role, User
from backend.app.seed import ADMINISTRATOR_ROLE, MEMBER_ROLE, ensure_permissions

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- the vocabulary -------------------------------------------------------------------


def test_every_declared_permission_exists_as_a_row(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        codes = {permission.code for permission in session.scalars(select(Permission)).all()}
    assert codes == set(vocabulary.CODES)


def test_reconciling_the_vocabulary_is_idempotent(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        ensure_permissions(session)
        ensure_permissions(session)
        session.commit()
        assert len(session.scalars(select(Permission)).all()) == len(vocabulary.CODES)


def test_the_administrator_holds_everything_so_lookups_need_no_special_case(
    client: TestClient,
) -> None:
    with client.app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == ADMINISTRATOR_ROLE))
        assert {permission.code for permission in role.permissions} == set(vocabulary.CODES)
        assert role.is_system


def test_the_member_role_can_only_log_time(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == MEMBER_ROLE))
        assert {permission.code for permission in role.permissions} == {vocabulary.TIMESHEET_LOG}


def test_a_permission_added_later_reaches_the_administrator_on_restart(
    client: TestClient, settings
) -> None:
    """A code introduced in a release must not leave the super user unable to use it."""
    from backend.app.seed import ensure_administrator_role

    with client.app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == ADMINISTRATOR_ROLE))
        role.permissions = [
            permission
            for permission in role.permissions
            if permission.code != vocabulary.USER_MANAGE
        ]
        session.commit()
        assert not role.holds(vocabulary.USER_MANAGE)

        ensure_administrator_role(session)
        session.commit()
        assert role.holds(vocabulary.USER_MANAGE)


def test_the_member_role_is_not_regranted_behind_an_administrators_back(
    client: TestClient,
) -> None:
    from backend.app.seed import ensure_member_role

    with client.app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == MEMBER_ROLE))
        role.permissions = []
        session.commit()

        ensure_member_role(session)
        session.commit()
        assert role.permissions == [], "a deliberate removal must survive a restart"


# --- a custom role --------------------------------------------------------------------


@pytest.fixture
def reviewer(client: TestClient, administrator, make_person, sign_in):
    """Someone who may read every project and change none of them."""
    person = make_person("reviewer@example.com", "Rae Reviewer")

    with client.app.state.session_factory() as session:
        catalogue = ensure_permissions(session)
        role = Role(name="finance reviewer", description="Reads every project.")
        role.permissions.append(catalogue[vocabulary.PROJECT_VIEW_ANY])
        session.add(role)
        session.flush()
        session.get(User, person.id).role_id = role.id
        session.commit()

    return person


def test_a_view_any_role_reads_every_project_and_edits_none(
    client: TestClient, administrator, reviewer, sign_in
) -> None:
    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Ledger migration",
            "code": "ledger",
            "description": "",
            "comment": "",
            "owner_id": str(administrator.id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "",
            "proposed_duration_days": "",
            "start_date": "2026-01-01",
            "end_date": "2026-06-30",
        },
    )
    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "LEDGER"))

    sign_in(reviewer)
    assert "LEDGER" in client.get("/projects").text, "view_any means the list, too"
    assert client.get(f"/projects/{project_id}").status_code == 200
    assert client.get(f"/projects/{project_id}/edit").status_code == 403
    assert client.get("/projects/new").status_code == 403
    assert client.get("/users/new").status_code == 403
    assert client.post(f"/projects/{project_id}/tasks", data={"name": "Snuck"}).status_code == 403


def test_permissions_are_read_per_request_not_cached_until_restart(
    client: TestClient, administrator, reviewer, sign_in
) -> None:
    sign_in(reviewer)
    assert client.get("/projects/new").status_code == 403

    with client.app.state.session_factory() as session:
        catalogue = ensure_permissions(session)
        role = session.scalar(select(Role).where(Role.name == "finance reviewer"))
        role.permissions.append(catalogue[vocabulary.PROJECT_CREATE])
        session.commit()

    assert client.get("/projects/new").status_code == 200, "the next request, no restart"


def test_the_helpers_read_permissions_rather_than_the_role_name(
    client: TestClient, administrator, reviewer
) -> None:
    with client.app.state.session_factory() as session:
        person = session.get(User, reviewer.id)
        admin = session.get(User, administrator.id)
        project = Project(
            name="Anything",
            code="ANY",
            owner_id=administrator.id,
            start_date=__import__("datetime").date(2026, 1, 1),
        )
        session.add(project)
        session.flush()

        assert has_permission(person, vocabulary.PROJECT_VIEW_ANY)
        assert can_view(person, project)
        assert not can_manage(person, project)
        assert not can_create_projects(person)

        assert can_manage(admin, project)
        assert can_create_projects(admin)


def test_access_py_asks_permissions_and_never_the_administrator_flag() -> None:
    """Read the AST, not the text: prose about the rule must not be mistaken for breaking it."""
    tree = ast.parse((REPO_ROOT / "backend" / "app" / "access.py").read_text())
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }

    assert "is_administrator" not in names, (
        "authorization must read permissions, so a custom role behaves like a real one"
    )


# --- migrations -----------------------------------------------------------------------


def test_the_schema_builds_from_empty(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    engine = create_engine(url)
    migrate(engine, url)

    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(text("select name from sqlite_master where type='table'"))
        }
    assert {"permissions", "role_permissions", "roles", "time_notes"} <= tables


def test_an_existing_epic_0_database_is_stamped_and_upgraded_without_loss(tmp_path) -> None:
    """The upgrade path that matters: somebody already has data from EPIC 0."""
    database = tmp_path / "legacy.db"
    url = f"sqlite:///{database}"

    # Build the EPIC 0 schema exactly as it shipped, then put a row in it.
    baseline_engine = create_engine(url)
    config = alembic_config(url)
    from alembic import command

    command.upgrade(config, EPIC_0_BASELINE)
    connection = sqlite3.connect(database)
    connection.execute(
        "insert into roles (name, description, is_administrator, created_at, updated_at) "
        "values ('administrator', 'seeded', 1, '2026-01-01', '2026-01-01')"
    )
    connection.commit()
    connection.execute("delete from alembic_version")  # pre-Alembic database
    connection.commit()
    connection.close()
    baseline_engine.dispose()

    engine = create_engine(url)
    migrate(engine, url)

    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from roles")).scalar() == 1, (
            "an upgrade must never cost somebody their data"
        )
        assert connection.execute(text("select count(*) from permissions")).scalar() == 0
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(roles)"))}
    assert "is_system" in columns


def test_the_models_and_the_migrations_do_not_drift(tmp_path) -> None:
    """A model changed without a migration is a deploy that fails on somebody else's machine."""
    database = tmp_path / "drift.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    migrate(engine, url)
    engine.dispose()

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "TS_SECRET_KEY": "check", "TS_DATABASE_URL": url},
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- protecting the system roles ------------------------------------------------------


def test_a_system_role_cannot_be_deleted_renamed_or_demoted(client: TestClient) -> None:
    from backend.app.seed import SystemRoleError

    with client.app.state.session_factory() as session:
        role = session.scalar(select(Role).where(Role.name == ADMINISTRATOR_ROLE))

        session.delete(role)
        with pytest.raises(SystemRoleError):
            session.commit()
        session.rollback()

        role = session.scalar(select(Role).where(Role.name == ADMINISTRATOR_ROLE))
        role.name = "sudo"
        with pytest.raises(SystemRoleError):
            session.commit()
        session.rollback()

        role = session.scalar(select(Role).where(Role.name == ADMINISTRATOR_ROLE))
        role.is_administrator = False
        with pytest.raises(SystemRoleError):
            session.commit()
        session.rollback()

        assert session.scalar(select(Role).where(Role.name == ADMINISTRATOR_ROLE)) is not None


def test_a_custom_role_is_freely_deletable(client: TestClient) -> None:
    with client.app.state.session_factory() as session:
        role = Role(name="temporary", description="Made up for this test.")
        session.add(role)
        session.commit()

        session.delete(role)
        session.commit()
        assert session.scalar(select(Role).where(Role.name == "temporary")) is None
