"""Badges, the daily digest, and the promise that an unconfigured mailer stays silent."""

from __future__ import annotations

import logging
import smtplib
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import notifications
from backend.app.config import Settings
from backend.app.models import Project, Task, TimeNote, User, utcnow
from backend.app.notifications import (
    build_digest,
    pending_count,
    send_daily_digests,
    send_email,
)
from backend.app.queue import AGEING_DAYS

MONDAY = date(2026, 2, 2)


@pytest.fixture
def world(client: TestClient, administrator, make_person, sign_in):
    approver = make_person("approver@example.com", "Ade Approver")
    alice = make_person("alice@example.com", "Alice Consultant")

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
    client.post(f"/projects/{project_id}/members", data={"user_id": str(alice.id)})
    client.post(
        f"/projects/{project_id}/members",
        data={"user_id": str(approver.id), "is_approver": "true", "approval_order": "1"},
    )
    client.post(f"/projects/{project_id}/tasks", data={"name": "Delivery"})
    with client.app.state.session_factory() as session:
        task_id = session.scalar(select(Task.id).where(Task.project_id == project_id))

    return {
        "project_id": project_id,
        "task_id": task_id,
        "administrator": administrator,
        "approver": approver,
        "alice": alice,
    }


def submit_days(client: TestClient, world, sign_in, count: int = 2) -> None:
    sign_in(world["alice"])
    for offset in range(count):
        day = MONDAY + timedelta(days=offset)
        client.post(
            f"/projects/{world['project_id']}/notes",
            data={
                "work_date": day.isoformat(),
                "task_id": str(world["task_id"]),
                "hours": "8",
                "detail": f"day {offset}",
                "month": "2026-02",
            },
        )
    client.post(
        f"/projects/{world['project_id']}/notes/submit",
        data={"from_date": "2026-02-01", "to_date": "2026-02-28", "month": "2026-02"},
    )


def user_in_session(client: TestClient, person) -> tuple:
    session = client.app.state.session_factory()
    return session, session.get(User, person.id)


class Mailbox:
    """Stands in for an SMTP server, and records what would have gone out."""

    def __init__(self, *, fails: bool = False) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fails = fails

    def __call__(self, settings: Settings, to: str, subject: str, body: str) -> bool:
        if not settings.email_enabled:
            raise AssertionError("send_email must not be reached when email is off")
        if self.fails:
            return False
        self.sent.append((to, subject, body))
        return True


# --- the badge ------------------------------------------------------------------------


def test_the_badge_counts_what_is_waiting_and_clears_when_the_queue_empties(
    client: TestClient, world, sign_in
) -> None:
    submit_days(client, world, sign_in, count=2)

    sign_in(world["approver"])
    page = " ".join(client.get("/").text.split())
    assert 'class="nav__badge"' in page
    assert "2 waiting on you" in page

    with client.app.state.session_factory() as session:
        note_ids = [note.id for note in session.scalars(select(TimeNote)).all()]
    client.post("/approvals/approve-group", data={"note_ids": note_ids})

    assert 'class="nav__badge"' not in client.get("/").text


def test_the_badge_is_personal(client: TestClient, world, sign_in) -> None:
    submit_days(client, world, sign_in, count=1)

    sign_in(world["alice"])
    assert 'class="nav__badge"' not in client.get("/").text, (
        "logging time does not put anything in your own queue"
    )


def test_the_badge_counts_without_loading_the_queue(client: TestClient, world, sign_in) -> None:
    submit_days(client, world, sign_in, count=3)

    session, approver = user_in_session(client, world["approver"])
    with session:
        assert pending_count(session, approver) == 3


# --- the digest -----------------------------------------------------------------------


def test_a_digest_summarises_who_is_waiting_and_for_how_long(
    client: TestClient, world, sign_in
) -> None:
    submit_days(client, world, sign_in, count=2)

    with client.app.state.session_factory() as session:
        for note in session.scalars(select(TimeNote)).all():
            note.submitted_at = utcnow() - timedelta(days=AGEING_DAYS + 1)
        session.commit()

    session, approver = user_in_session(client, world["approver"])
    with session:
        digest = build_digest(session, approver)

    assert digest is not None
    assert digest.total == 2
    assert "2 timesheet entries waiting" in digest.subject
    assert f"oldest {AGEING_DAYS + 1} days" in digest.subject
    assert "Alice Consultant: 2 entries, 16h" in digest.body
    assert "turn this daily summary off" in digest.body


def test_no_digest_when_nothing_is_waiting(client: TestClient, world) -> None:
    session, approver = user_in_session(client, world["approver"])
    with session:
        assert build_digest(session, approver) is None


def test_a_digest_lists_only_what_that_person_may_decide(
    client: TestClient, world, sign_in
) -> None:
    submit_days(client, world, sign_in, count=1)

    session, alice = user_in_session(client, world["alice"])
    with session:
        assert build_digest(session, alice) is None, "the author is not their own approver"


# --- email is off by default ----------------------------------------------------------


def test_nothing_is_sent_or_queued_when_smtp_is_not_configured(
    client: TestClient, world, sign_in, settings, caplog
) -> None:
    submit_days(client, world, sign_in, count=2)
    assert settings.email_enabled is False

    with caplog.at_level(logging.WARNING), client.app.state.session_factory() as session:
        assert send_daily_digests(session, settings) == 0

    assert caplog.records == [], "an unconfigured optional feature is not a failure"

    with client.app.state.session_factory() as session:
        approver = session.get(User, world["approver"].id)
        assert approver.digest_sent_on is None, "and nothing is marked as sent"


