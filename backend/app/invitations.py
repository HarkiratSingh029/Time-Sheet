"""Invitation links.

A link is signed rather than stored, and it is **single-use without a table**: the token
carries a fingerprint of the password hash it was issued against, so the moment somebody
sets a password the link stops validating. A used link and a stale link fail the same way,
which is the behaviour we wanted anyway.

Expiry is `itsdangerous`'s own timestamp, so an abandoned invitation dies on its own.
"""

from __future__ import annotations

import hashlib
from typing import NamedTuple

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from backend.app.config import Settings
from backend.app.models import User

INVITATION_SALT = "ts-timesheets-invitation"
INVITATION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


class InvitationError(ValueError):
    """The link is expired, already used, or not one we issued."""


class Invitation(NamedTuple):
    user_id: int
    fingerprint: str


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=INVITATION_SALT)


def fingerprint(password_hash: str) -> str:
    """Short digest of the hash the token was issued against. Changing it kills the link."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def issue(user: User, settings: Settings) -> str:
    return _serializer(settings).dumps({"uid": user.id, "fp": fingerprint(user.password_hash)})


def read(token: str, settings: Settings) -> Invitation:
    try:
        payload = _serializer(settings).loads(token, max_age=INVITATION_MAX_AGE_SECONDS)
    except SignatureExpired as error:
        raise InvitationError(
            "This invitation has expired. Ask an administrator to send a new one."
        ) from error
    except BadSignature as error:
        raise InvitationError("This invitation link is not valid.") from error

    if not isinstance(payload, dict) or "uid" not in payload or "fp" not in payload:
        raise InvitationError("This invitation link is not valid.")
    return Invitation(user_id=int(payload["uid"]), fingerprint=str(payload["fp"]))


def check_unused(invitation: Invitation, user: User) -> None:
    if invitation.fingerprint != fingerprint(user.password_hash):
        raise InvitationError(
            "This invitation has already been used. Sign in, or ask for a new one."
        )
