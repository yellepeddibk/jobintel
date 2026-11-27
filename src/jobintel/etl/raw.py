from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobintel.core.config import settings
from jobintel.models import RawJob


def compute_content_hash(payload: dict[str, Any]) -> str:
    """Stable payload hash for idempotent ingestion. Key set and serialization are frozen."""
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
    """Insert a raw job unless this environment already has it. Returns True if inserted.

    Scoped to one environment so a development run cannot suppress a production ingest.
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
