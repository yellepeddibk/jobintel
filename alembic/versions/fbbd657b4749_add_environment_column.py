"""add_environment_column

Revision ID: fbbd657b4749
Revises:
Create Date: 2026-01-13 00:10:07.572108

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fbbd657b4749"
down_revision: str | Sequence[str] | None = None
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
    """Add environment column with safe migration for existing data.

    Strategy:
    1. Check if column already exists (idempotent)
    2. Add column as nullable with server_default
    3. Backfill existing rows (legacy: 'sample' source → 'test', others → 'production')
    4. Make column NOT NULL
    5. Create index

    Note: The 'sample' source handling is legacy behavior from when sample data
    existed in the project. Sample data has since been removed, but this migration
    preserves the original backfill logic for historical database compatibility.
    """
    # Compute index names once for consistent checks
    raw_idx = op.f("ix_raw_jobs_environment")
    runs_idx = op.f("ix_ingest_runs_environment")

    # raw_jobs table
    if not _has_column("raw_jobs", "environment"):
        op.add_column(
            "raw_jobs",
            sa.Column("environment", sa.String(), nullable=True, server_default="production"),
        )

        # Backfill: tag 'sample' source as 'test' environment, all others as 'production'
        op.execute("""
            UPDATE raw_jobs
            SET environment = CASE
                WHEN source = 'sample' THEN 'test'
                ELSE 'production'
            END
            WHERE environment IS NULL
        """)

        op.alter_column("raw_jobs", "environment", nullable=False)

    if not _has_index("raw_jobs", raw_idx):
        op.create_index(raw_idx, "raw_jobs", ["environment"], unique=False)

    # ingest_runs table
    if not _has_column("ingest_runs", "environment"):
        op.add_column(
            "ingest_runs",
            sa.Column("environment", sa.String(), nullable=True, server_default="production"),
        )

        # Backfill: tag 'sample' source as 'test' environment, all others as 'production'
        op.execute("""
            UPDATE ingest_runs
            SET environment = CASE
                WHEN source = 'sample' THEN 'test'
                ELSE 'production'
            END
            WHERE environment IS NULL
        """)

        op.alter_column("ingest_runs", "environment", nullable=False)

    if not _has_index("ingest_runs", runs_idx):
        op.create_index(runs_idx, "ingest_runs", ["environment"], unique=False)


def downgrade() -> None:
    """Remove environment columns (idempotent)."""
    # Compute index names once for consistent checks
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
