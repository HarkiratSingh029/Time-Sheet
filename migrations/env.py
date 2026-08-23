"""Alembic environment.

The URL comes from the application's own settings rather than alembic.ini, so there is
exactly one answer to "which database is this" (`backend/app/config.py`).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.app.config import load_settings
from backend.app.db import resolve_database_url
from backend.app.models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers defaults to True, which would switch off every logger the
    # application already created — including ts.notifications and ts.scheduler, whose
    # warnings are the only sign that a digest failed. Migrations run at startup, so the
    # default would silence the app in production.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    configured = config.get_main_option("sqlalchemy.url", default=None)
    if configured:
        return configured
    return resolve_database_url(load_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = database_url()

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things in place; batch mode rewrites the table.
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
