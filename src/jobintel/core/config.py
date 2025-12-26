from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Default to SQLite so the repo runs locally without Docker.
    DATABASE_URL: str = "sqlite:///./jobintel.db"


settings = Settings()
