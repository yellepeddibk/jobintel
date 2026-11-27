from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Windows: avoids a transient KeyError while pydantic_settings reads env vars.
if os.name == "nt":
    os.environ.setdefault("NODEFAULTCURRENTDIRECTORYINEXEPATH", "1")

# Absolute, so Streamlit finds .env from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Environment(StrEnum):
    """Supported runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    DATABASE_URL: str = "sqlite:///./jobintel.db"  # runs locally without Docker
    ENV: Literal["development", "test", "production"] = "development"

    @property
    def environment(self) -> Environment:
        """Get the current environment as an enum."""
        return Environment(self.ENV)

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.ENV == "production"


def _load_streamlit_secrets() -> None:
    """Copy Streamlit Cloud secrets to os.environ before pydantic reads them."""
    try:
        import streamlit as st

        if hasattr(st, "secrets") and len(st.secrets) > 0:
            for key in ("DATABASE_URL", "ENV"):
                if key in st.secrets:
                    os.environ[key] = str(st.secrets[key])
    except Exception:
        pass  # Not running in Streamlit context or no secrets


def get_settings() -> Settings:
    """Get application settings with Streamlit Cloud support."""
    _load_streamlit_secrets()
    return Settings()


def redact_db_url(url: str) -> str:
    """Database URL with the password replaced by '***'."""
    try:
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
        if parsed.password:
            parsed = parsed.set(password="***")
        return str(parsed)
    except Exception:
        # Fallback: crude redaction
        if "://" in url and "@" in url:
            protocol, rest = url.split("://", 1)
            if "@" in rest:
                creds, host = rest.rsplit("@", 1)
                if ":" in creds:
                    user, _ = creds.split(":", 1)
                    return f"{protocol}://{user}:***@{host}"
        return "***"


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def database_host(url: str) -> str:
    """The host a database URL points at, without credentials. '' for SQLite or unparsable."""
    try:
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
    except Exception:
        return ""
    if parsed.drivername.startswith("sqlite"):
        return ""
    return parsed.host or ""


def is_local_database(url: str) -> bool:
    """True for SQLite, localhost, loopback, or no host (local socket). Unparsable: False."""
    try:
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
    except Exception:
        return False
    if parsed.drivername.startswith("sqlite"):
        return True
    host = parsed.host or ""
    return not host or host in _LOCAL_HOSTS or host.startswith("127.")


settings = get_settings()
