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
PROJECT_ROOT = Path(__file__).resolve().parents[4]
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


settings = Settings()
