from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class RawJob(Base):
    __tablename__ = "raw_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    posted_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    hash: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)

    skills: Mapped[list[JobSkill]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_jobs_location", "location"),
        Index("idx_jobs_posted_at", "posted_at"),
    )


class JobSkill(Base):
    __tablename__ = "job_skills"

    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    skill: Mapped[str] = mapped_column(String, primary_key=True)

    job: Mapped[Job] = relationship(back_populates="skills")

    __table_args__ = (Index("idx_job_skills_skill", "skill"),)


class IngestRun(Base):
    """Track ingest run metrics for observability."""

    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String, nullable=False, index=True)
    search: Mapped[str | None] = mapped_column(String, nullable=True)
    limit: Mapped[int | None] = mapped_column(nullable=True)

    status: Mapped[str] = mapped_column(String, nullable=False)  # running|success|failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    fetched: Mapped[int] = mapped_column(default=0)
    inserted_raw: Mapped[int] = mapped_column(default=0)
    inserted_jobs: Mapped[int] = mapped_column(default=0)
    inserted_skills: Mapped[int] = mapped_column(default=0)

    warnings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("idx_ingest_runs_started_at", "started_at"),)