def test_send_email_refuses_quietly_when_email_is_off(settings) -> None:
    assert send_email(settings, "someone@example.com", "Subject", "Body") is False


def test_the_scheduler_does_not_start_without_email(client: TestClient, settings) -> None:
    from backend.app.scheduler import start

    assert start(client.app, client.app.state.session_factory, settings) is None


# --- sending --------------------------------------------------------------------------


@pytest.fixture
def mailing(settings: Settings) -> Settings:
    return settings.model_copy(update={"smtp_host": "smtp.example.com"})


def test_one_digest_goes_to_each_approver_with_work_waiting(
    client: TestClient, world, sign_in, mailing, monkeypatch
) -> None:
    submit_days(client, world, sign_in, count=2)
    mailbox = Mailbox()
    monkeypatch.setattr(notifications, "send_email", mailbox)

    with client.app.state.session_factory() as session:
        assert send_daily_digests(session, mailing) == 1

    assert len(mailbox.sent) == 1
    recipient, subject, _ = mailbox.sent[0]
    assert recipient == "approver@example.com"
    assert "2 timesheet entries" in subject


def test_a_restart_on_the_same_day_does_not_send_twice(
    client: TestClient, world, sign_in, mailing, monkeypatch
) -> None:
    submit_days(client, world, sign_in, count=1)
    mailbox = Mailbox()
    monkeypatch.setattr(notifications, "send_email", mailbox)

    with client.app.state.session_factory() as session:
        assert send_daily_digests(session, mailing) == 1
        assert send_daily_digests(session, mailing) == 0, "the day is already stamped"

    assert len(mailbox.sent) == 1

    # Tomorrow it goes again.
    with client.app.state.session_factory() as session:
        assert send_daily_digests(session, mailing, date.today() + timedelta(days=1)) == 1
    assert len(mailbox.sent) == 2


def test_opting_out_silences_email_but_not_the_badge(
    client: TestClient, world, sign_in, mailing, monkeypatch
) -> None:
    submit_days(client, world, sign_in, count=1)

    sign_in(world["approver"])
    client.post("/account/digest", data={})  # unchecked box posts nothing

    with client.app.state.session_factory() as session:
        assert session.get(User, world["approver"].id).digest_opt_out is True

    mailbox = Mailbox()
    monkeypatch.setattr(notifications, "send_email", mailbox)
    with client.app.state.session_factory() as session:
        assert send_daily_digests(session, mailing) == 0
    assert mailbox.sent == []

    assert 'class="nav__badge"' in client.get("/").text, "badges are not a subscription"


def test_opting_back_in_works(client: TestClient, world, sign_in) -> None:
    sign_in(world["approver"])
    client.post("/account/digest", data={})
    client.post("/account/digest", data={"receive_digest": "true"})

    with client.app.state.session_factory() as session:
        assert session.get(User, world["approver"].id).digest_opt_out is False


def test_a_failed_send_is_not_marked_as_sent_and_is_retried(
    client: TestClient, world, sign_in, mailing, monkeypatch
) -> None:
    submit_days(client, world, sign_in, count=1)

    failing = Mailbox(fails=True)
    monkeypatch.setattr(notifications, "send_email", failing)
    with client.app.state.session_factory() as session:
        assert send_daily_digests(session, mailing) == 0
        assert session.get(User, world["approver"].id).digest_sent_on is None

    working = Mailbox()
    monkeypatch.setattr(notifications, "send_email", working)
    with client.app.state.session_factory() as session:
        assert send_daily_digests(session, mailing) == 1, "the next cycle picks it up"


def test_an_smtp_failure_is_logged_rather_than_raised(mailing, monkeypatch, caplog) -> None:
    def explode(*_args, **_kwargs):
        raise smtplib.SMTPException("the mail server is having a day")

    monkeypatch.setattr(smtplib, "SMTP", explode)

    with caplog.at_level(logging.WARNING):
        assert send_email(mailing, "someone@example.com", "Subject", "Body") is False

    assert "could not be sent" in caplog.text


def test_a_deactivated_person_receives_nothing(
    client: TestClient, world, sign_in, mailing, monkeypatch
) -> None:
    submit_days(client, world, sign_in, count=1)

    sign_in(world["administrator"])
    client.post(f"/users/{world['approver'].id}/deactivate")

    mailbox = Mailbox()
    monkeypatch.setattr(notifications, "send_email", mailbox)
    with client.app.state.session_factory() as session:
        assert send_daily_digests(session, mailing) == 0
    assert mailbox.sent == []


def test_running_migrations_does_not_silence_the_application(client: TestClient, caplog) -> None:
    """Alembic's fileConfig disables existing loggers by default.

    Migrations run at startup, so that default would switch off ts.notifications and
    ts.scheduler in production — and the only sign a digest failed is a warning from them.
    The `client` fixture has already migrated by the time this runs.
    """
    logger = logging.getLogger("ts.notifications")

    assert logger.disabled is False
    with caplog.at_level(logging.WARNING):
        logger.warning("still audible")
    assert "still audible" in caplog.text
