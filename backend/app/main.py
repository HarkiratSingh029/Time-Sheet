"""TS Timesheets — application entry point.

One process serves the JSON API, the server-rendered pages, the static assets and the
generated brand tokens. See `docs/ARCHITECTURE.md`.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import REPO_ROOT, ConfigurationError, Settings, get_settings
from backend.app.db import init_database
from backend.app.seed import seed

BRAND_DIR = REPO_ROOT / "brands" / "dist"
STATIC_DIR = REPO_ROOT / "frontend" / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)

        engine, session_factory = init_database(settings)
        app.state.engine = engine
        app.state.session_factory = session_factory

        with session_factory() as session:
            created = seed(session, settings)
        if created is not None:
            print(f"seeded bootstrap administrator: {created.email}")

        yield

        engine.dispose()

    app = FastAPI(
        title="TS Timesheets",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
    )
    app.state.settings = settings

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/brand", StaticFiles(directory=BRAND_DIR), name="brand")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        # Placeholder until the UI shell lands in story 0.4 (#9).
        return (
            "<!doctype html><html><head><title>TS Timesheets</title>"
            '<link rel="stylesheet" href="/brand/tokens.css">'
            "<style>body{background:var(--color-background);color:var(--color-text-body);"
            "font-family:var(--font-sans);padding:var(--space-12)}"
            "h1{color:var(--color-text-heading)}</style></head>"
            "<body><h1>TS Timesheets</h1>"
            "<p>The application skeleton is running. The interface arrives in EPIC 0.4.</p>"
            "</body></html>"
        )

    return app


def build() -> FastAPI:
    """Uvicorn's factory — turns a configuration error into a clean exit, not a traceback.

    Run as `uvicorn backend.app.main:build --factory`. Building here rather than at import
    time keeps importing the module free of side effects, so tests can construct an app
    with their own settings.
    """
    try:
        return create_app()
    except ConfigurationError as error:
        print(f"TS Timesheets cannot start: {error}", file=sys.stderr)
        raise SystemExit(1) from error
