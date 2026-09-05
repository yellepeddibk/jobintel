"""scope_job_identity_by_environment

Revision ID: 7d2b1a4c9f30
Revises: fbbd657b4749
Create Date: 2026-09-05 00:00:00.000000

Expand step of an expand/use/contract rollout.

This revision widens the `jobs` schema so that normalized job identity is scoped to
an environment instead of being global. It deliberately changes no application
behavior: the currently deployed application keeps working against the migrated
schema unchanged, which is what lets the migration reach production before the
application code that depends on it.

What changes:

1. `jobs.environment` is added and every existing row is set to 'production'.
2. `UNIQUE(url)` and `UNIQUE(hash)` are replaced by `UNIQUE(environment, url)` and
   `UNIQUE(environment, hash)`.

Backfill basis. This is a stated assumption, not a verified fact: the production
database is believed to have held production data only, because local development
has used a separate local database and there is no known reason development or test
data would have been written to it. No production database was inspected to confirm
that. Every existing row is therefore backfilled to 'production'.

Why the column carries a server default that the ORM model does not declare.
The deployed application at the time this revision is applied does not know about
`jobs.environment`, so its INSERTs omit the column. The database-only
`DEFAULT 'production'` keeps those INSERTs working during the window between
applying this migration and deploying the application code that sets the value
explicitly. The default is intentionally absent from `src/jobintel/models.py`, so
that `create_all()` in tests produces a column with no default and any code path
that forgets to supply an environment fails loudly instead of silently being
labelled 'production'. The production schema is therefore ahead of the ORM model
for the length of the transition, on purpose. A later contract revision removes the
server default once the new application code is deployed and verified, which
restores agreement between the model and the database.

Relaxing uniqueness ahead of the application is safe. `transform_jobs()` in the
deployed version deduplicates by URL and hash in Python across the whole table, so
it is strictly stricter than either constraint and cannot produce rows that the
narrower constraints would have rejected.

Limitation this revision does not address: environment-scoped identity is enforced,
but there is no database-enforced pointer from a `jobs` row to the `raw_jobs` row it
was normalized from. A `raw_job_id` foreign key is deferred as a separate decision.

Downgrade is conditional. Restoring global `UNIQUE(url)` and `UNIQUE(hash)` is only
possible while no URL or hash is present under more than one environment. The
downgrade checks first and refuses with a clear error rather than failing on a raw
duplicate key or discarding rows.

No application module is imported here. Migrations must keep behaving the same way
years from now, which they cannot do if they call into code that is free to change.
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

# Names used only inside SQLite batch rebuilds. The real constraints on disk are
# unnamed (SQLAlchemy emits a bare "UNIQUE (url)"), and SQLite cannot drop an
# unnamed constraint in place. Batch mode rebuilds the table from the Python
# definition supplied as copy_from, so naming them there gives drop_constraint
# something to address without those names ever reaching the database.
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
    """Return the name of the unique constraint over exactly these columns.

    Returns an empty string when the constraint exists but is unnamed, which is how
    SQLite reports the inline "UNIQUE (url)" form. Returns None when absent.
    """
    insp = inspect(op.get_bind())
    for uc in insp.get_unique_constraints(table):
        if list(uc["column_names"]) == columns:
            return uc["name"] or ""
    return None


def _jobs_table(*, environment: str | None, uniques: str) -> sa.Table:
    """Standalone definition of jobs, for SQLite batch rebuilds.

    Written out rather than reflected or imported from the ORM so that this
    revision keeps describing the table as it was when the revision was authored.

    environment: None for absent, "nullable", or "not_null".
    uniques: "global" for UNIQUE(url)/UNIQUE(hash), "scoped" for the composite pair.
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

    # Declared so the rebuild recreates them; they are dropped with the old table.
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

    # Idempotent, and covers a column added by an earlier partial run without a default.
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
        # SQLite cannot drop an inline UNIQUE or set NOT NULL in place; batch mode
        # rebuilds the table from the definition below and copies the rows over.
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

    # The originals are unnamed in the model, so the database picked the name
    # (jobs_url_key on PostgreSQL). Look it up rather than assume it.
    for columns in (["url"], ["hash"]):
        name = _unique_constraint_on("jobs", columns)
        if name:
            op.drop_constraint(name, "jobs", type_="unique")

    if _unique_constraint_on("jobs", ["environment", "url"]) is None:
        op.create_unique_constraint(UQ_ENV_URL, "jobs", ["environment", "url"])
    if _unique_constraint_on("jobs", ["environment", "hash"]) is None:
        op.create_unique_constraint(UQ_ENV_HASH, "jobs", ["environment", "hash"])


def downgrade() -> None:
    """Restore global URL/hash uniqueness and drop jobs.environment.

    Refuses when the data can no longer satisfy the global constraints, which is the
    case as soon as two environments hold the same URL or hash.
    """
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
            # Batch mode needs a name here, so the rebuilt SQLite table ends up with
            # named constraints where the original had bare "UNIQUE (url)". Same
            # columns, same enforcement, different DDL text. PostgreSQL, which is
            # what production runs, takes the unnamed path below and is restored
            # exactly.
            batch_op.create_unique_constraint(_SQLITE_UQ_URL, ["url"])
            batch_op.create_unique_constraint(_SQLITE_UQ_HASH, ["hash"])
        return

    for name in (UQ_ENV_URL, UQ_ENV_HASH):
        if _unique_constraint_on("jobs", ["environment", "url"]) == name or _unique_constraint_on(
            "jobs", ["environment", "hash"]
        ) == name:
            op.drop_constraint(name, "jobs", type_="unique")

    op.drop_column("jobs", "environment")

    # Recreated unnamed, letting the database choose the name it used originally.
    if _unique_constraint_on("jobs", ["url"]) is None:
        op.create_unique_constraint(None, "jobs", ["url"])
    if _unique_constraint_on("jobs", ["hash"]) is None:
        op.create_unique_constraint(None, "jobs", ["hash"])
