"""scope_job_identity_by_environment

Revision ID: 7d2b1a4c9f30
Revises: fbbd657b4749
Create Date: 2026-09-05 00:00:00.000000

Expand step: adds jobs.environment (existing rows backfilled to 'production', assumed
production-only) and replaces UNIQUE(url)/UNIQUE(hash) with per-environment uniques.

The temporary DEFAULT 'production' kept the previously deployed app's INSERTs working
until c4e81f6a2b57 removed it. Downgrade refuses if a URL or hash exists in more than
one environment. No application imports, so the migration never changes behavior.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d2b1a4c9f30"
down_revision: str | Sequence[str] | None = "fbbd657b4749"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UQ_ENV_URL = "uq_jobs_environment_url"
UQ_ENV_HASH = "uq_jobs_environment_hash"

# Names for SQLite batch rebuilds only: the on-disk constraints are unnamed, and
# batch mode needs a name to drop them.
_SQLITE_UQ_URL = "uq_jobs_url"
_SQLITE_UQ_HASH = "uq_jobs_hash"

ENVIRONMENT_DEFAULT = "production"


def _has_column(table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    insp = inspect(op.get_bind())
    return column in {c["name"] for c in insp.get_columns(table)}


def _is_nullable(table: str, column: str) -> bool:
    """Check whether an existing column is nullable."""
    insp = inspect(op.get_bind())
    for col in insp.get_columns(table):
        if col["name"] == column:
            return bool(col["nullable"])
    raise RuntimeError(f"Column {table}.{column} not found")


def _unique_constraint_on(table: str, columns: list[str]) -> str | None:
    """Name of the unique constraint on exactly these columns: '' if unnamed, None if absent."""
    insp = inspect(op.get_bind())
    for uc in insp.get_unique_constraints(table):
        if list(uc["column_names"]) == columns:
            return uc["name"] or ""
    return None


def _jobs_table(*, environment: str | None, uniques: str) -> sa.Table:
    """jobs as of this revision, for SQLite batch rebuilds (written out, not imported).

    environment: None, "nullable" or "not_null". uniques: "global" or "scoped".
    """
    columns: list[sa.Column] = [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("posted_at", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hash", sa.String(), nullable=True),
    ]
    if environment is not None:
        columns.append(
            sa.Column(
                "environment",
                sa.String(),
                nullable=(environment == "nullable"),
                server_default=ENVIRONMENT_DEFAULT,
            )
        )

    if uniques == "global":
        constraints: list[sa.SchemaItem] = [
            sa.UniqueConstraint("url", name=_SQLITE_UQ_URL),
            sa.UniqueConstraint("hash", name=_SQLITE_UQ_HASH),
        ]
    else:
        constraints = [
            sa.UniqueConstraint("environment", "url", name=UQ_ENV_URL),
            sa.UniqueConstraint("environment", "hash", name=UQ_ENV_HASH),
        ]

    # Declared so the rebuild recreates them.
    indexes: list[sa.SchemaItem] = [
        sa.Index("idx_jobs_location", "location"),
        sa.Index("idx_jobs_posted_at", "posted_at"),
    ]

    return sa.Table("jobs", sa.MetaData(), *columns, *constraints, *indexes)


def _duplicate_count(column: str) -> int:
    """Count values of a jobs column that appear on more than one row."""
    if column not in ("url", "hash"):
        raise ValueError(f"Unsupported column: {column}")

    sql = (
        f"SELECT count(*) FROM ("
        f" SELECT {column} FROM jobs WHERE {column} IS NOT NULL"
        f" GROUP BY {column} HAVING count(*) > 1"
        f") t"
    )
    return op.get_bind().execute(sa.text(sql)).scalar_one()


def upgrade() -> None:
    """Add jobs.environment and scope URL/hash uniqueness to it."""
    if not _has_column("jobs", "environment"):
        op.add_column(
            "jobs",
            sa.Column(
                "environment",
                sa.String(),
                nullable=True,
                server_default=ENVIRONMENT_DEFAULT,
            ),
        )

    # Idempotent; also covers a partial earlier run.
    op.execute(f"UPDATE jobs SET environment = '{ENVIRONMENT_DEFAULT}' WHERE environment IS NULL")

    already_scoped = (
        not _is_nullable("jobs", "environment")
        and _unique_constraint_on("jobs", ["environment", "url"]) is not None
        and _unique_constraint_on("jobs", ["environment", "hash"]) is not None
        and _unique_constraint_on("jobs", ["url"]) is None
        and _unique_constraint_on("jobs", ["hash"]) is None
    )
    if already_scoped:
        return

    if op.get_bind().dialect.name == "sqlite":
        # SQLite cannot alter constraints in place; batch mode rebuilds the table.
        with op.batch_alter_table(
            "jobs",
            copy_from=_jobs_table(environment="nullable", uniques="global"),
        ) as batch_op:
            batch_op.alter_column(
                "environment",
                existing_type=sa.String(),
                existing_server_default=ENVIRONMENT_DEFAULT,
                nullable=False,
            )
            batch_op.drop_constraint(_SQLITE_UQ_URL, type_="unique")
            batch_op.drop_constraint(_SQLITE_UQ_HASH, type_="unique")
            batch_op.create_unique_constraint(UQ_ENV_URL, ["environment", "url"])
            batch_op.create_unique_constraint(UQ_ENV_HASH, ["environment", "hash"])
        return

    op.alter_column(
        "jobs",
        "environment",
        existing_type=sa.String(),
        existing_nullable=True,
        nullable=False,
    )

    # The database named the originals (jobs_url_key on Postgres); look them up.
    for columns in (["url"], ["hash"]):
        name = _unique_constraint_on("jobs", columns)
        if name:
            op.drop_constraint(name, "jobs", type_="unique")

    if _unique_constraint_on("jobs", ["environment", "url"]) is None:
        op.create_unique_constraint(UQ_ENV_URL, "jobs", ["environment", "url"])
    if _unique_constraint_on("jobs", ["environment", "hash"]) is None:
        op.create_unique_constraint(UQ_ENV_HASH, "jobs", ["environment", "hash"])


def downgrade() -> None:
    """Restore global uniques and drop jobs.environment; refuse on cross-environment duplicates."""
    if not _has_column("jobs", "environment"):
        return

    for column in ("url", "hash"):
        duplicates = _duplicate_count(column)
        if duplicates:
            raise RuntimeError(
                f"Cannot downgrade: {duplicates} {column} value(s) appear on more than "
                "one jobs row, so the global UNIQUE constraint this downgrade restores "
                "cannot be satisfied. Reconcile or remove the cross-environment rows "
                "first; this migration will not discard them for you."
            )

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            "jobs",
            copy_from=_jobs_table(environment="not_null", uniques="scoped"),
        ) as batch_op:
            batch_op.drop_constraint(UQ_ENV_URL, type_="unique")
            batch_op.drop_constraint(UQ_ENV_HASH, type_="unique")
            batch_op.drop_column("environment")
            # Named here (batch mode requires it); Postgres restores them unnamed below.
            batch_op.create_unique_constraint(_SQLITE_UQ_URL, ["url"])
            batch_op.create_unique_constraint(_SQLITE_UQ_HASH, ["hash"])
        return

    for name in (UQ_ENV_URL, UQ_ENV_HASH):
        if _unique_constraint_on("jobs", ["environment", "url"]) == name or _unique_constraint_on(
            "jobs", ["environment", "hash"]
        ) == name:
            op.drop_constraint(name, "jobs", type_="unique")

    op.drop_column("jobs", "environment")

    # Unnamed, so the database picks its original name.
    if _unique_constraint_on("jobs", ["url"]) is None:
        op.create_unique_constraint(None, "jobs", ["url"])
    if _unique_constraint_on("jobs", ["hash"]) is None:
        op.create_unique_constraint(None, "jobs", ["hash"])
