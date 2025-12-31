from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

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


def upsert_raw_job(session: Session, payload: dict[str, Any]) -> bool:
    """Insert a raw job if we have not seen it before.

    Returns True if inserted, False if it already existed.
    """
    payload = dict(payload)  # do not mutate caller
    payload.setdefault("content_hash", compute_content_hash(payload))

    url = payload.get("url")
    content_hash = payload.get("content_hash")

    stmt = select(RawJob.id).where(
        RawJob.payload_json["content_hash"].as_string() == content_hash
    )
    if url:
        stmt = stmt.where(RawJob.payload_json["url"].as_string() == url)

    exists = session.execute(stmt).first()
    if exists:
        return False

    session.add(RawJob(source=payload.get("source", "unknown"), payload_json=payload))
    session.flush()
    return True
