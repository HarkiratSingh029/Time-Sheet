"""Flash messages that survive a redirect.

Carried in their own signed, short-lived cookie and cleared as soon as they are read. The
alternative — a server-side queue — would mean a table and a cleanup job for something
that lives for exactly one redirect.
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from backend.app.config import Settings

FLASH_COOKIE = "ts_flash"
FLASH_SALT = "ts-timesheets-flash"

Level = Literal["success", "error", "warning", "info"]


def _serializer(settings: Settings) -> URLSafeSerializer:
    return URLSafeSerializer(settings.secret_key, salt=FLASH_SALT)


def flash(response: Response, settings: Settings, text: str, level: Level = "success") -> None:
    payload = _serializer(settings).dumps([{"text": text, "level": level}])
    response.set_cookie(
        FLASH_COOKIE,
        payload,
        max_age=60,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )


def take_messages(request: Request, settings: Settings) -> list[dict[str, str]]:
    """Read and consume. The caller must clear the cookie on the response it returns."""
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return []
    try:
        messages = _serializer(settings).loads(raw)
    except (BadSignature, json.JSONDecodeError):
        return []
    return messages if isinstance(messages, list) else []


def clear(response: Response) -> None:
    response.delete_cookie(FLASH_COOKIE, path="/")
