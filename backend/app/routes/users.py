"""Adding a person, at the minimum EPIC 0 needs.

A project cannot be staffed with people who do not exist, so EPIC 0 needs *some* way to
create one. This is that and nothing more: an administrator types an email, a name and a
first password. Invitations, editing, deactivation and role assignment are story 1.2 of
EPIC 1 (#3).
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from backend.app.access import require_user_management
from backend.app.deps import RequiredUser, SessionDep, SettingsDep
from backend.app.flash import flash
from backend.app.models import User
from backend.app.routes.auth import MINIMUM_PASSWORD_LENGTH
from backend.app.security import hash_password
from backend.app.seed import ensure_member_role
from backend.app.templating import render

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/new", response_class=HTMLResponse)
async def new_user_form(request: Request, user: RequiredUser):
    require_user_management(user)
    return render(request, "users/form.html", user=user, error=None)


@router.post("")
async def create_user(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    email: str = Form(...),
    full_name: str = Form(...),
    password: str = Form(...),
    title: str = Form(""),
):
    require_user_management(user)

    email = email.strip().lower()
    error = _problem(session, email, full_name, password)
    if error is not None:
        return render(
            request,
            "users/form.html",
            user=user,
            error=error,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    session.add(
        User(
            email=email,
            full_name=full_name.strip(),
            title=title.strip(),
            password_hash=hash_password(password),
            role_id=ensure_member_role(session).id,
        )
    )
    session.commit()

    response = RedirectResponse("/projects", status_code=status.HTTP_303_SEE_OTHER)
    flash(response, settings, f"{email} can now sign in.")
    return response


def _problem(session, email: str, full_name: str, password: str) -> str | None:
    if not email or "@" not in email:
        return "That does not look like an email address."
    if not full_name.strip():
        return "A full name is required."
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        return f"Choose a password of at least {MINIMUM_PASSWORD_LENGTH} characters."
    if session.scalar(select(User.id).where(User.email == email).limit(1)) is not None:
        return f"{email} already has an account."
    return None
