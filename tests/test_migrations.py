"""Migration coverage for the jobs environment expansion (revision 7d2b1a4c9f30).

These tests run Alembic against a temporary SQLite file rather than the in-memory
database the rest of the suite uses, because Alembic opens its own connection and an
in-memory database would not survive it.

The starting schema is written out as explicit DDL instead of being built from
`jobintel.models`. That is deliberate: this test has to keep describing the schema as
it was when the migration was written. Building it from the ORM would silently track
future model changes, and the test would stop exercising the migration at exactly the
point the model moves ahead of it.

Two revisions are covered, the two halves of an expand/use/contract rollout:

- `7d2b1a4c9f30` (expand) adds `jobs.environment` with a temporary
  `DEFAULT 'production'`, so the application deployed at the time, which did not set
  the column, kept inserting successfully.
- `c4e81f6a2b57` (contract) removes that default now that the deployed application
  sets `environment` explicitly. After it, a write that omits an environment fails
  instead of being silently labelled production.

Tests assert against whichever of those they are about. Ones describing behavior
specific to the expand revision target `EXPAND_REVISION` by name, because "head" has
moved past it; ones describing the current schema contract stay on "head", which is
what proves the contract step preserved NOT NULL, both composite uniques and every row.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PREVIOUS_HEAD = "fbbd657b4749"

# The expand revision, which added jobs.environment together with a temporary
# database-only DEFAULT 'production'. Tests that assert behavior specific to that
# revision target it by name rather than "head", so they keep testing what they were
# written to test as later revisions land. The contract revision c4e81f6a2b57 removes
# that default, so "head" no longer has one; tests that assert the current schema
# contract stay on "head" deliberately, which is what proves the contract step
# preserved NOT NULL, both composite uniques, and every row.
EXPAND_REVISION = "7d2b1a4c9f30"

# The schema as of revision fbbd657b4749: raw_jobs and ingest_runs carry an
# environment column, jobs and job_skills do not, and jobs has global uniqueness on
# url and hash. Matches what SQLAlchemy's create_all() emitted for SQLite at the time
# this migration was authored.
PRE_MIGRATION_DDL = (
    """
    CREATE TABLE raw_jobs (
        id INTEGER NOT NULL,
        source VARCHAR NOT NULL,
        payload_json JSON NOT NULL,
        ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
        environment VARCHAR NOT NULL,
        PRIMARY KEY (id)
    )
    """,
    "CREATE INDEX ix_raw_jobs_environment ON raw_jobs (environment)",
    """
    CREATE TABLE jobs (
        id INTEGER NOT NULL,
        title VARCHAR NOT NULL,
        company VARCHAR,
        location VARCHAR,
        url VARCHAR,
        posted_at DATE,
        description TEXT,
        hash VARCHAR,
        PRIMARY KEY (id),
        UNIQUE (url),
        UNIQUE (hash)
    )
    """,
    "CREATE INDEX idx_jobs_location ON jobs (location)",
    "CREATE INDEX idx_jobs_posted_at ON jobs (posted_at)",
    """
    CREATE TABLE job_skills (
        job_id INTEGER NOT NULL,
        skill VARCHAR NOT NULL,
        PRIMARY KEY (job_id, skill),
        FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_job_skills_skill ON job_skills (skill)",
    """
    CREATE TABLE ingest_runs (
        id INTEGER NOT NULL,
        source VARCHAR NOT NULL,
        search VARCHAR,
        "limit" INTEGER,
        environment VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        started_at DATETIME NOT NULL,
        finished_at DATETIME,
        fetched INTEGER NOT NULL,
        inserted_raw INTEGER NOT NULL,
        inserted_jobs INTEGER NOT NULL,
        inserted_skills INTEGER NOT NULL,
        warnings JSON,
        error TEXT,
        PRIMARY KEY (id)
    )
    """,
    "CREATE INDEX ix_ingest_runs_environment ON ingest_runs (environment)",
    "CREATE INDEX ix_ingest_runs_source ON ingest_runs (source)",
    "CREATE INDEX idx_ingest_runs_started_at ON ingest_runs (started_at)",
)

