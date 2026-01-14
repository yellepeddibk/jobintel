from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from jobintel.core.config import settings


def _connect_args(url: str) -> dict[str, Any]:
    # SQLite needs this, Postgres must NOT receive it
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args(settings.DATABASE_URL),
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Initialize database schema.

    For production: Runs Alembic migrations (alembic upgrade head).
    For development/testing: Uses SQLAlchemy create_all() for quick setup.

    This function is safe to call multiple times.
    """

    if settings.is_production:
        # Production: use Alembic migrations for safe, versioned schema changes
        import os
        from pathlib import Path

        from alembic.config import Config

        from alembic import command

        project_root = Path(__file__).resolve().parents[2]
        alembic_ini = project_root / "alembic.ini"
        config = Config(str(alembic_ini))
        config.set_main_option("script_location", str(project_root / "alembic"))

        old_cwd = os.getcwd()
        try:
            os.chdir(project_root)
            command.upgrade(config, "head")
        finally:
            os.chdir(old_cwd)
    else:
        # Development/testing: use create_all() for quick setup
        import jobintel.models  # noqa: F401
        from jobintel.models import Base

        Base.metadata.create_all(bind=engine)
