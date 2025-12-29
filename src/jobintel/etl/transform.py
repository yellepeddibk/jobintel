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


def transform_jobs(session: Session) -> int:
    # Seed seen sets from existing DB rows for idempotency across runs.
    existing = session.execute(select(Job.url, Job.hash)).all()
    seen_urls = {u for (u, _) in existing if u}
    seen_hashes = {h for (_, h) in existing if h}

    inserted = 0

    raw_rows = session.execute(select(RawJob)).scalars().all()
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
