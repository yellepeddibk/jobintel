from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from jobintel.models import JobSkill


def top_skills(session: Session, limit: int = 20) -> list[tuple[str, int]]:
    rows = session.execute(
        select(JobSkill.skill, func.count().label("n"))
        .group_by(JobSkill.skill)
        .order_by(desc("n"), JobSkill.skill)
        .limit(limit)
    ).all()

    return [(skill, int(n)) for (skill, n) in rows]
