"""Chains of up to five approvers, signed in order."""

from __future__ import annotations

from datetime import date

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
)

DAY = date(2026, 2, 3)
MONTH = "2026-02"


def project_payload(owner_id: int, **overrides) -> dict[str, str]:
    payload = {
        "name": "Ledger migration",
        "code": "ledger",
        "description": "",
        "comment": "",
        "owner_id": str(owner_id),
        "manager_id": "",
        "status": "active",
        "cost": "",
        "billable_hours_budget": "",
        "proposed_duration_days": "",
        "start_date": "2026-01-01",
        "end_date": "2026-06-30",
        "required_approvals": "3",
    }
    payload.update({key: str(value) for key, value in overrides.items()})
    return payload


@pytest.fixture
def chain(client: TestClient, administrator, make_person, sign_in):
    """A project needing three signatures, with the approvers ordered 1, 2, 3."""
    consultant = make_person("consultant@example.com", "Priya Consultant")
    first = make_person("first@example.com", "Ana First")
    second = make_person("second@example.com", "Bo Second")
    third = make_person("third@example.com", "Cy Third")

    sign_in(administrator)
    client.post("/projects", data=project_payload(administrator.id))
    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "LEDGER"))

    client.post(f"/projects/{project_id}/members", data={"user_id": str(consultant.id)})
    for position, person in enumerate((first, second, third), start=1):
        client.post(
            f"/projects/{project_id}/members",
            data={
                "user_id": str(person.id),
                "is_approver": "true",
                "approval_order": str(position),
            },
        )
    client.post(f"/projects/{project_id}/tasks", data={"name": "Discovery"})

    with client.app.state.session_factory() as session:
        task_id = session.scalar(select(Task.id).where(Task.project_id == project_id))

    return {
        "project_id": project_id,
        "task_id": task_id,
        "administrator": administrator,
        "consultant": consultant,
        "first": first,
        "second": second,
        "third": third,
    }


def submit_a_day(client: TestClient, chain, sign_in) -> int:
    sign_in(chain["consultant"])
    client.post(
        f"/projects/{chain['project_id']}/notes",
        data={
            "work_date": DAY.isoformat(),
            "task_id": str(chain["task_id"]),
            "hours": "8",
            "detail": "Reviewed the legacy schema",
            "month": MONTH,
        },
    )
    client.post(
        f"/projects/{chain['project_id']}/notes/submit",
        data={"from_date": DAY.isoformat(), "month": MONTH},
    )
    with client.app.state.session_factory() as session:
        return session.scalar(select(TimeNote.id).where(TimeNote.project_id == chain["project_id"]))


def note_state(client: TestClient, note_id: int) -> TimeNoteState:
    with client.app.state.session_factory() as session:
        return session.get(TimeNote, note_id).state


def decisions(client: TestClient, note_id: int) -> list[ApprovalDecision]:
    with client.app.state.session_factory() as session:
        rows = session.scalars(
            select(Approval).where(Approval.time_note_id == note_id).order_by(Approval.sequence)
        ).all()
        return [row.decision for row in rows]


# --- opening the chain ----------------------------------------------------------------


def test_submitting_opens_one_row_per_approver_in_order(client: TestClient, chain, sign_in) -> None:
    note_id = submit_a_day(client, chain, sign_in)

    with client.app.state.session_factory() as session:
        rows = session.scalars(
            select(Approval).where(Approval.time_note_id == note_id).order_by(Approval.sequence)
        ).all()

    assert [row.sequence for row in rows] == [1, 2, 3]
    assert [row.approver_id for row in rows] == [
        chain["first"].id,
        chain["second"].id,
        chain["third"].id,
    ]
    assert all(row.decision is ApprovalDecision.PENDING for row in rows)


def test_the_chain_is_capped_at_what_the_project_asks_for(
    client: TestClient, chain, administrator, sign_in
) -> None:
    sign_in(administrator)
    client.post(
        f"/projects/{chain['project_id']}/edit",
        data=project_payload(administrator.id, required_approvals="2"),
    )

    note_id = submit_a_day(client, chain, sign_in)
    assert len(decisions(client, note_id)) == 2