# Rows that exist before the migration: one ordinary row, one with NULL url and hash
# (allowed then and now, because both engines treat NULLs as distinct in a unique
# constraint), and a job_skills child to prove the SQLite table rebuild keeps it.
SEED_JOBS = (
    "INSERT INTO jobs (id, title, company, location, url, posted_at, description, hash)"
    " VALUES (1, 'Data Engineer', 'Acme', 'Remote', 'https://example.com/j/1',"
    " '2026-01-10', 'Python and SQL', 'hash-1')",
    "INSERT INTO jobs (id, title, company, location, url, posted_at, description, hash)"
    " VALUES (2, 'Analyst', 'Acme', 'Berlin', NULL, NULL, 'SQL', NULL)",
    "INSERT INTO jobs (id, title, company, location, url, posted_at, description, hash)"
    " VALUES (3, 'Scientist', 'DataMinds', 'Munich', NULL, NULL, 'pandas', NULL)",
    "INSERT INTO job_skills (job_id, skill) VALUES (1, 'python')",
    "INSERT INTO job_skills (job_id, skill) VALUES (1, 'sql')",
)


def _unique_constraints(engine) -> dict[tuple[str, ...], str | None]:
    """Map each unique constraint on jobs to its name, keyed by its columns."""
    return {
        tuple(uc["column_names"]): uc["name"]
        for uc in inspect(engine).get_unique_constraints("jobs")
    }


@pytest.fixture
def migration_db(tmp_path, monkeypatch):
    """A SQLite database at the previous Alembic head, plus a Config pointed at it.

    Patches the settings singleton because alembic/env.py reads DATABASE_URL from it
    and overwrites whatever the Config carries.
    """
    from jobintel.core import config as jobintel_config

    db_path = tmp_path / "migration.db"
    url = f"sqlite+pysqlite:///{db_path}"

    engine = create_engine(url)
    with engine.begin() as conn:
        for statement in PRE_MIGRATION_DDL:
            conn.execute(text(statement))
        for statement in SEED_JOBS:
            conn.execute(text(statement))

    monkeypatch.setattr(jobintel_config.settings, "DATABASE_URL", url, raising=False)

    # Config without an .ini file: env.py then skips fileConfig, which would otherwise
    # reconfigure logging for the rest of the test session.
    cfg = Config()
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.stamp(cfg, PREVIOUS_HEAD)

    yield engine, cfg

    engine.dispose()


def test_upgrade_adds_environment_column_as_not_null(migration_db):
    engine, cfg = migration_db

    command.upgrade(cfg, "head")

    columns = {c["name"]: c for c in inspect(engine).get_columns("jobs")}
    assert "environment" in columns
    assert columns["environment"]["nullable"] is False


def test_expand_revision_leaves_a_temporary_server_default_behind(migration_db):
    """The default is what kept the pre-deploy application inserting successfully.

    Targets the expand revision by name, not "head": the contract revision removes
    this default, and asserting it at "head" would test the opposite of the truth
    once that landed.
    """
    engine, cfg = migration_db

    command.upgrade(cfg, EXPAND_REVISION)

    columns = {c["name"]: c for c in inspect(engine).get_columns("jobs")}
    assert "production" in str(columns["environment"]["default"])


def test_upgrade_backfills_existing_rows_to_production(migration_db):
    engine, cfg = migration_db

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, environment FROM jobs ORDER BY id")).all()
    assert rows == [(1, "production"), (2, "production"), (3, "production")]


def test_upgrade_replaces_global_uniqueness_with_scoped_uniqueness(migration_db):
    engine, cfg = migration_db

    before = _unique_constraints(engine)
    assert ("url",) in before
    assert ("hash",) in before

    command.upgrade(cfg, "head")

    after = _unique_constraints(engine)
    assert ("url",) not in after
    assert ("hash",) not in after
    assert after[("environment", "url")] == "uq_jobs_environment_url"
    assert after[("environment", "hash")] == "uq_jobs_environment_hash"


def test_upgrade_preserves_rows_indexes_and_child_table(migration_db):
    engine, cfg = migration_db

    command.upgrade(cfg, "head")

    insp = inspect(engine)
    assert {idx["name"] for idx in insp.get_indexes("jobs")} >= {
        "idx_jobs_location",
        "idx_jobs_posted_at",
    }

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 3
        assert conn.execute(text("SELECT count(*) FROM job_skills")).scalar_one() == 2
        assert conn.execute(
            text("SELECT title, company, url, hash FROM jobs WHERE id = 1")
        ).one() == ("Data Engineer", "Acme", "https://example.com/j/1", "hash-1")

    fks = insp.get_foreign_keys("job_skills")
    assert any(fk["referred_table"] == "jobs" for fk in fks)


