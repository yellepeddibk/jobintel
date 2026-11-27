"""drop_temporary_jobs_environment_default

Revision ID: c4e81f6a2b57
Revises: 7d2b1a4c9f30
Create Date: 2026-09-06 00:00:00.000000

Contract step: removes the temporary DEFAULT 'production' from jobs.environment, so a
write that omits the environment fails instead of being labelled production.

Preserves NOT NULL, both per-environment uniques, every row, and all indexes.
Postgres drops the default in place; SQLite rebuilds the table from the definition
below. Downgrade restores the default.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e81f6a2b57"
down_revision: str | Sequence[str] | None = "7d2b1a4c9f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UQ_ENV_URL = "uq_jobs_environment_url"
UQ_ENV_HASH = "uq_jobs_environment_hash"

ENVIRONMENT_DEFAULT = "production"


def _environment_column() -> dict:
    """Reflect the jobs.environment column."""
    for col in inspect(op.get_bind()).get_columns("jobs"):
        if col["name"] == "environment":
            return col
    raise RuntimeError("jobs.environment not found; revision 7d2b1a4c9f30 must run first")


def _has_server_default() -> bool:
    default = _environment_column()["default"]
    return default is not None and ENVIRONMENT_DEFAULT in str(default)


def _jobs_table(*, with_default: bool) -> sa.Table:
    """jobs as of this revision, for SQLite batch rebuilds (written out, not imported)."""
    return sa.Table(
        "jobs",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "environment",
            sa.String(),
            nullable=False,
            server_default=ENVIRONMENT_DEFAULT if with_default else None,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("posted_at", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hash", sa.String(), nullable=True),
        sa.UniqueConstraint("environment", "url", name=UQ_ENV_URL),
        sa.UniqueConstraint("environment", "hash", name=UQ_ENV_HASH),
        sa.Index("idx_jobs_location", "location"),
        sa.Index("idx_jobs_posted_at", "posted_at"),
    )


def _set_default(*, to_default: str | None) -> None:
    """Add or remove the server default, whichever the dialect requires."""
    if op.get_bind().dialect.name == "sqlite":
        # SQLite can't alter a default in place. recreate="always" because the block
        # has no operations, so "auto" would skip the rebuild.
        with op.batch_alter_table(
            "jobs",
            copy_from=_jobs_table(with_default=to_default is not None),
            recreate="always",
        ):
            pass
        return

    op.alter_column(
        "jobs",
        "environment",
        existing_type=sa.String(),
        existing_nullable=False,
        server_default=to_default,
    )


def upgrade() -> None:
    """Remove the temporary DEFAULT 'production' from jobs.environment."""
    if not _has_server_default():
        return
    _set_default(to_default=None)


def downgrade() -> None:
    """Restore the temporary DEFAULT 'production' on jobs.environment."""
    if _has_server_default():
        return
    _set_default(to_default=ENVIRONMENT_DEFAULT)
