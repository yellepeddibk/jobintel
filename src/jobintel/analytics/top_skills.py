from __future__ import annotations

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from jobintel.models import Job, JobSkill

# Default environment filter, matching analytics.queries.
PRODUCTION_ENV = "production"


def top_skills(
    session: Session,
    limit: int = 20,
    environment: str = PRODUCTION_ENV,
) -> list[tuple[str, int]]:
    """Top skills by mention count, for one environment.

    JobSkill has no environment column: it derives one through job_id, so the
    filter goes through Job. Before this was environment-aware it aggregated every
    environment at once, which meant a development run could move the numbers a
    production report printed.
    """
    rows = session.execute(
        select(JobSkill.skill, func.count().label("n"))
        .join(Job, Job.id == JobSkill.job_id)
        .where(Job.environment == environment)
        .group_by(JobSkill.skill)
        .order_by(desc("n"), JobSkill.skill)
        .limit(limit)
    ).all()

    return [(skill, int(n)) for (skill, n) in rows]
