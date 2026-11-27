"""Revision 2e3a197ea25f removes DEFAULT 'production' from raw_jobs and ingest_runs."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from jobintel.models import IngestRun, RawJob

PREVIOUS_HEAD = "c4e81f6a2b57"
REVISION = "2e3a197ea25f"
TABLES = ("raw_jobs", "ingest_runs")

SEED = (
    "INSERT INTO raw_jobs (id, source, payload_json, environment)"
    " VALUES (1, 'remotive', '{\"url\": \"https://example.com/1\"}', 'development')",
    # Written without an environment while the default still exists.
    "INSERT INTO raw_jobs (id, source, payload_json)"
    " VALUES (2, 'arbeitnow', '{\"url\": \"https://example.com/2\"}')",
    "INSERT INTO ingest_runs (id, source, environment, status, started_at, fetched,"
    " inserted_raw, inserted_jobs, inserted_skills)"
    " VALUES (1, 'remotive', 'test', 'success', '2026-01-04 00:00:00', 3, 3, 2, 1)",
)


def _environment_default(engine, table: str):
    for col in inspect(engine).get_columns(table):
        if col["name"] == "environment":
            return col["default"]
    raise AssertionError(f"{table}.environment missing")


def _snapshot(engine) -> dict:
    insp = inspect(engine)
    with engine.connect() as conn:
        rows = {
            "raw_jobs": conn.execute(
                text("SELECT id, source, environment FROM raw_jobs ORDER BY id")
            ).all(),
            "ingest_runs": conn.execute(
                text("SELECT id, source, environment, status, fetched FROM ingest_runs ORDER BY id")
            ).all(),
            "ingested_at_set": conn.execute(
                text("SELECT count(*) FROM raw_jobs WHERE ingested_at IS NOT NULL")
            ).scalar_one(),
        }
    rows["indexes"] = {t: sorted(ix["name"] for ix in insp.get_indexes(t)) for t in TABLES}
    rows["nullable"] = {t: {c["name"]: c["nullable"] for c in insp.get_columns(t)} for t in TABLES}
    rows["ingested_at_default"] = next(
        c["default"] for c in insp.get_columns("raw_jobs") if c["name"] == "ingested_at"
    )
    return rows


@pytest.fixture
def previous_head(empty_db):
    engine, cfg = empty_db
    command.upgrade(cfg, PREVIOUS_HEAD)
    with engine.begin() as conn:
        for statement in SEED:
            conn.execute(text(statement))
    return engine, cfg


def test_previous_head_still_has_the_defaults(previous_head):
    engine, _ = previous_head
    for table in TABLES:
        assert "production" in str(_environment_default(engine, table))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT environment FROM raw_jobs WHERE id = 2")).scalar_one() == (
            "production"
        )


def test_upgrade_removes_both_defaults(previous_head):
    engine, cfg = previous_head

    command.upgrade(cfg, REVISION)

    for table in TABLES:
        assert _environment_default(engine, table) is None


def test_upgrade_preserves_rows_indexes_and_other_defaults(previous_head):
    engine, cfg = previous_head
    before = _snapshot(engine)

    command.upgrade(cfg, REVISION)

    assert _snapshot(engine) == before


@pytest.mark.parametrize("table", TABLES)
def test_insert_without_environment_fails_after_upgrade(previous_head, table):
    engine, cfg = previous_head
    command.upgrade(cfg, REVISION)

    statement = {
        "raw_jobs": "INSERT INTO raw_jobs (source, payload_json) VALUES ('remotive', '{}')",
        "ingest_runs": (
            "INSERT INTO ingest_runs (source, status, started_at, fetched, inserted_raw,"
            " inserted_jobs, inserted_skills)"
            " VALUES ('remotive', 'running', '2026-01-04 00:00:00', 0, 0, 0, 0)"
        ),
    }[table]
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text(statement))


def test_downgrade_restores_the_defaults_and_round_trips(previous_head):
    engine, cfg = previous_head
    before = _snapshot(engine)

    command.upgrade(cfg, REVISION)
    command.downgrade(cfg, PREVIOUS_HEAD)

    for table in TABLES:
        assert "production" in str(_environment_default(engine, table))
    assert _snapshot(engine) == before

    command.upgrade(cfg, REVISION)
    for table in TABLES:
        assert _environment_default(engine, table) is None
    assert _snapshot(engine) == before


def test_upgrade_is_rerunnable(previous_head):
    engine, cfg = previous_head
    command.upgrade(cfg, REVISION)
    before = _snapshot(engine)

    # Re-stamp and run again: the revision sees no default left and does nothing.
    command.stamp(cfg, PREVIOUS_HEAD)
    command.upgrade(cfg, REVISION)

    for table in TABLES:
        assert _environment_default(engine, table) is None
    assert _snapshot(engine) == before


def test_orm_writes_without_environment_fail(session):
    """The models no longer fill in 'production' either."""
    session.add(RawJob(source="remotive", payload_json={"url": "https://example.com/x"}))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.add(IngestRun(source="remotive", status="running", started_at=datetime.now(UTC)))
    with pytest.raises(IntegrityError):
        session.flush()
