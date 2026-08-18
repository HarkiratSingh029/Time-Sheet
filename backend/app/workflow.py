"""The time-note state machine.

```
draft ──submit──► submitted ──all required approvals approved──► approved
  ▲                    │
  └────reject──────────┘
```

EPIC 0 runs one approver per project. The chain of up to five arrives in EPIC 1, so the
shape here is deliberately the general one — `Approval.sequence` exists, and
`is_settled` counts approvals rather than assuming a single row. Generalising later is
then a change to who gets rows, not a rewrite of what a row means.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    ProjectMember,
    TimeNote,
    TimeNoteState,
    User,
    utcnow,
)


class WorkflowError(ValueError):
    """A transition that is not allowed from the note's current state."""


def approvers_for(session: Session, project: Project) -> list[User]:
    """The people who sign off time on this project, in a stable order.

    Falls back to the owner: a project with no approver named would otherwise strand every
    submission in a queue nobody can see.
    """
    memberships = session.scalars(
        select(ProjectMember)
        .where(ProjectMember.project_id == project.id, ProjectMember.is_approver.is_(True))
        .order_by(ProjectMember.id)
    ).all()

    approvers = [membership.user for membership in memberships]
    if approvers:
        return approvers[: project.required_approvals]
    return [project.owner]


def submit(session: Session, note: TimeNote) -> None:
    """Move a draft to submitted and open its approval rows."""
    if note.state is not TimeNoteState.DRAFT:
        raise WorkflowError("Only a draft can be submitted.")

    note.state = TimeNoteState.SUBMITTED
    note.submitted_at = utcnow()

    # A rejected-then-resubmitted note already has rows; they are reopened, not duplicated.
    existing = {approval.sequence: approval for approval in note.approvals}
    for sequence, approver in enumerate(approvers_for(session, note.project), start=1):
        approval = existing.get(sequence)
        if approval is None:
            session.add(
                Approval(
                    time_note_id=note.id,
                    approver_id=approver.id,
                    sequence=sequence,
                    decision=ApprovalDecision.PENDING,
                )
            )
        else:
            approval.approver_id = approver.id
            approval.decision = ApprovalDecision.PENDING
            approval.decided_at = None
            approval.comment = ""


def can_decide(session: Session, user: User, note: TimeNote) -> bool:
    """An administrator may decide anywhere; otherwise you must hold a row on this note."""
    if user.role.is_administrator:
        return True
    return any(approval.approver_id == user.id for approval in note.approvals)


def approve(session: Session, user: User, note: TimeNote) -> None:
    approval = _open_approval_for(session, user, note)
    approval.decision = ApprovalDecision.APPROVED
    approval.decided_at = utcnow()
    approval.comment = ""

    if _every_approval_settled(note):
        note.state = TimeNoteState.APPROVED


def reject(session: Session, user: User, note: TimeNote, comment: str) -> None:
    """Send the day back to its author, with the reason attached.

    A rejection without a comment is refused here as well as in the database: the author
    is being asked to redo work, and "no" without a reason is not an answer.
    """
    if not comment.strip():
        raise WorkflowError("A rejection needs a comment explaining what to change.")

    approval = _open_approval_for(session, user, note)
    approval.decision = ApprovalDecision.REJECTED
    approval.decided_at = utcnow()
    approval.comment = comment.strip()

    note.state = TimeNoteState.DRAFT
    note.submitted_at = None


def pending_notes_for(session: Session, user: User) -> list[TimeNote]:
    """The approver's queue: submitted notes awaiting a decision from this person."""
    query = (
        select(TimeNote)
        .join(Approval, Approval.time_note_id == TimeNote.id)
        .where(
            TimeNote.state == TimeNoteState.SUBMITTED,
            Approval.decision == ApprovalDecision.PENDING,
        )
    )
    if not user.role.is_administrator:
        query = query.where(Approval.approver_id == user.id)

    return list(session.scalars(query.order_by(TimeNote.work_date, TimeNote.id).distinct()).all())


def latest_rejection(note: TimeNote) -> Approval | None:
    rejections = [
        approval
        for approval in note.approvals
        if approval.decision is ApprovalDecision.REJECTED and approval.comment
    ]
    return max(rejections, key=lambda approval: approval.decided_at or utcnow(), default=None)


def _open_approval_for(session: Session, user: User, note: TimeNote) -> Approval:
    if note.state is not TimeNoteState.SUBMITTED:
        raise WorkflowError("Only a submitted entry can be decided.")

    for approval in sorted(note.approvals, key=lambda row: row.sequence):
        if approval.decision is not ApprovalDecision.PENDING:
            continue
        if approval.approver_id == user.id or user.role.is_administrator:
            return approval

    raise WorkflowError("You are not an approver on this entry.")


def _every_approval_settled(note: TimeNote) -> bool:
    return bool(note.approvals) and all(
        approval.decision is ApprovalDecision.APPROVED for approval in note.approvals
    )
