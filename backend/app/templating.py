"""Jinja environment and the one helper every page renders through.

`render` exists so no route has to remember the three things every page needs: the signed-
in user, the navigation, and the flash messages — which must also be consumed, or they
reappear on the next page.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from backend.app.config import REPO_ROOT
from backend.app.flash import clear as clear_flash
from backend.app.flash import take_messages

TEMPLATES_DIR = REPO_ROOT / "frontend" / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)

NAV = [
    ("Overview", "/"),
    ("Projects", "/projects"),
    ("My time", "/me"),
    ("Approvals", "/approvals"),
    ("People", "/users"),
    ("Account", "/account"),
]


def nav_items(path: str) -> list[dict[str, Any]]:
    return [
        {
            "label": label,
            "href": href,
            # "/" redirects to /dashboard for anyone who may see it, so the Overview item
            # has to light up on both. Pointing the nav straight at /dashboard would offer
            # a link that 404s for everybody else.
            "active": path == href
            or (href == "/" and path == "/dashboard")
            or (href != "/" and path.startswith(href)),
        }
        for label, href in NAV
    ]


def _pending_badge(request: Request, user: Any) -> int:
    """How many entries are waiting on this person, for the nav badge.

    One query per rendered page. At thirty users that is cheaper than any cache would be
    to keep honest, and a badge that lags is worse than no badge.
    """
    if user is None:
        return 0

    from backend.app.notifications import pending_count

    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        return 0
    with factory() as session:
        return pending_count(session, session.merge(user, load=False))


def render(
    request: Request,
    template: str,
    *,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    settings = request.app.state.settings
    messages = take_messages(request, settings)

    response = templates.TemplateResponse(
        request,
        template,
        {
            "nav_items": nav_items(request.url.path),
            "messages": messages,
            "pending_badge": _pending_badge(request, context.get("user")),
            **context,
        },
        status_code=status_code,
    )
    if messages:
        clear_flash(response)
    return response
