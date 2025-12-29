from sqlalchemy import select, text

from jobintel.db import SessionLocal, init_db
from jobintel.etl.load_raw import load_raw_jobs
from jobintel.etl.transform import transform_jobs
from jobintel.models import Job


def test_transform_dedupes_by_url():
    init_db()

    with SessionLocal() as session:
        # Clear persistent SQLite tables so test is repeatable
        session.execute(text("DELETE FROM job_skills"))
        session.execute(text("DELETE FROM jobs"))
        session.execute(text("DELETE FROM raw_jobs"))
        session.commit()

        load_raw_jobs(session, "data/sample_jobs.jsonl")
        inserted = transform_jobs(session)

        # sample has 4 raw rows but 1 duplicate url
        assert inserted == 3

        jobs = session.execute(select(Job)).scalars().all()
        assert len(jobs) == 3
