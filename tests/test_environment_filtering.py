"""Tests for environment separation - ensures test data never pollutes production views.

This is a guardrail test to prevent regression on environment filtering.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jobintel.analytics.queries import PRODUCTION_ENV, get_kpis, get_top_skills
from jobintel.etl.raw import upsert_raw_job
from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.transform import transform_jobs
from jobintel.models import Base, RawJob


@pytest.fixture
def session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_queries_exclude_non_production_data(session):
    """Dashboard queries should only return production environment data."""
    # Insert a production job
    prod_payload = {
        "source": "remotive",
        "title": "Production Engineer",
        "company": "RealCorp",
        "location": "Remote",
        "url": "https://real.com/job/1",
        "description": "Python, AWS, Docker",
    }
    upsert_raw_job(session, prod_payload, environment="production")

    # Insert a test job (using a real source, but tagged as test environment)
    test_payload = {
        "source": "arbeitnow",
        "title": "Test Engineer",
        "company": "TestCorp",
        "location": "Nowhere",
        "url": "https://test.com/job/1",
        "description": "Testing, QA, Selenium",
    }
    upsert_raw_job(session, test_payload, environment="test")

    session.commit()

    # Transform to jobs table
    transform_jobs(session)
    extract_skills_for_all_jobs(session)

    # Get KPIs - should only count production job
    kpis = get_kpis(session, environment=PRODUCTION_ENV)
    assert kpis["total_jobs"] == 1, "KPIs should only count production jobs"

    # Get top skills - should only include production job skills
    skills = get_top_skills(session, environment=PRODUCTION_ENV)
    skill_names = [s[0] for s in skills]

    # Production job has Python, AWS, Docker
    assert "python" in skill_names, "Production job skills should appear"

    # Test job has Selenium - should NOT appear
    assert "selenium" not in skill_names, "Test job skills should be excluded"


def test_environment_is_correctly_tagged(session):
    """Data should be tagged with the specified environment."""
    payload = {
        "source": "remotive",
        "title": "Test Job",
        "url": "https://example.com/1",
    }

    # When we explicitly set environment='test', it should stick
    upsert_raw_job(session, payload, environment="test")
    session.commit()

    raw_job = session.query(RawJob).first()
    assert raw_job.environment == "test", "Job should have test environment"
    assert raw_job.source == "remotive"


def test_production_is_default_environment_constant():
    """PRODUCTION_ENV constant should be 'production'."""
    assert PRODUCTION_ENV == "production"