def test_same_url_and_hash_may_exist_in_two_environments(migration_db):
    """The point of the expansion: one posting per environment, not one overall."""
    engine, cfg = migration_db

    command.upgrade(cfg, "head")

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (title, url, hash, environment)"
                " VALUES ('Data Engineer', 'https://example.com/j/1', 'hash-1', 'development')"
            )
        )

    with engine.connect() as conn:
        environments = conn.execute(
            text(
                "SELECT environment FROM jobs WHERE url = 'https://example.com/j/1'"
                " ORDER BY environment"
            )
        ).scalars().all()
    assert environments == ["development", "production"]


@pytest.mark.parametrize(
    ("column", "value"),
    [("url", "https://example.com/j/1"), ("hash", "hash-1")],
)
def test_duplicate_within_one_environment_is_still_rejected(migration_db, column, value):
    engine, cfg = migration_db

    command.upgrade(cfg, "head")

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO jobs (title, {column}, environment)"
                f" VALUES ('Duplicate', '{value}', 'production')"
            )
        )


def test_multiple_null_urls_remain_allowed(migration_db):
    """NULLs stay distinct under the composite constraint, as they were before."""
    engine, cfg = migration_db

    command.upgrade(cfg, "head")

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (title, url, hash, environment)"
                " VALUES ('No URL', NULL, NULL, 'production')"
            )
        )

    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM jobs WHERE url IS NULL AND environment = 'production'")
        ).scalar_one()
    assert count == 3


def test_upgrade_is_rerunnable_against_an_already_migrated_schema(migration_db):
    """Re-running upgrade() must be a no-op, matching the guards in fbbd657b4749."""
    engine, cfg = migration_db

    command.upgrade(cfg, "head")
    command.stamp(cfg, PREVIOUS_HEAD, purge=True)
    command.upgrade(cfg, "head")

    after = _unique_constraints(engine)
    assert ("url",) not in after
    assert after[("environment", "url")] == "uq_jobs_environment_url"
    assert after[("environment", "hash")] == "uq_jobs_environment_hash"

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 3


def test_downgrade_restores_global_uniqueness_and_drops_the_column(migration_db):
    engine, cfg = migration_db

    command.upgrade(cfg, "head")
    command.downgrade(cfg, PREVIOUS_HEAD)

    columns = {c["name"] for c in inspect(engine).get_columns("jobs")}
    assert "environment" not in columns

    after = _unique_constraints(engine)
    assert ("url",) in after
    assert ("hash",) in after
    assert ("environment", "url") not in after

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 3
        assert conn.execute(text("SELECT count(*) FROM job_skills")).scalar_one() == 2


def test_downgrade_refuses_rather_than_discard_cross_environment_rows(migration_db):
    """Global uniqueness cannot be restored once two environments share a URL.

    The refusal belongs to the expand revision's downgrade, so this starts there.
    Downgrading from "head" would run the contract revision's downgrade first and
    leave the version stamped between the two, which says nothing extra about the
    guard being tested.
    """
    engine, cfg = migration_db

    command.upgrade(cfg, EXPAND_REVISION)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (title, url, hash, environment)"
                " VALUES ('Data Engineer', 'https://example.com/j/1', 'hash-1', 'development')"
            )
        )

    with pytest.raises(RuntimeError, match="Cannot downgrade"):
        command.downgrade(cfg, PREVIOUS_HEAD)

    # The refusal must leave the schema and the rows untouched.
    columns = {c["name"] for c in inspect(engine).get_columns("jobs")}
    assert "environment" in columns
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 4


def test_upgrade_downgrade_upgrade_round_trip(migration_db):
    engine, cfg = migration_db

    command.upgrade(cfg, "head")
    command.downgrade(cfg, PREVIOUS_HEAD)
    command.upgrade(cfg, "head")

    columns = {c["name"]: c for c in inspect(engine).get_columns("jobs")}
    assert columns["environment"]["nullable"] is False

    after = _unique_constraints(engine)
    assert after[("environment", "url")] == "uq_jobs_environment_url"
    assert after[("environment", "hash")] == "uq_jobs_environment_hash"

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 3
        assert conn.execute(
            text("SELECT count(*) FROM jobs WHERE environment = 'production'")
        ).scalar_one() == 3


