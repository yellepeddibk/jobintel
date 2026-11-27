"""drop_raw_and_run_environment_defaults

Revision ID: 2e3a197ea25f
Revises: c4e81f6a2b57
Create Date: 2026-11-26 00:00:00.000000

Removes DEFAULT 'production' from raw_jobs.environment and ingest_runs.environment
(left by fbbd657b4749), as c4e81f6a2b57 did for jobs, so a missing environment fails.

Preserves NOT NULL, rows, indexes and the ingested_at default. Postgres drops the
defaults in place; SQLite rebuilds from the definitions below. Both directions are
idempotent; downgrade restores the defaults.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2e3a197ea25f"
down_revision: str | Sequence[str] | None = "c4e81f6a2b57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENVIRONMENT_DEFAULT = "production"
TABLES = ("raw_jobs", "ingest_runs")


def _has_server_default(table: str) -> bool:
    for col in inspect(op.get_bind()).get_columns(table):
        if col["name"] == "environment":
            default = col["default"]
            return default is not None and ENVIRONMENT_DEFAULT in str(default)
    raise RuntimeError(f"{table}.environment not found; revision fbbd657b4749 must run first")


def _environment_column(*, with_default: bool) -> sa.Column:
    return sa.Column(
        "environment",
        sa.String(),
        nullable=False,
        server_default=ENVIRONMENT_DEFAULT if with_default else None,
    )


def _raw_jobs_table(*, with_default: bool) -> sa.Table:
    """Standalone definition of `raw_jobs`, for SQLite batch rebuilds."""
    return sa.Table(
        "raw_jobs",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        _environment_column(with_default=with_default),
        sa.Index("ix_raw_jobs_environment", "environment"),
    )


def _ingest_runs_table(*, with_default: bool) -> sa.Table:
    """Standalone definition of `ingest_runs`, for SQLite batch rebuilds."""
    return sa.Table(
        "ingest_runs",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("search", sa.String(), nullable=True),
        sa.Column("limit", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched", sa.Integer(), nullable=False),
        sa.Column("inserted_raw", sa.Integer(), nullable=False),
        sa.Column("inserted_jobs", sa.Integer(), nullable=False),
        sa.Column("inserted_skills", sa.Integer(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _environment_column(with_default=with_default),
        sa.Index("ix_ingest_runs_source", "source"),
        sa.Index("ix_ingest_runs_environment", "environment"),
        sa.Index("idx_ingest_runs_started_at", "started_at"),
    )


_DEFINITIONS = {"raw_jobs": _raw_jobs_table, "ingest_runs": _ingest_runs_table}


def _set_default(table: str, *, to_default: str | None) -> None:
    """Add or remove the server default, whichever the dialect requires."""
    if op.get_bind().dialect.name == "sqlite":
        # recreate="always": the block has no operations, so "auto" would skip it.
        with op.batch_alter_table(
            table,
            copy_from=_DEFINITIONS[table](with_default=to_default is not None),
            recreate="always",
        ):
            pass
        return

    op.alter_column(
        table,
        "environment",
        existing_type=sa.String(),
        existing_nullable=False,
        server_default=to_default,
    )


def upgrade() -> None:
    """Remove DEFAULT 'production' from raw_jobs.environment and ingest_runs.environment."""
    for table in TABLES:
        if _has_server_default(table):
            _set_default(table, to_default=None)


def downgrade() -> None:
    """Restore DEFAULT 'production' on raw_jobs.environment and ingest_runs.environment."""
    for table in TABLES:
        if not _has_server_default(table):
            _set_default(table, to_default=ENVIRONMENT_DEFAULT)
