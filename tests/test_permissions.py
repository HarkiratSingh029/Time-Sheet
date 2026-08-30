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

from backend.app.models import Project, ProjectMember, Task, User

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


def test_only_a_user_manager_adds_people(client: TestClient, world, sign_in) -> None:
    for person in (world["owner"], world["member"], world["outsider"]):
        sign_in(person)
        assert client.get("/users").status_code == 403
        assert client.get("/users/new").status_code == 403
        invited = client.post(
            "/users",
            data={
                "email": "sneak@example.com",
                "full_name": "Sneaky Person",
                "role_id": "2",
            },
        )
        assert invited.status_code == 403


def test_a_non_manager_cannot_edit_or_deactivate_anyone(client: TestClient, world, sign_in) -> None:
    target = world["member"]
    for person in (world["owner"], world["outsider"]):
        sign_in(person)
        assert client.get(f"/users/{target.id}/edit").status_code == 403
        assert client.post(f"/users/{target.id}/deactivate").status_code == 403
        assert client.post(f"/users/{target.id}/invitation").status_code == 403

    with client.app.state.session_factory() as session:
        assert session.get(User, target.id).is_active


# --- signed out -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/projects", "/projects/new", "/projects/1", "/projects/1/edit", "/users/new"]
)
def test_every_project_page_requires_a_session(client: TestClient, path: str) -> None:
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- approvals ------------------------------------------------------------------------


def _submitted_note(client: TestClient, world, sign_in) -> int:
    """The member logs and submits a day; the owner is the fallback approver."""
    from datetime import date

    from backend.app.models import TimeNote

    project_id = world["project_id"]
    sign_in(world["member"])
    client.post(
        f"/projects/{project_id}/notes",
        data={
            "work_date": date(2026, 2, 3).isoformat(),
            "task_id": str(world["task_id"]),
            "hours": "4",
            "detail": "Logged by the member",
            "month": "2026-02",
        },
    )
    client.post(
        f"/projects/{project_id}/notes/submit",
        data={"from_date": date(2026, 2, 3).isoformat(), "month": "2026-02"},
    )
    with client.app.state.session_factory() as session:
        return session.scalar(select(TimeNote.id).where(TimeNote.project_id == project_id))


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_only_an_approver_or_administrator_decides(
    client: TestClient, world, sign_in, action: str
) -> None:
    from backend.app.models import TimeNote, TimeNoteState

    note_id = _submitted_note(client, world, sign_in)

    for person in (world["member"], world["outsider"]):
        sign_in(person)
        response = client.post(f"/approvals/{note_id}/{action}", data={"comment": "Nope"})
        assert response.status_code == 404, f"{person.email} must not decide this entry"

    with client.app.state.session_factory() as session:
        assert session.get(TimeNote, note_id).state is TimeNoteState.SUBMITTED


def test_the_queue_shows_each_person_only_their_own_work(
    client: TestClient, world, sign_in
) -> None:
    _submitted_note(client, world, sign_in)

    sign_in(world["owner"])
    assert "Logged by the member" in client.get("/approvals").text, "the owner approves by default"

    for person in (world["member"], world["outsider"]):
        sign_in(person)
        assert "Logged by the member" not in client.get("/approvals").text


def test_bulk_approve_is_refused_to_a_non_approver(client: TestClient, world, sign_in) -> None:
    """A bulk action is a convenience over the same rule, never a way around it."""
    from backend.app.models import TimeNote, TimeNoteState

    note_id = _submitted_note(client, world, sign_in)

    for person in (world["member"], world["outsider"]):
        sign_in(person)
        response = client.post("/approvals/approve-group", data={"note_ids": [note_id]})
        assert "not yours to decide" in response.text

    with client.app.state.session_factory() as session:
        assert session.get(TimeNote, note_id).state is TimeNoteState.SUBMITTED


# --- search ---------------------------------------------------------------------------


def test_search_never_reveals_a_project_you_cannot_open(client: TestClient, world, sign_in) -> None:
    """The whole point of the feature's scoping: a search box is not an enumeration oracle."""
    sign_in(world["outsider"])
    page = " ".join(client.get("/search?q=LEDGER").text.split())

    assert "Ledger migration" not in page
    assert "Nothing matched" in page

    with_filter = " ".join(
        client.get(f"/search?q=Discovery&project_id={world['project_id']}").text.split()
    )
    # The term itself is echoed into the search box, so assert on what would leak: the
    # project's name, and a link to it.
    assert "Ledger migration" not in with_filter, "a filter cannot widen the scope either"
    assert f'/projects/{world["project_id"]}"' not in with_filter