def test_application_writes_environment_rather_than_inheriting_the_server_default(
    migration_db,
):
    """The use step's half of the expand/use/contract contract.

    Runs at the expand revision on purpose, because that is where the temporary
    DEFAULT 'production' still exists and can therefore mask a missing write. The
    ORM declares no default of its own, so this pins that the application supplies
    the value itself rather than leaning on the column default.

    Transforming a 'development' environment against this production-shaped
    database is the sharp version of that question: if the value came from the
    server default the rows would read 'production'. Once the contract revision
    removes the default there is nothing left to lean on, which is what
    test_insert_without_environment_fails_after_contract_revision covers instead.
    """
    from fixtures import TEST_JOB_PAYLOADS
    from sqlalchemy.orm import sessionmaker

    from jobintel.analytics.queries import get_kpis, get_top_skills
    from jobintel.etl.raw import upsert_raw_job
    from jobintel.etl.skills import extract_skills_for_all_jobs
    from jobintel.etl.transform import transform_jobs

    engine, cfg = migration_db
    command.upgrade(cfg, EXPAND_REVISION)

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM job_skills"))
        conn.execute(text("DELETE FROM jobs"))
        conn.commit()

    # The column default really is still there, so the test is not vacuous.
    default = next(
        c["default"] for c in inspect(engine).get_columns("jobs") if c["name"] == "environment"
    )
    assert "production" in str(default)

    session = sessionmaker(bind=engine)()
    try:
        inserted_raw = sum(
            upsert_raw_job(session, payload, environment="development")
            for payload in TEST_JOB_PAYLOADS
        )
        session.commit()
        assert inserted_raw == len(TEST_JOB_PAYLOADS)

        assert transform_jobs(session, environment="development") == len(TEST_JOB_PAYLOADS)
        assert extract_skills_for_all_jobs(session, environment="development") > 0

        with engine.connect() as conn:
            environments = conn.execute(
                text("SELECT DISTINCT environment FROM jobs")
            ).scalars().all()
        assert environments == ["development"], (
            "jobs.environment came from the server default, not the application"
        )

        # And the production view of that same database stays empty.
        assert get_kpis(session, environment="production")["total_jobs"] == 0
        assert get_top_skills(session, environment="production") == []

        assert get_kpis(session, environment="development")["total_jobs"] == len(
            TEST_JOB_PAYLOADS
        )
        assert get_top_skills(session, environment="development")

        # Re-running one environment stays idempotent.
        assert transform_jobs(session, environment="development") == 0
    finally:
        session.close()


# --------------------------------------------------------------------------
# Contract revision c4e81f6a2b57: the temporary server default is removed.
# --------------------------------------------------------------------------


def _environment_default(engine):
    column = next(
        c for c in inspect(engine).get_columns("jobs") if c["name"] == "environment"
    )
    return column["default"]


def test_contract_revision_removes_the_server_default(migration_db):
    engine, cfg = migration_db

    command.upgrade(cfg, EXPAND_REVISION)
    assert "production" in str(_environment_default(engine)), "expand default missing"

    command.upgrade(cfg, "head")
    assert _environment_default(engine) is None


def test_contract_revision_keeps_environment_not_null(migration_db):
    engine, cfg = migration_db

    command.upgrade(cfg, "head")

    column = next(
        c for c in inspect(engine).get_columns("jobs") if c["name"] == "environment"
    )
    assert column["nullable"] is False


def test_contract_revision_keeps_both_composite_unique_constraints(migration_db):
    engine, cfg = migration_db

    command.upgrade(cfg, "head")

    after = _unique_constraints(engine)
    assert after[("environment", "url")] == "uq_jobs_environment_url"
    assert after[("environment", "hash")] == "uq_jobs_environment_hash"
    assert ("url",) not in after
    assert ("hash",) not in after


