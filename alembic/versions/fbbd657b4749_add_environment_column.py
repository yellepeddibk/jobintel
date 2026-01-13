"""add_environment_column

Revision ID: fbbd657b4749
Revises:
Create Date: 2026-01-13 00:10:07.572108

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fbbd657b4749"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add environment column with safe migration for existing data.

    Strategy:
    1. Add column as nullable with server_default
    2. Backfill existing rows: 'sample' source → 'test', others → 'production'
    3. Make column NOT NULL
    4. Create index
    """
    # Add environment column to raw_jobs (nullable initially)
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

    # Make column NOT NULL and add index
    op.alter_column("raw_jobs", "environment", nullable=False)
    op.create_index(op.f("ix_raw_jobs_environment"), "raw_jobs", ["environment"], unique=False)

    # Add environment column to ingest_runs (nullable initially)
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

    # Make column NOT NULL and add index
    op.alter_column("ingest_runs", "environment", nullable=False)
    op.create_index(
        op.f("ix_ingest_runs_environment"), "ingest_runs", ["environment"], unique=False
    )


def downgrade() -> None:
    """Remove environment columns."""
    op.drop_index(op.f("ix_raw_jobs_environment"), table_name="raw_jobs")
    op.drop_column("raw_jobs", "environment")
    op.drop_index(op.f("ix_ingest_runs_environment"), table_name="ingest_runs")
    op.drop_column("ingest_runs", "environment")
