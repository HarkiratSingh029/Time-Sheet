"""The application starts, serves its health check, and refuses to start misconfigured."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.config import ConfigurationError, Settings, load_settings
from backend.app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return load_settings(
        _env_file=None,
        secret_key="test-secret-key",
        data_dir=tmp_path / "data",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_healthz_reports_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_renders(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "TS Timesheets" in response.text


def test_generated_brand_tokens_are_served(client: TestClient) -> None:
    response = client.get("/brand/tokens.css")
    assert response.status_code == 200
    assert "--color-primary" in response.text


def test_startup_creates_the_uploads_directory(settings: Settings) -> None:
    assert not settings.uploads_dir.exists()
    with TestClient(create_app(settings)):
        assert settings.uploads_dir.is_dir()


def test_missing_secret_key_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TS_SECRET_KEY", raising=False)

    with pytest.raises(ConfigurationError) as failure:
        load_settings(_env_file=None)

    assert "TS_SECRET_KEY" in str(failure.value)


def test_docs_are_hidden_in_production(tmp_path) -> None:
    production = load_settings(
        _env_file=None,
        secret_key="test-secret-key",
        data_dir=tmp_path / "data",
        env="production",
    )
    with TestClient(create_app(production)) as client:
        assert client.get("/api/docs").status_code == 404
