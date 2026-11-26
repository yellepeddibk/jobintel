from __future__ import annotations

import argparse
import sys
import traceback

from jobintel.analytics.top_skills import top_skills
from jobintel.core.config import settings
from jobintel.db import SessionLocal, init_db
from jobintel.etl.pipeline import run_ingest_sources


def main() -> int:
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

    print(f"🔄 Running ingestion for: {', '.join(sources)}")
    outcomes = run_ingest_sources(
        SessionLocal,
        sources,
        search=args.search or "",
        limit=args.limit,
    )

    for outcome in outcomes:
        if outcome.ok:
            result = outcome.result
            print(
                f"✅ {outcome.source}: fetched={result.fetched} "
                f"inserted_raw={result.inserted_raw} "
                f"inserted_jobs={result.inserted_jobs} "
                f"inserted_skills={result.inserted_skills}"
            )
        else:
            # The full traceback goes to the job log, which is where the dashboard
            # points viewers for failed-run details.
            print(f"❌ {outcome.source}: failed", file=sys.stderr)
            traceback.print_exception(outcome.error)

    # Show top skills across all sources
    with SessionLocal() as session:
        print(f"\n📊 Top {args.top} skills:")
        rows = top_skills(session, limit=int(args.top), environment=settings.ENV)
        print("skill\tcount")
        for skill, n in rows:
            print(f"{skill}\t{n}")

    failed = [o.source for o in outcomes if not o.ok]
    if failed:
        print(f"\n{len(failed)} of {len(outcomes)} sources failed: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
