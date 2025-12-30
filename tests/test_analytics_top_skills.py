from sqlalchemy import text

from jobintel.analytics.top_skills import top_skills
from jobintel.db import SessionLocal, init_db
from jobintel.etl.load_raw import load_raw_jobs
from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.transform import transform_jobs


def test_top_skills_returns_counts():
    init_db()

    with SessionLocal() as session:
        session.execute(text("DELETE FROM job_skills"))
        session.execute(text("DELETE FROM jobs"))
        session.execute(text("DELETE FROM raw_jobs"))
        session.commit()

        load_raw_jobs(session, "data/sample_jobs.jsonl")
        transform_jobs(session)
        extract_skills_for_all_jobs(session)

        rows = top_skills(session, limit=50)
        skills = {s for (s, _) in rows}

        assert "python" in skills
        assert all(n > 0 for (_, n) in rows)
