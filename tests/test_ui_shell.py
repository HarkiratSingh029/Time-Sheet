"""The UI shell: the layout every page inherits, and the rules the design system sets.

The theme toggle itself runs in the browser, so its behaviour is covered by
`tests/theme_contract.mjs`, which this module runs as part of the suite.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.config import Settings, load_settings
from backend.app.main import create_app
from backend.app.models import User
from backend.app.security import hash_password

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "frontend" / "templates"
APP_CSS = REPO_ROOT / "frontend" / "static" / "css" / "app.css"

ADMIN_EMAIL = "admin@example.com"
PASSWORD = "a-properly-long-password"
STATES = ("draft", "submitted", "approved", "rejected")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return load_settings(
        _env_file=None,
        secret_key="test-secret-key",
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'data' / 'ui.db'}",
        admin_email=ADMIN_EMAIL,
        admin_password="bootstrap-password",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    with TestClient(create_app(settings)) as test_client:
        with test_client.app.state.session_factory() as session:
            user = session.scalar(select(User).where(User.email == ADMIN_EMAIL))
            user.password_hash = hash_password(PASSWORD)
            session.commit()
        yield test_client


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    client.post("/login", data={"email": ADMIN_EMAIL, "password": PASSWORD})
    return client


# --- the shell ------------------------------------------------------------------------


def test_the_shell_wraps_a_signed_in_page(signed_in: TestClient) -> None:
    page = signed_in.get("/").text

    assert '<link rel="stylesheet" href="/brand/tokens.css">' in page
    assert '<link rel="stylesheet" href="/static/css/app.css">' in page
    assert 'class="skip-link"' in page, "keyboard users need a skip link"
    assert "data-theme-toggle" in page
    assert 'action="/logout"' in page
    assert ADMIN_EMAIL in page


def test_the_signed_out_shell_offers_no_user_menu(client: TestClient) -> None:
    page = client.get("/login").text

    assert "data-theme-toggle" in page, "the theme is switchable before signing in too"
    assert 'action="/logout"' not in page
    assert ADMIN_EMAIL not in page


def test_the_theme_is_applied_before_first_paint(client: TestClient) -> None:
    head = client.get("/login").text.split("</head>")[0]

    assert "localStorage.getItem" in head, (
        "reading the stored theme after paint shows the wrong palette for a frame"
    )
    assert "data-theme" in head


def test_the_theme_module_is_served(client: TestClient) -> None:
    response = client.get("/static/js/theme.js")
    assert response.status_code == 200
    assert "ts-theme" in response.text


def test_every_page_template_extends_the_layout() -> None:
    pages = [
        path
        for path in TEMPLATES.rglob("*.html")
        if path.name != "base.html" and not path.name.startswith("_")
    ]
    assert pages, "no page templates found"

    for page in pages:
        assert "{% extends" in page.read_text(), f"{page.name} does not extend base.html"


# --- the design system ----------------------------------------------------------------


def test_status_chips_carry_a_label_not_only_a_colour(
    client: TestClient, make_person, sign_in
) -> None:
    # An administrator is redirected to the portfolio, so sign in as somebody who lands on
    # the overview itself.
    sign_in(make_person("member@example.com", "Mo Member"))
    page = client.get("/").text

    for state in STATES:
        assert f"chip--{state}" in page
        assert state.capitalize() in page, (
            f"{state} must read as text, so it survives greyscale and colour blindness"
        )


def test_the_stylesheet_uses_tokens_rather_than_literal_colours() -> None:
    css = APP_CSS.read_text()
    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stripped)
    assert not re.search(r"\b(rgba?|hsla?)\s*\(", stripped)
    assert "var(--color-" in stripped


def test_focus_is_always_visible() -> None:
    css = APP_CSS.read_text()
    focus_rule = css.split(":focus-visible")[1].split("}")[0]
    assert "outline" in focus_rule
    assert "var(--color-accent)" in focus_rule


# --- the browser half -----------------------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_theme_toggle_behaviour() -> None:
    result = subprocess.run(
        ["node", str(REPO_ROOT / "tests" / "theme_contract.mjs")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
