"""Managing the people who use the platform.

Replaces the EPIC 0 stub, where an administrator chose somebody else's password. Joining is
now by invitation: the administrator creates the account, the person sets their own
password from a signed, expiring, single-use link.

Nobody is ever deleted. A user with logged time is deactivated instead — their time notes
and approvals are the record of work that actually happened.
"""

from __future__ import annotations

import secrets
from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from backend.app.access import require_user_management
from backend.app.auth import issue_session, set_password
from backend.app.deps import RequiredUser, SessionDep, SettingsDep
from backend.app.flash import flash
from backend.app.invitations import InvitationError, check_unused, issue, read
from backend.app.models import Role, User, utcnow
from backend.app.routes.auth import MINIMUM_PASSWORD_LENGTH
from backend.app.security import hash_password
from backend.app.templating import render

router = APIRouter(tags=["users"])


class PersonFormError(ValueError):
    """The submitted details cannot be saved, with a reason an administrator can act on."""


# --- the people list ------------------------------------------------------------------


@router.get("/users", response_class=HTMLResponse)
async def list_people(request: Request, session: SessionDep, user: RequiredUser):
    require_user_management(user)
    people = session.scalars(select(User).order_by(User.full_name)).all()
    return render(request, "users/list.html", user=user, people=people)


@router.get("/users/new", response_class=HTMLResponse)
async def new_person_form(request: Request, session: SessionDep, user: RequiredUser):
    require_user_management(user)
    return render(
        request, "users/form.html", user=user, person=None, roles=_roles(session), error=None
    )


@router.post("/users")
async def invite_person(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    email: str = Form(...),
    full_name: str = Form(...),
    role_id: int = Form(...),
    title: str = Form(""),
):
    require_user_management(user)

    email = email.strip().lower()
    try:
        _check_email(session, email, person_id=None)
        _check_name(full_name)
        role = _role_or_error(session, role_id)
    except PersonFormError as error:
        return render(
            request,
            "users/form.html",
            user=user,
            person=None,
            roles=_roles(session),
            error=str(error),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    person = User(
        email=email,
        full_name=full_name.strip(),
        title=title.strip(),
        # Unguessable and never shared: the invitation link is the only way in, and setting
        # a password invalidates it.
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role_id=role.id,
        invited_at=utcnow(),
    )
    session.add(person)
    session.commit()

    response = RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)
    flash(response, settings, _invitation_message(request, person, settings))
    return response


@router.get("/users/{person_id}/edit", response_class=HTMLResponse)
async def edit_person_form(
    request: Request, session: SessionDep, user: RequiredUser, person_id: int
):
    require_user_management(user)
    return render(
        request,
        "users/form.html",
        user=user,
        person=_person(session, person_id),
        roles=_roles(session),
        error=None,
    )


@router.post("/users/{person_id}/edit")
async def update_person(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    person_id: int,
    email: str = Form(...),
    full_name: str = Form(...),
    role_id: int = Form(...),
    title: str = Form(""),
    skills: str = Form(""),
    resume_summary: str = Form(""),
    tenure_started_on: str = Form(""),
):
    require_user_management(user)
    person = _person(session, person_id)

    try:
        email = email.strip().lower()
        _check_email(session, email, person_id=person.id)
        _check_name(full_name)
        role = _role_or_error(session, role_id)
        _check_not_removing_the_last_administrator(session, person, role)

        person.email = email
        person.full_name = full_name.strip()
        person.title = title.strip()
        person.skills = skills.strip()
        person.resume_summary = resume_summary.strip()
        person.tenure_started_on = _optional_date(tenure_started_on)
        person.role_id = role.id
        session.commit()
    except PersonFormError as error:
        session.rollback()
        return render(
            request,
            "users/form.html",
            user=user,
            person=session.get(User, person_id),
            roles=_roles(session),
            error=str(error),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    response = RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)
    flash(response, settings, f"{person.email} updated.")
    return response


@router.post("/users/{person_id}/deactivate")
async def deactivate_person(
    session: SessionDep, settings: SettingsDep, user: RequiredUser, person_id: int
):
    require_user_management(user)
    person = _person(session, person_id)
    response = RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)

    if person.id == user.id:
        flash(response, settings, "You cannot deactivate your own account.", "error")
        return response

    try:
        _check_not_removing_the_last_administrator(session, person, None)
    except PersonFormError as error:
        flash(response, settings, str(error), "error")
        return response

    person.is_active = False
    session.commit()
    flash(response, settings, f"{person.email} can no longer sign in.")
    return response


