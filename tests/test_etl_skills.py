"""Tests for ETL skills extraction."""

from fixtures import seed_test_data

from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.transform import transform_jobs
from jobintel.models import JobSkill


def test_extract_skills_is_idempotent(session):
    """Running skills extraction twice should not duplicate skills."""
    seed_test_data(session, environment="test")
    transform_jobs(session)

    first = extract_skills_for_all_jobs(session)
    second = extract_skills_for_all_jobs(session)

    assert first > 0, "First run should extract some skills"
    assert second == 0, "Second run should extract zero (already processed)"

    # Verify no duplicate (job_id, skill) pairs
    pairs = session.query(JobSkill.job_id, JobSkill.skill).all()
    assert len(pairs) == len(set(pairs)), "Should have no duplicate skill assignments"
