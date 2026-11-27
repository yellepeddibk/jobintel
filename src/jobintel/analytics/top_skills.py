from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from jobintel.models import Job, JobSkill

PRODUCTION_ENV = "production"


def top_skills(
    session: Session,
    limit: int = 20,
    environment: str = PRODUCTION_ENV,
) -> list[tuple[str, int]]:
    """Top skills by mention count in one environment (filtered through Job)."""
    rows = session.execute(
        select(JobSkill.skill, func.count().label("n"))
        .join(Job, Job.id == JobSkill.job_id)
        .where(Job.environment == environment)
        .group_by(JobSkill.skill)
        .order_by(desc("n"), JobSkill.skill)
        .limit(limit)
    ).all()

    return [(skill, int(n)) for (skill, n) in rows]
