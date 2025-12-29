from sqlalchemy import inspect

from jobintel.db import engine, init_db


def test_init_db_creates_tables():
    init_db()
    tables = set(inspect(engine).get_table_names())
    assert {"raw_jobs", "jobs", "job_skills"}.issubset(tables)
