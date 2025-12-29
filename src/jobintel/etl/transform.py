from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobintel.models import Job, RawJob


def _safe_date(v: Any) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


def job_hash(title: str | None, company: str | None, location: str | None, posted_at: date | None) -> str:
    s = "|".join([(title or "").strip().lower(), (company or "").strip().lower(), (location or "").strip().lower(), str(posted_at or "")])
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def transform_jobs(session: Session) -> int:
    inserted = 0

    raw_rows = session.execute(select(RawJob)).scalars().all()
    for r in raw_rows:
        p = r.payload_json or {}
        url = p.get("url")
        if not url:
            continue

        # Dedupe: if job with same URL exists, skip
        exists = session.execute(select(Job.id).where(Job.url == url)).first()
        if exists:
            continue

        title = p.get("title")
        company = p.get("company")
        location = p.get("location")
        posted_at = _safe_date(p.get("posted_at"))
        description = p.get("description")

        h = job_hash(title, company, location, posted_at)

        session.add(
            Job(
                title=title,
                company=company,
                location=location,
                url=url,
                posted_at=posted_at,
                description=description,
                hash=h,
            )
        )
        inserted += 1

    session.commit()
    return inserted
