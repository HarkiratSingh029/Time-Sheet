"""TS Timesheets — application entry point.

One process serves the JSON API, the server-rendered pages, the static assets and the
generated brand tokens. See `docs/ARCHITECTURE.md`.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.app.config import REPO_ROOT, ConfigurationError, Settings, get_settings
from backend.app.db import init_database
from backend.app.deps import NotAuthenticated, PasswordChangeRequired, RequiredUser
from backend.app.routes import approvals as approval_routes
from backend.app.routes import auth as auth_routes
from backend.app.routes import dashboard as dashboard_routes
from backend.app.routes import exports as export_routes
from backend.app.routes import me as me_routes
from backend.app.routes import projects as project_routes
from backend.app.routes import search as search_routes
from backend.app.routes import timesheet as timesheet_routes
from backend.app.routes import users as user_routes
from backend.app.routes.dashboard import can_see_portfolio
from backend.app.scheduler import start as start_digests
from backend.app.scheduler import stop as stop_digests
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

        digest_task = start_digests(app, session_factory, settings)

        yield

        await stop_digests(digest_task)
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

    @app.exception_handler(NotAuthenticated)
    async def _unauthenticated(request: Request, _exception: NotAuthenticated) -> Response:
        if _wants_json(request):
            return JSONResponse(
                {"detail": "Not authenticated"}, status_code=status.HTTP_401_UNAUTHORIZED
            )
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.exception_handler(PasswordChangeRequired)
    async def _password_change_required(
        request: Request, _exception: PasswordChangeRequired
    ) -> Response:
        if _wants_json(request):
            return JSONResponse(
                {"detail": "Password change required"}, status_code=status.HTTP_403_FORBIDDEN
            )
        return RedirectResponse("/account/password", status_code=status.HTTP_303_SEE_OTHER)

    app.include_router(auth_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(export_routes.router)
    app.include_router(me_routes.router)
    app.include_router(search_routes.router)
    app.include_router(approval_routes.router)
    app.include_router(project_routes.router)
    app.include_router(timesheet_routes.router)
    app.include_router(user_routes.router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, user: RequiredUser) -> Response:
        # Whoever can see the portfolio lands on it; everyone else keeps the overview.
        if can_see_portfolio(user):
            return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        # Everybody else lands on their own time, which is the question they actually have.
        return RedirectResponse("/me", status_code=status.HTTP_303_SEE_OTHER)

    return app


def _wants_json(request: Request) -> bool:
    """This is a server-rendered app: redirect by default, and answer in JSON only when
    the caller asked for it. Sniffing for `text/html` instead would 401 every client that
    sends `Accept: */*`, which is most of them."""
    accept = request.headers.get("accept", "")
    asked_for_json = "application/json" in accept and "text/html" not in accept
    return asked_for_json or request.url.path.startswith("/api")


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
