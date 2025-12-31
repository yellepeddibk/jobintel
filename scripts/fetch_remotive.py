from __future__ import annotations

import argparse

from jobintel.db import SessionLocal, init_db
from jobintel.etl.raw import upsert_raw_job
from jobintel.etl.sources.remotive import fetch_remotive_jobs


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch live jobs from Remotive into raw_jobs.")
    ap.add_argument("--search", default=None, help="Search query, e.g. 'data engineer'")
    ap.add_argument("--category", default=None, help="Remotive category filter")
    ap.add_argument("--company", dest="company_name", default=None, help="Company name filter")
    ap.add_argument("--limit", type=int, default=100, help="Max jobs to fetch")
    args = ap.parse_args()

    init_db()
    payloads = fetch_remotive_jobs(
        search=args.search,
        category=args.category,
        company_name=args.company_name,
        limit=args.limit,
    )

    inserted = 0
    with SessionLocal() as session:
        for payload in payloads:
            if upsert_raw_job(session, payload):
                inserted += 1
        session.commit()

    print(f"fetched={len(payloads)} inserted_raw={inserted}")


if __name__ == "__main__":
    main()
