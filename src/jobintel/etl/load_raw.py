from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from jobintel.core.config import settings
from jobintel.etl.raw import upsert_raw_job


def load_raw_jobs(
    session: Session,
    jsonl_path: str | Path,
    environment: str | None = None,
) -> int:
    """Load a JSONL file of payloads into raw_jobs, skipping ones already stored."""
    env = environment or settings.ENV
    path = Path(jsonl_path)
    inserted = 0

    if not path.exists():
        raise FileNotFoundError(f"JSONL file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)

            if "source" not in payload or not payload["source"]:
                raise ValueError(f"JSONL payload missing required field 'source': {payload}")

            if upsert_raw_job(session, payload, environment=env):
                inserted += 1

    return inserted
