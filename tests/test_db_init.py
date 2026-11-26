"""init_db(): create_all() outside production, Alembic only in production."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

import jobintel.db as db
from alembic import command
from jobintel.core.config import settings
from jobintel.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TABLES = {"raw_jobs", "jobs", "job_skills", "ingest_runs"}


def test_init_db_creates_tables():
    db.init_db()
    tables = set(inspect(db.engine).get_table_names())
    assert TABLES.issubset(tables)


@pytest.fixture
def production(tmp_path, monkeypatch):
    """ENV=production against an empty temporary SQLite file. Returns its URL."""
    url = f"sqlite+pysqlite:///{(tmp_path / 'prod-like.db').as_posix()}"
    monkeypatch.setattr(settings, "ENV", "production")
    # alembic/env.py reads the URL from the settings singleton.
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    # alembic.ini's logging config would otherwise reconfigure logging for the rest
    # of the test session when env.py runs.
    monkeypatch.setattr("logging.config.fileConfig", lambda *a, **k: None)
    return url


@pytest.fixture
def create_all_calls(monkeypatch):
    """Records any create_all() call instead of running it."""
    calls = []
    monkeypatch.setattr(Base.metadata, "create_all", lambda *a, **k: calls.append(k))
    return calls


def test_production_builds_an_empty_database_through_migrations(production, create_all_calls):
    db.init_db()

    engine = create_engine(production)
    try:
        assert TABLES <= set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()
    cfg = Config()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    assert version == ScriptDirectory.from_config(cfg).get_current_head()
    assert create_all_calls == []


def test_production_raises_a_migration_failure_instead_of_falling_back(
    production, create_all_calls, monkeypatch
):
    def fail(*args, **kwargs):
        raise RuntimeError("migration exploded")

    monkeypatch.setattr(command, "upgrade", fail)

    with pytest.raises(RuntimeError, match="migration exploded"):
        db.init_db()

    assert create_all_calls == []
    engine = create_engine(production)
    try:
        assert not set(inspect(engine).get_table_names()) & TABLES
    finally:
        engine.dispose()


def test_production_refuses_without_alembic_config(
    production, create_all_calls, monkeypatch, tmp_path
):
    monkeypatch.setattr(db, "ALEMBIC_INI", tmp_path / "missing" / "alembic.ini")

    with pytest.raises(RuntimeError, match="Cannot run migrations"):
        db.init_db()

    assert create_all_calls == []
