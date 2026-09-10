"""The demo seed builds a database a person could actually have produced.

The point of this dataset is that it went through the real transitions, so the assertions
here are about workflow consequences — chains of the right length, rejections carrying a
reason, nothing logged outside its project's window — rather than about row counts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import select

from backend.app.config import Settings
from backend.app.db import init_database
from backend.app.demo import (
    DEMO_PASSWORD,
    OLDEST_PENDING_DAYS,
    PEOPLE,
    DemoDataError,
    build_demo,
)
from backend.app.metrics import oldest_pending_approval_days
from backend.app.models import (
    Approval,
    ApprovalDecision,
    AuditEvent,
    Project,
    ProjectStatus,
    TimeNote,
    TimeNoteState,
    User,
    as_utc,
)
from backend.app.security import verify_password
from backend.app.workflow import latest_rejection

# Pinned, so the generated history does not drift with the calendar.
AS_OF = date(2026, 9, 10)


@pytest.fixture
def demo(settings: Settings):
    _engine, factory = init_database(settings)
    with factory() as session:
        build_demo(session, settings, as_of=AS_OF)
        session.commit()
        yield session


def test_demo_seed_populates_a_database_worth_looking_at(demo, settings: Settings):
    people = demo.scalars(select(User)).all()
    # Everyone in the cast, plus the bootstrap administrator seed() creates.
    assert len(people) == len(PEOPLE) + 1

    consultant = demo.scalar(select(User).where(User.email == "alex@example.com"))
    assert verify_password(DEMO_PASSWORD, consultant.password_hash)

    projects = demo.scalars(select(Project)).all()
    assert {project.code for project in projects} == {"ATLAS", "HELIX", "NOVA"}

    states = dict.fromkeys(TimeNoteState, 0)
    for note in demo.scalars(select(TimeNote)).all():
        states[note.state] += 1

    # An all-approved demo shows an empty approval queue, which is the screen people most
    # want to see. Work in every live state is the whole reason this exists.
    assert states[TimeNoteState.APPROVED] > 0
    assert states[TimeNoteState.SUBMITTED] > 0
    assert states[TimeNoteState.DRAFT] > 0


def test_no_note_falls_outside_its_project_window(demo):
    for project in demo.scalars(select(Project)).all():
        for note in project.time_notes:
            assert note.work_date >= project.start_date
            if project.end_date is not None:
                assert note.work_date <= project.end_date
            assert note.work_date.weekday() < 5
            assert 0 < note.duration_minutes <= 1440


def test_the_two_approver_project_builds_a_sequenced_chain(demo):
    atlas = demo.scalar(select(Project).where(Project.code == "ATLAS"))
    assert atlas.required_approvals == 2

    approved = [note for note in atlas.time_notes if note.state is TimeNoteState.APPROVED]
    assert approved, "ATLAS should have settled work in it"

    for note in approved:
        chain = sorted(note.approvals, key=lambda row: row.sequence)
        assert [row.sequence for row in chain] == [1, 2]
        assert all(row.decision is ApprovalDecision.APPROVED for row in chain)
        # Both signatures come from the named approvers, in the order the project sets.
        assert [row.approver.email for row in chain] == ["daniel@example.com", "mei@example.com"]


def test_a_submitted_note_waits_on_its_first_approver(demo):
    atlas = demo.scalar(select(Project).where(Project.code == "ATLAS"))
    pending = [note for note in atlas.time_notes if note.state is TimeNoteState.SUBMITTED]
    assert pending, "the approval queue should not be empty"

    for note in pending:
        open_rows = [row for row in note.approvals if row.decision is ApprovalDecision.PENDING]
        assert open_rows
        assert min(row.sequence for row in open_rows) == 1


def test_rejected_work_goes_back_to_draft_carrying_the_reason(demo):
    rejected = [
        note
        for note in demo.scalars(select(TimeNote)).all()
        if any(row.decision is ApprovalDecision.REJECTED for row in note.approvals)
    ]
    assert rejected, "a demo without a rejection never shows the reason on screen"

    for note in rejected:
        # reject() sends the day back to its author; it does not park it in a dead state.
        assert note.state is TimeNoteState.DRAFT
        assert note.submitted_at is None
        assert latest_rejection(note).comment.strip()


def test_the_completed_project_is_fully_settled(demo):
    nova = demo.scalar(select(Project).where(Project.code == "NOVA"))
    assert nova.status is ProjectStatus.COMPLETED
    assert nova.time_notes
    assert all(note.state is TimeNoteState.APPROVED for note in nova.time_notes)


def test_every_transition_left_an_audit_event(demo):
    moved = [
        note
        for note in demo.scalars(select(TimeNote)).all()
        if note.state is not TimeNoteState.DRAFT
    ]
    assert moved
    for note in moved:
        events = demo.scalars(select(AuditEvent).where(AuditEvent.time_note_id == note.id)).all()
        assert events, f"note {note.id} changed state with no trail"


def test_it_refuses_to_overwrite_an_existing_database(demo, settings: Settings):
    before = demo.scalar(select(Approval).limit(1))

    with pytest.raises(DemoDataError, match="already has projects"):
        build_demo(demo, settings, as_of=AS_OF)

    demo.rollback()
    assert demo.scalar(select(Approval).limit(1)) is not None
    assert before is not None


def test_history_is_dated_in_the_past_not_at_seed_time(demo):
    """submit() stamps utcnow(); a demo that keeps that has no approval age to report."""
    submitted = [
        note for note in demo.scalars(select(TimeNote)).all() if note.submitted_at is not None
    ]
    assert submitted

    for note in submitted:
        sent = as_utc(note.submitted_at).date()
        # Sent after the work happened, and never later than the day the seed ran for.
        assert note.work_date <= sent <= AS_OF

    for note in submitted:
        for approval in note.approvals:
            if approval.decided_at is not None:
                assert as_utc(approval.decided_at) >= as_utc(note.submitted_at)


def test_the_pending_queue_stays_inside_the_amber_window(demo, settings: Settings):
    """Bounded on purpose: an unbounded backlog paints every project red."""
    for project in demo.scalars(select(Project)).all():
        waiting = oldest_pending_approval_days(
            demo, project.id, now=datetime.combine(AS_OF, time(23, 59), tzinfo=UTC)
        )
        assert waiting <= OLDEST_PENDING_DAYS
        assert waiting < settings.health_red_approval_days


def test_the_running_projects_have_a_deadline_to_show(demo):
    """The dashboard's upcoming-deadlines panel needs an open project with an end date."""
    running = [
        project
        for project in demo.scalars(select(Project)).all()
        if project.status is ProjectStatus.ACTIVE
    ]
    assert running
    for project in running:
        assert project.end_date is not None
        assert project.end_date > AS_OF
