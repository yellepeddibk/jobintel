"""Tests for ETL transform step."""

from fixtures import TEST_JOB_DUPLICATE, seed_test_data

from jobintel.etl.raw import upsert_raw_job
from jobintel.etl.transform import transform_jobs
from jobintel.models import Job


def test_transform_dedupes_by_url(session):
    """Transform should deduplicate jobs by URL."""
    # Insert test data (4 unique jobs)
    seed_test_data(session, environment="test")

    # Insert a duplicate (same URL as first job)
    upsert_raw_job(session, TEST_JOB_DUPLICATE, environment="test")
    session.commit()

    inserted = transform_jobs(session)

    # Should only create 4 unique jobs (duplicate URL skipped)
    assert inserted == 4

    jobs = session.query(Job).all()
    assert len(jobs) == 4

    # Verify no duplicate URLs
    urls = [j.url for j in jobs]
    assert len(urls) == len(set(urls)), "Jobs should have unique URLs"
