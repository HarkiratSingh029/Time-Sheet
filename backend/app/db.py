"""Database engine, session lifecycle and the invariants SQLite cannot express alone.

SQLite in WAL mode is the whole database (`docs/ARCHITECTURE.md` §2). Two pragmas make it
behave the way the rest of the code assumes: WAL so a reader never blocks the writer, and
`foreign_keys=ON` because SQLite ignores foreign keys by default — a silently unenforced
relationship is worse than none at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection as SQLiteConnection

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from fastapi import Request
from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import REPO_ROOT, Settings
from backend.app.models import Project, Task, TimeNote

SQLITE_PREFIX = "sqlite:///"

ALEMBIC_INI = REPO_ROOT / "alembic.ini"

# The revision describing the schema as EPIC 0 shipped it, before Alembic existed. A
# database created by the old `create_all` matches this exactly, so it is stamped here and
# then upgraded rather than rebuilt.
EPIC_0_BASELINE = "da2d3ed4245c"


class InvariantError(ValueError):
    """A cross-row rule no single-table constraint can express."""


def resolve_database_url(url: str) -> str:
    """Anchor a relative SQLite path to the repo root, so cwd cannot change the database."""
    if not url.startswith(SQLITE_PREFIX):
        return url
    path = url[len(SQLITE_PREFIX) :]
    if path.startswith("/") or path == ":memory:":
        return url
    return f"{SQLITE_PREFIX}{(REPO_ROOT / path).as_posix()}"


def create_db_engine(settings: Settings) -> Engine:
    url = resolve_database_url(settings.database_url)

    if url.startswith(SQLITE_PREFIX):
        file_path = url[len(SQLITE_PREFIX) :]
        if file_path != ":memory:":
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        url,
        # Uvicorn serves requests from a thread pool; the connection is not pinned to one.
        connect_args={"check_same_thread": False} if url.startswith(SQLITE_PREFIX) else {},
        echo=False,
        future=True,
    )
    _apply_sqlite_pragmas(engine)
    return engine


def _apply_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: object, _record: object) -> None:
        if not isinstance(dbapi_connection, SQLiteConnection):
            return
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    event.listen(factory, "before_flush", _enforce_time_note_invariants)
    return factory


def _enforce_time_note_invariants(session: Session, _context: object, _instances: object) -> None:
    """Guard the two rules that span tables, so no route can bypass them.

    A `CHECK` constraint cannot read another table, so these live at the session boundary
    rather than in a handler — every write path flushes through here.
    """
    for instance in list(session.new) + list(session.dirty):
        if not isinstance(instance, TimeNote):
            continue

        project = instance.project or session.get(Project, instance.project_id)
        if project is None:
            continue

        if instance.work_date < project.start_date:
            raise InvariantError(
                f"work_date {instance.work_date} is before project {project.code} "
                f"starts on {project.start_date}"
            )
        if project.end_date and instance.work_date > project.end_date:
            raise InvariantError(
                f"work_date {instance.work_date} is after project {project.code} "
                f"ends on {project.end_date}"
            )

        task = instance.task or session.get(Task, instance.task_id)
        if task is not None and task.project_id != project.id:
            raise InvariantError(
                f"task {task.id} belongs to project {task.project_id}, not project {project.id}"
            )


def alembic_config(url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _needs_stamping(engine: Engine) -> bool:
    """True for a pre-Alembic database: it has our tables but no revision recorded."""
    with engine.connect() as connection:
        if MigrationContext.configure(connection).get_current_revision() is not None:
            return False
        return inspect(engine).has_table("roles")


def migrate(engine: Engine, url: str) -> None:
    config = alembic_config(url)
    if _needs_stamping(engine):
        command.stamp(config, EPIC_0_BASELINE)
    command.upgrade(config, "head")


def init_database(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    url = resolve_database_url(settings.database_url)
    engine = create_db_engine(settings)
    migrate(engine, url)
    return engine, create_session_factory(engine)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """One session per unit of work: commit on success, roll back on anything else."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency. The factory is built once, during lifespan startup."""
    yield from session_scope(request.app.state.session_factory)
