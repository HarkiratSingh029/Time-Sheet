"""The approver's queue at month-end scale: filters, grouping, ageing and bulk approval."""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    ProjectMember,
    Task,
    TimeNote,
    TimeNoteState,
    utcnow,
)
from backend.app.queue import AGEING_DAYS, Filters, group_by_person_and_week, parse_filters
from backend.app.security import hash_password
from backend.app.seed import ensure_member_role

MONDAY = date(2026, 2, 2)


def flat(response) -> str:
    """Templates wrap for readability; assertions should not care where the line broke."""
    return " ".join(response.text.split())


def results(response) -> str:
    """Only the entries, not the filter dropdowns — which list everything on purpose, so
    that an approver can switch filters without first clearing the one they applied."""
    page = flat(response)
    marker = "data-queue-results"
    return page[page.index(marker) :] if marker in page else ""


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    """One approver over two projects, with two consultants logging on each."""
    approver = make_person("approver@example.com", "Ade Approver")
    alice = make_person("alice@example.com", "Alice Consultant")
    bob = make_person("bob@example.com", "Bob Consultant")

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
                "start_date": "2026-01-01",
                "end_date": "2026-06-30",
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
        client.post(f"/projects/{project_id}/tasks", data={"name": "Delivery"})
        with client.app.state.session_factory() as session:
            task_id = session.scalar(select(Task.id).where(Task.project_id == project_id))
        projects[code] = {"id": project_id, "task_id": task_id}

    return {
        "projects": projects,
        "administrator": administrator,
        "approver": approver,
        "alice": alice,
        "bob": bob,
    }


def log_and_submit(
    client: TestClient, world, person, code: str, day: date, hours: str = "8"
) -> None:
    project = world["projects"][code]
    client.post(
        f"/projects/{project['id']}/notes",
        data={
            "work_date": day.isoformat(),
            "task_id": str(project["task_id"]),
            "hours": hours,
            "detail": f"{person.email} on {day}",
            "month": day.strftime("%Y-%m"),
        },
    )
    client.post(
        f"/projects/{project['id']}/notes/submit",
        data={"from_date": day.isoformat(), "month": day.strftime("%Y-%m")},
    )


def fill_queue(client: TestClient, world, sign_in) -> None:
    """Alice: three days on ledger, one on portal. Bob: two on ledger, the next week."""
    sign_in(world["alice"])
    for offset in (0, 1, 2):
        log_and_submit(client, world, world["alice"], "ledger", MONDAY + timedelta(days=offset))
    log_and_submit(client, world, world["alice"], "portal", MONDAY)

    sign_in(world["bob"])
    for offset in (7, 8):
        log_and_submit(client, world, world["bob"], "ledger", MONDAY + timedelta(days=offset))


# --- grouping -------------------------------------------------------------------------


def test_entries_group_by_person_project_and_week(client: TestClient, world, sign_in) -> None:
    fill_queue(client, world, sign_in)

    sign_in(world["approver"])
    page = flat(client.get("/approvals"))

    assert "Alice Consultant · week of 02 Feb 2026" in page
    assert "Bob Consultant · week of 09 Feb 2026" in page
    assert "24h" in page, "Alice's three ledger days total in one group"
    assert "16h" in page, "Bob's two days total in another"


