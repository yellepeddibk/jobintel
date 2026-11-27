"""Analytics queries for the dashboard. All filter by `Job.environment`."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import Date, Integer, and_, cast, distinct, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql import expression

from jobintel.models import IngestRun, Job, JobSkill, RawJob

PRODUCTION_ENV = "production"

Bucket = Literal["6h", "day", "week"]

# Trends chart job_date, which has no time of day, so 6h buckets are not offered.
TrendBucket = Literal["day", "week"]
TREND_BUCKETS: tuple[TrendBucket, ...] = ("day", "week")


def bucket_expr(
    ts_col: expression.ColumnElement,
    bucket: Bucket,
    dialect_name: str,
) -> expression.Label:
    """Time bucket expression labeled 'bucket', for Postgres or SQLite."""
    if dialect_name == "postgresql":
        if bucket == "6h":
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
        expr = func.datetime(
            func.strftime("%Y-%m-%d ", ts_col),
            func.printf("%02d:00:00", bucket_hour),
        )
    elif bucket == "day":
        expr = func.date(ts_col)
    else:  # week, starting Monday: %w is 0=Sun..6=Sat
        w = cast(func.strftime("%w", ts_col), Integer)
        days_since_monday = (w + 6) % 7
        expr = func.date(ts_col, func.printf("-%d days", days_since_monday))

    return expr.label("bucket")


def authoritative_raw_jobs():
    """Subquery: each (environment, url) with source and ingested_at of its lowest-id raw row.

    That is the row transform_jobs built the job from, so content and metadata agree,
    and a job with several raw rows yields one result row. GROUP BY min(id) because
    SQLite has no DISTINCT ON.
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
    """Join on environment and url; a URL can exist in several environments."""
    return and_(raw.c.url == Job.url, raw.c.environment == Job.environment)


def job_date(raw) -> expression.ColumnElement:
    """The date every Analytics widget uses: posting date, else ingestion date."""
    return func.coalesce(Job.posted_at, func.date(raw.c.ingested_at, type_=Date))


def _apply_job_filters(
    q,
    source: str | None,
    date_from: date | None,
    date_to: date | None,
    search: str | None,
):
    """Source, date-range and search filters shared by the Analytics queries."""
    if source or date_from or date_to:
        raw = authoritative_raw_jobs()
        # Outer join for dates alone, so jobs without a raw match still count.
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
    """Jobs in one environment, with the shared filters applied."""
    q = session.query(Job).filter(Job.environment == environment)
    return _apply_job_filters(q, source, date_from, date_to, search)


def get_last_updated(session: Session, environment: str = PRODUCTION_ENV) -> datetime | None:
    """Finish time of the environment's latest successful ingest, or None."""
    return session.execute(
        select(func.max(IngestRun.finished_at)).where(
            IngestRun.environment == environment,
            IngestRun.status == "success",
        )
    ).scalar()


def get_kpis(
    session: Session,
    source: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    environment: str = PRODUCTION_ENV,
) -> dict:
    """KPIs: total_jobs, jobs_last_7d, unique_companies, sources_count."""
    base_q = _base_job_query(session, source, date_from, date_to, search, environment)
    total_jobs = base_q.count()

    # Last 7 days ignores the selected date range.
    seven_days_ago = date.today() - timedelta(days=7)
    jobs_7d_q = _base_job_query(session, source, seven_days_ago, None, search, environment)
    jobs_last_7d = jobs_7d_q.count()

    companies_q = _base_job_query(session, source, date_from, date_to, search, environment)
    unique_companies = (
        companies_q.with_entities(func.count(distinct(Job.company)))
        .filter(Job.company.isnot(None))
        .scalar()
        or 0
    )

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
    """Top skills by number of distinct jobs mentioning them."""
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
    """Daily up to 60 days, else weekly. No end means today; no start means all history."""
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
    """Skill counts per time bucket of `job_date`: [{"bucket", "skill", "count"}, ...].

    Raises ValueError for a granularity other than 'day' or 'week'.
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

    # Outer join so jobs without a raw match still chart by posting date.
    if source:
        q = q.join(raw, job_raw_onclause(raw)).filter(raw.c.source == source)
    else:
        q = q.outerjoin(raw, job_raw_onclause(raw))

    if date_from:
        q = q.filter(day >= date_from)

    if date_to:
        q = q.filter(day <= date_to)

    q = q.filter(day.isnot(None))

    q = q.group_by(bucket_col, JobSkill.skill).order_by(bucket_col)

    rows = q.all()

    result = []
    for bucket, skill, count in rows:
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
    """Top skills per source: {"remotive": [(skill, count), ...], ...}."""
    sources = [
        row[0]
        for row in session.execute(
            select(RawJob.source)
            .where(RawJob.environment == environment)
            .distinct()
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
