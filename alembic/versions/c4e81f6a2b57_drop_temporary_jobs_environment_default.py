"""drop_temporary_jobs_environment_default

Revision ID: c4e81f6a2b57
Revises: 7d2b1a4c9f30
Create Date: 2026-09-06 00:00:00.000000

Contract step of the expand/use/contract rollout, and the last one.

Revision 7d2b1a4c9f30 added `jobs.environment` with a database-only
`DEFAULT 'production'`. That default existed for one reason: during the window
between migrating the database and deploying the application, the running code did
not know the column existed, so its INSERTs omitted it and the default kept them
working. That application is deployed now and sets `environment` explicitly on
every job it writes, so the default has no remaining job to do.

Leaving it would be actively harmful. A permanent `DEFAULT 'production'` on the one
column the environment invariant rests on means any future write path that forgets
to supply an environment is silently labelled production rather than failing. That
is precisely the class of defect the invariant exists to prevent, and 'production'
is the most damaging value it could quietly choose. After this revision the
database supplies no fallback and a missing environment raises.

What this revision must preserve, whatever a given dialect does physically:

- `NOT NULL` on `jobs.environment`
- `uq_jobs_environment_url` and `uq_jobs_environment_hash`
- every row, and every column value in it
- `idx_jobs_location`, `idx_jobs_posted_at`, and the `job_skills` foreign key
- environment semantics; no stored value is changed

The downgrade restores `DEFAULT 'production'`, which returns the schema to the
state the deployed application ran against during the expand window.

Dialect handling, and the two dialects are not equivalent in what they physically
do. PostgreSQL, which is what production runs, drops the default in place with a
single `ALTER TABLE ... ALTER COLUMN ... DROP DEFAULT`: no row is read or rewritten
and no index is rebuilt.

SQLite has no such statement, so the default can only be removed by rebuilding the
table. `op.batch_alter_table` with an explicit `copy_from` creates a new table,
copies every row into it, drops the original and renames the replacement. Rows are
therefore physically read and rewritten and the indexes are recreated. What is
guaranteed on SQLite is preservation of values, constraints, index definitions and
foreign key semantics, not that the storage is left untouched.

The definition below omits the server default so the rebuilt table has none, and
restates every column, constraint and index, because the rebuild recreates the
table from that definition alone: anything left out would be silently lost.

The jobs table is written out here rather than imported from `jobintel.models`.
Migrations have to keep behaving the same way years from now, which they cannot do
if they read a model that is free to change underneath them.
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

# The temporary default this revision removes, and that its downgrade restores.
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
    """Standalone definition of `jobs`, for SQLite batch rebuilds.

    Written out rather than reflected or imported so this revision keeps describing
    the table as it was when the revision was authored.
    """
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
        # SQLite cannot alter a column default in place. Batch mode rebuilds the
        # table from the definition below, which carries the wanted default (or
        # none) and restates every constraint and index so the rebuild keeps them.
        # recreate="always" is required: the block issues no operations of its own,
        # and the default "auto" would decide no rebuild was needed and do nothing.
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
