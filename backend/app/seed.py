"""First-run seeding.

Applied only to an empty database: the `administrator` role and the bootstrap super user
from `TS_ADMIN_EMAIL` / `TS_ADMIN_PASSWORD`. Running it again is a no-op, so it is safe on
every start — a seed that must only be run once by hand is a seed someone runs twice.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.models import Role, User
from backend.app.security import hash_password

ADMINISTRATOR_ROLE = "administrator"


def ensure_administrator_role(session: Session) -> Role:
    role = session.scalar(select(Role).where(Role.name == ADMINISTRATOR_ROLE))
    if role is None:
        role = Role(
            name=ADMINISTRATOR_ROLE,
            description="License holder and default super user. Unrestricted across all data.",
            is_administrator=True,
        )
        session.add(role)
        session.flush()
    return role


def seed(session: Session, settings: Settings) -> User | None:
    """Create the first administrator. Returns the user when it was created, else None."""
    role = ensure_administrator_role(session)

    if session.scalar(select(User).limit(1)) is not None:
        session.commit()
        return None

    administrator = User(
        email=settings.admin_email.strip().lower(),
        password_hash=hash_password(settings.admin_password),
        full_name="Administrator",
        title="Administrator",
        role_id=role.id,
    )
    session.add(administrator)
    session.commit()
    return administrator
