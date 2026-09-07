#!/usr/bin/env python
"""Backfill Job.posted_at and recompute Job.hash for one environment.

Thin entry point. All behavior lives in jobintel.etl.repair.

Dry run (default, writes nothing):
    python scripts/repair_posted_at.py --environment production

Apply:
    python scripts/repair_posted_at.py --environment production --apply

This does not run ingestion. Materializing postings that the old hash suppressed
is the normal pipeline's job, run separately afterwards.
"""

from __future__ import annotations

import argparse

from jobintel.core.config import redact_db_url, settings
from jobintel.db import SessionLocal
from jobintel.etl.repair import RepairError, apply_repair


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--environment",
        required=True,
        help="Environment to repair. Required; there is no default.",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes. Without this the run is a dry run.",
    )
    args = ap.parse_args()

    print(f"Database:    {redact_db_url(settings.DATABASE_URL)}")
    print(f"Environment: {args.environment}")
    print(f"Mode:        {'APPLY' if args.apply else 'dry run (no writes)'}")

    with SessionLocal() as session:
        try:
            result = apply_repair(session, args.environment, dry_run=not args.apply)
        except RepairError as exc:
            print(f"\nRefused: {exc}")
            return 1

    print(f"\n  jobs examined:        {result.examined:,}")
    print(f"  rows needing change:  {result.updated:,}")
    print(f"  posted_at backfilled: {result.posted_at_backfilled:,}")
    print(f"  hashes recomputed:    {result.hashes_changed:,}")
    print(f"  still without a date: {result.still_undated:,}")

    if result.dry_run:
        print("\nDry run: nothing was written. Re-run with --apply to commit.")
    else:
        print("\nApplied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
