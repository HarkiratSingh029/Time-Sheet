"""First-run seeding, and the invariants the seed keeps on every start.

Two things run every time, not only on an empty database:

* the permission vocabulary is reconciled with `backend/app/permissions.py`, so a code
  added in one release exists as a row in the next;
* the administrator role is regranted everything, so a permission introduced later does
  not silently leave the super user unable to use it.

Creating the bootstrap administrator, by contrast, happens only when there are no users at
all. Running the whole thing twice is a no-op — a seed that must only ever be run once by
hand is a seed someone runs twice.
"""

from __future__ import annotations

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from backend.app import permissions as vocabulary
from backend.app.config import Settings
from backend.app.models import Permission, Role, User
from backend.app.security import hash_password

ADMINISTRATOR_ROLE = "administrator"
MEMBER_ROLE = "member"


def ensure_permissions(session: Session) -> dict[str, Permission]:
    existing = {
        permission.code: permission for permission in session.scalars(select(Permission)).all()
    }
    for spec in vocabulary.ALL:
        permission = existing.get(spec.code)
        if permission is None:
            permission = Permission(code=spec.code, description=spec.description)
            session.add(permission)
            existing[spec.code] = permission
        else:
            permission.description = spec.description
    session.flush()
    return existing


def _ensure_role(
    session: Session,
    name: str,
    description: str,
    *,
    administrator: bool,
    grants: tuple[str, ...],
    regrant: bool,
) -> Role:
    role = session.scalar(select(Role).where(Role.name == name))
    if role is None:
        role = Role(
            name=name,
            description=description,
            is_administrator=administrator,
            is_system=True,
        )
        session.add(role)
        session.flush()
        regrant = True

    if regrant:
        catalogue = ensure_permissions(session)
        held = {permission.code for permission in role.permissions}
        for code in grants:
            if code not in held:
                role.permissions.append(catalogue[code])
    session.flush()
    return role


def ensure_administrator_role(session: Session) -> Role:
    """The super user. Regranted every permission on each start, by design."""
    return _ensure_role(
        session,
        ADMINISTRATOR_ROLE,
        "License holder and default super user. Unrestricted across all data.",
        administrator=True,
        grants=vocabulary.ADMINISTRATOR_GRANTS,
        regrant=True,
    )


def ensure_member_role(session: Session) -> Role:
    """The default non-administrator role.

    Granted only `timesheet.log` on creation and never regranted afterwards: an
    administrator who removes a permission from it should not find it back after a restart.
    """
    return _ensure_role(
        session,
        MEMBER_ROLE,
        "Standard user. Access follows project membership.",
        administrator=False,
        grants=vocabulary.MEMBER_GRANTS,
        regrant=False,
    )


class SystemRoleError(RuntimeError):
    """A system role was deleted or renamed. It underpins access for everyone."""


class UserDeletionError(RuntimeError):
    """A user with a work history was deleted. Deactivate them instead."""


@event.listens_for(Session, "before_flush")
def _protect_system_roles(session: Session, _context: object, _instances: object) -> None:
    """The administrator role cannot be deleted, renamed, or demoted.

    Guarded at the session boundary rather than in a route, so a future admin screen
    cannot reach around it (the same reasoning as the time-note invariants in db.py).
    """
    for instance in session.deleted:
        if isinstance(instance, Role) and instance.is_system:
            raise SystemRoleError(f"The {instance.name} role is built in and cannot be deleted.")

        if isinstance(instance, User) and (instance.time_notes or instance.memberships):
            raise UserDeletionError(
                f"{instance.email} has a work history. Deactivate the account instead — "
                "their time notes are the record of work that actually happened."
            )

    for instance in session.dirty:
        if not isinstance(instance, Role) or not instance.is_system:
            continue
        history = inspect(instance).attrs
        if history.name.history.has_changes():
            raise SystemRoleError("A built-in role cannot be renamed.")
        if history.is_system.history.has_changes():
            raise SystemRoleError("A built-in role cannot stop being built in.")
        if instance.name == ADMINISTRATOR_ROLE and history.is_administrator.history.has_changes():
            raise SystemRoleError("The administrator role cannot be demoted.")


def seed(session: Session, settings: Settings) -> User | None:
    """Reconcile roles and permissions; create the first administrator on an empty database."""
    ensure_permissions(session)
    role = ensure_administrator_role(session)
    ensure_member_role(session)

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
