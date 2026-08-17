"""Login, logout and the session cookie.

The session is a signed, timestamped cookie rather than a row in a table: with no
third-party clients and no horizontal scaling, it is the mechanism with the fewest moving
parts that still expires and cannot be forged (`docs/ARCHITECTURE.md` §2).

The cookie carries the user id and one flag — whether this user must still change the
bootstrap password. The flag is decided once, at login, because verifying an Argon2 hash
on every request would cost more than the check is worth.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.models import User
from backend.app.security import hash_password, verify_password

SESSION_COOKIE = "ts_session"
SESSION_SALT = "ts-timesheets-session"

USER_ID = "uid"
MUST_CHANGE_PASSWORD = "pwc"


def serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=SESSION_SALT)


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def uses_bootstrap_password(user: User, settings: Settings) -> bool:
    """True when the seeded administrator still has the password from `.env`."""
    return user.role.is_administrator and verify_password(
        settings.admin_password, user.password_hash
    )


def issue_session(
    response: Response, user: User, settings: Settings, *, must_change_password: bool
) -> None:
    token = serializer(settings).dumps(
        {USER_ID: user.id, MUST_CHANGE_PASSWORD: must_change_password}
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )


def read_session(request: Request, settings: Settings) -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        payload = serializer(settings).loads(token, max_age=settings.session_max_age)
    except (SignatureExpired, BadSignature):
        return None
    return payload if isinstance(payload, dict) else None


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def set_password(user: User, password: str) -> None:
    user.password_hash = hash_password(password)
