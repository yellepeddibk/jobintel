from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobintel.core.config import settings
from jobintel.models import Job, RawJob


def _safe_date(v: Any) -> date | None:
    """Parse an adapter's `posted_at` to a date, or None.

    Accepts dates, datetimes, epoch numbers (UTC), ISO dates and datetimes (with or
    without offset or Z), and numeric epoch strings.
    """
    if v is None or v == "":
        return None
    if isinstance(v, bool):  # bool is an int; would otherwise parse as 1970-01-01
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
    try:  # fromisoformat rejects a trailing Z
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
    """Normalize one environment's raw rows into `jobs`, skipping known URLs and hashes.

    No all-environments mode: callers loop, so each environment is a stated choice.
    """
    env = environment or settings.ENV

    # Seed from this environment only, so one environment cannot suppress another.
    existing = session.execute(
        select(Job.url, Job.hash).where(Job.environment == env)
    ).all()
    seen_urls = {u for (u, _) in existing if u}
    seen_hashes = {h for (_, h) in existing if h}

    inserted = 0

    # Lowest id wins, matching how analytics resolves a job's raw metadata.
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
