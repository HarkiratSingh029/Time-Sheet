"""Request dependencies: who is asking, and may they be here.

Authorization is per object rather than per page (`docs/ARCHITECTURE.md` §4); these
dependencies answer only the first question — is there a valid session at all.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from backend.app.auth import MUST_CHANGE_PASSWORD, USER_ID, read_session
from backend.app.config import Settings
from backend.app.db import get_session
from backend.app.models import User


class NotAuthenticated(Exception):
    """No valid session. Rendered as a redirect for pages, 401 for the API."""


class PasswordChangeRequired(Exception):
    """Authenticated, but still on the bootstrap password."""


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings_from_app)]
SessionDep = Annotated[Session, Depends(get_session)]


def current_user(request: Request, session: SessionDep, settings: SettingsDep) -> User | None:
    payload = read_session(request, settings)
    if payload is None:
        return None

    user = session.get(User, payload.get(USER_ID))
    if user is None or not user.is_active:
        return None

    request.state.must_change_password = bool(payload.get(MUST_CHANGE_PASSWORD))
    return user


CurrentUser = Annotated[User | None, Depends(current_user)]


def require_user(request: Request, user: CurrentUser) -> User:
    if user is None:
        raise NotAuthenticated
    if getattr(request.state, "must_change_password", False):
        raise PasswordChangeRequired
    return user


def require_user_allowing_password_change(user: CurrentUser) -> User:
    """For the password-change screen itself, which must stay reachable while blocked."""
    if user is None:
        raise NotAuthenticated
    return user


RequiredUser = Annotated[User, Depends(require_user)]
PasswordChangeUser = Annotated[User, Depends(require_user_allowing_password_change)]