@router.post("/users/{person_id}/reactivate")
async def reactivate_person(
    session: SessionDep, settings: SettingsDep, user: RequiredUser, person_id: int
):
    require_user_management(user)
    person = _person(session, person_id)
    person.is_active = True
    session.commit()

    response = RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)
    flash(response, settings, f"{person.email} can sign in again.")
    return response


@router.post("/users/{person_id}/invitation")
async def resend_invitation(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: RequiredUser,
    person_id: int,
):
    require_user_management(user)
    person = _person(session, person_id)
    response = RedirectResponse("/users", status_code=status.HTTP_303_SEE_OTHER)

    if person.has_accepted_invitation:
        flash(response, settings, f"{person.email} has already signed in.", "warning")
        return response

    # A fresh secret retires any link already in circulation.
    person.password_hash = hash_password(secrets.token_urlsafe(32))
    person.invited_at = utcnow()
    session.commit()

    flash(response, settings, _invitation_message(request, person, settings))
    return response


# --- accepting an invitation ----------------------------------------------------------


@router.get("/invitations/{token}", response_class=HTMLResponse)
async def accept_form(request: Request, session: SessionDep, settings: SettingsDep, token: str):
    try:
        person = _invited_person(session, settings, token)
    except InvitationError as error:
        return render(
            request,
            "users/invitation_invalid.html",
            error=str(error),
            status_code=status.HTTP_410_GONE,
        )
    return render(request, "users/accept.html", person=person, token=token, error=None)


@router.post("/invitations/{token}")
async def accept_invitation(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    token: str,
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    try:
        person = _invited_person(session, settings, token)
    except InvitationError as error:
        return render(
            request,
            "users/invitation_invalid.html",
            error=str(error),
            status_code=status.HTTP_410_GONE,
        )

    if password != confirm_password:
        return render(
            request,
            "users/accept.html",
            person=person,
            token=token,
            error="Those passwords do not match.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        return render(
            request,
            "users/accept.html",
            person=person,
            token=token,
            error=f"Choose a password of at least {MINIMUM_PASSWORD_LENGTH} characters.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    set_password(person, password)
    person.last_signed_in_at = utcnow()
    session.commit()

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    issue_session(response, person, settings, must_change_password=False)
    return response


# --- helpers --------------------------------------------------------------------------


def _invitation_message(request: Request, person: User, settings: SettingsDep) -> str:
    link = request.url_for("accept_form", token=issue(person, settings))
    # Email delivery is story 1.6; until then the administrator passes the link on.
    return f"Invitation for {person.email}: {link}"


def _invited_person(session: SessionDep, settings: SettingsDep, token: str) -> User:
    invitation = read(token, settings)
    person = session.get(User, invitation.user_id)
    if person is None or not person.is_active:
        raise InvitationError("This invitation is no longer valid.")
    check_unused(invitation, person)
    return person


def _person(session: SessionDep, person_id: int) -> User:
    person = session.get(User, person_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")
    return person


def _roles(session: SessionDep) -> list[Role]:
    return list(session.scalars(select(Role).order_by(Role.name)).all())


def _role_or_error(session: SessionDep, role_id: int) -> Role:
    role = session.get(Role, role_id)
    if role is None:
        raise PersonFormError("Pick a role that exists.")
    return role


def _check_email(session: SessionDep, email: str, *, person_id: int | None) -> None:
    if not email or "@" not in email:
        raise PersonFormError("That does not look like an email address.")
    query = select(User.id).where(User.email == email)
    if person_id is not None:
        query = query.where(User.id != person_id)
    if session.scalar(query.limit(1)) is not None:
        raise PersonFormError(f"{email} already has an account.")


def _check_name(full_name: str) -> None:
    if not full_name.strip():
        raise PersonFormError("A full name is required.")


def _check_not_removing_the_last_administrator(
    session: SessionDep, person: User, new_role: Role | None
) -> None:
    """Locking everyone out of administration is not a state the product should allow."""
    if not person.role.is_administrator:
        return
    if new_role is not None and new_role.is_administrator:
        return

    remaining = session.scalar(
        select(User.id)
        .join(Role, Role.id == User.role_id)
        .where(Role.is_administrator.is_(True), User.is_active.is_(True), User.id != person.id)
        .limit(1)
    )
    if remaining is None:
        raise PersonFormError("This is the last active administrator. Promote somebody else first.")


def _optional_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise PersonFormError("Tenure start must be a valid date.") from error
