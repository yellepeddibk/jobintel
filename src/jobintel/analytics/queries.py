"""Analytics queries for the JobIntel dashboard.

All functions return plain Python structures for easy testing and caching.

Environment separation is read straight off `Job.environment`. Normalized jobs
carry their own environment, so nothing here reconstructs one by matching a job
URL back to raw_jobs. Where a query still needs raw metadata (source,
ingested_at) the join matches environment as well as URL, because the same URL
may now legitimately exist in more than one environment.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from sqlalchemy import Date, Integer, and_, cast, distinct, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql import expression

from jobintel.models import IngestRun, Job, JobSkill, RawJob

# Default environment filter for dashboard queries
PRODUCTION_ENV = "production"

# Bucket granularity options
Bucket = Literal["6h", "day", "week"]

# Skill trends are charted by job_date, a date without a time of day, so 6-hour
# buckets would be indistinguishable from daily ones and are not offered.
TrendBucket = Literal["day", "week"]
TREND_BUCKETS: tuple[TrendBucket, ...] = ("day", "week")


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


def job_date(raw) -> expression.ColumnElement:
    """The date analytics filters and charts a job by.

    Its posting date, or the date it was ingested when the source gave none. Every
    Analytics widget uses this one rule, so a date range means the same thing on all
    of them, and a job without a posting date is still counted. `raw` is the subquery
    from `authoritative_raw_jobs()`, joined to the job.

    `date(...)` truncates the ingestion timestamp on both dialects: a function on
    SQLite, a function-style cast on PostgreSQL.
    """
    return func.coalesce(Job.posted_at, func.date(raw.c.ingested_at, type_=Date))


def _apply_job_filters(
    q,
    source: str | None,
    date_from: date | None,
    date_to: date | None,
    search: str | None,
):
    """Source, date-range and search filters shared by the Analytics queries.

    raw_jobs is joined only when a source or date filter needs it: inner for a source
    filter, which needs a match, and outer for dates alone, so a job with no matchable
    raw row still counts by its posting date.
    """
    if source or date_from or date_to:
        raw = authoritative_raw_jobs()
        if source:
            q = q.join(raw, job_raw_onclause(raw)).filter(raw.c.source == source)
        else:
            q = q.outerjoin(raw, job_raw_onclause(raw))
        if date_from:
            q = q.filter(job_date(raw) >= date_from)
        if date_to:
            q = q.filter(job_date(raw) <= date_to)

    if search:
        like = f"%{search}%"
        q = q.filter(Job.title.ilike(like) | Job.company.ilike(like) | Job.location.ilike(like))

    return q


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
    URL. Dates are filtered by `job_date`.
    """
    q = session.query(Job).filter(Job.environment == environment)
    return _apply_job_filters(q, source, date_from, date_to, search)


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
    q = _apply_job_filters(q, source, date_from, date_to, search)

    q = (
        q.group_by(JobSkill.skill)
        .order_by(func.count(distinct(JobSkill.job_id)).desc())
        .limit(limit)
    )

    return [(skill, int(n)) for skill, n in q.all()]


def auto_granularity(date_from: date | None, date_to: date | None, *, today: date) -> TrendBucket:
    """Pick a bucket size for the span being charted.

    Up to 60 days: daily. Longer: weekly. A missing end date means the span runs to
    `today`, which is how the dashboard's "Last N days" ranges arrive. A missing start
    date means all history, charted weekly. Trends are charted by `job_date`, which
    has no time of day, so nothing finer than daily is offered.
    """
    if date_from is None:
        return "week"
    days = ((date_to or today) - date_from).days
    return "day" if days <= 60 else "week"


def get_skill_trends(
    session: Session,
    skills: list[str],
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    granularity: TrendBucket | None = None,
    environment: str = PRODUCTION_ENV,
) -> list[dict]:
    """Get skill counts over time, bucketed by `job_date`.

    Filtered and grouped by the same `job_date` rule as the other Analytics queries,
    so a date range selects the same jobs here as in the KPIs and top skills.

    Args:
        session: SQLAlchemy session
        skills: List of skill names to track
        source: Optional source filter
        date_from: Optional start date (inclusive)
        date_to: Optional end date (inclusive)
        granularity: 'day' or 'week'. If None, chosen by `auto_granularity`.
        environment: Environment filter (default: production)

    Returns:
        List of dicts: [{"bucket": str, "skill": str, "count": int}, ...]
        Bucket is a timestamp string for charting.

    Raises:
        ValueError: granularity is not 'day' or 'week'.
    """
    if not skills:
        return []

    if granularity is None:
        granularity = auto_granularity(date_from, date_to, today=date.today())
    if granularity not in TREND_BUCKETS:
        raise ValueError(
            f"Trend granularity must be one of {TREND_BUCKETS}, not {granularity!r}: "
            "trends are charted by date, which has no time of day."
        )

    dialect_name = session.bind.dialect.name if session.bind else "sqlite"

    # The raw join supplies the ingestion date job_date falls back to. Resolving
    # through the authoritative subquery puts each job in exactly one bucket rather
    # than one per raw version. Outer, so a job with no matchable raw row is still
    # charted by its posting date; inner when filtering by source, which needs one.
    raw = authoritative_raw_jobs()
    day = job_date(raw)
    bucket_col = bucket_expr(day, granularity, dialect_name)
    count_col = func.count(distinct(JobSkill.job_id)).label("count")

    q = (
        session.query(bucket_col, JobSkill.skill, count_col)
        .join(Job, Job.id == JobSkill.job_id)
        .filter(Job.environment == environment)
        .filter(JobSkill.skill.in_(skills))
    )

    if source:
        q = q.join(raw, job_raw_onclause(raw)).filter(raw.c.source == source)
    else:
        q = q.outerjoin(raw, job_raw_onclause(raw))

    if date_from:
        q = q.filter(day >= date_from)

    if date_to:
        q = q.filter(day <= date_to)

    # A job with neither a posting date nor a raw row cannot be placed in a bucket.
    q = q.filter(day.isnot(None))

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
