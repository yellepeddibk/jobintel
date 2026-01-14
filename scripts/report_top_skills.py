from __future__ import annotations

from jobintel.analytics.top_skills import top_skills
from jobintel.db import SessionLocal, init_db


def main() -> None:
    init_db()
    with SessionLocal() as session:
        rows = top_skills(session, limit=20)

    if not rows:
        print("No skills found. Run the live ETL first:")
        print("  python scripts/run_live_etl.py")
        return

    print("skill\tcount")
    for skill, n in rows:
        print(f"{skill}\t{n}")


if __name__ == "__main__":
    main()
