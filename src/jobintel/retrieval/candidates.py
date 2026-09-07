"""SQL side of retrieval: hard filters, candidate streaming, and top-k hydration.

Only genuine hard filters reach SQL. Nothing here selects on relevance, and nothing
truncates the candidate set, so no job can be dropped before it has been scored. In
particular recency is never used to pre-select candidates: a highly relevant older
job must be able to outrank a pile of newer irrelevant ones.

No query is ever issued per candidate. A retrieval costs:
  1. one streamed query for the candidate rows
  2. skills, loaded conditionally by the service:
       ceil(candidates / ID_BATCH) queries when preferred_skills ranks something,
       otherwise ceil(returned / ID_BATCH) for the top-k alone
  3. ceil(returned / ID_BATCH) queries hydrating only the top-k

The query count is bounded by the number of ID_BATCH-sized batches rather than by the
number of candidates, so it is not a fixed number: each further batch adds one query.
Which of the two skill paths applies is decided in service.retrieve(); load_skills()
itself simply batches whatever ids it is given.
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

# How many ids per IN clause when loading skills or hydrating. SQLite has a bound
# variable limit that older builds set as low as 999, so batches stay well under it
# and the same code path works on PostgreSQL unchanged.
ID_BATCH = 400


@dataclass(frozen=True)
class CandidateRow:
    """The columns scoring needs, and nothing else.

    `description` is carried only for the duration of scoring one row; the service
    discards it immediately rather than holding every description in memory while
    ranking the whole corpus.
    """

    job_id: int
    environment: str
    title: str
    company: str | None
    location: str | None
    posted_at: date | None
    job_hash: str | None
    description: str | None


def _hard_filters(query: RetrievalQuery, today: date) -> list[ColumnElement[bool]]:
    """Every condition that decides candidacy. None of these affect the score."""
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
        # One correlated EXISTS per required skill, AND-ed: the job must have all.
        conditions.append(
            select(JobSkill.job_id)
            .where(JobSkill.job_id == Job.id, JobSkill.skill == skill)
            .exists()
        )

    return conditions


def _apply_source_filter(stmt: Select, query: RetrievalQuery) -> Select:
    """Restrict to jobs whose authoritative raw row carries one of these sources.

    Uses the established rule (same environment, matching URL, lowest RawJob.id) via
    the shared subquery, so a job is matched to exactly one raw row and the join
    cannot fan a job out into several result rows.
    """
    if not query.sources:
        return stmt
    raw = authoritative_raw_jobs()
    return stmt.join(raw, job_raw_onclause(raw)).where(raw.c.source.in_(query.sources))


def iter_candidates(
    session: Session, query: RetrievalQuery, *, today: date
) -> Iterator[CandidateRow]:
    """Stream every job passing the hard filters. No LIMIT, no ORDER BY.

    Ordering is established entirely by the Python sort, so results cannot depend on
    the order the database happened to return rows in. Streaming keeps descriptions
    from accumulating: the service scores each row and drops it.
    """
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
    """Every skill for these jobs, in batched IN queries rather than one per job.

    Returns only jobs that have at least one skill, so a missing key means the job
    has no extracted skills at all, which is what the skills component reports as
    `present = False`.
    """
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
    """Fetch the full fields for the returned jobs only, after ranking.

    Source comes through the same authoritative rule as filtering, but as an OUTER
    join: a job whose raw row is not resolvable keeps `source = None` rather than
    being dropped. The subquery yields one row per (environment, url), so this cannot
    duplicate a job.
    """
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
    """How many rows passed the hard filters, for the record."""
    stmt = select(func.count()).select_from(Job).where(and_(*_hard_filters(query, today)))
    stmt = _apply_source_filter(stmt, query)
    return int(session.execute(stmt).scalar_one())
