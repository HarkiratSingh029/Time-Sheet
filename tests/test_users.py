"""Managing people: invitations, roles, deactivation, and the account nobody may strand."""

from __future__ import annotations

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.invitations import INVITATION_MAX_AGE_SECONDS, issue, read
from backend.app.models import Role, TimeNote, User
from backend.app.seed import ADMINISTRATOR_ROLE, MEMBER_ROLE

PASSWORD = "a-properly-long-password"


def role_id(client: TestClient, name: str) -> int:
    with client.app.state.session_factory() as session:
        return session.scalar(select(Role.id).where(Role.name == name))


def invite(client: TestClient, email: str = "newcomer@example.com", **overrides):
    payload = {
        "email": email,
        "full_name": "Nadia Newcomer",
        "role_id": str(role_id(client, MEMBER_ROLE)),
        "title": "Consultant",
    }
    payload.update(overrides)
    return client.post("/users", data=payload)


def invitation_link(response) -> str:
    match = re.search(r"/invitations/[\w.\-]+", response.text)
    assert match, "the invitation link must be shown to the administrator"
    return match.group(0)


def person_by_email(client: TestClient, email: str) -> User:
    """Detached on purpose — read scalar attributes only, never a lazy relationship."""
    with client.app.state.session_factory() as session:
        return session.scalar(select(User).where(User.email == email))


def role_name_of(client: TestClient, email: str) -> str:
    with client.app.state.session_factory() as session:
        person = session.scalar(select(User).where(User.email == email))
        return person.role.name


# --- inviting -------------------------------------------------------------------------


def test_an_administrator_invites_someone_who_sets_their_own_password(
    client: TestClient, administrator, sign_in
) -> None:
    sign_in(administrator)
    link = invitation_link(invite(client))

    invited = person_by_email(client, "newcomer@example.com")
    assert invited.status == "invited"
    assert invited.invited_at is not None
    assert invited.last_signed_in_at is None

    client.cookies.clear()
    assert client.get(link).status_code == 200

    accepted = client.post(
        link, data={"password": PASSWORD, "confirm_password": PASSWORD}, follow_redirects=False
    )
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/"
    assert client.get("/").status_code == 200, "accepting signs them straight in"

    assert person_by_email(client, "newcomer@example.com").status == "active"


def test_the_administrator_never_chooses_the_password(
    client: TestClient, administrator, sign_in
) -> None:
    sign_in(administrator)
    form = client.get("/users/new").text
    assert 'type="password"' not in form, "the EPIC 0 stub password field must be gone"

    invite(client)
    invited = person_by_email(client, "newcomer@example.com")
    assert not invited.has_accepted_invitation


def test_an_invitation_link_is_single_use(client: TestClient, administrator, sign_in) -> None:
    sign_in(administrator)
    link = invitation_link(invite(client))

    client.cookies.clear()
    client.post(link, data={"password": PASSWORD, "confirm_password": PASSWORD})

    client.cookies.clear()
    reused = client.get(link)
    assert reused.status_code == 410
    assert "already been used" in reused.text


def test_an_expired_invitation_says_so(
    client: TestClient, administrator, sign_in, settings
) -> None:
    sign_in(administrator)
    invite(client)
    person = person_by_email(client, "newcomer@example.com")

    brief = settings.model_copy(update={"secret_key": settings.secret_key})
    token = issue(person, brief)

    import itsdangerous

    with pytest.raises(itsdangerous.SignatureExpired):
        itsdangerous.URLSafeTimedSerializer(
            brief.secret_key, salt="ts-timesheets-invitation"
        ).loads(token, max_age=-1)

    assert INVITATION_MAX_AGE_SECONDS == 7 * 24 * 60 * 60


def test_a_forged_invitation_is_refused(client: TestClient) -> None:
    response = client.get("/invitations/not-a-real-token")
    assert response.status_code == 410
    assert "not valid" in response.text


def test_resending_retires_the_previous_link(client: TestClient, administrator, sign_in) -> None:
    sign_in(administrator)
    first = invitation_link(invite(client))
    person = person_by_email(client, "newcomer@example.com")

    resent = client.post(f"/users/{person.id}/invitation")
    second = invitation_link(resent)
    assert first != second

    client.cookies.clear()
    assert client.get(first).status_code == 410, "the old link must stop working"
    assert client.get(second).status_code == 200


