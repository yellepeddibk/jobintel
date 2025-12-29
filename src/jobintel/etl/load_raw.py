from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from jobintel.models import RawJob


def load_raw_jobs(session: Session, path: str | Path) -> int:
    p = Path(path)
    n = 0
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            source = payload.get("source", "unknown")
            session.add(RawJob(source=source, payload_json=payload))
            n += 1

    session.commit()
    return n
