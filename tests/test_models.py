"""The data layer: pragmas, constraints, cross-table invariants and seeding.

One module for the whole layer rather than one per model — these rules only mean anything
together, and a fast suite is worth more than a granular one (CHANGE_MANAGEMENT.md §4.7).
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import Settings, load_settings
from backend.app.db import InvariantError, init_database, resolve_database_url
from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    ProjectMember,
    Task,
    TimeNote,
    TimeNoteState,
    User,
)
from backend.app.security import hash_password, verify_password
from backend.app.seed import ADMINISTRATOR_ROLE, seed

PROJECT_START = date(2026, 1, 1)
PROJECT_END = date(2026, 6, 30)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return load_settings(
        _env_file=None,
        secret_key="test-secret-key",
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'data' / 'test.db'}",
        admin_email="Admin@Example.COM",
        admin_password="bootstrap-password",
    )


@pytest.fixture
def session(settings: Settings) -> Session:
    _engine, factory = init_database(settings)
    with factory() as db_session:
        seed(db_session, settings)
        yield db_session


@pytest.fixture
def project(session: Session) -> Project:
    owner = session.scalar(User.__table__.select().with_only_columns(User.id))
    project = Project(
        name="Ledger migration",
        code="LEDGER",
        owner_id=owner,
        start_date=PROJECT_START,
        end_date=PROJECT_END,
        required_approvals=1,
    )
    session.add(project)
    session.commit()
    return project


@pytest.fixture
def task(session: Session, project: Project) -> Task:
    task = Task(project_id=project.id, name="Discovery")
    session.add(task)
    session.commit()
    return task


def make_note(project: Project, task: Task, user_id: int, **overrides) -> TimeNote:
    values = {
        "project_id": project.id,
        "task_id": task.id,
        "user_id": user_id,
        "work_date": date(2026, 2, 3),
        "duration_minutes": 480,
        "detail": "Reviewed the existing ledger schema",
    }
    values.update(overrides)
    return TimeNote(**values)


# --- engine ---------------------------------------------------------------------------


def test_sqlite_runs_in_wal_with_foreign_keys_enforced(settings: Settings) -> None:
    engine, _factory = init_database(settings)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_relative_sqlite_paths_anchor_to_the_repo_root() -> None:
    assert resolve_database_url("sqlite:///data/app.db").startswith("sqlite:////")
    assert resolve_database_url("sqlite:////absolute/app.db") == "sqlite:////absolute/app.db"
    assert resolve_database_url("postgresql://host/db") == "postgresql://host/db"


# --- seeding --------------------------------------------------------------------------


def test_seeding_is_idempotent_and_normalises_the_admin_email(
    session: Session, settings: Settings
) -> None:
    assert seed(session, settings) is None, "a second seed must not create another admin"

    admins = session.query(User).all()
    assert len(admins) == 1
    assert admins[0].email == "admin@example.com"
    assert admins[0].role.name == ADMINISTRATOR_ROLE
    assert admins[0].is_administrator


def test_the_admin_password_is_hashed_not_stored(session: Session) -> None:
    administrator = session.query(User).one()
    assert "bootstrap-password" not in administrator.password_hash
    assert administrator.password_hash.startswith("$argon2")
    assert verify_password("bootstrap-password", administrator.password_hash)
    assert not verify_password("wrong-password", administrator.password_hash)


# --- constraints ----------------------------------------------------------------------


def test_duration_must_be_positive_and_within_a_day(
    session: Session, project: Project, task: Task
) -> None:
    administrator = session.query(User).one()

    for invalid in (0, -30, 1441):
        session.add(make_note(project, task, administrator.id, duration_minutes=invalid))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_required_approvals_must_be_between_one_and_five(session: Session) -> None:
    owner = session.query(User).one()

    for invalid in (0, 6):
        session.add(
            Project(
                name="Out of range",
                code=f"RANGE{invalid}",
                owner_id=owner.id,
                start_date=PROJECT_START,
                required_approvals=invalid,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_a_project_cannot_end_before_it_starts(session: Session) -> None:
    owner = session.query(User).one()
    session.add(
        Project(
            name="Backwards",
            code="BACK",
            owner_id=owner.id,
            start_date=PROJECT_END,
            end_date=PROJECT_START,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_membership_is_unique_per_project_and_user(session: Session, project: Project) -> None:
    administrator = session.query(User).one()
    session.add(ProjectMember(project_id=project.id, user_id=administrator.id))
    session.commit()

    session.add(ProjectMember(project_id=project.id, user_id=administrator.id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_a_rejection_requires_a_comment(session: Session, project: Project, task: Task) -> None:
    administrator = session.query(User).one()
    note = make_note(project, task, administrator.id, state=TimeNoteState.SUBMITTED)
    session.add(note)
    session.commit()

    session.add(
        Approval(
            time_note_id=note.id,
            approver_id=administrator.id,
            sequence=1,
            decision=ApprovalDecision.REJECTED,
            comment="   ",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.add(
        Approval(
            time_note_id=note.id,
            approver_id=administrator.id,
            sequence=1,
            decision=ApprovalDecision.REJECTED,
            comment="Duration does not match the statement of work",
        )
    )
    session.commit()


def test_deleting_a_project_with_time_notes_is_refused(
    session: Session, project: Project, task: Task
) -> None:
    administrator = session.query(User).one()
    session.add(make_note(project, task, administrator.id))
    session.commit()

    session.delete(project)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- cross-table invariants -----------------------------------------------------------


def test_work_date_must_fall_inside_the_project_window(
    session: Session, project: Project, task: Task
) -> None:
    administrator = session.query(User).one()

    for outside in (date(2025, 12, 31), date(2026, 7, 1)):
        session.add(make_note(project, task, administrator.id, work_date=outside))
        with pytest.raises(InvariantError):
            session.commit()
        session.rollback()

    session.add(make_note(project, task, administrator.id, work_date=PROJECT_START))
    session.commit()


def test_a_time_note_cannot_borrow_a_task_from_another_project(
    session: Session, project: Project, task: Task
) -> None:
    administrator = session.query(User).one()
    other = Project(
        name="Unrelated",
        code="OTHER",
        owner_id=administrator.id,
        start_date=PROJECT_START,
        end_date=PROJECT_END,
    )
    session.add(other)
    session.commit()

    session.add(make_note(other, task, administrator.id))
    with pytest.raises(InvariantError):
        session.commit()
    session.rollback()


def test_durations_are_stored_in_minutes(session: Session, project: Project, task: Task) -> None:
    administrator = session.query(User).one()
    note = make_note(project, task, administrator.id, duration_minutes=7 * 60 + 30)
    session.add(note)
    session.commit()

    assert session.get(TimeNote, note.id).duration_minutes == 450
    assert note.state is TimeNoteState.DRAFT


def test_password_hashes_are_salted_per_user() -> None:
    assert hash_password("same-password") != hash_password("same-password")
