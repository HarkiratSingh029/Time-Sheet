"""The per-project dashboard: the screen that answers "is this on track, and are we billing it?"."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    ProjectStatus,
    Task,
    TimeNote,
    TimeNoteState,
    utcnow,
)

TODAY = date.today()
START = TODAY - timedelta(days=60)
END = TODAY + timedelta(days=60)


def flat(response) -> str:
    return " ".join(response.text.split())


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    alice = make_person("alice@example.com", "Alice Consultant")
    bob = make_person("bob@example.com", "Bob Consultant")
    outsider = make_person("outsider@example.com", "Ozzy Outsider")

    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Ledger migration",
            "code": "ledger",
            "description": "Move the ledger off the legacy host.",
            "comment": "Client asked for a fortnightly update.",
            "owner_id": str(administrator.id),
            "manager_id": str(alice.id),
            "status": "active",
            "cost": "50000",
            "billable_hours_budget": "100",
            "proposed_duration_days": "120",
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
            "required_approvals": "1",
        },
    )
    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "LEDGER"))

    for person in (alice, bob):
        client.post(f"/projects/{project_id}/members", data={"user_id": str(person.id)})
    client.post(f"/projects/{project_id}/tasks", data={"name": "Delivery", "is_billable": "true"})
    client.post(f"/projects/{project_id}/tasks", data={"name": "Admin"})

    with client.app.state.session_factory() as session:
        tasks = {
            task.name: task.id
            for task in session.scalars(select(Task).where(Task.project_id == project_id)).all()
        }

    return {
        "project_id": project_id,
        "tasks": tasks,
        "administrator": administrator,
        "alice": alice,
        "bob": bob,
        "outsider": outsider,
    }


def log(client: TestClient, world, person, task: str, day: date, hours: float, state=None) -> int:
    with client.app.state.session_factory() as session:
        note = TimeNote(
            project_id=world["project_id"],
            task_id=world["tasks"][task],
            user_id=person.id,
            work_date=day,
            duration_minutes=int(hours * 60),
            detail=f"{person.email} {hours}h",
            state=state or TimeNoteState.DRAFT,
        )
        if note.state is TimeNoteState.SUBMITTED:
            note.submitted_at = utcnow()
        session.add(note)
        session.commit()
        return note.id


def dashboard(client: TestClient, world) -> str:
    return flat(client.get(f"/projects/{world['project_id']}"))


# --- the fields the data model asks for -----------------------------------------------


def test_every_per_project_field_from_the_data_model_appears(
    client: TestClient, world, sign_in
) -> None:
    """docs/DATA_MODEL.md §4: status, completion, hours vs budget, approved vs unapproved,
    consultants, cost burn, dates, owner, manager, comment."""
    log(client, world, world["alice"], "Delivery", START + timedelta(days=1), 8)
    sign_in(world["administrator"])
    page = dashboard(client, world)

    for expected in (
        "Active",  # status
        "Completion",  # against proposed duration
        "Hour budget",  # hours vs billable_hours_budget
        "Approved",  # approved hours
        "Awaiting approval",  # unapproved hours
        "Consultants",  # consultants staffed
        "Cost burn",
        "Schedule",
        "Client asked for a fortnightly update.",  # comment
        "Alice Consultant",  # manager
    ):
        assert expected in page, f"{expected} is missing from the dashboard"


def test_approved_and_unapproved_are_distinct_figures(client: TestClient, world, sign_in) -> None:
    log(
        client,
        world,
        world["alice"],
        "Delivery",
        START + timedelta(days=1),
        16,
        TimeNoteState.APPROVED,
    )
    log(
        client,
        world,
        world["alice"],
        "Delivery",
        START + timedelta(days=2),
        4,
        TimeNoteState.SUBMITTED,
    )
    log(client, world, world["bob"], "Delivery", START + timedelta(days=3), 8)

    sign_in(world["administrator"])
    page = dashboard(client, world)

    assert 'Approved</span> <span class="figure__value numeric"> 16.0h' in page
    assert "Awaiting approval" in page and "4.0h" in page
    assert "28.0h" in page, "the total is shown as its own figure"
    assert "not a substitute for approved" in page, (
        "the total must say what it is, so nobody reads it as signed-off work"
    )


def test_the_health_chip_carries_a_text_label(client: TestClient, world, sign_in) -> None:
    sign_in(world["administrator"])
    page = dashboard(client, world)

    assert "chip--health-green" in page
    assert "Green" in page, "colour alone does not survive greyscale or colour blindness"


def test_health_explains_itself_on_the_page(client: TestClient, world, sign_in) -> None:
    log(
        client,
        world,
        world["alice"],
        "Delivery",
        START + timedelta(days=1),
        24,
        TimeNoteState.APPROVED,
    )
    for offset in range(2, 14):
        log(
            client,
            world,
            world["alice"],
            "Delivery",
            START + timedelta(days=offset),
            8,
            TimeNoteState.APPROVED,
        )

    sign_in(world["administrator"])
    page = dashboard(client, world)

    assert "chip--health-red" in page
    assert "Over the hour budget" in page


# --- the awkward states ---------------------------------------------------------------


def test_a_project_with_no_time_renders_cleanly(client: TestClient, world, sign_in) -> None:
    sign_in(world["administrator"])
    page = dashboard(client, world)

    assert "0.0h" in page
    assert "Nobody has logged time on this project yet." in page
    assert "No time logged yet." in page
    assert "chip--health-green" in page
    assert "—h" not in page, "an empty project shows zeroes, not dashes"


def test_a_project_without_a_budget_says_so_rather_than_showing_nothing(
    client: TestClient, administrator, sign_in
) -> None:
    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Open ended",
            "code": "open",
            "description": "",
            "comment": "",
            "owner_id": str(administrator.id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "",
            "proposed_duration_days": "",
            "start_date": START.isoformat(),
            "end_date": "",
            "required_approvals": "1",
        },
    )
    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "OPEN"))

    page = flat(client.get(f"/projects/{project_id}"))
    assert "No budget set" in page
    assert "Open ended" in page
    assert "chip--health-green" in page


# --- who logged what ------------------------------------------------------------------


def test_the_consultant_breakdown_splits_by_state(client: TestClient, world, sign_in) -> None:
    log(
        client,
        world,
        world["alice"],
        "Delivery",
        START + timedelta(days=1),
        16,
        TimeNoteState.APPROVED,
    )
    log(client, world, world["alice"], "Delivery", START + timedelta(days=2), 4)
    log(
        client,
        world,
        world["bob"],
        "Delivery",
        START + timedelta(days=3),
        8,
        TimeNoteState.APPROVED,
    )

    sign_in(world["administrator"])
    page = dashboard(client, world)

    assert "Who logged what" in page
    assert "Alice Consultant" in page and "Bob Consultant" in page
    assert "20.0h" in page, "Alice's own total, not the project's"
    assert "8.0h" in page


def test_the_breakdown_counts_only_people_who_logged_time(
    client: TestClient, world, sign_in
) -> None:
    log(client, world, world["alice"], "Delivery", START + timedelta(days=1), 8)

    sign_in(world["administrator"])
    page = dashboard(client, world)

    assert "Alice Consultant" in page
    # Bob is a member but has logged nothing, so he is not in the breakdown table.
    breakdown = page.split("Who logged what")[1].split("Recent timesheets")[0]
    assert "Bob Consultant" not in breakdown


def test_recent_timesheets_link_to_their_history(client: TestClient, world, sign_in) -> None:
    note_id = log(client, world, world["alice"], "Delivery", START + timedelta(days=1), 8)

    sign_in(world["administrator"])
    page = dashboard(client, world)

    assert "Recent timesheets" in page
    assert f"/projects/{world['project_id']}/notes/{note_id}/history" in page
    assert "chip--draft" in page


def test_the_billable_split_is_shown(client: TestClient, world, sign_in) -> None:
    log(client, world, world["alice"], "Delivery", START + timedelta(days=1), 8)
    log(client, world, world["alice"], "Admin", START + timedelta(days=2), 2)

    sign_in(world["administrator"])
    page = dashboard(client, world)

    assert "Billable" in page
    assert "2.0h non-billable" in page


def test_an_ageing_approval_is_surfaced(client: TestClient, world, sign_in) -> None:
    note_id = log(
        client,
        world,
        world["alice"],
        "Delivery",
        START + timedelta(days=1),
        8,
        TimeNoteState.SUBMITTED,
    )
    with client.app.state.session_factory() as session:
        note = session.get(TimeNote, note_id)
        note.submitted_at = utcnow() - timedelta(days=9)
        session.add(
            Approval(
                time_note_id=note.id,
                approver_id=world["administrator"].id,
                sequence=1,
                decision=ApprovalDecision.PENDING,
            )
        )
        session.commit()

    sign_in(world["administrator"])
    page = dashboard(client, world)

    assert "Oldest approval" in page
    assert "9d" in page
    assert "chip--health-amber" in page


def test_a_closed_project_is_not_shown_as_failing(client: TestClient, world, sign_in) -> None:
    for offset in range(1, 16):
        log(
            client,
            world,
            world["alice"],
            "Delivery",
            START + timedelta(days=offset),
            8,
            TimeNoteState.APPROVED,
        )

    with client.app.state.session_factory() as session:
        project = session.get(Project, world["project_id"])
        project.status = ProjectStatus.COMPLETED
        session.commit()

    sign_in(world["administrator"])
    page = dashboard(client, world)

    assert "chip--health-green" in page
    assert "Project is closed." in page


# --- access holds ---------------------------------------------------------------------


def test_an_outsider_still_gets_404(client: TestClient, world, sign_in) -> None:
    sign_in(world["outsider"])
    assert client.get(f"/projects/{world['project_id']}").status_code == 404


def test_a_member_sees_their_project_but_not_another(
    client: TestClient, world, administrator, sign_in
) -> None:
    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Unrelated",
            "code": "other",
            "description": "",
            "comment": "",
            "owner_id": str(administrator.id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "",
            "proposed_duration_days": "",
            "start_date": START.isoformat(),
            "end_date": "",
            "required_approvals": "1",
        },
    )
    with client.app.state.session_factory() as session:
        other_id = session.scalar(select(Project.id).where(Project.code == "OTHER"))

    sign_in(world["bob"])
    assert client.get(f"/projects/{world['project_id']}").status_code == 200
    assert client.get(f"/projects/{other_id}").status_code == 404, (
        "metrics must not become a way to see a project you cannot open"
    )


def test_the_dashboard_requires_a_session(client: TestClient, world) -> None:
    client.cookies.clear()  # the fixture signed in to build the world
    response = client.get(f"/projects/{world['project_id']}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- cost -----------------------------------------------------------------------------


def test_cost_burn_is_pro_rata_on_hours_used(client: TestClient, world, sign_in) -> None:
    """Budget 100h, cost 50,000. Log 25h and a quarter of the cost is burnt."""
    for offset in range(1, 5):
        log(client, world, world["alice"], "Delivery", START + timedelta(days=offset), 6.25)

    sign_in(world["administrator"])
    page = dashboard(client, world)

    assert "12500.0" in page
    assert "25.0h" in page


# --- it aggregates, it does not scan --------------------------------------------------


def test_the_panel_aggregates_rather_than_loading_every_entry(
    client: TestClient, world, sign_in
) -> None:
    """The figures come from SQL sums, so the page cost must not grow with the timesheet.

    Asserted as a query count rather than a duration: a timing test on a laptop measures
    the laptop, but a page that starts loading rows per entry shows up here immediately.
    """
    from sqlalchemy import event

    for offset in range(1, 41):
        log(client, world, world["alice"], "Delivery", START + timedelta(days=offset), 8)

    sign_in(world["administrator"])

    queries: list[str] = []
    engine = client.app.state.engine

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_conn, _cursor, statement, *_args) -> None:
        queries.append(statement)

    try:
        assert client.get(f"/projects/{world['project_id']}").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(queries) < 25, (
        f"the page ran {len(queries)} queries over 40 entries — the panel should aggregate"
    )
