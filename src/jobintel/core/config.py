from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict

# Windows: Streamlit (and some shells) can trigger a transient KeyError while
# pydantic_settings iterates env vars. Setting this makes env parsing stable.
if os.name == "nt":
    os.environ.setdefault("NODEFAULTCURRENTDIRECTORYINEXEPATH", "1")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Default to SQLite so the repo runs locally without Docker.
    DATABASE_URL: str = "sqlite:///./jobintel.db"


settings = Settings()
