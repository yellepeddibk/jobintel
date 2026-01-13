"""Analytics queries for the JobIntel dashboard.

All functions return plain Python structures for easy testing and caching.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from jobintel.models import IngestRun, Job, JobSkill, RawJob


def _base_job_query(
    session: Session,
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
):
    """Build base query for jobs with optional filters.

    Returns a query that selects Job with optional RawJob join for source filtering.
    """
    # We need to join RawJob to get source info
    url_expr = RawJob.payload_json["url"].as_string()
    q = session.query(Job).outerjoin(RawJob, url_expr == Job.url)

    if source:
        q = q.filter(RawJob.source == source)

    if date_from:
        q = q.filter(Job.posted_at >= date_from)

    if date_to:
        q = q.filter(Job.posted_at <= date_to)

    if search:
        like = f"%{search}%"
        q = q.filter(Job.title.ilike(like) | Job.company.ilike(like) | Job.location.ilike(like))

    return q


def get_kpis(
    session: Session,
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
) -> dict:
    """Get key performance indicators for the dashboard.

    Returns:
        dict with keys: total_jobs, jobs_last_7d, unique_companies, sources_count
    """
    # Total jobs with filters
    base_q = _base_job_query(session, source, date_from, date_to, search)
    total_jobs = base_q.count()

    # Jobs in last 7 days (ignoring other date filters for this metric)
    seven_days_ago = date.today() - timedelta(days=7)
    jobs_7d_q = _base_job_query(session, source, seven_days_ago, None, search)
    jobs_last_7d = jobs_7d_q.count()

    # Unique companies
    companies_q = _base_job_query(session, source, date_from, date_to, search)
    unique_companies = (
        companies_q.with_entities(func.count(distinct(Job.company)))
        .filter(Job.company.isnot(None))
        .scalar()
        or 0
    )

    # Number of sources (from successful ingest runs)
    sources_count = (
        session.execute(
            select(func.count(distinct(IngestRun.source))).where(IngestRun.status == "success")
        ).scalar()
        or 0
    )

    return {
        "total_jobs": total_jobs,
        "jobs_last_7d": jobs_last_7d,
        "unique_companies": unique_companies,
        "sources_count": sources_count,
    }


def get_top_skills(
    session: Session,
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    limit: int = 20,
) -> list[tuple[str, int]]:
    """Get top skills by number of distinct jobs mentioning them.

    Uses COUNT(DISTINCT job_id) to avoid over-counting from duplicates.
    """
    url_expr = RawJob.payload_json["url"].as_string()

    q = (
        session.query(JobSkill.skill, func.count(distinct(JobSkill.job_id)).label("n"))
        .join(Job, Job.id == JobSkill.job_id)
        .outerjoin(RawJob, url_expr == Job.url)
    )

    if source:
        q = q.filter(RawJob.source == source)

    if date_from:
        q = q.filter(Job.posted_at >= date_from)

    if date_to:
        q = q.filter(Job.posted_at <= date_to)

    if search:
        like = f"%{search}%"
        q = q.filter(Job.title.ilike(like) | Job.company.ilike(like) | Job.location.ilike(like))

    q = (
        q.group_by(JobSkill.skill)
        .order_by(func.count(distinct(JobSkill.job_id)).desc())
        .limit(limit)
    )

    return [(skill, int(n)) for skill, n in q.all()]


def get_skill_trends(
    session: Session,
    skills: list[str],
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    """Get skill counts over time, bucketed by week.

    Returns list of dicts: [{"week": date, "skill": str, "count": int}, ...]
    Bucketing is done in Python for cross-DB compatibility.
    Uses ingested_at as fallback when posted_at is NULL.
    """
    if not skills:
        return []

    url_expr = RawJob.payload_json["url"].as_string()

    # Select both dates so we can use ingested_at as fallback
    q = (
        session.query(Job.posted_at, RawJob.ingested_at, JobSkill.skill)
        .join(JobSkill, JobSkill.job_id == Job.id)
        .outerjoin(RawJob, url_expr == Job.url)
        .filter(JobSkill.skill.in_(skills))
    )

    if source:
        q = q.filter(RawJob.source == source)

    if date_from:
        # Filter on either date
        q = q.filter((Job.posted_at >= date_from) | (RawJob.ingested_at >= date_from))

    if date_to:
        q = q.filter((Job.posted_at <= date_to) | (RawJob.ingested_at <= date_to))

    # Fetch all rows and bucket in Python
    rows = q.all()

    # Bucket by week (Monday start)
    from collections import defaultdict

    weekly_counts: dict[tuple[date, str], int] = defaultdict(int)
    for posted_at, ingested_at, skill in rows:
        # Use posted_at if available, otherwise fall back to ingested_at
        effective_date = posted_at
        if effective_date is None and ingested_at is not None:
            effective_date = ingested_at.date()
        if effective_date:
            # Get the Monday of the week
            week_start = effective_date - timedelta(days=effective_date.weekday())
            weekly_counts[(week_start, skill)] += 1

    # Convert to list of dicts
    result = [
        {"week": week, "skill": skill, "count": count}
        for (week, skill), count in sorted(weekly_counts.items())
    ]

    return result


def get_top_skills_by_source(
    session: Session,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    limit: int = 10,
) -> dict[str, list[tuple[str, int]]]:
    """Get top skills for each source.

    Returns dict: {"remotive": [(skill, count), ...], "remoteok": [...]}
    """
    # Get all sources first
    sources = [
        row[0]
        for row in session.execute(select(distinct(RawJob.source)).order_by(RawJob.source)).all()
    ]

    result = {}
    for source in sources:
        result[source] = get_top_skills(
            session,
            source=source,
            date_from=date_from,
            date_to=date_to,
            search=search,
            limit=limit,
        )

    return result
