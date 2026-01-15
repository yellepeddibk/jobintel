from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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


def init_db(skip_migrations: bool = False) -> None:
    """Initialize database schema.

    Args:
        skip_migrations: If True, use create_all() even in production.
                        Useful for Streamlit Cloud where tables already exist.

    For production (with migrations): Runs Alembic migrations (alembic upgrade head).
    For development/testing/skip: Uses SQLAlchemy create_all() for quick setup.

    This function is safe to call multiple times.
    """
    from jobintel.models import Base

    if settings.is_production and not skip_migrations:
        # Production: use Alembic migrations for safe, versioned schema changes
        try:
            import os
            from pathlib import Path

            from alembic.config import Config

            from alembic import command

            project_root = Path(__file__).resolve().parents[2]
            alembic_ini = project_root / "alembic.ini"

            if not alembic_ini.exists():
                # Alembic not available (e.g., Streamlit Cloud) - use create_all
                Base.metadata.create_all(bind=engine)
                return

            config = Config(str(alembic_ini))
            config.set_main_option("script_location", str(project_root / "alembic"))

            old_cwd = os.getcwd()
            try:
                os.chdir(project_root)
                command.upgrade(config, "head")
            finally:
                os.chdir(old_cwd)
        except Exception:
            # Fallback to create_all if migrations fail
            Base.metadata.create_all(bind=engine)
    else:
        # Development/testing: use create_all() for quick setup
        Base.metadata.create_all(bind=engine)
