"""Application configuration.

Every setting arrives as a `TS_`-prefixed environment variable, documented in
`.env.example`. A missing required variable stops the process at startup with a readable
message — a server running on a silently defaulted secret key is worse than one that
refuses to start.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class ConfigurationError(RuntimeError):
    """Configuration is missing or invalid; the process must not continue."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TS_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str

    data_dir: Path = Path("data")
    database_url: str = "sqlite:///data/app.db"

    admin_email: str = "admin@example.com"
    admin_password: str = "change-me"

    env: Literal["development", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    session_max_age: int = 86_400

    tunnel_token: str = ""

    # Email is off unless a host is configured: a laptop must never send anything.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from: str = "ts-timesheets@localhost"
    digest_hour: int = 8

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host.strip())

    @property
    def uploads_dir(self) -> Path:
        return self.resolved_data_dir / "uploads"

    @property
    def resolved_data_dir(self) -> Path:
        directory = self.data_dir
        return directory if directory.is_absolute() else REPO_ROOT / directory


def load_settings(**overrides: object) -> Settings:
    """Build settings, translating pydantic's error into something an operator can act on."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as error:
        missing = [
            f"TS_{'_'.join(str(part) for part in problem['loc']).upper()}"
            for problem in error.errors()
            if problem["type"] == "missing"
        ]
        if missing:
            raise ConfigurationError(
                f"missing required configuration: {', '.join(missing)}. "
                "Copy .env.example to .env and fill it in."
            ) from error
        raise ConfigurationError(f"invalid configuration: {error}") from error


@lru_cache
def get_settings() -> Settings:
    return load_settings()