def test_contract_revision_preserves_rows_indexes_and_child_table(migration_db):
    """Every column value must survive, not just the ones the revision reasons about.

    On SQLite the contract revision rebuilds the table: it copies every row into a new
    one, drops the original and renames. A column omitted from the rebuild definition
    would be lost silently, so the comparison covers all nine columns rather than a
    representative few.

    Columns are listed explicitly rather than using SELECT *, because the rebuild also
    reorders them (the contract revision's table definition places environment second,
    the expand revision's placed it last). SELECT * would compare tuples in two
    different column orders and prove nothing.
    """
    all_columns = (
        "id, environment, title, company, location, url, posted_at, description, hash"
    )
    engine, cfg = migration_db

    command.upgrade(cfg, EXPAND_REVISION)
    with engine.connect() as conn:
        before = conn.execute(text(f"SELECT {all_columns} FROM jobs ORDER BY id")).all()
        before_skills = conn.execute(
            text("SELECT job_id, skill FROM job_skills ORDER BY job_id, skill")
        ).all()
    assert before, "fixture seeded no rows, so this test would be vacuous"

    command.upgrade(cfg, "head")

    insp = inspect(engine)
    assert {idx["name"] for idx in insp.get_indexes("jobs")} >= {
        "idx_jobs_location",
        "idx_jobs_posted_at",
    }
    assert any(fk["referred_table"] == "jobs" for fk in insp.get_foreign_keys("job_skills"))

    with engine.connect() as conn:
        after = conn.execute(text(f"SELECT {all_columns} FROM jobs ORDER BY id")).all()
        after_skills = conn.execute(
            text("SELECT job_id, skill FROM job_skills ORDER BY job_id, skill")
        ).all()
    assert after == before
    assert after_skills == before_skills


def test_insert_without_environment_fails_after_contract_revision(migration_db):
    """The whole point: a missing environment must raise, not become production."""
    engine, cfg = migration_db

    command.upgrade(cfg, "head")

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("INSERT INTO jobs (title, url, hash) VALUES ('No env', 'u', 'h')"))

    with engine.connect() as conn:
        leaked = conn.execute(text("SELECT count(*) FROM jobs WHERE title = 'No env'")).scalar_one()
    assert leaked == 0


def test_application_writes_still_succeed_after_contract_revision(migration_db):
    """The application supplies environment explicitly, so nothing depends on the default."""
    from fixtures import TEST_JOB_PAYLOADS
    from sqlalchemy.orm import sessionmaker

    from jobintel.etl.raw import upsert_raw_job
    from jobintel.etl.skills import extract_skills_for_all_jobs
    from jobintel.etl.transform import transform_jobs

    engine, cfg = migration_db
    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM job_skills"))
        conn.execute(text("DELETE FROM jobs"))
        conn.commit()

    session = sessionmaker(bind=engine)()
    try:
        for payload in TEST_JOB_PAYLOADS:
            upsert_raw_job(session, payload, environment="development")
        session.commit()

        assert transform_jobs(session, environment="development") == len(TEST_JOB_PAYLOADS)
        assert extract_skills_for_all_jobs(session, environment="development") > 0

        with engine.connect() as conn:
            environments = conn.execute(
                text("SELECT DISTINCT environment FROM jobs")
            ).scalars().all()
        assert environments == ["development"]
    finally:
        session.close()


def test_contract_downgrade_restores_the_server_default(migration_db):
    engine, cfg = migration_db

    command.upgrade(cfg, "head")
    assert _environment_default(engine) is None

    command.downgrade(cfg, EXPAND_REVISION)

    assert "production" in str(_environment_default(engine))
    column = next(
        c for c in inspect(engine).get_columns("jobs") if c["name"] == "environment"
    )
    assert column["nullable"] is False
    after = _unique_constraints(engine)
    assert after[("environment", "url")] == "uq_jobs_environment_url"
    assert after[("environment", "hash")] == "uq_jobs_environment_hash"


def test_contract_upgrade_downgrade_upgrade_round_trip(migration_db):
    engine, cfg = migration_db

    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, environment, title FROM jobs ORDER BY id")).all()

    command.downgrade(cfg, EXPAND_REVISION)
    command.upgrade(cfg, "head")

    assert _environment_default(engine) is None
    column = next(
        c for c in inspect(engine).get_columns("jobs") if c["name"] == "environment"
    )
    assert column["nullable"] is False
    after = _unique_constraints(engine)
    assert after[("environment", "url")] == "uq_jobs_environment_url"
    assert after[("environment", "hash")] == "uq_jobs_environment_hash"

    with engine.connect() as conn:
        after = conn.execute(text("SELECT id, environment, title FROM jobs ORDER BY id")).all()
    assert after == rows


def test_contract_upgrade_is_rerunnable(migration_db):
    """Re-running the contract revision against a schema that already lacks the default."""
    engine, cfg = migration_db

    command.upgrade(cfg, "head")
    command.stamp(cfg, EXPAND_REVISION, purge=True)
    command.upgrade(cfg, "head")

    assert _environment_default(engine) is None
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 3
