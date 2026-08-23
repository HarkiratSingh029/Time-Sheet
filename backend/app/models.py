"""The objects TS Timesheets is made of.

Mirrors `docs/DATA_MODEL.md`. Rules that must hold regardless of which route writes the
row live here as database constraints, not as validation in a handler — a check that only
exists in one code path is a check that a future code path will forget.
"""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MAX_APPROVALS_PER_PROJECT = 5


def enum_column(enum_type: type[enum.Enum], length: int = 16) -> Enum:
    """Store the enum's value, not its member name.

    SQLAlchemy defaults to persisting `REJECTED`; we persist `rejected`, so the CHECK
    constraints below and anyone reading the database by hand see the same strings the
    application uses.
    """
    return Enum(
        enum_type,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
    )


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProjectStatus(enum.StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TimeNoteState(enum.StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalDecision(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(TimestampMixin, Base):
    """One thing a role may do.

    The vocabulary is closed and lives in `backend/app/permissions.py`; rows exist so a
    role can point at them, not so anyone can invent a verb the code never checks.
    """

    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")

    roles: Mapped[list[Role]] = relationship(
        secondary=role_permissions, back_populates="permissions"
    )


class Role(TimestampMixin, Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")

    # The super-role. Kept as a column so it can be protected and seeded; authorization
    # itself reads permissions and never this flag (see backend/app/access.py).
    is_administrator: Mapped[bool] = mapped_column(Boolean, default=False)

    # A system role is created by the seed and cannot be deleted or renamed.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    users: Mapped[list[User]] = relationship(back_populates="role")
    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, back_populates="roles", lazy="selectin"
    )

    def holds(self, code: str) -> bool:
        return any(permission.code == code for permission in self.permissions)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))

    # What this consultant can be staffed on, and since when (docs/DATA_MODEL.md §1).
    skills: Mapped[str] = mapped_column(Text, default="")
    resume_summary: Mapped[str] = mapped_column(Text, default="")
    tenure_started_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Opting out silences email only; in-app badges are part of the interface, not a
    # subscription.
    digest_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    # The day the last digest went out, so a restart cannot send a second one.
    digest_sent_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_signed_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    role: Mapped[Role] = relationship(back_populates="users")
    memberships: Mapped[list[ProjectMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    time_notes: Mapped[list[TimeNote]] = relationship(back_populates="user")

    @property
    def is_administrator(self) -> bool:
        return self.role.is_administrator

    def holds(self, code: str) -> bool:
        return self.role.holds(code)

    @property
    def status(self) -> str:
        """What an administrator needs to see at a glance in the people list."""
        if not self.is_active:
            return "deactivated"
        if self.last_signed_in_at is None:
            return "invited"
        return "active"

    @property
    def has_accepted_invitation(self) -> bool:
        return self.last_signed_in_at is not None


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            f"required_approvals BETWEEN 1 AND {MAX_APPROVALS_PER_PROJECT}",
            name="ck_project_required_approvals_range",
        ),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_project_dates"),
        CheckConstraint("cost IS NULL OR cost >= 0", name="ck_project_cost_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    comment: Mapped[str] = mapped_column(Text, default="")

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    status: Mapped[ProjectStatus] = mapped_column(
        enum_column(ProjectStatus), default=ProjectStatus.PLANNED
    )

    cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    billable_hours_budget: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposed_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    required_approvals: Mapped[int] = mapped_column(Integer, default=1)

    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    manager: Mapped[User | None] = relationship(foreign_keys=[manager_id])
    members: Mapped[list[ProjectMember]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="project", cascade="all, delete-orphan")
    time_notes: Mapped[list[TimeNote]] = relationship(back_populates="project")
    files: Mapped[list[ProjectFile]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(TimestampMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_member"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Whether this member signs off time notes on this project.
    is_approver: Mapped[bool] = mapped_column(Boolean, default=False)

    # Position in the approval chain. Approver 2 sees an entry only once 1 has approved,
    # so the order is part of the rule, not a display preference.
    approval_order: Mapped[int] = mapped_column(Integer, default=1)

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    is_billable: Mapped[bool] = mapped_column(Boolean, default=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)

    project: Mapped[Project] = relationship(back_populates="tasks")
    time_notes: Mapped[list[TimeNote]] = relationship(back_populates="task")


class TimeNote(TimestampMixin, Base):
    """One consultant's entry for one day on one project. Duration is stored in minutes."""

    __tablename__ = "time_notes"
    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="ck_time_note_duration_positive"),
        CheckConstraint("duration_minutes <= 1440", name="ck_time_note_duration_within_a_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    work_date: Mapped[date] = mapped_column(Date, index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer)
    detail: Mapped[str] = mapped_column(Text, default="")

    state: Mapped[TimeNoteState] = mapped_column(
        enum_column(TimeNoteState),
        default=TimeNoteState.DRAFT,
        index=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="time_notes")
    task: Mapped[Task] = relationship(back_populates="time_notes")
    user: Mapped[User] = relationship(back_populates="time_notes")
    approvals: Mapped[list[Approval]] = relationship(
        back_populates="time_note", cascade="all, delete-orphan", order_by="Approval.sequence"
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="time_note",
        cascade="all, delete-orphan",
        order_by="AuditEvent.id",
    )


class Approval(TimestampMixin, Base):
    """One approver's decision on one time note."""

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("time_note_id", "sequence", name="uq_approval_sequence"),
        CheckConstraint(
            f"sequence BETWEEN 1 AND {MAX_APPROVALS_PER_PROJECT}",
            name="ck_approval_sequence_range",
        ),
        CheckConstraint(
            "decision <> 'rejected' OR length(trim(comment)) > 0",
            name="ck_approval_rejection_has_comment",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    time_note_id: Mapped[int] = mapped_column(
        ForeignKey("time_notes.id", ondelete="CASCADE"), index=True
    )
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)

    decision: Mapped[ApprovalDecision] = mapped_column(
        enum_column(ApprovalDecision), default=ApprovalDecision.PENDING
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")

    time_note: Mapped[TimeNote] = relationship(back_populates="approvals")
    approver: Mapped[User] = relationship()


class AuditAction(enum.StrEnum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    REOPENED = "reopened"


class AuditEvent(Base):
    """One recorded transition on a time note.

    Append-only, and deliberately not a `TimestampMixin`: an audit row has no `updated_at`
    because it is never updated. What happened, who did it, and when — corrections are new
    rows, never edits to old ones.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    time_note_id: Mapped[int] = mapped_column(
        ForeignKey("time_notes.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    action: Mapped[AuditAction] = mapped_column(enum_column(AuditAction))
    from_state: Mapped[str] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text, default="")

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    time_note: Mapped[TimeNote] = relationship(back_populates="audit_events")
    actor: Mapped[User] = relationship()


class ProjectFile(TimestampMixin, Base):
    """Metadata for a file on the local volume under data/uploads/<project_id>/."""

    __tablename__ = "project_files"
    __table_args__ = (CheckConstraint("size_bytes >= 0", name="ck_project_file_size"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="files")
    uploaded_by: Mapped[User] = relationship()
