"""Ages are measured against one clock.

`submitted_at` is stored in UTC. Every age derived from it — the queue's ageing marker, the
digest wording, the approval age that moves project health — must be measured against UTC
too. Subtracting a UTC timestamp from a *local* date reads a day older than reality for
most of the day in most timezones, which is #47.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.app.metrics import oldest_pending_approval_days
from backend.app.models import (
    Approval,
    ApprovalDecision,
    Project,
    Task,
    TimeNote,
    TimeNoteState,
    User,
    as_utc,
    utcnow,
)
from backend.app.notifications import build_digest
from backend.app.queue import age_in_days
from backend.app.security import hash_password
from backend.app.seed import ensure_member_role

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def note_submitted(moment: datetime | None) -> TimeNote:
    return TimeNote(
        project_id=1,
        task_id=1,
        user_id=1,
        work_date=datetime.now(UTC).date(),
        duration_minutes=60,
        state=TimeNoteState.SUBMITTED,
        submitted_at=moment,
    )


# --- the failing case, pinned ---------------------------------------------------------


def test_an_entry_submitted_moments_ago_has_waited_no_days() -> None:
    assert age_in_days(note_submitted(utcnow())) == 0


def test_a_utc_timestamp_whose_local_date_is_a_day_ahead_still_reads_zero() -> None:
    """The exact shape of #47: same instant, different calendar days.

    23:30 UTC is already tomorrow anywhere east of UTC+1. Comparing calendar dates would
    call this a day old; comparing instants knows it is half an hour.
    """
    late_utc = datetime.now(UTC).replace(hour=23, minute=30)
    half_an_hour_later = late_utc + timedelta(minutes=30)

    assert age_in_days(note_submitted(late_utc), now=half_an_hour_later) == 0


def test_a_naive_stored_timestamp_is_read_back_as_utc() -> None:
    """SQLite drops the zone; attaching it at the boundary is what keeps the maths honest."""
    naive = utcnow().replace(tzinfo=None)

    assert as_utc(naive).tzinfo is UTC
    assert age_in_days(note_submitted(naive)) == 0, "and it must not raise, either"


@pytest.mark.parametrize("days", [1, 7, 14, 30])
def test_age_counts_elapsed_days_exactly(days: int) -> None:
    assert age_in_days(note_submitted(utcnow() - timedelta(days=days))) == days


def test_an_unsubmitted_entry_has_no_age() -> None:
    assert age_in_days(note_submitted(None)) == 0


# --- one age, everywhere --------------------------------------------------------------


@pytest.fixture
def waiting_entry(client, administrator, settings):
    """One submitted entry, waiting exactly nine days."""
    with client.app.state.session_factory() as session:
        person = User(
            email="waiting@example.com",
            full_name="Wanda Waiting",
            password_hash=hash_password("a-properly-long-password"),
            role_id=ensure_member_role(session).id,
        )
        session.add(person)
        session.flush()

        project = Project(
            name="Ledger",
            code="LEDGER",
            owner_id=administrator.id,
            start_date=datetime.now(UTC).date() - timedelta(days=30),
            end_date=datetime.now(UTC).date() + timedelta(days=30),
        )
        session.add(project)
        session.flush()
        task = Task(project_id=project.id, name="Delivery")
        session.add(task)
        session.flush()

        note = TimeNote(
            project_id=project.id,
            task_id=task.id,
            user_id=person.id,
            work_date=datetime.now(UTC).date() - timedelta(days=9),
            duration_minutes=480,
            state=TimeNoteState.SUBMITTED,
            submitted_at=utcnow() - timedelta(days=9),
        )
        session.add(note)
        session.flush()
        session.add(
            Approval(
                time_note_id=note.id,
                approver_id=administrator.id,
                sequence=1,
                decision=ApprovalDecision.PENDING,
            )
        )
        session.commit()
        return project.id


def test_queue_digest_and_health_all_read_the_same_age(
    client, waiting_entry, administrator, settings
) -> None:
    with client.app.state.session_factory() as session:
        note = session.scalar(select(TimeNote))
        approver = session.get(User, administrator.id)

        from_queue = age_in_days(note)
        from_metrics = oldest_pending_approval_days(session, waiting_entry)
        from_digest = build_digest(session, approver).oldest_days

    assert from_queue == 9
    assert from_metrics == 9
    assert from_digest == 9, "three readings of one entry must not disagree"


# --- and it holds wherever the server is ----------------------------------------------


@pytest.mark.parametrize("timezone_name", ["UTC", "Pacific/Kiritimati", "Pacific/Midway"])
def test_the_suite_agrees_in_any_local_timezone(timezone_name: str) -> None:
    """UTC+14 and UTC-11: the two extremes where local and UTC dates differ most.

    Run in a subprocess because the C library caches the zone on first use.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_clock.py", "-k", "not any_local", "-q"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "TZ": timezone_name},
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
