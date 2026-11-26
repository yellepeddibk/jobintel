from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jobintel.core.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


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


def init_db() -> None:
    """Prepare the database schema. Safe to call more than once.

    Production: runs Alembic migrations to head, and any error propagates. There is
    deliberately no create_all() fallback: it would hide the failure, leave pending
    schema changes unapplied, and create tables Alembic has no record of.

    Development and test: create_all() for quick local setup.
    """
    if settings.is_production:
        _upgrade_to_head()
        return

    from jobintel.models import Base

    Base.metadata.create_all(bind=engine)


def _upgrade_to_head() -> None:
    """Run `alembic upgrade head` against the configured database."""
    from alembic.config import Config

    from alembic import command

    if not ALEMBIC_INI.exists():
        raise RuntimeError(
            f"Cannot run migrations: {ALEMBIC_INI} not found. Production schema changes "
            "go through Alembic only."
        )

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))

    old_cwd = os.getcwd()
    try:
        os.chdir(PROJECT_ROOT)
        command.upgrade(config, "head")
    finally:
        os.chdir(old_cwd)
