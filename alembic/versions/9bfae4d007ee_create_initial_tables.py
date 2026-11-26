"""create_initial_tables

Revision ID: 9bfae4d007ee
Revises:
Create Date: 2026-11-26 00:00:00.000000

Baseline revision: the four tables as they existed before Alembic was introduced.

The tables were first created with `create_all()`, and the first Alembic revision,
fbbd657b4749, was written to alter those existing tables. Nothing in the chain created
them, so `alembic upgrade head` failed on an empty database with NoSuchTableError.
This revision is now the root of the chain and creates them, so an empty database can
be built by migrations alone.

The definitions are the schema `create_all()` produced from `jobintel.models` at
commit 772d6e6^, immediately before fbbd657b4749 was added: no `environment` column
anywhere, and global `UNIQUE(url)` and `UNIQUE(hash)` on `jobs`, left unnamed as
`unique=True` emitted them. The later revisions find those constraints by their
columns, not their names, so this matches what they expect on both dialects.

Each table is created only if it does not already exist. A database that already has
the tables, whether or not it was ever stamped, is left exactly as it was, which is
the situation fbbd657b4749 was written for.

Databases already stamped at a later revision are unaffected: Alembic treats every
ancestor of the stamped revision as applied and never runs this one against them.

Downgrade refuses. Because upgrade skips tables that already exist, this revision may
not have created the tables it would drop, and dropping them would delete every row.

No application module is imported here. Migrations must keep behaving the same way
years from now, which they cannot do if they call into code that is free to change.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9bfae4d007ee"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the pre-Alembic tables that do not already exist."""
    if not _has_table("raw_jobs"):
        op.create_table(
            "raw_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column(
                "ingested_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    if not _has_table("jobs"):
        op.create_table(
            "jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("company", sa.String(), nullable=True),
            sa.Column("location", sa.String(), nullable=True),
            sa.Column("url", sa.String(), nullable=True, unique=True),
            sa.Column("posted_at", sa.Date(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("hash", sa.String(), nullable=True, unique=True),
        )
        op.create_index("idx_jobs_location", "jobs", ["location"])
        op.create_index("idx_jobs_posted_at", "jobs", ["posted_at"])

    if not _has_table("job_skills"):
        op.create_table(
            "job_skills",
            sa.Column(
                "job_id",
                sa.Integer(),
                sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("skill", sa.String(), primary_key=True),
        )
        op.create_index("idx_job_skills_skill", "job_skills", ["skill"])

    if not _has_table("ingest_runs"):
        op.create_table(
            "ingest_runs",
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
        )
        op.create_index(op.f("ix_ingest_runs_source"), "ingest_runs", ["source"])
        op.create_index("idx_ingest_runs_started_at", "ingest_runs", ["started_at"])


def downgrade() -> None:
    """Refuse: this revision cannot know whether it created the tables."""
    raise RuntimeError(
        "Refusing to downgrade past the baseline revision 9bfae4d007ee: it would drop "
        "raw_jobs, jobs, job_skills and ingest_runs and every row in them, and those "
        "tables may predate this revision."
    )
