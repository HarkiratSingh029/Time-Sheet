"""Telling people there is work waiting.

Two halves, deliberately independent:

* **In-app badges** are part of the interface. They always work, need no configuration,
  and cannot be opted out of.
* **A daily digest** is email, and email is off unless `TS_SMTP_HOST` is set. On a laptop
  nothing is sent, nothing is queued, and nothing is logged as an error — an unconfigured
  optional feature is not a failure.

One digest per approver per day, not one email per event. The point is to stop approvals
ageing, and a mailbox nobody reads does not do that.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.models import User
from backend.app.queue import AGEING_DAYS, Filters, age_in_days, pending_count, pending_for

logger = logging.getLogger("ts.notifications")


@dataclass
class Digest:
    recipient: User
    total: int
    oldest_days: int
    lines: list[str]

    @property
    def subject(self) -> str:
        entries = "entry" if self.total == 1 else "entries"
        if self.oldest_days >= AGEING_DAYS:
            return f"{self.total} timesheet {entries} waiting — oldest {self.oldest_days} days"
        return f"{self.total} timesheet {entries} waiting for you"

    @property
    def body(self) -> str:
        return "\n".join(
            [
                f"Hello {self.recipient.full_name or self.recipient.email},",
                "",
                f"{self.total} timesheet {'entry' if self.total == 1 else 'entries'} "
                "are waiting on your approval:",
                "",
                *self.lines,
                "",
                "Approve or send them back in TS Timesheets.",
                "",
                "You can turn this daily summary off from your account page.",
            ]
        )


__all__ = ["Digest", "build_digest", "pending_count", "send_daily_digests", "send_email"]


def build_digest(session: Session, user: User, today: date | None = None) -> Digest | None:
    """Only what is waiting on them personally — see `awaiting_this_person(strict=True)`."""
    notes = pending_for(session, user, Filters(), strict=True)
    if not notes:
        return None

    by_person: dict[str, list] = {}
    for note in notes:
        by_person.setdefault(note.user.full_name or note.user.email, []).append(note)

    lines = []
    for person, theirs in sorted(by_person.items()):
        hours = sum(note.duration_minutes for note in theirs) / 60
        oldest = max(age_in_days(note, today) for note in theirs)
        entries = "entry" if len(theirs) == 1 else "entries"
        waited = "today" if oldest == 0 else f"waiting up to {oldest} days"
        lines.append(f"  - {person}: {len(theirs)} {entries}, {hours:g}h, {waited}")

    return Digest(
        recipient=user,
        total=len(notes),
        oldest_days=max(age_in_days(note, today) for note in notes),
        lines=lines,
    )


def send_email(settings: Settings, to: str, subject: str, body: str) -> bool:
    """Returns whether it was sent. Never raises: a mail server is not worth a 500."""
    if not settings.email_enabled:
        return False

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        # Logged and left for the next cycle: today's digest is not worth a retry storm.
        logger.warning("digest to %s could not be sent: %s", to, error)
        return False
    return True


def send_daily_digests(session: Session, settings: Settings, today: date | None = None) -> int:
    """Send one digest to each approver who has entries waiting. Returns how many went out.

    `digest_sent_on` is what makes a restart safe: the day is recorded per recipient, so
    starting the app three times before lunch still sends one email.
    """
    if not settings.email_enabled:
        return 0

    today = today or date.today()
    sent = 0

    for user in session.scalars(select(User).where(User.is_active.is_(True))).all():
        if user.digest_opt_out or user.digest_sent_on == today:
            continue

        digest = build_digest(session, user, today)
        if digest is None:
            continue

        if send_email(settings, user.email, digest.subject, digest.body):
            user.digest_sent_on = today
            sent += 1

    session.commit()
    return sent
