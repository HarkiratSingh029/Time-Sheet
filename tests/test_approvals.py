"""The approval half of the workflow, and the whole EPIC 0 path end to end."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import (
    ApprovalDecision,
    Project,
    ProjectMember,
    Task,
    TimeNote,
    TimeNoteState,
)

START = date(2026, 1, 1)
END = date(2026, 6, 30)
DAY = date(2026, 2, 3)
MONTH = "2026-02"


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    """A project with a consultant who logs, an approver who signs off, and an outsider."""
    consultant = make_person("consultant@example.com", "Priya Consultant")
    approver = make_person("approver@example.com", "Ade Approver")
    outsider = make_person("outsider@example.com", "Ozzy Outsider")

    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Ledger migration",
            "code": "ledger",
            "description": "",
            "comment": "",
            "owner_id": str(administrator.id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "600",
            "proposed_duration_days": "",
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
        },
    )
    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "LEDGER"))

    client.post(f"/projects/{project_id}/members", data={"user_id": str(consultant.id)})
    client.post(
        f"/projects/{project_id}/members",
        data={"user_id": str(approver.id), "is_approver": "true"},
    )
    client.post(f"/projects/{project_id}/tasks", data={"name": "Discovery", "is_billable": "true"})

    with client.app.state.session_factory() as session:
        task_id = session.scalar(select(Task.id).where(Task.project_id == project_id))

    return {
        "project_id": project_id,
        "task_id": task_id,
        "administrator": administrator,
        "consultant": consultant,
        "approver": approver,
        "outsider": outsider,
    }


def log_and_submit(client: TestClient, world, *, day: date = DAY, hours: str = "7.5") -> int:
    project_id = world["project_id"]
    client.post(
        f"/projects/{project_id}/notes",
        data={
            "work_date": day.isoformat(),
            "task_id": str(world["task_id"]),
            "hours": hours,
            "detail": "Reviewed the legacy schema",
            "month": MONTH,
        },
    )
    client.post(
        f"/projects/{project_id}/notes/submit",
        data={"from_date": day.isoformat(), "month": MONTH},
    )
    with client.app.state.session_factory() as session:
        return session.scalar(
            select(TimeNote.id).where(TimeNote.project_id == project_id, TimeNote.work_date == day)
        )


def note_state(client: TestClient, note_id: int) -> TimeNoteState:
    with client.app.state.session_factory() as session:
        return session.get(TimeNote, note_id).state


# --- the queue ------------------------------------------------------------------------


def test_a_submitted_entry_reaches_the_approver_in_one_page_load(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    log_and_submit(client, world)

    sign_in(world["approver"])
    queue = client.get("/approvals")

    assert queue.status_code == 200
    assert "consultant@example.com" in queue.text
    assert "7.5h" in queue.text
    assert "Reviewed the legacy schema" in queue.text


def test_the_queue_is_empty_before_anything_is_submitted(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["approver"])
    assert "Nothing is waiting on you" in client.get("/approvals").text


def test_a_draft_never_appears_in_the_queue(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    client.post(
        f"/projects/{world['project_id']}/notes",
        data={
            "work_date": DAY.isoformat(),
            "task_id": str(world["task_id"]),
            "hours": "4",
            "detail": "Still writing this up",
            "month": MONTH,
        },
    )

    sign_in(world["approver"])
    assert "Still writing this up" not in client.get("/approvals").text


def test_someone_elses_queue_is_not_yours(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    log_and_submit(client, world)

    sign_in(world["outsider"])
    assert "consultant@example.com" not in client.get("/approvals").text

    sign_in(world["consultant"])
    assert "Nothing is waiting on you" in client.get("/approvals").text, (
        "logging time does not make you your own approver"
    )


# --- approving ------------------------------------------------------------------------


def test_approving_settles_the_note_and_freezes_it(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    note_id = log_and_submit(client, world)

    sign_in(world["approver"])
    response = client.post(f"/approvals/{note_id}/approve")

    assert "Approved 03 Feb" in response.text
    assert note_state(client, note_id) is TimeNoteState.APPROVED

    with client.app.state.session_factory() as session:
        note = session.get(TimeNote, note_id)
        assert [approval.decision for approval in note.approvals] == [ApprovalDecision.APPROVED]

    sign_in(world["consultant"])
    blocked = client.post(
        f"/projects/{world['project_id']}/notes/{note_id}",
        data={"task_id": str(world["task_id"]), "hours": "1", "detail": "Changed my mind"},
    )
    assert "approved" in blocked.text.lower()
    with client.app.state.session_factory() as session:
        assert session.get(TimeNote, note_id).duration_minutes == 450


def test_an_approved_entry_leaves_the_queue(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    note_id = log_and_submit(client, world)

    sign_in(world["approver"])
    client.post(f"/approvals/{note_id}/approve")
    assert "Nothing is waiting on you" in client.get("/approvals").text


def test_an_administrator_may_approve_anywhere(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    note_id = log_and_submit(client, world)

    sign_in(world["administrator"])
    client.post(f"/approvals/{note_id}/approve")

    assert note_state(client, note_id) is TimeNoteState.APPROVED


# --- rejecting ------------------------------------------------------------------------


def test_rejecting_returns_the_day_with_its_reason(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    note_id = log_and_submit(client, world)

    sign_in(world["approver"])
    response = client.post(
        f"/approvals/{note_id}/reject", data={"comment": "Split this across two tasks."}
    )

    assert "Sent 03 Feb back" in response.text
    assert note_state(client, note_id) is TimeNoteState.DRAFT

    sign_in(world["consultant"])
    calendar = client.get(f"/projects/{world['project_id']}/calendar?month={MONTH}")
    assert "was sent back" in calendar.text
    assert "Split this across two tasks." in calendar.text, "the author must see the reason"


def test_a_rejection_without_a_comment_is_refused(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    note_id = log_and_submit(client, world)

    sign_in(world["approver"])
    response = client.post(f"/approvals/{note_id}/reject", data={"comment": "   "})

    assert "needs a comment" in response.text
    assert note_state(client, note_id) is TimeNoteState.SUBMITTED, "still awaiting a decision"


def test_a_rejected_day_can_be_corrected_and_resubmitted(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    note_id = log_and_submit(client, world)

    sign_in(world["approver"])
    client.post(f"/approvals/{note_id}/reject", data={"comment": "Too many hours."})

    sign_in(world["consultant"])
    project_id = world["project_id"]
    client.post(
        f"/projects/{project_id}/notes/{note_id}",
        data={"task_id": str(world["task_id"]), "hours": "6", "detail": "Corrected"},
    )
    client.post(
        f"/projects/{project_id}/notes/submit",
        data={"from_date": DAY.isoformat(), "month": MONTH},
    )

    assert note_state(client, note_id) is TimeNoteState.SUBMITTED

    with client.app.state.session_factory() as session:
        note = session.get(TimeNote, note_id)
        assert note.duration_minutes == 360
        assert len(note.approvals) == 1, "resubmitting reopens the row rather than adding one"
        assert note.approvals[0].decision is ApprovalDecision.PENDING

    sign_in(world["approver"])
    client.post(f"/approvals/{note_id}/approve")
    assert note_state(client, note_id) is TimeNoteState.APPROVED


# --- who may decide -------------------------------------------------------------------


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_a_non_approver_cannot_decide_by_url_or_form_post(
    client: TestClient, world, sign_in, action: str
) -> None:
    sign_in(world["consultant"])
    note_id = log_and_submit(client, world)

    for person in (world["consultant"], world["outsider"]):
        sign_in(person)
        response = client.post(
            f"/approvals/{note_id}/{action}", data={"comment": "Letting myself off"}
        )
        assert response.status_code == 404, f"{person.email} must not reach this entry"

    assert note_state(client, note_id) is TimeNoteState.SUBMITTED


def test_the_queue_requires_a_session(client: TestClient) -> None:
    response = client.get("/approvals", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- fallback -------------------------------------------------------------------------


def test_a_project_with_no_named_approver_falls_back_to_its_owner(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    consultant = make_person("solo@example.com", "Solo Consultant")

    sign_in(administrator)
    client.post(
        "/projects",
        data={
            "name": "Unstaffed",
            "code": "solo",
            "description": "",
            "comment": "",
            "owner_id": str(administrator.id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "",
            "proposed_duration_days": "",
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
        },
    )
    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "SOLO"))
    client.post(f"/projects/{project_id}/members", data={"user_id": str(consultant.id)})
    client.post(f"/projects/{project_id}/tasks", data={"name": "Discovery"})
    with client.app.state.session_factory() as session:
        task_id = session.scalar(select(Task.id).where(Task.project_id == project_id))
        assert not session.scalars(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.is_approver.is_(True)
            )
        ).all()

    sign_in(consultant)
    client.post(
        f"/projects/{project_id}/notes",
        data={
            "work_date": DAY.isoformat(),
            "task_id": str(task_id),
            "hours": "3",
            "detail": "Nobody named to approve this",
            "month": MONTH,
        },
    )
    client.post(
        f"/projects/{project_id}/notes/submit",
        data={"from_date": DAY.isoformat(), "month": MONTH},
    )

    sign_in(administrator)
    assert "Nobody named to approve this" in client.get("/approvals").text, (
        "a submission with no approver named must not vanish into a queue nobody can see"
    )


# --- the whole EPIC 0 path ------------------------------------------------------------


def test_the_full_epic_0_path(client: TestClient, world, sign_in) -> None:
    """login → project → assign → log → submit → approve, as one journey."""
    sign_in(world["consultant"])

    calendar = client.get(f"/projects/{world['project_id']}/calendar?month={MONTH}")
    assert calendar.status_code == 200

    note_id = log_and_submit(client, world, hours="8")
    assert note_state(client, note_id) is TimeNoteState.SUBMITTED
    assert (
        "chip--submitted"
        in client.get(f"/projects/{world['project_id']}/calendar?month={MONTH}").text
    )

    sign_in(world["approver"])
    assert "8h" in client.get("/approvals").text
    client.post(f"/approvals/{note_id}/approve")

    sign_in(world["consultant"])
    final = client.get(f"/projects/{world['project_id']}/calendar?month={MONTH}")
    assert "chip--approved" in final.text
    assert note_state(client, note_id) is TimeNoteState.APPROVED
