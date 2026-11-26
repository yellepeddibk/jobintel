"""`alembic upgrade head` must build the full schema on an empty database.

Runs Alembic against a temporary SQLite file. Before the baseline revision
9bfae4d007ee existed, the chain began with a revision that altered tables nothing had
created, so an empty database failed with NoSuchTableError.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from jobintel.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE = "9bfae4d007ee"
TABLES = {"raw_jobs", "jobs", "job_skills", "ingest_runs"}


@pytest.fixture
def empty_db(tmp_path, monkeypatch):
    """An empty SQLite file, plus an Alembic Config pointed at it.

    Patches the settings singleton because alembic/env.py reads DATABASE_URL from it.
    """
    from jobintel.core import config as jobintel_config

    url = f"sqlite+pysqlite:///{(tmp_path / 'empty.db').as_posix()}"
    monkeypatch.setattr(jobintel_config.settings, "DATABASE_URL", url, raising=False)

    # Config without an .ini file: env.py then skips fileConfig, which would otherwise
    # reconfigure logging for the rest of the test session.
    cfg = Config()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))

    engine = create_engine(url)
    yield engine, cfg
    engine.dispose()


def _schema(engine) -> dict[str, dict]:
    """Structure that must agree between migrations and models, per table."""
    insp = inspect(engine)
    result = {}
    for table in sorted(TABLES):
        result[table] = {
            "columns": {c["name"]: c["nullable"] for c in insp.get_columns(table)},
            "primary_key": sorted(insp.get_pk_constraint(table)["constrained_columns"]),
            "uniques": sorted(
                tuple(uc["column_names"]) for uc in insp.get_unique_constraints(table)
            ),
            "indexes": sorted(
                (ix["name"], tuple(ix["column_names"]), bool(ix["unique"]))
                for ix in insp.get_indexes(table)
            ),
            "foreign_keys": sorted(
                (tuple(fk["constrained_columns"]), fk["referred_table"])
                for fk in insp.get_foreign_keys(table)
            ),
        }
    return result


def test_upgrade_head_builds_an_empty_database(empty_db):
    engine, cfg = empty_db

    command.upgrade(cfg, "head")

    assert TABLES <= set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == command.ScriptDirectory.from_config(cfg).get_current_head()


def test_migrated_schema_matches_the_models(empty_db, tmp_path):
    """Migrations and models must describe the same tables.

    Compares columns and nullability, keys, unique constraints, indexes and foreign
    keys. Server defaults are not compared, because of one known, pre-existing
    difference: revision fbbd657b4749 gives raw_jobs.environment and
    ingest_runs.environment a database default of 'production' that the models do not
    declare.
    """
    engine, cfg = empty_db
    command.upgrade(cfg, "head")

    from_models = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'models.db').as_posix()}")
    Base.metadata.create_all(bind=from_models)
    try:
        assert _schema(engine) == _schema(from_models)
    finally:
        from_models.dispose()


def test_migrated_empty_database_enforces_the_current_contract(empty_db):
    engine, cfg = empty_db
    command.upgrade(cfg, "head")

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO jobs (environment, title, url, hash) VALUES ('test', 'A', 'u', 'h')")
        )
        # Same url and hash in another environment is allowed.
        conn.execute(
            text(
                "INSERT INTO jobs (environment, title, url, hash) "
                "VALUES ('production', 'A', 'u', 'h')"
            )
        )
    # A job with no environment is rejected: there is no default to fall back on.
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO jobs (title) VALUES ('no environment')"))


def test_baseline_leaves_existing_tables_alone(empty_db):
    """A database whose tables predate Alembic, unstamped, still upgrades."""
    engine, cfg = empty_db
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE raw_jobs (id INTEGER PRIMARY KEY, marker VARCHAR)"))
        conn.execute(text("INSERT INTO raw_jobs (id, marker) VALUES (1, 'kept')"))

    command.upgrade(cfg, BASELINE)

    columns = {c["name"] for c in inspect(engine).get_columns("raw_jobs")}
    assert "marker" in columns  # not recreated
    with engine.connect() as conn:
        assert conn.execute(text("SELECT marker FROM raw_jobs")).scalar_one() == "kept"
    assert TABLES <= set(inspect(engine).get_table_names())


def test_downgrade_past_the_baseline_refuses(empty_db):
    engine, cfg = empty_db
    command.upgrade(cfg, BASELINE)

    with pytest.raises(RuntimeError, match="Refusing to downgrade past the baseline"):
        command.downgrade(cfg, "base")

    assert TABLES <= set(inspect(engine).get_table_names())
