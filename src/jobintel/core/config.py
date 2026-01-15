from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Windows: Streamlit (and some shells) can trigger a transient KeyError while
# pydantic_settings iterates env vars. Setting this makes env parsing stable.
if os.name == "nt":
    os.environ.setdefault("NODEFAULTCURRENTDIRECTORYINEXEPATH", "1")

# Compute absolute path to .env so Streamlit finds it regardless of cwd
# Path: config.py -> core/ -> jobintel/ -> src/ -> PROJECT_ROOT
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Environment(str, Enum):
    """Supported runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    # Default to SQLite so the repo runs locally without Docker.
    DATABASE_URL: str = "sqlite:///./jobintel.db"

    # Runtime environment (development | test | production)
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


settings = get_settings()