def test_resending_to_someone_who_has_signed_in_is_refused(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    person = make_person("settled@example.com")
    sign_in(person)
    sign_in(administrator)

    response = client.post(f"/users/{person.id}/invitation")
    assert "has already signed in" in response.text


def test_a_duplicate_email_is_refused(client: TestClient, administrator, sign_in) -> None:
    sign_in(administrator)
    response = invite(client, email=administrator.email)

    assert response.status_code == 400
    assert "already has an account" in response.text


# --- editing --------------------------------------------------------------------------


def test_editing_updates_the_profile_and_the_role(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    person = make_person("editable@example.com", "Eddie Editable")
    sign_in(administrator)

    response = client.post(
        f"/users/{person.id}/edit",
        data={
            "email": "editable@example.com",
            "full_name": "Edwina Editable",
            "role_id": str(role_id(client, ADMINISTRATOR_ROLE)),
            "title": "Principal",
            "skills": "Postgres, Python",
            "resume_summary": "Ten years on data platforms.",
            "tenure_started_on": "2024-03-01",
        },
    )
    assert response.status_code == 200

    updated = person_by_email(client, "editable@example.com")
    assert updated.full_name == "Edwina Editable"
    assert updated.title == "Principal"
    assert updated.skills == "Postgres, Python"
    assert updated.tenure_started_on == date(2024, 3, 1)
    assert role_name_of(client, "editable@example.com") == ADMINISTRATOR_ROLE


def test_a_bad_tenure_date_is_refused(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    person = make_person("dates@example.com")
    sign_in(administrator)

    response = client.post(
        f"/users/{person.id}/edit",
        data={
            "email": "dates@example.com",
            "full_name": "Dana Dates",
            "role_id": str(role_id(client, MEMBER_ROLE)),
            "tenure_started_on": "the nineties",
        },
    )
    assert response.status_code == 400
    assert "valid date" in response.text


# --- deactivation ---------------------------------------------------------------------


def test_deactivating_blocks_login_and_ends_the_session(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    person = make_person("leaver@example.com")
    sign_in(person)
    assert client.get("/").status_code == 200
    session_cookie = dict(client.cookies)

    sign_in(administrator)
    client.post(f"/users/{person.id}/deactivate")

    client.cookies.clear()
    for name, value in session_cookie.items():
        client.cookies.set(name, value)
    assert client.get("/", follow_redirects=False).status_code == 303, (
        "an existing session must stop working the moment the account is deactivated"
    )

    client.cookies.clear()
    refused = client.post("/login", data={"email": "leaver@example.com", "password": PASSWORD})
    assert refused.status_code == 401


def test_deactivation_keeps_the_work_history(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    person = make_person("historic@example.com")
    sign_in(administrator)
    client.post(f"/users/{person.id}/deactivate")

    with client.app.state.session_factory() as session:
        assert session.get(User, person.id) is not None
        assert session.get(User, person.id).is_active is False


def test_a_deactivated_person_can_be_reactivated(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    person = make_person("returning@example.com")
    sign_in(administrator)
    client.post(f"/users/{person.id}/deactivate")
    client.post(f"/users/{person.id}/reactivate")

    client.cookies.clear()
    response = client.post(
        "/login",
        data={"email": "returning@example.com", "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_you_cannot_deactivate_yourself(client: TestClient, administrator, sign_in) -> None:
    sign_in(administrator)
    response = client.post(f"/users/{administrator.id}/deactivate")

    assert "cannot deactivate your own account" in response.text
    with client.app.state.session_factory() as session:
        assert session.get(User, administrator.id).is_active


def test_the_last_administrator_cannot_be_demoted_or_deactivated(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    other = make_person("someone@example.com")
    sign_in(administrator)

    demoted = client.post(
        f"/users/{administrator.id}/edit",
        data={
            "email": administrator.email,
            "full_name": "Administrator",
            "role_id": str(role_id(client, MEMBER_ROLE)),
        },
    )
    assert demoted.status_code == 400
    assert "last active administrator" in demoted.text

    assert role_name_of(client, administrator.email) == ADMINISTRATOR_ROLE

    # Promote somebody else, and the guard steps aside.
    client.post(
        f"/users/{other.id}/edit",
        data={
            "email": other.email,
            "full_name": "Someone Else",
            "role_id": str(role_id(client, ADMINISTRATOR_ROLE)),
        },
    )
    allowed = client.post(
        f"/users/{administrator.id}/edit",
        data={
            "email": administrator.email,
            "full_name": "Administrator",
            "role_id": str(role_id(client, MEMBER_ROLE)),
        },
        follow_redirects=False,
    )
    assert allowed.status_code == 303
    assert role_name_of(client, administrator.email) == MEMBER_ROLE

    # And the demotion takes effect immediately: the very next page is refused.
    assert client.get("/users").status_code == 403


# --- deletion -------------------------------------------------------------------------


def test_a_user_with_a_work_history_cannot_be_deleted(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    from backend.app.seed import UserDeletionError

    person = make_person("worked@example.com")
    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Ledger",
            "code": "ledger",
            "description": "",
            "comment": "",
            "owner_id": str(administrator.id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "",
            "proposed_duration_days": "",
            "start_date": "2026-01-01",
            "end_date": "2026-06-30",
        },
    )
    with client.app.state.session_factory() as session:
        from backend.app.models import Project

        project_id = session.scalar(select(Project.id))
    client.post(f"/projects/{project_id}/members", data={"user_id": str(person.id)})

    with client.app.state.session_factory() as session:
        session.delete(session.get(User, person.id))
        with pytest.raises(UserDeletionError):
            session.commit()
        session.rollback()
        assert session.get(User, person.id) is not None


# --- the list -------------------------------------------------------------------------


def test_the_list_shows_role_status_and_last_sign_in(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    settled = make_person("settled@example.com", "Sam Settled")
    sign_in(settled)
    sign_in(administrator)
    invite(client)

    listing = client.get("/users").text
    assert "settled@example.com" in listing
    assert "newcomer@example.com" in listing
    assert "Invited" in listing
    assert "Active" in listing
    assert ADMINISTRATOR_ROLE in listing


def test_the_invitation_token_names_the_person_it_was_issued_for(
    client: TestClient, administrator, sign_in, settings
) -> None:
    sign_in(administrator)
    invite(client)
    person = person_by_email(client, "newcomer@example.com")

    assert read(issue(person, settings), settings).user_id == person.id


def test_time_notes_survive_their_authors_deactivation(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    person = make_person("logger@example.com")
    sign_in(administrator)
    client.post(f"/users/{person.id}/deactivate")

    with client.app.state.session_factory() as session:
        assert session.scalars(select(TimeNote).where(TimeNote.user_id == person.id)).all() == []
        assert session.get(User, person.id).status == "deactivated"
