"""Load sample jobs for local development/testing.

This script is BLOCKED in production to prevent test data pollution.
Sample data is always tagged with environment='test'.
"""

import sys

from jobintel.core.config import settings
from jobintel.db import SessionLocal, init_db
from jobintel.etl.load_raw import load_raw_jobs
from jobintel.etl.pipeline import run_postprocess

# Environment where sample data is stored - never production!
SAMPLE_ENVIRONMENT = "test"


def main() -> None:
    # Kill switch: refuse to run sample ETL in production
    if settings.is_production:
        print("❌ ERROR: Sample ETL is blocked in production environment.")
        print("   Set ENV=development or ENV=test to load sample data.")
        sys.exit(1)

    init_db()
    with SessionLocal() as session:
        # Always tag sample data with test environment
        raw = load_raw_jobs(session, "data/sample_jobs.jsonl", environment=SAMPLE_ENVIRONMENT)
        jobs, skills = run_postprocess(session)

    print(f"loaded_raw={raw} inserted_jobs={jobs} inserted_skills={skills}")
    print(f"✓ Sample data loaded with environment='{SAMPLE_ENVIRONMENT}'")


if __name__ == "__main__":
    main()
