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


def test_the_same_posting_can_be_ingested_once_per_environment(session):
    """Deduplication is scoped to the environment, not global.

    Unscoped, a development row suppressed the production ingest of the same
    posting: raw_jobs never recorded it, so no production job could be normalized,
    and nothing failed to signal the loss.
    """
    payload = {
        "source": "remotive",
        "url": "https://example.com/job/1",
        "title": "Data Engineer",
        "company": "ExampleCo",
        "location": "Remote",
        "posted_at": "2025-12-30",
        "description": "We use Python, SQL, and AWS.",
    }

    assert upsert_raw_job(session, payload, environment="development") is True
    assert upsert_raw_job(session, payload, environment="production") is True
    session.commit()

    environments = session.execute(
        select(RawJob.environment).order_by(RawJob.environment)
    ).scalars().all()
    assert environments == ["development", "production"]


def test_duplicate_within_one_environment_is_still_suppressed(session):
    payload = {
        "source": "remotive",
        "url": "https://example.com/job/2",
        "title": "Analyst",
        "description": "SQL",
    }

    assert upsert_raw_job(session, payload, environment="production") is True
    assert upsert_raw_job(session, payload, environment="production") is False
    session.commit()

    assert session.execute(select(func.count()).select_from(RawJob)).scalar_one() == 1


def test_environment_scoped_raw_dedup_reaches_normalized_jobs(session):
    """The end of the chain: both environments get their own normalized job."""
    from jobintel.etl.transform import transform_jobs
    from jobintel.models import Job

    payload = {
        "source": "remotive",
        "url": "https://example.com/job/3",
        "title": "Data Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Python and AWS",
    }

    upsert_raw_job(session, payload, environment="development")
    upsert_raw_job(session, payload, environment="production")
    session.commit()

    assert transform_jobs(session, environment="production") == 1
    assert transform_jobs(session, environment="development") == 1

    rows = session.execute(
        select(Job.environment, Job.url).order_by(Job.environment)
    ).all()
    assert rows == [
        ("development", "https://example.com/job/3"),
        ("production", "https://example.com/job/3"),
    ]
