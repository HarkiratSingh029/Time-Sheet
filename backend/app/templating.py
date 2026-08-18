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
]


def nav_items(path: str) -> list[dict[str, Any]]:
    return [
        {
            "label": label,
            "href": href,
            "active": path == href or (href != "/" and path.startswith(href)),
        }
        for label, href in NAV
    ]


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
            **context,
        },
        status_code=status_code,
    )
    if messages:
        clear_flash(response)
    return response
