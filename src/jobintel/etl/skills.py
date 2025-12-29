from __future__ import annotations

import re
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobintel.models import Job, JobSkill

_SKILL_PATTERNS: dict[str, str] = {
    "python": r"\bpython\b",
    "sql": r"\bsql\b",
    "pandas": r"\bpandas\b",
    "aws": r"\baws\b|\bamazon web services\b",
    "fastapi": r"\bfastapi\b",
    "postgres": r"\bpostgres(?:ql)?\b",
    "docker": r"\bdocker\b",
    "scikit-learn": r"\bscikit[- ]learn\b|\bsklearn\b",
    "pytest": r"\bpytest\b",
    "ci": r"\bci\b|\bcontinuous integration\b",
}


def extract_skills(text: str | None) -> set[str]:
    if not text:
        return set()
    t = text.lower()
    found: set[str] = set()
    for skill, pat in _SKILL_PATTERNS.items():
        if re.search(pat, t):
            found.add(skill)
    return found


def extract_skills_for_jobs(session: Session, jobs: Iterable[Job]) -> int:
    existing_pairs = set(session.execute(select(JobSkill.job_id, JobSkill.skill)).all())

    inserted = 0
    for job in jobs:
        if job.id is None:
            continue
        for skill in extract_skills(job.description):
            key = (job.id, skill)
            if key in existing_pairs:
                continue
            session.add(JobSkill(job_id=job.id, skill=skill))
            existing_pairs.add(key)
            inserted += 1

    session.commit()
    return inserted


def extract_skills_for_all_jobs(session: Session) -> int:
    jobs = session.execute(select(Job)).scalars().all()
    return extract_skills_for_jobs(session, jobs)
