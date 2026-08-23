"""Login, logout and the forced first-login password change."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.app.auth import authenticate, clear_session, issue_session, set_password
from backend.app.auth import uses_bootstrap_password as still_on_bootstrap_password
from backend.app.deps import PasswordChangeUser, SessionDep, SettingsDep, current_user
from backend.app.flash import flash
from backend.app.models import utcnow
from backend.app.security import verify_password
from backend.app.templating import render

router = APIRouter(tags=["auth"])

MINIMUM_PASSWORD_LENGTH = 12


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, session: SessionDep, settings: SettingsDep):
    if current_user(request, session, settings) is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "login.html", error=None)


@router.post("/login")
async def login(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    email: str = Form(...),
    password: str = Form(...),
):
    user = authenticate(session, email, password)
    if user is None:
        return render(
            request,
            "login.html",
            error="That email and password do not match an active account.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user.last_signed_in_at = utcnow()
    session.commit()

    must_change = still_on_bootstrap_password(user, settings)
    destination = "/account/password" if must_change else "/"
    response = RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
    issue_session(response, user, settings, must_change_password=must_change)
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_session(response)
    return response


@router.get("/account", response_class=HTMLResponse)
async def account(request: Request, user: PasswordChangeUser, settings: SettingsDep):
    return render(
        request,
        "account.html",
        user=user,
        email_enabled=settings.email_enabled,
    )


@router.post("/account/digest")
async def set_digest_preference(
    session: SessionDep,
    settings: SettingsDep,
    user: PasswordChangeUser,
    receive_digest: bool = Form(False),
):
    user.digest_opt_out = not receive_digest
    session.add(user)
    session.commit()

    response = RedirectResponse("/account", status_code=status.HTTP_303_SEE_OTHER)
    flash(
        response,
        settings,
        "Daily summary on." if receive_digest else "Daily summary off. Badges stay.",
    )
    return response


@router.get("/account/password", response_class=HTMLResponse)
async def password_form(request: Request, user: PasswordChangeUser):
    return render(
        request,
        "account_password.html",
        user=user,
        error=None,
        forced=getattr(request.state, "must_change_password", False),
    )


@router.post("/account/password")
async def change_password(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: PasswordChangeUser,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    forced = getattr(request.state, "must_change_password", False)

    error = _password_problem(user.password_hash, current_password, new_password, confirm_password)
    if error is not None:
        return render(
            request,
            "account_password.html",
            user=user,
            error=error,
            forced=forced,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    set_password(user, new_password)
    session.add(user)
    session.commit()

    # Reissue the cookie so the "must change password" flag is dropped.
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    issue_session(response, user, settings, must_change_password=False)
    return response


def _password_problem(
    password_hash: str, current_password: str, new_password: str, confirm_password: str
) -> str | None:
    if not verify_password(current_password, password_hash):
        return "Your current password is not correct."
    if new_password != confirm_password:
        return "The new passwords do not match."
    if len(new_password) < MINIMUM_PASSWORD_LENGTH:
        return f"Choose a password of at least {MINIMUM_PASSWORD_LENGTH} characters."
    if verify_password(new_password, password_hash):
        return "The new password must be different from the current one."
    return None
