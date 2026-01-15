#!/usr/bin/env python
"""
Verify production database environment integrity.

Checks that no test/development data has leaked into production tables.

Usage:
    python scripts/check_prod_data.py

Exit codes:
    0 - Database is clean (prod only) OR non-prod env with warning
    1 - Production env with non-production data found (hard fail)
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _add_src_to_path() -> None:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))


def check_environment_distribution() -> tuple[dict[str, int], dict[str, int]]:
    """Get counts of records by environment for RawJob and IngestRun."""
    from sqlalchemy import func, select

    _add_src_to_path()
    from jobintel.db import SessionLocal
    from jobintel.models import IngestRun, RawJob

    with SessionLocal() as session:
        # RawJob distribution
        raw_query = select(RawJob.environment, func.count(RawJob.id)).group_by(RawJob.environment)
        raw_counts = dict(session.execute(raw_query).fetchall())

        # IngestRun distribution
        run_query = select(IngestRun.environment, func.count(IngestRun.id)).group_by(
            IngestRun.environment
        )
        run_counts = dict(session.execute(run_query).fetchall())

    return raw_counts, run_counts


def main() -> int:
    _add_src_to_path()
    from jobintel.core.config import redact_db_url, settings

    print(f"Environment: {settings.ENV}")
    print(f"Database: {redact_db_url(settings.DATABASE_URL)}")
    print()

    try:
        raw_counts, run_counts = check_environment_distribution()
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return 1

    print("RawJob records by environment:")
    if raw_counts:
        for env, count in sorted(raw_counts.items()):
            marker = " [!]" if env in ("test", "development") else ""
            print(f"  {env}: {count}{marker}")
    else:
        print("  (none)")

    print()
    print("IngestRun records by environment:")
    if run_counts:
        for env, count in sorted(run_counts.items()):
            marker = " [!]" if env in ("test", "development") else ""
            print(f"  {env}: {count}{marker}")
    else:
        print("  (none)")

    # Check for problems
    non_prod_raw = sum(count for env, count in raw_counts.items() if env != "production")
    non_prod_runs = sum(count for env, count in run_counts.items() if env != "production")

    print()
    if non_prod_raw > 0 or non_prod_runs > 0:
        if settings.is_production:
            print("ERROR: Non-production data found in production database!")
            print("This is a hard failure in production mode.")
            return 1
        else:
            print("WARNING: Non-production environment rows found (development/test).")
            print("Dashboard will filter these out in production mode. (Not failing in dev/test.)")
            return 0
    else:
        print("OK: Database contains only production data.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
