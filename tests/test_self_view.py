"""The consultant's own view.

Every other screen answers somebody else's question. This one answers "what did I do, and
what still needs me" — and it must answer it for the signed-in person only, whatever
permissions they hold.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import metrics
from backend.app.metrics import month_bounds
from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    Task,
    TimeNote,
    TimeNoteState,
    User,
    utcnow,
)

TODAY = date.today()
MONTH_FIRST, MONTH_LAST = month_bounds(TODAY)
START = MONTH_FIRST - timedelta(days=90)


def flat(response) -> str:
    return " ".join(response.text.split())


def a_day_this_month(offset: int = 0) -> date:
    """A date inside the current month, whichever day of it we happen to run on."""
    return min(MONTH_FIRST + timedelta(days=offset), MONTH_LAST, TODAY)


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    alice = make_person("alice@example.com", "Alice Consultant")
    bob = make_person("bob@example.com", "Bob Consultant")
    approver = make_person("approver@example.com", "Ade Approver")

    sign_in(administrator)
    projects = {}
    for code, name in (("ledger", "Ledger migration"), ("portal", "Client portal")):
        client.post(
            "/projects",
            data={
                "name": name,
                "code": code,
                "description": "",
                "comment": "",
                "owner_id": str(administrator.id),
                "manager_id": "",
                "status": "active",
                "cost": "",
                "billable_hours_budget": "",
                "proposed_duration_days": "",
                "start_date": START.isoformat(),
                "end_date": (TODAY + timedelta(days=60)).isoformat(),
                "required_approvals": "1",
            },
        )
        with client.app.state.session_factory() as session:
            project_id = session.scalar(select(Project.id).where(Project.code == code.upper()))
        for person in (alice, bob):
            client.post(f"/projects/{project_id}/members", data={"user_id": str(person.id)})
        client.post(
            f"/projects/{project_id}/members",
            data={"user_id": str(approver.id), "is_approver": "true", "approval_order": "1"},
        )
        client.post(
            f"/projects/{project_id}/tasks", data={"name": "Delivery", "is_billable": "true"}
        )
        client.post(f"/projects/{project_id}/tasks", data={"name": "Admin"})
        with client.app.state.session_factory() as session:
            tasks = {
                task.name: task.id
                for task in session.scalars(select(Task).where(Task.project_id == project_id)).all()
            }
        projects[code] = {"id": project_id, "tasks": tasks}

    return {
        "projects": projects,
        "administrator": administrator,
        "alice": alice,
        "bob": bob,
        "approver": approver,
    }


def log(
    client: TestClient,
    world,
    code: str,
    person,
    hours: float,
    day: date,
    task: str = "Delivery",
    state: TimeNoteState = TimeNoteState.DRAFT,
    rejected_by=None,
    comment: str = "",
) -> int:
    project = world["projects"][code]
    with client.app.state.session_factory() as session:
        note = TimeNote(
            project_id=project["id"],
            task_id=project["tasks"][task],
            user_id=person.id,
            work_date=day,
            duration_minutes=int(hours * 60),
            detail="Work",
            state=state,
        )
        if state is TimeNoteState.SUBMITTED:
            note.submitted_at = utcnow()
        session.add(note)
        session.flush()

        if state is TimeNoteState.SUBMITTED:
            session.add(
                Approval(
                    time_note_id=note.id,
                    approver_id=world["approver"].id,
                    sequence=1,
                    decision=ApprovalDecision.PENDING,
                )
            )
        if rejected_by is not None:
            session.add(
                Approval(
                    time_note_id=note.id,
                    approver_id=rejected_by.id,
                    sequence=1,
                    decision=ApprovalDecision.REJECTED,
                    comment=comment or "Please split this across two tasks.",
                    decided_at=utcnow(),
                )
            )
        session.commit()
        return note.id


def page(client: TestClient) -> str:
    response = client.get("/me")
    assert response.status_code == 200
    return flat(response)


# --- it is your time, and only yours --------------------------------------------------


def test_it_shows_only_the_signed_in_persons_entries(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], 8, a_day_this_month(0))
    log(client, world, "ledger", world["bob"], 4, a_day_this_month(1))

    sign_in(world["alice"])

    with client.app.state.session_factory() as session:
        mine = metrics.own_time(session, session.get(User, world["alice"].id))

    assert mine.hours.logged == 8.0, "Bob's four hours are not Alice's"


def test_an_administrator_sees_their_own_time_not_everyones(
    client: TestClient, world, sign_in
) -> None:
    log(client, world, "ledger", world["alice"], 8, a_day_this_month(0))
    log(client, world, "ledger", world["administrator"], 2, a_day_this_month(1))

    sign_in(world["administrator"])

    with client.app.state.session_factory() as session:
        mine = metrics.own_time(session, session.get(User, world["administrator"].id))

    assert mine.hours.logged == 2.0, (
        "seeing every project does not mean this page shows every project's time"
    )
    assert "Alice Consultant" not in page(client)


def test_the_page_takes_no_user_parameter(client: TestClient, world, sign_in) -> None:
    """There is nothing to tamper with: the page is always your own."""
    log(client, world, "ledger", world["bob"], 6, a_day_this_month(0))

    sign_in(world["alice"])
    tampered = flat(client.get(f"/me?user_id={world['bob'].id}"))

    assert "6h" not in tampered
    assert "Nothing logged this month" in tampered


def test_it_requires_a_session(client: TestClient, world) -> None:
    client.cookies.clear()
    response = client.get("/me", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- the four states --------------------------------------------------------------


def test_the_four_states_are_separate_and_sum_to_the_total(
    client: TestClient, world, sign_in
) -> None:
    log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        a_day_this_month(0),
        state=TimeNoteState.APPROVED,
    )
    log(
        client,
        world,
        "ledger",
        world["alice"],
        4,
        a_day_this_month(1),
        state=TimeNoteState.SUBMITTED,
    )
    log(client, world, "ledger", world["alice"], 2, a_day_this_month(2))
    log(
        client,
        world,
        "ledger",
        world["alice"],
        1,
        a_day_this_month(3),
        rejected_by=world["approver"],
    )

    sign_in(world["alice"])
    with client.app.state.session_factory() as session:
        mine = metrics.own_time(session, session.get(User, world["alice"].id))

    assert mine.hours.approved == 8.0
    assert mine.hours.submitted == 4.0
    assert mine.hours.draft == 3.0, "the sent-back day is a draft again"
    assert mine.hours.logged == 15.0
    assert (
        mine.hours.approved + mine.hours.submitted + mine.hours.draft + mine.hours.rejected
        == mine.hours.logged
    )


def test_the_states_appear_separately_on_the_page(client: TestClient, world, sign_in) -> None:
    log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        a_day_this_month(0),
        state=TimeNoteState.APPROVED,
    )
    log(
        client,
        world,
        "ledger",
        world["alice"],
        4,
        a_day_this_month(1),
        state=TimeNoteState.SUBMITTED,
    )

    sign_in(world["alice"])
    rendered = page(client)

    for label in ("Approved", "Awaiting approval", "Draft", "Sent back", "Logged in total"):
        assert label in rendered
    assert "not a substitute for approved" in rendered


def test_the_billable_split_is_shown(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], 8, a_day_this_month(0))
    log(client, world, "ledger", world["alice"], 2, a_day_this_month(1), task="Admin")

    sign_in(world["alice"])
    assert "2.0h non-billable" in page(client)


# --- by project -----------------------------------------------------------------------


def test_hours_are_broken_down_by_project(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], 8, a_day_this_month(0))
    log(client, world, "portal", world["alice"], 3, a_day_this_month(1))

    sign_in(world["alice"])
    rendered = page(client)

    assert "LEDGER" in rendered and "PORTAL" in rendered
    assert "8.0h" in rendered and "3.0h" in rendered


def test_the_figures_agree_with_the_project_dashboard(
    client: TestClient, world, settings, sign_in
) -> None:
    """The same person and period, read two ways, must not disagree."""
    log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        a_day_this_month(0),
        state=TimeNoteState.APPROVED,
    )
    log(client, world, "ledger", world["alice"], 4, a_day_this_month(1))

    with client.app.state.session_factory() as session:
        alice = session.get(User, world["alice"].id)
        mine = metrics.own_time(session, alice)
        per_person = metrics.per_consultant(session, world["projects"]["ledger"]["id"])

    hers = next(row for row in per_person if row.person.id == alice.id)
    ledger = next(row for row in mine.by_project if row.project.code == "LEDGER")

    assert ledger.hours.approved == hers.hours.approved == 8.0
    assert ledger.hours.draft == hers.hours.draft == 4.0


def test_last_month_is_not_this_month(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], 8, a_day_this_month(0))
    # A legal day: the amount is irrelevant, only that it falls in the previous month.
    log(client, world, "ledger", world["alice"], 8, MONTH_FIRST - timedelta(days=10))

    sign_in(world["alice"])
    with client.app.state.session_factory() as session:
        mine = metrics.own_time(session, session.get(User, world["alice"].id))

    assert mine.hours.logged == 8.0


# --- what needs them ------------------------------------------------------------------


def test_a_sent_back_day_appears_first_with_the_comment(client: TestClient, world, sign_in) -> None:
    log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        a_day_this_month(0),
        rejected_by=world["approver"],
        comment="Split this across two tasks.",
    )

    sign_in(world["alice"])
    rendered = page(client)

    assert "Needs you" in rendered
    assert "Split this across two tasks." in rendered
    assert "approver@example.com" in rendered
    assert rendered.index("Needs you") < rendered.index("This month"), (
        "what needs correcting comes before the summary"
    )
    assert "Correct it" in rendered


def test_an_old_draft_is_surfaced(client: TestClient, world, sign_in) -> None:
    old = TODAY - timedelta(days=20)
    if old < MONTH_FIRST:
        old = MONTH_FIRST
    log(client, world, "ledger", world["alice"], 8, old)

    sign_in(world["alice"])

    with client.app.state.session_factory() as session:
        mine = metrics.own_time(session, session.get(User, world["alice"].id))

    if (TODAY - old).days >= 7:
        assert mine.stale_drafts, "a draft older than a week was probably meant to be submitted"
    else:
        assert mine.stale_drafts == []


def test_a_fresh_draft_is_not_nagged_about(client: TestClient, world, sign_in) -> None:
    log(client, world, "ledger", world["alice"], 8, TODAY)

    sign_in(world["alice"])
    with client.app.state.session_factory() as session:
        mine = metrics.own_time(session, session.get(User, world["alice"].id))

    assert mine.stale_drafts == []


def test_a_sent_back_day_is_not_also_counted_as_a_stale_draft(
    client: TestClient, world, sign_in
) -> None:
    old = max(MONTH_FIRST, TODAY - timedelta(days=20))
    log(client, world, "ledger", world["alice"], 8, old, rejected_by=world["approver"])

    sign_in(world["alice"])
    with client.app.state.session_factory() as session:
        mine = metrics.own_time(session, session.get(User, world["alice"].id))

    assert len(mine.sent_back) == 1
    assert mine.stale_drafts == [], "one problem, listed once"
    assert mine.needs_attention == 1


# --- waiting on somebody else ---------------------------------------------------------


def test_submitted_days_show_where_the_chain_has_reached(
    client: TestClient, world, sign_in
) -> None:
    note_id = log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        a_day_this_month(0),
        state=TimeNoteState.SUBMITTED,
    )

    sign_in(world["alice"])
    rendered = page(client)

    assert "Waiting on an approver" in rendered
    assert "0 of 1 approved" in rendered
    assert "with Ade Approver" in rendered
    assert f"/projects/{world['projects']['ledger']['id']}/notes/{note_id}/history" in rendered


def test_nothing_submitted_says_so(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    assert "Nothing of yours is waiting on an approver." in page(client)


def test_an_approver_gets_a_link_to_their_queue_not_a_second_copy(
    client: TestClient, world, sign_in
) -> None:
    log(
        client,
        world,
        "ledger",
        world["alice"],
        8,
        a_day_this_month(0),
        state=TimeNoteState.SUBMITTED,
    )

    sign_in(world["approver"])
    rendered = page(client)

    assert "waiting on your approval" in rendered
    assert 'href="/approvals"' in rendered
    assert "Alice Consultant" not in rendered, "the queue is not repeated here"


def test_somebody_who_approves_nothing_sees_no_queue_line(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["alice"])
    assert "waiting on your approval" not in page(client)


# --- the empty case -------------------------------------------------------------------


def test_someone_with_no_time_is_told_how_to_log_it(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    rendered = page(client)

    assert "Nothing logged this month" in rendered
    assert "double-click a day" in rendered, "an empty state that says what to do next"
    assert "week view" in rendered
    assert '/projects"' in rendered


def test_a_consultant_lands_here(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    landing = client.get("/", follow_redirects=False)

    assert landing.status_code == 303
    assert landing.headers["location"] == "/me"


def test_the_nav_offers_it_to_everybody(client: TestClient, world, sign_in) -> None:
    for person in (world["alice"], world["administrator"]):
        sign_in(person)
        assert 'href="/me"' in flat(client.get("/projects"))
