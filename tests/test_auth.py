"""Authentication: sessions, protection, expiry, and the forced first-login change."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.auth import SESSION_COOKIE
from backend.app.config import Settings, load_settings
from backend.app.main import create_app
from backend.app.models import User
from backend.app.security import hash_password, verify_password

ADMIN_EMAIL = "admin@example.com"
BOOTSTRAP_PASSWORD = "bootstrap-password"
GOOD_PASSWORD = "a-properly-long-password"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return load_settings(
        _env_file=None,
        secret_key="test-secret-key",
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'data' / 'auth.db'}",
        admin_email=ADMIN_EMAIL,
        admin_password=BOOTSTRAP_PASSWORD,
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def set_admin_password(client: TestClient, password: str) -> None:
    """Move the seeded admin off the bootstrap password without going through the UI."""
    with client.app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.email == ADMIN_EMAIL))
        user.password_hash = hash_password(password)
        session.commit()


def sign_in(client: TestClient, password: str = GOOD_PASSWORD):
    return client.post(
        "/login", data={"email": ADMIN_EMAIL, "password": password}, follow_redirects=False
    )


# --- protection -----------------------------------------------------------------------


def test_the_index_redirects_to_login_when_signed_out(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_an_api_client_gets_401_rather_than_a_redirect(client: TestClient) -> None:
    response = client.get("/", headers={"accept": "application/json"}, follow_redirects=False)
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}


def test_healthz_stays_public(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200


# --- signing in -----------------------------------------------------------------------


def test_correct_credentials_set_a_session_and_land_on_the_index(client: TestClient) -> None:
    set_admin_password(client, GOOD_PASSWORD)

    response = sign_in(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SESSION_COOKIE in response.cookies

    index = client.get("/")
    assert index.status_code == 200
    assert ADMIN_EMAIL in index.text


def test_wrong_credentials_return_the_form_and_set_no_cookie(client: TestClient) -> None:
    set_admin_password(client, GOOD_PASSWORD)

    response = sign_in(client, password="not-the-password")
    assert response.status_code == 401
    assert SESSION_COOKIE not in response.cookies
    assert "do not match" in response.text


def test_an_inactive_user_cannot_sign_in(client: TestClient) -> None:
    set_admin_password(client, GOOD_PASSWORD)
    with client.app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.email == ADMIN_EMAIL))
        user.is_active = False
        session.commit()

    assert sign_in(client).status_code == 401


def test_the_session_cookie_is_httponly_and_lax(client: TestClient) -> None:
    set_admin_password(client, GOOD_PASSWORD)
    header = sign_in(client).headers["set-cookie"]

    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Secure" not in header, "development must not set Secure, or local login breaks"


def test_production_marks_the_cookie_secure(tmp_path) -> None:
    production = load_settings(
        _env_file=None,
        secret_key="test-secret-key",
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'data' / 'prod.db'}",
        admin_email=ADMIN_EMAIL,
        admin_password=BOOTSTRAP_PASSWORD,
        env="production",
    )
    with TestClient(create_app(production)) as client:
        set_admin_password(client, GOOD_PASSWORD)
        assert "Secure" in sign_in(client).headers["set-cookie"]


# --- session integrity ----------------------------------------------------------------


def test_a_tampered_cookie_is_rejected(client: TestClient) -> None:
    set_admin_password(client, GOOD_PASSWORD)
    sign_in(client)

    client.cookies.set(SESSION_COOKIE, "not-a-real-token")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_an_expired_cookie_is_rejected(settings: Settings) -> None:
    # itsdangerous timestamps have one-second resolution, so a one-second session needs
    # just over two seconds to be reliably past its age. This is the only slow test in the
    # suite, and expiry is worth proving rather than assuming.
    expiring = settings.model_copy(update={"session_max_age": 1})
    with TestClient(create_app(expiring)) as client:
        set_admin_password(client, GOOD_PASSWORD)
        sign_in(client)
        assert client.get("/", follow_redirects=False).status_code == 200

        time.sleep(2.1)
        assert client.get("/", follow_redirects=False).status_code == 303


def test_logout_clears_the_session(client: TestClient) -> None:
    set_admin_password(client, GOOD_PASSWORD)
    sign_in(client)
    assert client.get("/").status_code == 200

    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


# --- the forced first-login password change -------------------------------------------


def test_the_bootstrap_password_forces_a_change_before_anything_else(client: TestClient) -> None:
    response = sign_in(client, password=BOOTSTRAP_PASSWORD)
    assert response.status_code == 303
    assert response.headers["location"] == "/account/password"

    # Every other page bounces back to the change screen until it is done.
    blocked = client.get("/", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/account/password"

    assert client.get("/account/password").status_code == 200


def test_changing_the_password_lifts_the_block_and_rehashes(client: TestClient) -> None:
    sign_in(client, password=BOOTSTRAP_PASSWORD)

    response = client.post(
        "/account/password",
        data={
            "current_password": BOOTSTRAP_PASSWORD,
            "new_password": GOOD_PASSWORD,
            "confirm_password": GOOD_PASSWORD,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert client.get("/").status_code == 200

    with client.app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.email == ADMIN_EMAIL))
        assert verify_password(GOOD_PASSWORD, user.password_hash)
        assert not verify_password(BOOTSTRAP_PASSWORD, user.password_hash)


@pytest.mark.parametrize(
    ("current", "new", "confirm", "expected"),
    [
        ("wrong", GOOD_PASSWORD, GOOD_PASSWORD, "current password is not correct"),
        (BOOTSTRAP_PASSWORD, GOOD_PASSWORD, "mismatched-password", "do not match"),
        (BOOTSTRAP_PASSWORD, "short", "short", "at least 12 characters"),
        (BOOTSTRAP_PASSWORD, BOOTSTRAP_PASSWORD, BOOTSTRAP_PASSWORD, "must be different"),
    ],
)
def test_a_bad_password_change_is_refused(
    client: TestClient, current: str, new: str, confirm: str, expected: str
) -> None:
    sign_in(client, password=BOOTSTRAP_PASSWORD)

    response = client.post(
        "/account/password",
        data={"current_password": current, "new_password": new, "confirm_password": confirm},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert expected in response.text
