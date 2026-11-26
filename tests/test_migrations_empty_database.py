"""`alembic upgrade head` must build the full schema on an empty database.

Runs Alembic against a temporary SQLite file, and also against a fresh PostgreSQL
database when TEST_POSTGRES_URL is set. Before the baseline revision 9bfae4d007ee
existed, the chain began with a revision that altered tables nothing had created, so
an empty database failed with NoSuchTableError.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from jobintel.models import Base

BASELINE = "9bfae4d007ee"
TABLES = {"raw_jobs", "jobs", "job_skills", "ingest_runs"}

# The make_database and empty_db fixtures live in conftest.py.


def _schema(engine) -> dict[str, dict]:
    """Structure that must agree between migrations and models, per table."""
    insp = inspect(engine)
    result = {}
    for table in sorted(TABLES):
        result[table] = {
            "columns": {
                c["name"]: {"nullable": c["nullable"], "default": c["default"]}
                for c in insp.get_columns(table)
            },
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


def test_migrated_schema_matches_the_models(empty_db, make_database):
    """Migrations and models must describe the same tables.

    Compares columns and nullability, keys, unique constraints, indexes and foreign
    keys, and server defaults.
    """
    engine, cfg = empty_db
    command.upgrade(cfg, "head")

    from_models = create_engine(make_database("models"))
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