def test_a_shorter_named_list_is_not_padded_with_nobody(
    client: TestClient, administrator, make_person, sign_in
) -> None:
    """Requiring three signatures from two named approvers must not freeze the entry."""
    consultant = make_person("solo@example.com")
    only = make_person("only@example.com")

    sign_in(administrator)
    client.post("/projects", data=project_payload(administrator.id, code="short"))
    with client.app.state.session_factory() as session:
        project_id = session.scalar(select(Project.id).where(Project.code == "SHORT"))
    client.post(f"/projects/{project_id}/members", data={"user_id": str(consultant.id)})
    client.post(
        f"/projects/{project_id}/members",
        data={"user_id": str(only.id), "is_approver": "true", "approval_order": "1"},
    )
    client.post(f"/projects/{project_id}/tasks", data={"name": "Discovery"})
    with client.app.state.session_factory() as session:
        task_id = session.scalar(select(Task.id).where(Task.project_id == project_id))

    sign_in(consultant)
    client.post(
        f"/projects/{project_id}/notes",
        data={
            "work_date": DAY.isoformat(),
            "task_id": str(task_id),
            "hours": "4",
            "detail": "",
            "month": MONTH,
        },
    )
    client.post(
        f"/projects/{project_id}/notes/submit",
        data={"from_date": DAY.isoformat(), "month": MONTH},
    )
    with client.app.state.session_factory() as session:
        note_id = session.scalar(select(TimeNote.id).where(TimeNote.project_id == project_id))

    assert len(decisions(client, note_id)) == 1

    sign_in(only)
    client.post(f"/approvals/{note_id}/approve")
    assert note_state(client, note_id) is TimeNoteState.APPROVED


# --- taking turns ---------------------------------------------------------------------


def test_approver_two_waits_for_approver_one(client: TestClient, chain, sign_in) -> None:
    note_id = submit_a_day(client, chain, sign_in)

    sign_in(chain["second"])
    assert "Reviewed the legacy schema" not in client.get("/approvals").text
    assert client.post(f"/approvals/{note_id}/approve").status_code == 404

    sign_in(chain["first"])
    assert "Reviewed the legacy schema" in client.get("/approvals").text
    client.post(f"/approvals/{note_id}/approve")

    sign_in(chain["second"])
    assert "Reviewed the legacy schema" in client.get("/approvals").text, "now it is their turn"


def test_the_note_settles_only_after_the_last_signature(client: TestClient, chain, sign_in) -> None:
    note_id = submit_a_day(client, chain, sign_in)

    for person in (chain["first"], chain["second"]):
        sign_in(person)
        client.post(f"/approvals/{note_id}/approve")
        assert note_state(client, note_id) is TimeNoteState.SUBMITTED, "not settled yet"

    sign_in(chain["third"])
    client.post(f"/approvals/{note_id}/approve")
    assert note_state(client, note_id) is TimeNoteState.APPROVED
    assert decisions(client, note_id) == [ApprovalDecision.APPROVED] * 3


def test_decide_any_widens_the_queue_but_does_not_skip_the_order(
    client: TestClient, chain, sign_in
) -> None:
    note_id = submit_a_day(client, chain, sign_in)

    sign_in(chain["administrator"])
    client.post(f"/approvals/{note_id}/approve")

    # The administrator decided step 1, not the whole chain.
    assert decisions(client, note_id) == [
        ApprovalDecision.APPROVED,
        ApprovalDecision.PENDING,
        ApprovalDecision.PENDING,
    ]
    assert note_state(client, note_id) is TimeNoteState.SUBMITTED


# --- rejection ------------------------------------------------------------------------


def test_a_rejection_anywhere_returns_the_day_and_clears_the_rest(
    client: TestClient, chain, sign_in
) -> None:
    note_id = submit_a_day(client, chain, sign_in)

    sign_in(chain["first"])
    client.post(f"/approvals/{note_id}/approve")

    sign_in(chain["second"])
    client.post(f"/approvals/{note_id}/reject", data={"comment": "Wrong task."})

    assert note_state(client, note_id) is TimeNoteState.DRAFT
    assert decisions(client, note_id) == [
        ApprovalDecision.APPROVED,
        ApprovalDecision.REJECTED,
        ApprovalDecision.PENDING,
    ]

    sign_in(chain["third"])
    assert "Reviewed the legacy schema" not in client.get("/approvals").text


