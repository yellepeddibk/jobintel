from __future__ import annotations

import argparse

from jobintel.analytics.top_skills import top_skills
from jobintel.db import SessionLocal, init_db
from jobintel.etl.raw import upsert_raw_job
from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.sources.remotive import fetch_remotive_jobs
from jobintel.etl.transform import transform_jobs


def main() -> None:
    ap = argparse.ArgumentParser(description="Live ETL: fetch -> transform -> skills -> report")
    ap.add_argument("--search", default=None)
    ap.add_argument("--category", default=None)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    init_db()
    payloads = fetch_remotive_jobs(search=args.search, category=args.category, limit=args.limit)

    with SessionLocal() as session:
        inserted_raw = 0
        for payload in payloads:
            if upsert_raw_job(session, payload):
                inserted_raw += 1
        session.commit()

        inserted_jobs = transform_jobs(session)
        session.commit()

        inserted_skills = extract_skills_for_all_jobs(session)
        session.commit()

        rows = top_skills(session, limit=int(args.top))

    print(
        f"fetched={len(payloads)} inserted_raw={inserted_raw} "
        f"inserted_jobs={inserted_jobs} inserted_skills={inserted_skills}"
    )
    print("skill\tcount")
    for skill, n in rows:
        print(f"{skill}\t{n}")


if __name__ == "__main__":
    main()
