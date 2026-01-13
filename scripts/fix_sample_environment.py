"""One-time script to fix sample data environment tagging."""

from sqlalchemy import text

from jobintel.db import engine

with engine.connect() as conn:
    # Tag 'sample' source as 'test' environment
    result1 = conn.execute(text("UPDATE raw_jobs SET environment = 'test' WHERE source = 'sample'"))
    print(f"Updated {result1.rowcount} sample rows to environment='test'")

    # Tag all other sources as 'production' (they were fetched from live APIs)
    result2 = conn.execute(
        text("UPDATE raw_jobs SET environment = 'production' WHERE source != 'sample'")
    )
    print(f"Updated {result2.rowcount} live source rows to environment='production'")

    conn.commit()

print("Done!")
