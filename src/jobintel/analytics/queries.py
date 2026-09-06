"""Analytics queries for the JobIntel dashboard.

All functions return plain Python structures for easy testing and caching.

Environment separation is read straight off `Job.environment`. Normalized jobs
carry their own environment, so nothing here reconstructs one by matching a job
URL back to raw_jobs. Where a query still needs raw metadata (source,
ingested_at) the join matches environment as well as URL, because the same URL
may now legitimately exist in more than one environment.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import Integer, and_, cast, distinct, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql import expression

from jobintel.models import IngestRun, Job, JobSkill, RawJob

# Default environment filter for dashboard queries
PRODUCTION_ENV = "production"

# Bucket granularity options
Bucket = Literal["6h", "day", "week"]


def bucket_expr(
    ts_col: expression.ColumnElement,
    bucket: Bucket,
    dialect_name: str,
) -> expression.Label:
    """
    Time bucketing that works on Postgres + SQLite.
    Returns an expression labeled 'bucket'.
    """
    if dialect_name == "postgresql":
        if bucket == "6h":
            # date_trunc('day', ts) + floor(extract(hour)/6) * interval '6 hours'
            hours_block = func.floor(func.extract("hour", ts_col) / 6)
            expr = func.date_trunc("day", ts_col) + (hours_block * text("interval '6 hours'"))
        elif bucket == "day":
            expr = func.date_trunc("day", ts_col)
        else:  # week
            expr = func.date_trunc("week", ts_col)

        return expr.label("bucket")

    # SQLite
    if bucket == "6h":
        hour_int = cast(func.strftime("%H", ts_col), Integer)
        bucket_hour = hour_int - (hour_int % 6)
        # Build: YYYY-MM-DD HH:00:00 where HH is 00/06/12/18
        expr = func.datetime(
            func.strftime("%Y-%m-%d ", ts_col),
            func.printf("%02d:00:00", bucket_hour),
        )
    elif bucket == "day":
        expr = func.date(ts_col)
    else:  # week - deterministic ISO week start (Monday)
        # strftime('%w') returns 0=Sun..6=Sat
        # Convert to days since Monday: (w + 6) % 7 gives Mon=0..Sun=6
        w = cast(func.strftime("%w", ts_col), Integer)
        days_since_monday = (w + 6) % 7
        expr = func.date(ts_col, func.printf("-%d days", days_since_monday))

    return expr.label("bucket")


def authoritative_raw_jobs():
    """Subquery giving each (environment, url) pair its originating raw_jobs row.

    A normalized job has no database-enforced pointer back to the raw row it came
    from; `raw_job_id` is deliberately not part of this change. Raw metadata is
    therefore resolved by rule: **the lowest-id raw_jobs row sharing the job's
    environment and url**. That is the same row `transform_jobs` builds the job
    from, since it reads raw rows in id order, so a job's content and its reported
    source and ingested_at come from one payload rather than from whichever row the
    planner happened to match.

    This also removes fan-out. A job with several raw rows in one environment
    otherwise produces several result rows, and `.distinct()` does not fix it,
    because the differing column is exactly the metadata being selected. Verified
    on SQLite and PostgreSQL.

    Uses min(id) with GROUP BY rather than DISTINCT ON, which PostgreSQL supports
    and SQLite does not.

    Returns a subquery with columns: environment, url, source, ingested_at.
    """
    url_expr = RawJob.payload_json["url"].as_string()
    first_raw = (
        select(
            RawJob.environment.label("environment"),
            url_expr.label("url"),
            func.min(RawJob.id).label("raw_id"),
        )
        .where(url_expr.isnot(None))
        .group_by(RawJob.environment, url_expr)
        .subquery("first_raw")
    )
    return (
        select(
            first_raw.c.environment,
            first_raw.c.url,
            RawJob.source.label("source"),
            RawJob.ingested_at.label("ingested_at"),
        )
        .join(RawJob, RawJob.id == first_raw.c.raw_id)
        .subquery("authoritative_raw")
    )


def job_raw_onclause(raw):
    """Join a job to its raw metadata on environment AND url.

    Matching on URL alone is not sufficient any more: the same posting may exist
    in several environments, so a URL-only join would let one environment's raw
    metadata attach to another environment's job.
    """
    return and_(raw.c.url == Job.url, raw.c.environment == Job.environment)


def _base_job_query(
    session: Session,
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    environment: str = PRODUCTION_ENV,
):
    """Build base query for jobs with optional filters.

    The environment gate is `Job.environment` alone, so a job is counted on its own
    recorded provenance and does not depend on a raw row still being matchable by
    URL. raw_jobs is joined only when a source filter is requested.
    """
    q = session.query(Job).filter(Job.environment == environment)

    if source:
        raw = authoritative_raw_jobs()
        q = q.join(raw, job_raw_onclause(raw)).filter(raw.c.source == source)

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
    environment: str = PRODUCTION_ENV,
) -> dict:
    """Get key performance indicators for the dashboard.

    Returns:
        dict with keys: total_jobs, jobs_last_7d, unique_companies, sources_count
    """
    # Total jobs with filters
    base_q = _base_job_query(session, source, date_from, date_to, search, environment)
    total_jobs = base_q.count()

    # Jobs in last 7 days (ignoring other date filters for this metric)
    seven_days_ago = date.today() - timedelta(days=7)
    jobs_7d_q = _base_job_query(session, source, seven_days_ago, None, search, environment)
    jobs_last_7d = jobs_7d_q.count()

    # Unique companies
    companies_q = _base_job_query(session, source, date_from, date_to, search, environment)
    unique_companies = (
        companies_q.with_entities(func.count(distinct(Job.company)))
        .filter(Job.company.isnot(None))
        .scalar()
        or 0
    )

    # Number of sources (from successful ingest runs in this environment)
    sources_count = (
        session.execute(
            select(func.count(distinct(IngestRun.source))).where(
                IngestRun.status == "success",
                IngestRun.environment == environment,
            )
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
    environment: str = PRODUCTION_ENV,
) -> list[tuple[str, int]]:
    """Get top skills by number of distinct jobs mentioning them.

    Uses COUNT(DISTINCT job_id) to avoid over-counting from duplicates.
    """
    q = (
        session.query(JobSkill.skill, func.count(distinct(JobSkill.job_id)).label("n"))
        .join(Job, Job.id == JobSkill.job_id)
        .filter(Job.environment == environment)
    )

    if source:
        raw = authoritative_raw_jobs()
        q = q.join(raw, job_raw_onclause(raw)).filter(raw.c.source == source)

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
    granularity: Bucket | None = None,
    environment: str = PRODUCTION_ENV,
) -> list[dict]:
    """Get skill counts over time, bucketed by granularity.

    Args:
        session: SQLAlchemy session
        skills: List of skill names to track
        source: Optional source filter
        date_from: Optional start date (inclusive)
        date_to: Optional end date (inclusive)
        granularity: '6h', 'day', or 'week'. If None, auto-detects based on date range.
        environment: Environment filter (default: production)

    Returns:
        List of dicts: [{"bucket": str, "skill": str, "count": int}, ...]
        Bucket is a timestamp string for charting.
    """
    if not skills:
        return []

    # Auto-detect granularity based on date range
    if granularity is None:
        if date_from and date_to:
            days = (date_to - date_from).days
            if days <= 7:
                granularity = "6h"
            elif days <= 60:
                granularity = "day"
            else:
                granularity = "week"
        else:
            # Default to 6h to match ingestion cadence
            granularity = "6h"

    dialect_name = session.bind.dialect.name if session.bind else "sqlite"

    # ingested_at lives only on the raw row, so this query keeps a raw join, matched
    # on environment as well as URL. The join is inner by necessity: a job with no
    # resolvable raw row has no ingestion time and cannot be placed in a bucket.
    # Resolving through the authoritative subquery also puts each job in exactly one
    # bucket rather than one per raw version.
    raw = authoritative_raw_jobs()
    bucket_col = bucket_expr(raw.c.ingested_at, granularity, dialect_name)
    count_col = func.count(distinct(JobSkill.job_id)).label("count")

    q = (
        session.query(bucket_col, JobSkill.skill, count_col)
        .join(Job, Job.id == JobSkill.job_id)
        .join(raw, job_raw_onclause(raw))
        .filter(Job.environment == environment)
        .filter(JobSkill.skill.in_(skills))
    )

    if source:
        q = q.filter(raw.c.source == source)

    # Use proper datetime comparison for index efficiency
    if date_from:
        start_dt = datetime.combine(date_from, datetime.min.time())
        q = q.filter(raw.c.ingested_at >= start_dt)

    if date_to:
        end_dt = datetime.combine(date_to, datetime.max.time())
        q = q.filter(raw.c.ingested_at <= end_dt)

    # Group by bucket and skill
    q = q.group_by(bucket_col, JobSkill.skill).order_by(bucket_col)

    rows = q.all()

    # Convert to list of dicts
    result = []
    for bucket, skill, count in rows:
        # Convert bucket to string for consistent output
        bucket_str = str(bucket) if bucket else None
        if bucket_str:
            result.append({"bucket": bucket_str, "skill": skill, "count": int(count)})

    return result


def get_top_skills_by_source(
    session: Session,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    limit: int = 10,
    environment: str = PRODUCTION_ENV,
) -> dict[str, list[tuple[str, int]]]:
    """Get top skills for each source.

    Returns dict: {"remotive": [(skill, count), ...], "remoteok": [...]}
    """
    # Get all sources from the specified environment
    sources = [
        row[0]
        for row in session.execute(
            select(distinct(RawJob.source))
            .where(RawJob.environment == environment)
            .order_by(RawJob.source)
        ).all()
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
            environment=environment,
        )

    return result
