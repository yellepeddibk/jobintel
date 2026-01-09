from __future__ import annotations

import argparse

from jobintel.analytics.top_skills import top_skills
from jobintel.db import SessionLocal, init_db
from jobintel.etl.pipeline import run_etl_from_payloads
from jobintel.etl.sources.remotive import fetch_remotive_jobs


def main() -> None:
    ap = argparse.ArgumentParser(description="Live ETL: fetch -> transform -> skills -> report")
    ap.add_argument("--search", default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    init_db()

    # Fetch from Remotive (supports category arg not in registry interface)
    payloads = fetch_remotive_jobs(search=args.search, category=args.category, limit=args.limit)

    with SessionLocal() as session:
        # Run ETL pipeline on fetched payloads
        result = run_etl_from_payloads(session, payloads)

        rows = top_skills(session, limit=int(args.top))

    print(
        f"fetched={len(payloads)} inserted_raw={result.inserted_raw} "
        f"inserted_jobs={result.inserted_jobs} inserted_skills={result.inserted_skills}"
    )
    print("skill\tcount")
    for skill, n in rows:
        print(f"{skill}\t{n}")


if __name__ == "__main__":
    main()
