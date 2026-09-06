from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobintel.core.config import settings
from jobintel.models import RawJob


def compute_content_hash(payload: dict[str, Any]) -> str:
    """Stable hash for raw job payloads to make ingestion idempotent."""
    stable = {
        "source": payload.get("source"),
        "external_id": payload.get("external_id"),
        "url": payload.get("url"),
        "title": payload.get("title"),
        "company": payload.get("company"),
        "location": payload.get("location"),
        "posted_at": payload.get("posted_at"),
        "description": payload.get("description"),
    }
    blob = json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def upsert_raw_job(
    session: Session, payload: dict[str, Any], environment: str | None = None
) -> bool:
    """Insert a raw job if we have not seen it before in this environment.

    Args:
        session: SQLAlchemy session
        payload: Raw job payload dict
        environment: Environment tag (uses settings.ENV if None)

    Deduplication is scoped to the environment, so the same posting can be
    ingested once per environment while a repeat within one is still suppressed.
    Unscoped, a development run silently suppressed the production ingest of the
    same posting: raw_jobs never recorded it, so no production job could be
    normalized from it, and the loss was invisible because nothing failed.

    The content-hash key set and its serialization are unchanged. They are
    persistent data-format behavior, and altering either would invalidate every
    stored hash.

    Returns True if inserted, False if it already existed in this environment.
    """
    env = environment or settings.ENV
    payload = dict(payload)  # do not mutate caller
    payload.setdefault("content_hash", compute_content_hash(payload))

    url = payload.get("url")
    content_hash = payload.get("content_hash")

    stmt = select(RawJob.id).where(
        RawJob.environment == env,
        RawJob.payload_json["content_hash"].as_string() == content_hash,
    )
    if url:
        stmt = stmt.where(RawJob.payload_json["url"].as_string() == url)

    exists = session.execute(stmt).first()
    if exists:
        return False

    session.add(
        RawJob(
            source=payload.get("source", "unknown"),
            payload_json=payload,
            environment=env,
        )
    )
    session.flush()
    return True
