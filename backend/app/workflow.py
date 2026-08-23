"""The time-note state machine.

```
draft ──submit──► submitted ──all required approvals approved──► approved
  ▲                    │
  └────reject──────────┘
```

A project declares how many approvals it needs (1 to 5) and orders its approvers. Submitting
opens one row per approver, in that order, and the chain is **sequential**: approver *n+1*
sees an entry only once *n* has approved. A rejection anywhere sends the whole day back to
its author and clears the rest — there is nothing to keep deciding once the work is going
to change.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.access import can_decide_anywhere
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
    """The chain, in order, capped at what the project asks for.

    Falls back to the owner: a project with no approver named would otherwise strand every
    submission in a queue nobody can see. If fewer approvers are named than the project
    requires, the chain is as long as the named list — demanding a third signature from
    nobody would freeze the entry forever.
    """
    memberships = session.scalars(
        select(ProjectMember)
        .where(ProjectMember.project_id == project.id, ProjectMember.is_approver.is_(True))
        .order_by(ProjectMember.approval_order, ProjectMember.id)
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


def current_approval(note: TimeNote) -> Approval | None:
    """The one row the chain is waiting on. Later rows are not yet anybody's business."""
    for approval in sorted(note.approvals, key=lambda row: row.sequence):
        if approval.decision is ApprovalDecision.PENDING:
            return approval
    return None


def can_decide(session: Session, user: User, note: TimeNote) -> bool:
    """You may decide only when the chain has reached you.

    `approval.decide_any` reaches every entry, but still only the step that is actually
    open — it is a wider queue, not a way to skip ahead of approver 1.
    """
    open_step = current_approval(note)
    if open_step is None:
        return False
    return can_decide_anywhere(user) or open_step.approver_id == user.id


def chain_of(note: TimeNote) -> list[Approval]:
    return sorted(note.approvals, key=lambda row: row.sequence)


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

    # Nothing further is worth deciding: the day is going back to be changed. Later steps
    # are cleared so a resubmission starts the chain from the top.
    for later in note.approvals:
        if later.sequence > approval.sequence:
            later.decision = ApprovalDecision.PENDING
            later.decided_at = None
            later.comment = ""

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
    if not can_decide_anywhere(user):
        query = query.where(Approval.approver_id == user.id)

    notes = session.scalars(query.order_by(TimeNote.work_date, TimeNote.id).distinct()).all()

    # SQL cannot express "and it is your turn": the open step is the first pending row, so
    # filter in Python rather than leaving approver 2 looking at approver 1's work.
    return [note for note in notes if can_decide(session, user, note)]


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

    open_step = current_approval(note)
    if open_step is None:
        raise WorkflowError("Every approval on this entry has already been decided.")

    if open_step.approver_id == user.id or can_decide_anywhere(user):
        return open_step

    raise WorkflowError("This entry is waiting on an earlier approver.")


def _every_approval_settled(note: TimeNote) -> bool:
    return bool(note.approvals) and all(
        approval.decision is ApprovalDecision.APPROVED for approval in note.approvals
    )
