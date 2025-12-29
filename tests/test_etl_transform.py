from sqlalchemy import select

from jobintel.db import SessionLocal, init_db
from jobintel.etl.load_raw import load_raw_jobs
from jobintel.etl.transform import transform_jobs
from jobintel.models import Job


def test_transform_dedupes_by_url(tmp_path):
    init_db()

    # use the repo sample file
    with SessionLocal() as session:
        load_raw_jobs(session, "data/sample_jobs.jsonl")
        inserted = transform_jobs(session)

        # sample has 4 raw rows but 1 duplicate url
        assert inserted == 3

        jobs = session.execute(select(Job)).scalars().all()
        assert len(jobs) == 3
