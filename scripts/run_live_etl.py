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
    ap.add_argument(
        "--source",
        default="all",
        choices=["all", "remotive", "arbeitnow", "remoteok"],
        help="Source to ingest from (default: all)",
    )
    args = ap.parse_args()

    init_db()

    # Determine which sources to run
    sources = (
        ["remotive", "arbeitnow", "remoteok"]
        if args.source == "all"
        else [args.source]
    )

    with SessionLocal() as session:
        for source in sources:
            print(f"\n🔄 Running ingestion for {source}...")
            result = run_ingest(
                session,
                source_name=source,
                search=args.search or "",
                limit=args.limit,
            )
            print(
                f"✅ {source}: fetched={result.fetched} "
                f"inserted_raw={result.inserted_raw} "
                f"inserted_jobs={result.inserted_jobs} "
                f"inserted_skills={result.inserted_skills}"
            )

        # Show top skills across all sources
        print(f"\n📊 Top {args.top} skills:")
        rows = top_skills(session, limit=int(args.top))
        print("skill\tcount")
        for skill, n in rows:
            print(f"{skill}\t{n}")


if __name__ == "__main__":
    main()
