from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from jobintel.etl.raw import upsert_raw_job


def load_raw_jobs(session: Session, jsonl_path: str | Path) -> int:
    """Load raw jobs from a JSONL file into raw_jobs.

    Idempotent: reruns will skip jobs already seen (via content_hash, plus URL when present).
    """
    path = Path(jsonl_path)
    inserted = 0

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            payload.setdefault("source", "sample")

            if upsert_raw_job(session, payload):
                inserted += 1

    return inserted
