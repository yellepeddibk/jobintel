from __future__ import annotations

import argparse

from jobintel.analytics.top_skills import top_skills
from jobintel.db import SessionLocal, init_db
from jobintel.etl.pipeline import run_ingest


def main() -> None:
    ap = argparse.ArgumentParser(description="Live ETL: fetch -> transform -> skills -> report")
    ap.add_argument("--search", default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--source", default="remotive", help="Source to ingest from")
    args = ap.parse_args()

    init_db()

    with SessionLocal() as session:
        # Run full ingest pipeline (creates IngestRun record)
        result = run_ingest(
            session,
            source_name=args.source,
            search=args.search or "",
            limit=args.limit,
        )

        rows = top_skills(session, limit=int(args.top))

    print(
        f"fetched={result.fetched} inserted_raw={result.inserted_raw} "
        f"inserted_jobs={result.inserted_jobs} inserted_skills={result.inserted_skills}"
    )
    print("skill\tcount")
    for skill, n in rows:
        print(f"{skill}\t{n}")


if __name__ == "__main__":
    main()
