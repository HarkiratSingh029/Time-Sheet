"""Who may reach what.

Authorization is the one class of bug here that is genuinely dangerous: a wrong number on
a dashboard is embarrassing, another company's timesheet on your screen is not. So every
project route is asserted against administrator, owner, member and outsider — and the
outsider is checked on the *write* routes too, not only on the pages.

A project a user may not see returns **404, not 403**. A 403 confirms it exists.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import Project, ProjectMember, Task

START = "2026-01-01"
END = "2026-06-30"


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    """One project owned by an owner, staffed by a member, with an outsider looking on."""
    owner = make_person("owner@example.com", "Olive Owner")
    member = make_person("member@example.com", "Mo Member")
    outsider = make_person("outsider@example.com", "Ozzy Outsider")

    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Ledger migration",
            "code": "ledger",
            "description": "",
            "comment": "",
            "owner_id": str(owner.id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "",
            "proposed_duration_days": "",
            "start_date": START,
            "end_date": END,
        },
    )

    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "LEDGER"))

    client.post(f"/projects/{project_id}/members", data={"user_id": str(member.id)})
    client.post(f"/projects/{project_id}/tasks", data={"name": "Discovery"})

    with client.app.state.session_factory() as session:
        task_id = session.scalar(select(Task.id).where(Task.project_id == project_id))
        membership_id = session.scalar(
            select(ProjectMember.id).where(ProjectMember.project_id == project_id)
        )

    return {
        "project_id": project_id,
        "task_id": task_id,
        "membership_id": membership_id,
        "administrator": administrator,
        "owner": owner,
        "member": member,
        "outsider": outsider,
    }


# --- the list -------------------------------------------------------------------------


def test_the_list_shows_only_what_each_person_may_see(client: TestClient, world, sign_in) -> None:
    for person, visible in (
        (world["administrator"], True),
        (world["owner"], True),
        (world["member"], True),
        (world["outsider"], False),
    ):
        sign_in(person)
        listing = client.get("/projects").text
        assert ("LEDGER" in listing) is visible, f"{person.email} saw the wrong list"


# --- reading a project ----------------------------------------------------------------


def test_an_outsider_gets_404_not_403_by_direct_url(client: TestClient, world, sign_in) -> None:
    sign_in(world["outsider"])
    response = client.get(f"/projects/{world['project_id']}")

    assert response.status_code == 404, "403 would confirm the project exists"


def test_everyone_involved_can_read_the_project(client: TestClient, world, sign_in) -> None:
    for person in (world["administrator"], world["owner"], world["member"]):
        sign_in(person)
        assert client.get(f"/projects/{world['project_id']}").status_code == 200


# --- changing a project ---------------------------------------------------------------


def test_only_an_administrator_creates_projects(client: TestClient, world, sign_in) -> None:
    for person in (world["owner"], world["member"], world["outsider"]):
        sign_in(person)
        assert client.get("/projects/new").status_code == 403
        assert (
            client.post(
                "/projects",
                data={
                    "name": "Sneaky",
                    "code": "sneak",
                    "description": "",
                    "comment": "",
                    "owner_id": str(person.id),
                    "manager_id": "",
                    "status": "active",
                    "cost": "",
                    "billable_hours_budget": "",
                    "proposed_duration_days": "",
                    "start_date": START,
                    "end_date": "",
                },
            ).status_code
            == 403
        )

    with client.app.state.session_factory() as session:
        assert session.scalar(select(Project.id).where(Project.code == "SNEAK")) is None


def test_a_plain_member_cannot_edit_the_project(client: TestClient, world, sign_in) -> None:
    sign_in(world["member"])
    project_id = world["project_id"]

    assert client.get(f"/projects/{project_id}/edit").status_code == 403
    assert (
        client.post(
            f"/projects/{project_id}/edit",
            data={
                "name": "Renamed by a member",
                "code": "ledger",
                "description": "",
                "comment": "",
                "owner_id": str(world["owner"].id),
                "manager_id": "",
                "status": "active",
                "cost": "",
                "billable_hours_budget": "",
                "proposed_duration_days": "",
                "start_date": START,
                "end_date": END,
            },
        ).status_code
        == 403
    )

    with client.app.state.session_factory() as session:
        assert session.get(Project, project_id).name == "Ledger migration"


def test_the_owner_may_edit_their_own_project(client: TestClient, world, sign_in) -> None:
    sign_in(world["owner"])
    assert client.get(f"/projects/{world['project_id']}/edit").status_code == 200


# --- membership and tasks -------------------------------------------------------------


@pytest.mark.parametrize("route", ["members", "tasks"])
def test_a_member_cannot_add_members_or_tasks(
    client: TestClient, world, sign_in, route: str
) -> None:
    sign_in(world["member"])
    payload = {"user_id": str(world["outsider"].id)} if route == "members" else {"name": "Snuck"}

    assert client.post(f"/projects/{world['project_id']}/{route}", data=payload).status_code == 403


@pytest.mark.parametrize("route", ["members", "tasks"])
def test_an_outsider_writing_to_a_project_gets_404(
    client: TestClient, world, sign_in, route: str
) -> None:
    sign_in(world["outsider"])
    payload = {"user_id": str(world["outsider"].id)} if route == "members" else {"name": "Snuck"}

    response = client.post(f"/projects/{world['project_id']}/{route}", data=payload)
    assert response.status_code == 404, "a write must not confirm the project exists either"


def test_an_outsider_cannot_touch_a_task_or_a_membership(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["outsider"])
    project_id, task_id = world["project_id"], world["task_id"]

    rename = client.post(f"/projects/{project_id}/tasks/{task_id}", data={"name": "x"})
    assert rename.status_code == 404
    assert client.post(f"/projects/{project_id}/tasks/{task_id}/archive").status_code == 404
    assert (
        client.post(f"/projects/{project_id}/members/{world['membership_id']}/remove").status_code
        == 404
    )

    with client.app.state.session_factory() as session:
        assert session.get(Task, task_id).name == "Discovery"
        assert session.get(ProjectMember, world["membership_id"]) is not None


def test_the_owner_may_manage_members_and_tasks(client: TestClient, world, sign_in) -> None:
    sign_in(world["owner"])
    project_id = world["project_id"]

    assert (
        client.post(
            f"/projects/{project_id}/members", data={"user_id": str(world["outsider"].id)}
        ).status_code
        == 200
    )
    added_task = client.post(f"/projects/{project_id}/tasks", data={"name": "Delivery"})
    assert added_task.status_code == 200


# --- adding people --------------------------------------------------------------------


def test_only_an_administrator_adds_people(client: TestClient, world, sign_in) -> None:
    for person in (world["owner"], world["member"], world["outsider"]):
        sign_in(person)
        assert client.get("/users/new").status_code == 403
        assert (
            client.post(
                "/users",
                data={
                    "email": "sneak@example.com",
                    "full_name": "Sneaky Person",
                    "password": "a-properly-long-password",
                },
            ).status_code
            == 403
        )


def test_an_administrator_adds_a_person_who_can_then_sign_in(
    client: TestClient, administrator, sign_in
) -> None:
    sign_in(administrator)
    response = client.post(
        "/users",
        data={
            "email": "New.Person@Example.com",
            "full_name": "New Person",
            "password": "a-properly-long-password",
        },
    )
    assert response.status_code == 200
    assert "new.person@example.com can now sign in" in response.text

    client.cookies.clear()
    signed_in = client.post(
        "/login",
        data={"email": "new.person@example.com", "password": "a-properly-long-password"},
        follow_redirects=False,
    )
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/"


def test_a_duplicate_email_is_refused(client: TestClient, administrator, sign_in) -> None:
    sign_in(administrator)
    response = client.post(
        "/users",
        data={
            "email": administrator.email,
            "full_name": "Impostor",
            "password": "a-properly-long-password",
        },
    )

    assert response.status_code == 400
    assert "already has an account" in response.text


# --- signed out -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/projects", "/projects/new", "/projects/1", "/projects/1/edit", "/users/new"]
)
def test_every_project_page_requires_a_session(client: TestClient, path: str) -> None:
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
