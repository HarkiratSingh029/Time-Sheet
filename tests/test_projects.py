"""Projects, members and tasks: creating them, and the rules that constrain them."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import Project, ProjectMember, Task, TimeNote, TimeNoteState

START = "2026-01-01"
END = "2026-06-30"


def project_form(**overrides) -> dict[str, str]:
    form = {
        "name": "Ledger migration",
        "code": "ledger",
        "description": "Move the ledger off the legacy host.",
        "comment": "",
        "owner_id": "1",
        "manager_id": "",
        "status": "active",
        "cost": "48000",
        "billable_hours_budget": "600",
        "proposed_duration_days": "120",
        "start_date": START,
        "end_date": END,
    }
    form.update({key: str(value) for key, value in overrides.items()})
    return form


def create_project(client: TestClient, owner_id: int, **overrides) -> int:
    response = client.post("/projects", data=project_form(owner_id=owner_id, **overrides))
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        code = str(overrides.get("code", "ledger")).upper()
        return session.scalar(select(Project.id).where(Project.code == code))


# --- creating -------------------------------------------------------------------------


def test_an_administrator_creates_a_project_and_assigns_a_consultant(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    consultant = make_person("consultant@example.com", "Priya Consultant")
    sign_in(administrator)

    project_id = create_project(client, administrator.id)

    added = client.post(
        f"/projects/{project_id}/members",
        data={"user_id": str(consultant.id), "is_approver": "false"},
    )
    assert added.status_code == 200

    with client.app.state.session_factory() as session:
        members = session.scalars(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        ).all()
        assert [member.user_id for member in members] == [consultant.id]

    detail = client.get(f"/projects/{project_id}")
    assert "consultant@example.com" in detail.text


def test_the_code_is_stored_uppercase_and_must_be_unique(
    client: TestClient, administrator, sign_in
) -> None:
    sign_in(administrator)
    create_project(client, administrator.id, code="ledger")

    with client.app.state.session_factory() as session:
        assert session.scalar(select(Project.code).where(Project.name == "Ledger migration"))

    clash = client.post("/projects", data=project_form(owner_id=administrator.id, code="LEDGER"))
    assert clash.status_code == 400
    assert "already in use" in clash.text


def test_an_end_date_before_the_start_is_refused_with_a_readable_error(
    client: TestClient, administrator, sign_in
) -> None:
    sign_in(administrator)
    response = client.post(
        "/projects",
        data=project_form(owner_id=administrator.id, start_date=END, end_date=START),
    )

    assert response.status_code == 400
    assert "end date cannot fall before the start date" in response.text

    with client.app.state.session_factory() as session:
        assert session.scalar(select(Project.id)) is None


def test_a_bad_number_is_refused_rather_than_silently_dropped(
    client: TestClient, administrator, sign_in
) -> None:
    sign_in(administrator)
    response = client.post("/projects", data=project_form(owner_id=administrator.id, cost="lots"))

    assert response.status_code == 400
    assert "must be a number" in response.text


def test_an_open_ended_project_needs_no_end_date(
    client: TestClient, administrator, sign_in
) -> None:
    sign_in(administrator)
    project_id = create_project(client, administrator.id, end_date="")

    with client.app.state.session_factory() as session:
        assert session.get(Project, project_id).end_date is None


# --- editing --------------------------------------------------------------------------


def test_editing_updates_the_project(client: TestClient, administrator, sign_in) -> None:
    sign_in(administrator)
    project_id = create_project(client, administrator.id)

    response = client.post(
        f"/projects/{project_id}/edit",
        data=project_form(owner_id=administrator.id, name="Ledger migration, phase two"),
    )
    assert response.status_code == 200

    with client.app.state.session_factory() as session:
        assert session.get(Project, project_id).name == "Ledger migration, phase two"


def test_the_window_cannot_shrink_away_from_logged_time(
    client: TestClient, administrator, sign_in
) -> None:
    sign_in(administrator)
    project_id = create_project(client, administrator.id)
    client.post(f"/projects/{project_id}/tasks", data={"name": "Discovery", "is_billable": "true"})

    with client.app.state.session_factory() as session:
        task_id = session.scalar(select(Task.id).where(Task.project_id == project_id))
        session.add(
            TimeNote(
                project_id=project_id,
                task_id=task_id,
                user_id=administrator.id,
                work_date=date(2026, 2, 3),
                duration_minutes=480,
                state=TimeNoteState.DRAFT,
            )
        )
        session.commit()

    response = client.post(
        f"/projects/{project_id}/edit",
        data=project_form(owner_id=administrator.id, start_date="2026-03-01"),
    )

    assert response.status_code == 400
    assert "Time has already been logged" in response.text


# --- members --------------------------------------------------------------------------


def test_the_same_person_cannot_be_added_twice(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    consultant = make_person("twice@example.com")
    sign_in(administrator)
    project_id = create_project(client, administrator.id)

    client.post(f"/projects/{project_id}/members", data={"user_id": str(consultant.id)})
    again = client.post(f"/projects/{project_id}/members", data={"user_id": str(consultant.id)})

    assert "already on this project" in again.text
    with client.app.state.session_factory() as session:
        memberships = session.scalars(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        ).all()
        assert len(memberships) == 1


def test_a_member_with_logged_time_cannot_be_removed(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    consultant = make_person("logged@example.com")
    sign_in(administrator)
    project_id = create_project(client, administrator.id)
    client.post(f"/projects/{project_id}/members", data={"user_id": str(consultant.id)})
    client.post(f"/projects/{project_id}/tasks", data={"name": "Discovery"})

    with client.app.state.session_factory() as session:
        member_id = session.scalar(
            select(ProjectMember.id).where(ProjectMember.project_id == project_id)
        )
        task_id = session.scalar(select(Task.id).where(Task.project_id == project_id))
        session.add(
            TimeNote(
                project_id=project_id,
                task_id=task_id,
                user_id=consultant.id,
                work_date=date(2026, 2, 3),
                duration_minutes=120,
            )
        )
        session.commit()

    response = client.post(f"/projects/{project_id}/members/{member_id}/remove")
    assert "would orphan their time notes" in response.text

    with client.app.state.session_factory() as session:
        assert session.get(ProjectMember, member_id) is not None


def test_a_member_without_logged_time_can_be_removed(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    consultant = make_person("removable@example.com")
    sign_in(administrator)
    project_id = create_project(client, administrator.id)
    client.post(f"/projects/{project_id}/members", data={"user_id": str(consultant.id)})

    with client.app.state.session_factory() as session:
        member_id = session.scalar(
            select(ProjectMember.id).where(ProjectMember.project_id == project_id)
        )

    client.post(f"/projects/{project_id}/members/{member_id}/remove")

    with client.app.state.session_factory() as session:
        assert session.get(ProjectMember, member_id) is None


# --- tasks ----------------------------------------------------------------------------


def test_a_task_always_belongs_to_its_project(client: TestClient, administrator, sign_in) -> None:
    sign_in(administrator)
    first = create_project(client, administrator.id, code="one")
    second = create_project(client, administrator.id, code="two")

    client.post(f"/projects/{first}/tasks", data={"name": "Discovery", "is_billable": "true"})
    with client.app.state.session_factory() as session:
        task_id = session.scalar(select(Task.id).where(Task.project_id == first))

    # The task cannot be reached, or changed, through the other project.
    stolen = client.post(f"/projects/{second}/tasks/{task_id}", data={"name": "Renamed"})
    assert stolen.status_code == 404

    with client.app.state.session_factory() as session:
        assert session.get(Task, task_id).name == "Discovery"


def test_a_task_can_be_archived_and_restored(client: TestClient, administrator, sign_in) -> None:
    sign_in(administrator)
    project_id = create_project(client, administrator.id)
    client.post(f"/projects/{project_id}/tasks", data={"name": "Discovery"})

    with client.app.state.session_factory() as session:
        task_id = session.scalar(select(Task.id).where(Task.project_id == project_id))

    client.post(f"/projects/{project_id}/tasks/{task_id}/archive", data={"archived": "true"})
    with client.app.state.session_factory() as session:
        assert session.get(Task, task_id).is_archived is True

    client.post(f"/projects/{project_id}/tasks/{task_id}/archive", data={"archived": "false"})
    with client.app.state.session_factory() as session:
        assert session.get(Task, task_id).is_archived is False


def test_a_nameless_task_is_refused(client: TestClient, administrator, sign_in) -> None:
    sign_in(administrator)
    project_id = create_project(client, administrator.id)

    response = client.post(f"/projects/{project_id}/tasks", data={"name": "   "})
    assert "A task needs a name" in response.text

    with client.app.state.session_factory() as session:
        assert session.scalar(select(Task.id).where(Task.project_id == project_id)) is None


# --- flash messages -------------------------------------------------------------------


def test_a_flash_message_survives_the_redirect_and_is_shown_once(
    client: TestClient, administrator, sign_in
) -> None:
    sign_in(administrator)
    landing = client.post("/projects", data=project_form(owner_id=administrator.id))

    assert "Project LEDGER created." in landing.text
    assert "Project LEDGER created." not in client.get("/projects").text, (
        "a flash message that survives a second page load is a bug, not a feature"
    )