def test_a_group_totals_only_its_own_hours(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    log_and_submit(client, world, world["alice"], "ledger", MONDAY, hours="7.5")
    log_and_submit(client, world, world["alice"], "ledger", MONDAY + timedelta(days=1), hours="4")

    with client.app.state.session_factory() as session:
        notes = list(session.scalars(select(TimeNote)).all())
        for note in notes:
            _ = note.user, note.project
        groups = group_by_person_and_week(notes)

    assert len(groups) == 1
    assert groups[0].hours == 11.5


# --- filters --------------------------------------------------------------------------


def test_filters_narrow_the_queue_and_compose(client: TestClient, world, sign_in) -> None:
    fill_queue(client, world, sign_in)
    sign_in(world["approver"])
    ledger = world["projects"]["ledger"]["id"]

    by_project = results(client.get(f"/approvals?project_id={ledger}"))
    assert "PORTAL" not in by_project, "the group cards show the project code"
    assert "LEDGER" in by_project

    by_person = results(client.get(f"/approvals?person_id={world['bob'].id}"))
    assert "Bob Consultant" in by_person
    assert "Alice Consultant" not in by_person

    composed = results(
        client.get(
            f"/approvals?project_id={ledger}&person_id={world['alice'].id}"
            f"&since=2026-02-02&until=2026-02-03"
        )
    )
    assert "Alice Consultant" in composed
    assert "Bob Consultant" not in composed
    assert "16h" in composed, "two of Alice's three ledger days"


def test_a_filter_survives_a_reload_and_can_be_cleared(client: TestClient, world, sign_in) -> None:
    fill_queue(client, world, sign_in)
    sign_in(world["approver"])
    url = f"/approvals?person_id={world['bob'].id}"

    first = client.get(url).text
    assert first == client.get(url).text, "the same URL gives the same view"
    assert 'href="/approvals"' in first, "and offers a way back to everything"

    assert "Alice Consultant" in client.get("/approvals").text


def test_filters_that_match_nothing_say_so_without_hiding_the_queue(
    client: TestClient, world, sign_in
) -> None:
    fill_queue(client, world, sign_in)
    sign_in(world["approver"])

    page = client.get("/approvals?since=2030-01-01").text
    assert "Nothing matches those filters" in page
    assert "still waiting" in page


def test_an_unparseable_filter_is_ignored_rather_than_fatal(
    client: TestClient, world, sign_in
) -> None:
    fill_queue(client, world, sign_in)
    sign_in(world["approver"])

    response = client.get("/approvals?project_id=banana&since=not-a-date")
    assert response.status_code == 200
    assert "Alice Consultant" in response.text

    assert parse_filters("banana", None, "not-a-date", None) == Filters()


def test_the_filter_choices_only_offer_what_is_actually_queued(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["alice"])
    log_and_submit(client, world, world["alice"], "ledger", MONDAY)

    sign_in(world["approver"])
    page = client.get("/approvals").text
    assert "LEDGER" in page
    assert "PORTAL" not in page, "a project with nothing pending is not a useful filter"


# --- ageing ---------------------------------------------------------------------------


def test_entries_waiting_too_long_are_marked(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    log_and_submit(client, world, world["alice"], "ledger", MONDAY)

    with client.app.state.session_factory() as session:
        note = session.scalar(select(TimeNote))
        note.submitted_at = utcnow() - timedelta(days=AGEING_DAYS + 2)
        session.commit()

    sign_in(world["approver"])
    page = flat(client.get("/approvals"))
    assert f"Waiting {AGEING_DAYS + 2} days" in page


def test_a_fresh_entry_is_not_marked(client: TestClient, world, sign_in) -> None:
    sign_in(world["alice"])
    log_and_submit(client, world, world["alice"], "ledger", MONDAY)

    sign_in(world["approver"])
    # "Waiting" alone is a column header; the chip is what marks an ageing group.
    assert "days</span>" not in flat(client.get("/approvals"))


def test_the_oldest_group_comes_first(client: TestClient, world, sign_in) -> None:
    fill_queue(client, world, sign_in)

    with client.app.state.session_factory() as session:
        bobs = session.scalars(select(TimeNote).where(TimeNote.user_id == world["bob"].id)).all()
        for note in bobs:
            note.submitted_at = utcnow() - timedelta(days=20)
        session.commit()

    sign_in(world["approver"])
    page = results(client.get("/approvals"))
    assert page.index("Bob Consultant") < page.index("Alice Consultant"), (
        "what has waited longest should be at the top"
    )


# --- bulk approve ---------------------------------------------------------------------


def test_bulk_approve_settles_exactly_the_selected_group(
    client: TestClient, world, sign_in
) -> None:
    fill_queue(client, world, sign_in)
    sign_in(world["approver"])

    with client.app.state.session_factory() as session:
        alice_ledger = [
            note.id
            for note in session.scalars(
                select(TimeNote).where(
                    TimeNote.user_id == world["alice"].id,
                    TimeNote.project_id == world["projects"]["ledger"]["id"],
                )
            ).all()
        ]

    response = client.post("/approvals/approve-group", data={"note_ids": alice_ledger})
    assert "Approved 3 entries." in response.text

    with client.app.state.session_factory() as session:
        for note_id in alice_ledger:
            assert session.get(TimeNote, note_id).state is TimeNoteState.APPROVED
        untouched = session.scalars(
            select(TimeNote).where(TimeNote.user_id == world["bob"].id)
        ).all()
        assert all(note.state is TimeNoteState.SUBMITTED for note in untouched)


def test_bulk_approve_writes_an_audit_event_for_every_entry(
    client: TestClient, world, sign_in
) -> None:
    fill_queue(client, world, sign_in)
    sign_in(world["approver"])

    with client.app.state.session_factory() as session:
        bobs = [
            note.id
            for note in session.scalars(
                select(TimeNote).where(TimeNote.user_id == world["bob"].id)
            ).all()
        ]

    client.post("/approvals/approve-group", data={"note_ids": bobs})

    from backend.app.models import AuditAction, AuditEvent

    with client.app.state.session_factory() as session:
        for note_id in bobs:
            events = session.scalars(
                select(AuditEvent).where(
                    AuditEvent.time_note_id == note_id,
                    AuditEvent.action == AuditAction.APPROVED,
                )
            ).all()
            assert len(events) == 1, "a bulk action is audited like any other"


def test_bulk_approve_is_all_or_nothing(client: TestClient, world, make_person, sign_in) -> None:
    """Slipping somebody else's entry into the list must not approve the rest either."""
    fill_queue(client, world, sign_in)

    stranger = make_person("stranger@example.com", "Stan Stranger")
    sign_in(world["administrator"])
    with client.app.state.session_factory() as session:
        role_id = ensure_member_role(session).id
        other_project = Project(
            name="Unrelated",
            code="OTHER",
            owner_id=world["administrator"].id,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 6, 30),
        )
        session.add(other_project)
        session.flush()
        task = Task(project_id=other_project.id, name="Work")
        session.add(task)
        session.add(ProjectMember(project_id=other_project.id, user_id=stranger.id))
        session.flush()
        foreign = TimeNote(
            project_id=other_project.id,
            task_id=task.id,
            user_id=stranger.id,
            work_date=MONDAY,
            duration_minutes=60,
            state=TimeNoteState.SUBMITTED,
        )
        session.add(foreign)
        session.flush()
        session.add(
            Approval(
                time_note_id=foreign.id,
                approver_id=stranger.id,
                sequence=1,
                decision=ApprovalDecision.PENDING,
            )
        )
        session.commit()
        foreign_id = foreign.id
        assert role_id

    sign_in(world["approver"])
    with client.app.state.session_factory() as session:
        mine = [
            note.id
            for note in session.scalars(
                select(TimeNote).where(TimeNote.user_id == world["alice"].id)
            ).all()
        ]

    response = client.post("/approvals/approve-group", data={"note_ids": [*mine, foreign_id]})
    assert "none were approved" in response.text

    with client.app.state.session_factory() as session:
        for note_id in [*mine, foreign_id]:
            assert session.get(TimeNote, note_id).state is TimeNoteState.SUBMITTED


def test_bulk_approve_with_nothing_selected_says_so(client: TestClient, world, sign_in) -> None:
    sign_in(world["approver"])
    assert "Nothing was selected" in client.post("/approvals/approve-group", data={}).text


def test_there_is_no_bulk_reject(client: TestClient, world, sign_in) -> None:
    fill_queue(client, world, sign_in)
    sign_in(world["approver"])

    assert client.post("/approvals/reject-group", data={"note_ids": [1, 2]}).status_code == 404
    assert "reject-group" not in client.get("/approvals").text


def test_rejection_is_still_one_entry_at_a_time_with_a_comment(
    client: TestClient, world, sign_in
) -> None:
    fill_queue(client, world, sign_in)
    sign_in(world["approver"])

    with client.app.state.session_factory() as session:
        note_id = session.scalar(select(TimeNote.id))

    refused = client.post(f"/approvals/{note_id}/reject", data={"comment": "  "})
    assert "needs a comment" in refused.text

    client.post(f"/approvals/{note_id}/reject", data={"comment": "Wrong project."})
    with client.app.state.session_factory() as session:
        assert session.get(TimeNote, note_id).state is TimeNoteState.DRAFT


def test_deciding_returns_to_the_filtered_view(client: TestClient, world, sign_in) -> None:
    fill_queue(client, world, sign_in)
    sign_in(world["approver"])
    query = f"person_id={world['bob'].id}"

    with client.app.state.session_factory() as session:
        note_id = session.scalar(select(TimeNote.id).where(TimeNote.user_id == world["bob"].id))

    response = client.post(
        f"/approvals/{note_id}/approve", data={"next": query}, follow_redirects=False
    )
    assert response.headers["location"] == f"/approvals?{query}", (
        "an approver should land back where they were, not at the top of the queue"
    )


# --- scale ----------------------------------------------------------------------------


def test_the_queue_stays_quick_with_five_hundred_pending_entries(
    client: TestClient, world, sign_in
) -> None:
    project = world["projects"]["ledger"]
    approver_id = world["approver"].id

    with client.app.state.session_factory() as session:
        role_id = ensure_member_role(session).id
        people = []
        for index in range(10):
            person = client_user = None
            from backend.app.models import User

            client_user = User(
                email=f"bulk{index}@example.com",
                full_name=f"Bulk {index:02d}",
                password_hash=hash_password("a-properly-long-password"),
                role_id=role_id,
            )
            session.add(client_user)
            session.flush()
            session.add(ProjectMember(project_id=project["id"], user_id=client_user.id))
            people.append(client_user.id)
            assert person is None

        submitted = utcnow()
        for person_id in people:
            for offset in range(50):
                note = TimeNote(
                    project_id=project["id"],
                    task_id=project["task_id"],
                    user_id=person_id,
                    work_date=date(2026, 1, 1) + timedelta(days=offset),
                    duration_minutes=480,
                    detail="Bulk",
                    state=TimeNoteState.SUBMITTED,
                    submitted_at=submitted,
                )
                session.add(note)
                session.flush()
                session.add(
                    Approval(
                        time_note_id=note.id,
                        approver_id=approver_id,
                        sequence=1,
                        decision=ApprovalDecision.PENDING,
                    )
                )
        session.commit()

        assert session.scalar(select(TimeNote).where(TimeNote.state == TimeNoteState.SUBMITTED))

    sign_in(world["approver"])
    started = time.perf_counter()
    response = client.get("/approvals")
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert response.status_code == 200
    assert "500 entries waiting on you" in response.text
    assert elapsed_ms < 200, f"the queue took {elapsed_ms:.0f}ms on 500 entries"
