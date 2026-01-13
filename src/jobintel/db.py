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

    For development/testing: Uses SQLAlchemy create_all() for quick setup.
    For production: Should use Alembic migrations (alembic upgrade head).

    This function is safe to call multiple times; create_all() is idempotent.
    """
    # Make sure all model classes are imported so they register with Base.metadata
    import jobintel.models  # noqa: F401
    from jobintel.models import Base

    Base.metadata.create_all(bind=engine)
