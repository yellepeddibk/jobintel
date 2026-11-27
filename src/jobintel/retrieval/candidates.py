"""SQL side of retrieval: hard filters, candidate streaming, and top-k hydration.

SQL only filters; it never truncates or orders, so every candidate is scored.
Queries are batched by ID_BATCH, never issued per candidate.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.orm import Session

from jobintel.analytics.queries import authoritative_raw_jobs, job_raw_onclause
from jobintel.models import Job, JobSkill
from jobintel.retrieval.contracts import RetrievalQuery
from jobintel.retrieval.scoring import REMOTE_TOKENS

ID_BATCH = 400  # ids per IN clause; under SQLite's old 999-variable limit


@dataclass(frozen=True)
class CandidateRow:
    """Columns scoring needs. The service drops `description` right after scoring."""

    job_id: int
    environment: str
    title: str
    company: str | None
    location: str | None
    posted_at: date | None
    job_hash: str | None
    description: str | None


def _hard_filters(query: RetrievalQuery, today: date) -> list[ColumnElement[bool]]:
    """Conditions that decide candidacy; none affect the score."""
    conditions: list[ColumnElement[bool]] = [Job.environment == query.environment]

    if query.required_location:
        conditions.append(Job.location.ilike(f"%{query.required_location}%"))

    if query.remote_only:
        conditions.append(
            or_(*[Job.location.ilike(f"%{token}%") for token in REMOTE_TOKENS])
        )

    if query.posted_within_days is not None:
        cutoff = today - timedelta(days=query.posted_within_days)
        conditions.append(Job.posted_at.isnot(None))
        conditions.append(Job.posted_at >= cutoff)

    for skill in query.required_skills:
        # One EXISTS per required skill: the job must have all of them.
        conditions.append(
            select(JobSkill.job_id)
            .where(JobSkill.job_id == Job.id, JobSkill.skill == skill)
            .exists()
        )

    return conditions


def _apply_source_filter(stmt: Select, query: RetrievalQuery) -> Select:
    """Keep jobs whose authoritative raw row has one of the requested sources."""
    if not query.sources:
        return stmt
    raw = authoritative_raw_jobs()
    return stmt.join(raw, job_raw_onclause(raw)).where(raw.c.source.in_(query.sources))


def iter_candidates(
    session: Session, query: RetrievalQuery, *, today: date
) -> Iterator[CandidateRow]:
    """Stream every job passing the hard filters. No LIMIT or ORDER BY; Python sorts."""
    stmt = select(
        Job.id, Job.environment, Job.title, Job.company,
        Job.location, Job.posted_at, Job.hash, Job.description,
    ).where(and_(*_hard_filters(query, today)))
    stmt = _apply_source_filter(stmt, query)

    for row in session.execute(stmt).yield_per(500):
        yield CandidateRow(
            job_id=row.id,
            environment=row.environment,
            title=row.title,
            company=row.company,
            location=row.location,
            posted_at=row.posted_at,
            job_hash=row.hash,
            description=row.description,
        )


def load_skills(session: Session, job_ids: list[int]) -> dict[int, frozenset[str]]:
    """Skills per job, batched. Jobs without skills are absent from the result."""
    collected: dict[int, set[str]] = {}
    for start in range(0, len(job_ids), ID_BATCH):
        batch = job_ids[start : start + ID_BATCH]
        rows = session.execute(
            select(JobSkill.job_id, JobSkill.skill).where(JobSkill.job_id.in_(batch))
        ).all()
        for job_id, skill in rows:
            collected.setdefault(job_id, set()).add(skill)
    return {job_id: frozenset(skills) for job_id, skills in collected.items()}


def hydrate(
    session: Session, job_ids: list[int], environment: str
) -> dict[int, dict]:
    """Full fields for the returned jobs. Outer join: unresolved source stays None."""
    if not job_ids:
        return {}

    raw = authoritative_raw_jobs()
    hydrated: dict[int, dict] = {}
    for start in range(0, len(job_ids), ID_BATCH):
        batch = job_ids[start : start + ID_BATCH]
        stmt = (
            select(
                Job.id, Job.environment, Job.title, Job.company, Job.location,
                Job.url, Job.posted_at, Job.description, raw.c.source,
            )
            .outerjoin(raw, job_raw_onclause(raw))
            .where(Job.id.in_(batch), Job.environment == environment)
        )
        for row in session.execute(stmt).all():
            hydrated[row.id] = {
                "environment": row.environment,
                "title": row.title,
                "company": row.company,
                "location": row.location,
                "url": row.url,
                "posted_at": row.posted_at,
                "description": row.description,
                "source": row.source,
            }
    return hydrated


def count_candidates(session: Session, query: RetrievalQuery, *, today: date) -> int:
    """Number of rows passing the hard filters."""
    stmt = select(func.count()).select_from(Job).where(and_(*_hard_filters(query, today)))
    stmt = _apply_source_filter(stmt, query)
    return int(session.execute(stmt).scalar_one())
