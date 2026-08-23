"""The one scheduled job this product has.

In-process, per `docs/ARCHITECTURE.md` §7: no Redis, no Celery, no broker for a single
daily email at thirty users. The loop wakes every few minutes, and the day-stamp on each
recipient is what makes that safe — the work is idempotent, so an imprecise clock and a
mid-morning restart both cost nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.notifications import send_daily_digests

logger = logging.getLogger("ts.scheduler")

TICK_SECONDS = 300


async def run_digest_loop(
    factory: sessionmaker, settings: Settings, tick_seconds: int = TICK_SECONDS
) -> None:
    while True:
        try:
            if datetime.now().hour >= settings.digest_hour:
                await asyncio.to_thread(_send, factory, settings)
        except Exception:
            logger.exception("the digest run failed; trying again next tick")
        await asyncio.sleep(tick_seconds)


def _send(factory: sessionmaker, settings: Settings) -> None:
    with factory() as session:
        sent = send_daily_digests(session, settings)
    if sent:
        logger.info("sent %s digest(s)", sent)


def start(app, factory: sessionmaker, settings: Settings) -> asyncio.Task | None:
    """Started only when email is configured: an unused loop is a thing to explain later."""
    if not settings.email_enabled:
        logger.info("email is not configured; the daily digest is off")
        return None
    return asyncio.create_task(run_digest_loop(factory, settings))


async def stop(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
