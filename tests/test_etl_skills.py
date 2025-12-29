from sqlalchemy import select, text

from jobintel.db import SessionLocal, init_db
from jobintel.etl.load_raw import load_raw_jobs
from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.transform import transform_jobs
from jobintel.models import JobSkill


def test_extract_skills_is_idempotent():
    init_db()

    with SessionLocal() as session:
        # Clear persistent SQLite tables so test is repeatable
        session.execute(text("DELETE FROM job_skills"))
        session.execute(text("DELETE FROM jobs"))
        session.execute(text("DELETE FROM raw_jobs"))
        session.commit()

        load_raw_jobs(session, "data/sample_jobs.jsonl")
        transform_jobs(session)

        first = extract_skills_for_all_jobs(session)
        second = extract_skills_for_all_jobs(session)

        assert first > 0
        assert second == 0

        pairs = session.execute(select(JobSkill.job_id, JobSkill.skill)).all()
        assert len(pairs) == len(set(pairs))
