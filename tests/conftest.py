"""Shared fixtures: a configured app, and people to sign in as."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.config import Settings, load_settings
from backend.app.main import create_app
from backend.app.models import User
from backend.app.security import hash_password
from backend.app.seed import ensure_member_role

ADMIN_EMAIL = "admin@example.com"
PASSWORD = "a-properly-long-password"


@dataclass
class Person:
    id: int
    email: str
    password: str = PASSWORD


@pytest.fixture
def settings(tmp_path) -> Settings:
    return load_settings(
        _env_file=None,
        secret_key="test-secret-key",
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'data' / 'test.db'}",
        admin_email=ADMIN_EMAIL,
        admin_password="bootstrap-password",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def administrator(client: TestClient) -> Person:
    """The seeded administrator, moved off the bootstrap password."""
    with client.app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.email == ADMIN_EMAIL))
        user.password_hash = hash_password(PASSWORD)
        session.commit()
        return Person(id=user.id, email=user.email)


@pytest.fixture
def make_person(client: TestClient):
    def _make(email: str, full_name: str = "A Consultant") -> Person:
        with client.app.state.session_factory() as session:
            user = User(
                email=email,
                full_name=full_name,
                password_hash=hash_password(PASSWORD),
                role_id=ensure_member_role(session).id,
            )
            session.add(user)
            session.commit()
            return Person(id=user.id, email=user.email)

    return _make


@pytest.fixture
def sign_in(client: TestClient):
    def _sign_in(person: Person) -> None:
        client.cookies.clear()
        response = client.post("/login", data={"email": person.email, "password": person.password})
        assert response.status_code == 200, f"could not sign in as {person.email}"

    return _sign_in