def test_resubmitting_restarts_the_chain_from_the_top(client: TestClient, chain, sign_in) -> None:
    note_id = submit_a_day(client, chain, sign_in)

    sign_in(chain["first"])
    client.post(f"/approvals/{note_id}/approve")
    sign_in(chain["second"])
    client.post(f"/approvals/{note_id}/reject", data={"comment": "Split it."})

    sign_in(chain["consultant"])
    client.post(
        f"/projects/{chain['project_id']}/notes/submit",
        data={"from_date": DAY.isoformat(), "month": MONTH},
    )

    assert decisions(client, note_id) == [ApprovalDecision.PENDING] * 3, (
        "an approval given before the change must not stand after it"
    )
    assert len(decisions(client, note_id)) == 3, "and no duplicate rows"

    sign_in(chain["first"])
    assert "Reviewed the legacy schema" in client.get("/approvals").text


# --- changing the chain under live entries --------------------------------------------


def test_shortening_the_chain_with_entries_in_flight_is_refused(
    client: TestClient, chain, administrator, sign_in
) -> None:
    submit_a_day(client, chain, sign_in)

    sign_in(administrator)
    response = client.post(
        f"/projects/{chain['project_id']}/edit",
        data=project_payload(administrator.id, required_approvals="1"),
    )

    assert response.status_code == 400
    assert "waiting on the current chain" in response.text

    with client.app.state.session_factory() as session:
        assert session.get(Project, chain["project_id"]).required_approvals == 3


def test_lengthening_the_chain_is_allowed_and_applies_to_the_next_submission(
    client: TestClient, chain, administrator, make_person, sign_in
) -> None:
    sign_in(administrator)
    fourth = make_person("fourth@example.com", "Dee Fourth")
    client.post(
        f"/projects/{chain['project_id']}/members",
        data={"user_id": str(fourth.id), "is_approver": "true", "approval_order": "4"},
    )
    response = client.post(
        f"/projects/{chain['project_id']}/edit",
        data=project_payload(administrator.id, required_approvals="4"),
    )
    assert response.status_code == 200

    note_id = submit_a_day(client, chain, sign_in)
    assert len(decisions(client, note_id)) == 4


def test_an_approver_awaiting_entries_cannot_be_removed(
    client: TestClient, chain, administrator, sign_in
) -> None:
    submit_a_day(client, chain, sign_in)

    sign_in(administrator)
    with client.app.state.session_factory() as session:
        membership_id = session.scalar(
            select(ProjectMember.id).where(
                ProjectMember.project_id == chain["project_id"],
                ProjectMember.user_id == chain["first"].id,
            )
        )

    response = client.post(f"/projects/{chain['project_id']}/members/{membership_id}/remove")
    assert "waiting on this approver" in response.text

    with client.app.state.session_factory() as session:
        assert session.get(ProjectMember, membership_id) is not None


def test_an_out_of_range_approval_count_is_refused(
    client: TestClient, chain, administrator, sign_in
) -> None:
    sign_in(administrator)
    for invalid in ("0", "6"):
        response = client.post(
            f"/projects/{chain['project_id']}/edit",
            data=project_payload(administrator.id, required_approvals=invalid),
        )
        assert response.status_code == 400
        assert "between 1 and 5" in response.text


# --- the chain is visible -------------------------------------------------------------


def test_the_queue_shows_the_chain_and_where_it_has_reached(
    client: TestClient, chain, sign_in
) -> None:
    note_id = submit_a_day(client, chain, sign_in)

    sign_in(chain["first"])
    queue = client.get("/approvals").text
    assert "Approval chain" in queue
    assert "waiting on them now" in queue
    assert "not yet their turn" in queue

    client.post(f"/approvals/{note_id}/approve")
    sign_in(chain["second"])
    assert "approved" in client.get("/approvals").text
