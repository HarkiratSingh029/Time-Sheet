"""Withdraw, reopen, and the append-only record of how a day got where it is."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.models import (
    ApprovalDecision,
    AuditAction,
    AuditEvent,
    Project,
    Task,
    TimeNote,
    TimeNoteState,
)
from backend.app.seed import AuditTamperError

DAY = date(2026, 2, 3)
MONTH = "2026-02"


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    consultant = make_person("consultant@example.com", "Priya Consultant")
    approver = make_person("approver@example.com", "Ade Approver")
    outsider = make_person("outsider@example.com", "Ozzy Outsider")

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
            "required_approvals": "1",
        },
    )
    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "LEDGER"))

    client.post(f"/projects/{project_id}/members", data={"user_id": str(consultant.id)})
    client.post(
        f"/projects/{project_id}/members",
        data={"user_id": str(approver.id), "is_approver": "true", "approval_order": "1"},
    )
    client.post(f"/projects/{project_id}/tasks", data={"name": "Discovery"})
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


def log_day(client: TestClient, world) -> int:
    client.post(
        f"/projects/{world['project_id']}/notes",
        data={
            "work_date": DAY.isoformat(),
            "task_id": str(world["task_id"]),
            "hours": "8",
            "detail": "Reviewed the schema",
            "month": MONTH,
        },
    )
    with client.app.state.session_factory() as session:
        return session.scalar(select(TimeNote.id).where(TimeNote.project_id == world["project_id"]))


def submit(client: TestClient, world) -> None:
    client.post(
        f"/projects/{world['project_id']}/notes/submit",
        data={"from_date": DAY.isoformat(), "month": MONTH},
    )


def actions(client: TestClient, note_id: int) -> list[AuditAction]:
    with client.app.state.session_factory() as session:
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.time_note_id == note_id).order_by(AuditEvent.id)
        ).all()
        return [event.action for event in events]


def state(client: TestClient, note_id: int) -> TimeNoteState:
    with client.app.state.session_factory() as session:
        return session.get(TimeNote, note_id).state


# --- recording ------------------------------------------------------------------------


def test_every_transition_writes_exactly_one_event(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    note_id = log_day(client, world)
    assert actions(client, note_id) == [], "logging a draft is not a transition"

    submit(client, world)
    assert actions(client, note_id) == [AuditAction.SUBMITTED]

    sign_in(world["approver"])
    client.post(f"/approvals/{note_id}/reject", data={"comment": "Wrong task."})
    assert actions(client, note_id) == [AuditAction.SUBMITTED, AuditAction.REJECTED]

    sign_in(world["consultant"])
    submit(client, world)
    client.post(f"/projects/{world['project_id']}/notes/{note_id}/withdraw", data={"month": MONTH})
    assert actions(client, note_id) == [
        AuditAction.SUBMITTED,
        AuditAction.REJECTED,
        AuditAction.SUBMITTED,
        AuditAction.WITHDRAWN,
    ]

    submit(client, world)
    sign_in(world["approver"])
    client.post(f"/approvals/{note_id}/approve")

    sign_in(world["administrator"])
    client.post(
        f"/projects/{world['project_id']}/notes/{note_id}/reopen",
        data={"reason": "Billed to the wrong client.", "month": MONTH},
    )

    assert actions(client, note_id) == [
        AuditAction.SUBMITTED,
        AuditAction.REJECTED,
        AuditAction.SUBMITTED,
        AuditAction.WITHDRAWN,
        AuditAction.SUBMITTED,
        AuditAction.APPROVED,
        AuditAction.REOPENED,
    ]


def test_an_event_records_the_actor_the_states_and_the_reason(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    note_id = log_day(client, world)
    submit(client, world)

    sign_in(world["approver"])
    client.post(f"/approvals/{note_id}/reject", data={"comment": "Split across two tasks."})

    with client.app.state.session_factory() as session:
        event = session.scalars(
            select(AuditEvent).where(AuditEvent.action == AuditAction.REJECTED)
        ).one()
        assert event.actor_id == world["approver"].id
        assert event.from_state == "submitted"
        assert event.to_state == "draft"
        assert event.reason == "Split across two tasks."
        assert event.occurred_at is not None


# --- withdraw -------------------------------------------------------------------------


def test_an_author_withdraws_their_own_untouched_submission(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    note_id = log_day(client, world)
    submit(client, world)

    response = client.post(
        f"/projects/{world['project_id']}/notes/{note_id}/withdraw", data={"month": MONTH}
    )
    assert "back to draft" in response.text
    assert state(client, note_id) is TimeNoteState.DRAFT

    # And it is editable again.
    client.post(
        f"/projects/{world['project_id']}/notes/{note_id}",
        data={"task_id": str(world["task_id"]), "hours": "6", "detail": "Corrected"},
    )
    with client.app.state.session_factory() as session:
        assert session.get(TimeNote, note_id).duration_minutes == 360


def test_withdraw_is_refused_once_an_approver_has_decided(
    client: TestClient, world, make_person, sign_in
) -> None:
    """Two approvals, so a decision exists while the note is still submitted."""
    second = make_person("second@example.com", "Bo Second")
    sign_in(world["administrator"])
    client.post(
        f"/projects/{world['project_id']}/members",
        data={"user_id": str(second.id), "is_approver": "true", "approval_order": "2"},
    )
    client.post(
        f"/projects/{world['project_id']}/edit",
        data={
            "name": "Ledger",
            "code": "ledger",
            "description": "",
            "comment": "",
            "owner_id": str(world["administrator"].id),
            "manager_id": "",
            "status": "active",
            "cost": "",
            "billable_hours_budget": "",
            "proposed_duration_days": "",
            "start_date": "2026-01-01",
            "end_date": "2026-06-30",
            "required_approvals": "2",
        },
    )

    sign_in(world["consultant"])
    note_id = log_day(client, world)
    submit(client, world)

    sign_in(world["approver"])
    client.post(f"/approvals/{note_id}/approve")

    sign_in(world["consultant"])
    response = client.post(
        f"/projects/{world['project_id']}/notes/{note_id}/withdraw", data={"month": MONTH}
    )
    assert "already decided" in response.text
    assert state(client, note_id) is TimeNoteState.SUBMITTED


def test_nobody_withdraws_somebody_elses_day(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    note_id = log_day(client, world)
    submit(client, world)

    for person in (world["administrator"], world["approver"], world["outsider"]):
        sign_in(person)
        response = client.post(
            f"/projects/{world['project_id']}/notes/{note_id}/withdraw", data={"month": MONTH}
        )
        assert response.status_code == 404

    assert state(client, note_id) is TimeNoteState.SUBMITTED


# --- reopen ---------------------------------------------------------------------------


def test_an_administrator_reopens_approved_time_with_a_reason(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    note_id = log_day(client, world)
    submit(client, world)
    sign_in(world["approver"])
    client.post(f"/approvals/{note_id}/approve")
    assert state(client, note_id) is TimeNoteState.APPROVED

    sign_in(world["administrator"])
    response = client.post(
        f"/projects/{world['project_id']}/notes/{note_id}/reopen",
        data={"reason": "Billed to the wrong client.", "month": MONTH},
    )
    assert "Reopened" in response.text
    assert state(client, note_id) is TimeNoteState.DRAFT

    with client.app.state.session_factory() as session:
        note = session.get(TimeNote, note_id)
        assert all(a.decision is ApprovalDecision.PENDING for a in note.approvals)

    # Resubmitting opens the chain again rather than duplicating rows.
    sign_in(world["consultant"])
    submit(client, world)
    with client.app.state.session_factory() as session:
        assert len(session.get(TimeNote, note_id).approvals) == 1


def test_reopening_without_a_reason_is_refused(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    note_id = log_day(client, world)
    submit(client, world)
    sign_in(world["approver"])
    client.post(f"/approvals/{note_id}/approve")

    sign_in(world["administrator"])
    response = client.post(
        f"/projects/{world['project_id']}/notes/{note_id}/reopen",
        data={"reason": "   ", "month": MONTH},
    )
    assert "needs a reason" in response.text
    assert state(client, note_id) is TimeNoteState.APPROVED


def test_only_an_administrator_reopens(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    note_id = log_day(client, world)
    submit(client, world)
    sign_in(world["approver"])
    client.post(f"/approvals/{note_id}/approve")

    for person in (world["consultant"], world["approver"]):
        sign_in(person)
        response = client.post(
            f"/projects/{world['project_id']}/notes/{note_id}/reopen",
            data={"reason": "Let me at it", "month": MONTH},
        )
        assert "Only an administrator" in response.text

    assert state(client, note_id) is TimeNoteState.APPROVED


def test_a_draft_cannot_be_reopened(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    note_id = log_day(client, world)

    sign_in(world["administrator"])
    response = client.post(
        f"/projects/{world['project_id']}/notes/{note_id}/reopen",
        data={"reason": "Nothing to reopen", "month": MONTH},
    )
    assert "Only an approved entry" in response.text


# --- append-only ----------------------------------------------------------------------


def test_an_audit_event_cannot_be_edited_or_deleted(client: TestClient, world, sign_in) -> None:
    sign_in(world["consultant"])
    note_id = log_day(client, world)
    submit(client, world)

    with client.app.state.session_factory() as session:
        event = session.scalars(select(AuditEvent).where(AuditEvent.time_note_id == note_id)).one()

        event.reason = "a tidier version of history"
        with pytest.raises(AuditTamperError):
            session.commit()
        session.rollback()

        event = session.scalars(select(AuditEvent).where(AuditEvent.time_note_id == note_id)).one()
        session.delete(event)
        with pytest.raises(AuditTamperError):
            session.commit()
        session.rollback()

        assert session.scalars(select(AuditEvent).where(AuditEvent.time_note_id == note_id)).one()


def test_deleting_a_draft_takes_its_history_with_it(client: TestClient, world, sign_in) -> None:
    """A cascade is not tampering — the thing being described is going away too."""
    sign_in(world["consultant"])
    note_id = log_day(client, world)
    submit(client, world)
    client.post(f"/projects/{world['project_id']}/notes/{note_id}/withdraw", data={"month": MONTH})
    client.post(f"/projects/{world['project_id']}/notes/{note_id}/delete", data={"month": MONTH})

    with client.app.state.session_factory() as session:
        assert session.get(TimeNote, note_id) is None
        assert (
            session.scalars(select(AuditEvent).where(AuditEvent.time_note_id == note_id)).all()
            == []
        )


# --- who may read it ------------------------------------------------------------------


def test_the_history_is_visible_to_the_author_its_approvers_and_administrators(
    client: TestClient, world, sign_in
) -> None:
    sign_in(world["consultant"])
    note_id = log_day(client, world)
    submit(client, world)
    path = f"/projects/{world['project_id']}/notes/{note_id}/history"

    for person in (world["consultant"], world["approver"], world["administrator"]):
        sign_in(person)
        response = client.get(path)
        assert response.status_code == 200, f"{person.email} should read this history"
        assert "Submitted" in response.text


def test_the_history_is_invisible_to_everybody_else(
    client: TestClient, world, make_person, sign_in
) -> None:
    other_member = make_person("bystander@example.com", "Bys Tander")
    sign_in(world["administrator"])
    client.post(f"/projects/{world['project_id']}/members", data={"user_id": str(other_member.id)})

    sign_in(world["consultant"])
    note_id = log_day(client, world)
    submit(client, world)
    path = f"/projects/{world['project_id']}/notes/{note_id}/history"

    for person in (world["outsider"], other_member):
        sign_in(person)
        assert client.get(path).status_code == 404, "whose day it was is itself information"
