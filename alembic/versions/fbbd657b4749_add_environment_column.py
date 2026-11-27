"""add_environment_column

Revision ID: fbbd657b4749
Revises: 9bfae4d007ee
Create Date: 2026-01-13 00:10:07.572108

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fbbd657b4749"
down_revision: str | Sequence[str] | None = "9bfae4d007ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    bind = op.get_bind()
    insp = inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def _has_index(table: str, index: str) -> bool:
    """Check if an index exists on a table."""
    bind = op.get_bind()
    insp = inspect(bind)
    indexes = [idx["name"] for idx in insp.get_indexes(table)]
    return index in indexes


def upgrade() -> None:
    """Add environment to raw_jobs and ingest_runs, backfill, set NOT NULL, index. Idempotent.

    Backfill tags the legacy 'sample' source as 'test' and everything else 'production'.
    """
    raw_idx = op.f("ix_raw_jobs_environment")
    runs_idx = op.f("ix_ingest_runs_environment")

    # raw_jobs table
    if not _has_column("raw_jobs", "environment"):
        op.add_column(
            "raw_jobs",
            sa.Column("environment", sa.String(), nullable=True, server_default="production"),
        )

        op.execute("""
            UPDATE raw_jobs
            SET environment = CASE
                WHEN source = 'sample' THEN 'test'
                ELSE 'production'
            END
            WHERE environment IS NULL
        """)

        # Batch mode: SQLite rebuilds the table to set NOT NULL; Postgres alters in place.
        with op.batch_alter_table("raw_jobs") as batch_op:
            batch_op.alter_column("environment", existing_type=sa.String(), nullable=False)

    if not _has_index("raw_jobs", raw_idx):
        op.create_index(raw_idx, "raw_jobs", ["environment"], unique=False)

    # ingest_runs table
    if not _has_column("ingest_runs", "environment"):
        op.add_column(
            "ingest_runs",
            sa.Column("environment", sa.String(), nullable=True, server_default="production"),
        )

        op.execute("""
            UPDATE ingest_runs
            SET environment = CASE
                WHEN source = 'sample' THEN 'test'
                ELSE 'production'
            END
            WHERE environment IS NULL
        """)

        with op.batch_alter_table("ingest_runs") as batch_op:
            batch_op.alter_column("environment", existing_type=sa.String(), nullable=False)

    if not _has_index("ingest_runs", runs_idx):
        op.create_index(runs_idx, "ingest_runs", ["environment"], unique=False)


def downgrade() -> None:
    """Remove environment columns (idempotent)."""
    raw_idx = op.f("ix_raw_jobs_environment")
    runs_idx = op.f("ix_ingest_runs_environment")

    if _has_index("raw_jobs", raw_idx):
        op.drop_index(raw_idx, table_name="raw_jobs")
    if _has_column("raw_jobs", "environment"):
        op.drop_column("raw_jobs", "environment")

    if _has_index("ingest_runs", runs_idx):
        op.drop_index(runs_idx, table_name="ingest_runs")
    if _has_column("ingest_runs", "environment"):
        op.drop_column("ingest_runs", "environment")
