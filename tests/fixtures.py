"""Test fixtures - realistic job data for unit tests.

These replace the old sample_jobs.jsonl approach with controlled, minimal test data.
Uses real source names (remotive, arbeitnow) to test realistic scenarios.
"""

from jobintel.etl.raw import upsert_raw_job
from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.transform import transform_jobs

# Minimal realistic job payloads for testing
TEST_JOB_PAYLOADS = [
    {
        "source": "remotive",
        "url": "https://remotive.com/job/1001",
        "title": "Senior Python Developer",
        "company": "TechCorp",
        "location": "Remote",
        "description": "We need a Python developer with Django and PostgreSQL experience. "
        "FastAPI knowledge is a plus. AWS deployment skills required.",
        "tags": ["python", "django", "postgresql"],
        "posted_at": "2026-01-10",
    },
    {
        "source": "remotive",
        "url": "https://remotive.com/job/1002",
        "title": "Full Stack Engineer",
        "company": "StartupXYZ",
        "location": "Remote, USA",
        "description": "Looking for a full stack engineer with React and Node.js skills. "
        "Experience with TypeScript and Docker is preferred.",
        "tags": ["react", "nodejs", "typescript"],
        "posted_at": "2026-01-09",
    },
    {
        "source": "arbeitnow",
        "url": "https://arbeitnow.com/job/2001",
        "title": "DevOps Engineer",
        "company": "CloudScale Inc",
        "location": "Berlin, Germany",
        "description": "DevOps role requiring Kubernetes, Docker, and Terraform. "
        "AWS or GCP experience needed. Python scripting is a must.",
        "tags": ["devops", "kubernetes", "docker"],
        "posted_at": "2026-01-08",
    },
    {
        "source": "arbeitnow",
        "url": "https://arbeitnow.com/job/2002",
        "title": "Data Scientist",
        "company": "DataMinds",
        "location": "Munich, Germany",
        "description": "Data scientist position requiring Python, pandas, and scikit-learn. "
        "Experience with machine learning and SQL databases.",
        "tags": ["python", "data-science", "machine-learning"],
        "posted_at": "2026-01-07",
    },
]

# Duplicate URL to test deduplication
TEST_JOB_DUPLICATE = {
    "source": "remotive",
    "url": "https://remotive.com/job/1001",  # Same URL as first job
    "title": "Senior Python Developer (Updated)",
    "company": "TechCorp",
    "location": "Remote",
    "description": "Updated description for the same job.",
    "tags": ["python"],
    "posted_at": "2026-01-11",
}


def seed_test_data(session, environment: str = "production") -> int:
    """Insert test job payloads into the database.

    Args:
        session: SQLAlchemy session
        environment: Environment tag for the jobs

    Returns:
        Number of jobs inserted
    """
    inserted = 0
    for payload in TEST_JOB_PAYLOADS:
        if upsert_raw_job(session, payload, environment=environment):
            inserted += 1
    session.commit()
    return inserted


def seed_and_transform(session, environment: str = "production") -> dict:
    """Seed test data and run the full ETL pipeline.

    Args:
        session: SQLAlchemy session
        environment: Environment tag for the jobs

    Returns:
        Dict with counts: raw_inserted, jobs_transformed, skills_extracted
    """
    raw_inserted = seed_test_data(session, environment=environment)
    jobs_transformed = transform_jobs(session)
    skills_extracted = extract_skills_for_all_jobs(session)

    return {
        "raw_inserted": raw_inserted,
        "jobs_transformed": jobs_transformed,
        "skills_extracted": skills_extracted,
    }
