"""Jinja environment.

One shared instance so every route renders through the same configuration.
"""

from __future__ import annotations

from fastapi.templating import Jinja2Templates

from backend.app.config import REPO_ROOT

TEMPLATES_DIR = REPO_ROOT / "frontend" / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
