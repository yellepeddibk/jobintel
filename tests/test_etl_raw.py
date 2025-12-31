from sqlalchemy import func, select, text

from jobintel.db import SessionLocal, init_db
from jobintel.etl.raw import upsert_raw_job
from jobintel.models import RawJob


def test_upsert_raw_job_is_idempotent():
    init_db()

    payload = {
        "source": "test",
        "url": "https://example.com/job/1",
        "title": "Data Engineer",
        "company": "ExampleCo",
        "location": "Remote",
        "posted_at": "2025-12-30",
        "description": "We use Python, SQL, and AWS.",
    }

    with SessionLocal() as session:
        session.execute(text("DELETE FROM raw_jobs"))
        session.commit()

        assert upsert_raw_job(session, payload) is True
        session.commit()

        assert upsert_raw_job(session, payload) is False
        session.commit()

        n = session.execute(select(func.count()).select_from(RawJob)).scalar_one()
        assert n == 1
