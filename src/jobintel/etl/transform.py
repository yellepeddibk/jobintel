from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobintel.core.config import settings
from jobintel.models import Job, RawJob


def _safe_date(v: Any) -> date | None:
    """Normalize whatever a source put in `posted_at` to a date, or None.

    Every adapter emits a datetime rather than a bare date: arbeitnow an
    isoformat() of a Unix timestamp, remotive the upstream publication_date, and
    remoteok either an ISO datetime or a raw epoch. `date.fromisoformat` rejects
    all of those, so this previously returned None for effectively every real
    posting, leaving `posted_at` NULL and collapsing the date component of
    `job_hash` to an empty string.

    Accepted, in this order: date and datetime objects, Unix epoch numbers,
    bare ISO dates, ISO datetimes (with or without an offset, a trailing Z, or a
    space separator), and numeric epoch strings. Anything else is None.

    bool is rejected explicitly. It is a subclass of int, so without this True
    would be read as the epoch and silently become 1970-01-01.

    Epoch values are interpreted as UTC, matching how arbitrarily-timezoned
    upstream timestamps are already flattened to a date elsewhere.
    """
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v, tz=UTC).date()
        except (ValueError, OSError, OverflowError):
            return None

    s = str(v).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    # fromisoformat gained broad ISO 8601 support in 3.11 but still will not take
    # a trailing Z, so normalize that one spelling to an explicit UTC offset.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    if s.lstrip("-").replace(".", "", 1).isdigit():
        try:
            return datetime.fromtimestamp(float(s), tz=UTC).date()
        except (ValueError, OSError, OverflowError):
            return None
    return None


def job_hash(
    title: str | None,
    company: str | None,
    location: str | None,
    posted_at: date | None,
) -> str:
    s = "|".join(
        [
            (title or "").strip().lower(),
            (company or "").strip().lower(),
            (location or "").strip().lower(),
            str(posted_at or ""),
        ]
    )
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def transform_jobs(session: Session, environment: str | None = None) -> int:
    """Normalize raw jobs into `jobs` for exactly one environment.

    Args:
        session: SQLAlchemy session.
        environment: Environment to process. None resolves to settings.ENV, which
            matches upsert_raw_job and the rest of the pipeline. There is
            deliberately no all-environments mode: a caller that wants several
            loops over them explicitly, so the environment a row is written with
            is always a stated decision rather than a side effect.

    Only raw rows tagged with the resolved environment are read, and every job
    created is written with that same environment. Deduplication by URL and by
    normalized hash is scoped to it too, so the same posting may exist once per
    environment while a repeat within one environment is still suppressed.
    """
    env = environment or settings.ENV

    # Seed seen sets from existing rows in THIS environment, for idempotency
    # across runs. Scoping matters as much as the raw filter below: seeding
    # globally would let one environment's jobs suppress another's.
    existing = session.execute(
        select(Job.url, Job.hash).where(Job.environment == env)
    ).all()
    seen_urls = {u for (u, _) in existing if u}
    seen_hashes = {h for (_, h) in existing if h}

    inserted = 0

    # Ordered by id so which raw row wins is deterministic rather than dependent
    # on the query plan. Analytics resolves a job's raw metadata by the same rule
    # (lowest id for the environment and url), so content and metadata agree.
    raw_rows = (
        session.execute(
            select(RawJob).where(RawJob.environment == env).order_by(RawJob.id)
        )
        .scalars()
        .all()
    )
    for r in raw_rows:
        p = r.payload_json or {}

        url = p.get("url")
        if not url:
            continue

        title = p.get("title")
        company = p.get("company")
        location = p.get("location")
        posted_at = _safe_date(p.get("posted_at"))
        description = p.get("description")

        h = job_hash(title, company, location, posted_at)

        # Dedup within this run + across prior runs.
        if url in seen_urls or h in seen_hashes:
            continue

        session.add(
            Job(
                environment=env,
                title=title,
                company=company,
                location=location,
                url=url,
                posted_at=posted_at,
                description=description,
                hash=h,
            )
        )
        seen_urls.add(url)
        seen_hashes.add(h)
        inserted += 1

    session.commit()
    return inserted
