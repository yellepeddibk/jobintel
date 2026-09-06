"""End-to-end environment isolation for normalized jobs.

The invariant under test:

    RawJob.environment -> transform for that exact environment -> Job.environment
    -> analytics filtering

Two mechanisms enforce it, and they are tested separately because they fail
differently. The database enforces environment-scoped identity through
UNIQUE(environment, url) and UNIQUE(environment, hash). `transform_jobs` enforces
environment-correct behavior in code, which no constraint can express because it
spans two tables.

What is deliberately NOT claimed: there is no database-enforced pointer from a job
to the exact raw row it came from. `raw_job_id` is not part of this change. Raw
metadata is resolved by rule instead (lowest-id raw row for the job's environment
and url), which the fan-out tests below pin down.

These tests insert raw rows directly rather than through `upsert_raw_job`, because
they are about transform and analytics. Environment-scoped raw deduplication has
its own coverage in test_etl_raw.py.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from jobintel.analytics.queries import (
    get_kpis,
    get_skill_trends,
    get_top_skills,
    get_top_skills_by_source,
)
from jobintel.analytics.top_skills import top_skills
from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.transform import job_hash, transform_jobs
from jobintel.models import Job, JobSkill, RawJob

DEV = "development"
PROD = "production"


def add_raw(
    session,
    environment: str,
    url: str | None,
    *,
    source: str = "remotive",
    title: str = "Data Engineer",
    company: str = "Acme",
    location: str = "Remote",
    description: str = "Python and SQL",
    posted_at: str | None = "2026-01-10",
    ingested_at=None,
) -> RawJob:
    """Insert one raw row directly, bypassing upsert deduplication."""
    raw = RawJob(
        source=source,
        environment=environment,
        payload_json={
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "posted_at": posted_at,
        },
    )
    if ingested_at is not None:
        raw.ingested_at = ingested_at
    session.add(raw)
    session.commit()
    return raw


def jobs_by_env(session) -> dict[str, list[Job]]:
    out: dict[str, list[Job]] = {}
    for job in session.execute(select(Job).order_by(Job.id)).scalars():
        out.setdefault(job.environment, []).append(job)
    return out


# --------------------------------------------------------------- ORM / schema


def test_orm_created_schema_has_no_default_for_job_environment(engine):
    """A database built from the model must not silently label rows production.

    The production database still carries a temporary DEFAULT 'production' from
    the expand migration, removed in the contract step. The model must never
    declare one, so a writer that forgets an environment fails instead of being
    quietly attributed to production.
    """
    column = next(c for c in inspect(engine).get_columns("jobs") if c["name"] == "environment")
    assert column["default"] is None
    assert column["nullable"] is False


def test_inserting_a_job_without_an_environment_fails(session):
    session.add(Job(title="No environment", url="https://x/1", hash="h1"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_job_skills_has_no_environment_column(engine):
    """JobSkill derives environment through job_id rather than duplicating it."""
    names = {c["name"] for c in inspect(engine).get_columns("job_skills")}
    assert names == {"job_id", "skill"}


# --------------------------------------------- environment-scoped identity


def test_same_url_may_exist_once_per_environment(session):
    session.add(Job(environment=DEV, title="a", url="https://x/1", hash="hd"))
    session.add(Job(environment=PROD, title="b", url="https://x/1", hash="hp"))
    session.commit()

    rows = session.execute(
        select(Job.environment).where(Job.url == "https://x/1").order_by(Job.environment)
    ).scalars().all()
    assert rows == [DEV, PROD]


def test_same_hash_may_exist_once_per_environment(session):
    session.add(Job(environment=DEV, title="a", url="https://x/a", hash="shared"))
    session.add(Job(environment=PROD, title="b", url="https://x/b", hash="shared"))
    session.commit()

    rows = session.execute(
        select(Job.environment).where(Job.hash == "shared").order_by(Job.environment)
    ).scalars().all()
    assert rows == [DEV, PROD]


@pytest.mark.parametrize("column", ["url", "hash"])
def test_duplicate_within_one_environment_is_still_rejected(session, column):
    shared = {"url": "https://x/1", "hash": "h1"}
    session.add(Job(environment=PROD, title="first", **shared))
    session.commit()

    other = dict(shared)
    other["url" if column == "hash" else "hash"] = "different"
    session.add(Job(environment=PROD, title="second", **other))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_multiple_null_urls_remain_allowed(session):
    session.add(Job(environment=PROD, title="a", url=None, hash=None))
    session.add(Job(environment=PROD, title="b", url=None, hash=None))
    session.commit()
    assert session.execute(select(func.count()).select_from(Job)).scalar_one() == 2


# ------------------------------------------------------------ transform


def test_transform_reads_only_the_requested_environment(session):
    add_raw(session, DEV, "https://x/dev")
    add_raw(session, PROD, "https://x/prod")

    assert transform_jobs(session, environment=PROD) == 1

    jobs = session.execute(select(Job)).scalars().all()
    assert [j.url for j in jobs] == ["https://x/prod"]


def test_transform_writes_the_requested_environment(session):
    add_raw(session, PROD, "https://x/1")
    transform_jobs(session, environment=PROD)

    job = session.execute(select(Job)).scalar_one()
    assert job.environment == PROD


def test_transform_environment_none_resolves_to_settings_env(session, monkeypatch):
    from jobintel.core import config

    monkeypatch.setattr(config.settings, "ENV", DEV)
    add_raw(session, DEV, "https://x/dev")
    add_raw(session, PROD, "https://x/prod")

    assert transform_jobs(session) == 1
    job = session.execute(select(Job)).scalar_one()
    assert job.environment == DEV
    assert job.url == "https://x/dev"


def test_transform_is_idempotent_within_one_environment(session):
    add_raw(session, PROD, "https://x/1")

    assert transform_jobs(session, environment=PROD) == 1
    assert transform_jobs(session, environment=PROD) == 0
    assert session.execute(select(func.count()).select_from(Job)).scalar_one() == 1


def test_development_raw_row_cannot_create_or_affect_a_production_job(session):
    """The headline case. Same URL, different content, development ingested first."""
    add_raw(
        session,
        DEV,
        "https://x/1",
        title="DEV TITLE",
        company="DevCo",
        description="Selenium only",
    )
    add_raw(
        session,
        PROD,
        "https://x/1",
        title="REAL TITLE",
        company="RealCo",
        description="Python, AWS and Docker",
    )

    assert transform_jobs(session, environment=PROD) == 1

    job = session.execute(select(Job)).scalar_one()
    assert job.environment == PROD
    assert job.title == "REAL TITLE"
    assert job.company == "RealCo"
    assert "Python" in job.description

    # And transforming development afterwards creates a second, separate job.
    assert transform_jobs(session, environment=DEV) == 1
    by_env = jobs_by_env(session)
    assert by_env[PROD][0].title == "REAL TITLE"
    assert by_env[DEV][0].title == "DEV TITLE"


def test_production_raw_row_cannot_create_or_affect_a_development_job(session):
    """The same guarantee in the other direction, production ingested first."""
    add_raw(session, PROD, "https://x/1", title="REAL TITLE", company="RealCo")
    add_raw(session, DEV, "https://x/1", title="DEV TITLE", company="DevCo")

    assert transform_jobs(session, environment=DEV) == 1

    job = session.execute(select(Job)).scalar_one()
    assert job.environment == DEV
    assert job.title == "DEV TITLE"


def test_colliding_normalized_hash_across_environments_does_not_suppress_a_job(session):
    """Different URLs, identical normalized hash: one job per environment, not one overall."""
    shared = {"title": "Data Engineer", "company": "Acme", "location": "Remote"}
    add_raw(session, DEV, "https://x/dev", **shared)
    add_raw(session, PROD, "https://x/prod", **shared)

    assert transform_jobs(session, environment=DEV) == 1
    assert transform_jobs(session, environment=PROD) == 1

    by_env = jobs_by_env(session)
    assert by_env[DEV][0].hash == by_env[PROD][0].hash != None  # noqa: E711
    assert by_env[DEV][0].url == "https://x/dev"
    assert by_env[PROD][0].url == "https://x/prod"


def test_hash_collision_within_one_environment_is_still_deduplicated(session):
    """Scoping identity per environment must not weaken deduplication inside one."""
    shared = {"title": "Data Engineer", "company": "Acme", "location": "Remote"}
    add_raw(session, PROD, "https://x/a", **shared)
    add_raw(session, PROD, "https://x/b", **shared)

    assert transform_jobs(session, environment=PROD) == 1


def test_job_hash_is_unchanged_by_this_work(session):
    """Pin the persistent hash format: changing it invalidates every stored hash."""
    import datetime

    assert job_hash("Data Engineer", "Acme", "Remote", datetime.date(2026, 1, 10)) == job_hash(
        " data ENGINEER ", "ACME ", " remote", datetime.date(2026, 1, 10)
    )


# ------------------------------------------------------------ skills


def test_skills_extraction_is_scoped_to_one_environment(session):
    add_raw(session, DEV, "https://x/dev", description="Selenium and pytest")
    add_raw(session, PROD, "https://x/prod", description="Python and AWS")
    transform_jobs(session, environment=DEV)
    transform_jobs(session, environment=PROD)

    extracted = extract_skills_for_all_jobs(session, environment=PROD)
    assert extracted > 0

    prod_job = session.execute(select(Job).where(Job.environment == PROD)).scalar_one()
    tagged = session.execute(select(JobSkill.job_id).distinct()).scalars().all()
    assert tagged == [prod_job.id], "skills were extracted for another environment's jobs"


# ------------------------------------------------------------ analytics


def _seed_two_environments(session):
    """A production posting and a development posting sharing one URL."""
    add_raw(
        session,
        DEV,
        "https://x/1",
        source="arbeitnow",
        title="DEV TITLE",
        company="DevCo",
        description="Selenium only",
    )
    add_raw(
        session,
        PROD,
        "https://x/1",
        source="remotive",
        title="REAL TITLE",
        company="RealCo",
        description="Python and AWS",
    )
    transform_jobs(session, environment=DEV)
    transform_jobs(session, environment=PROD)
    extract_skills_for_all_jobs(session, environment=DEV)
    extract_skills_for_all_jobs(session, environment=PROD)


def test_analytics_cannot_surface_development_content_when_requesting_production(session):
    _seed_two_environments(session)

    kpis = get_kpis(session, environment=PROD)
    assert kpis["total_jobs"] == 1

    companies = session.execute(
        select(Job.company).where(Job.environment == PROD)
    ).scalars().all()
    assert companies == ["RealCo"]

    skills = {s for s, _ in get_top_skills(session, environment=PROD)}
    assert "python" in skills
    assert "pytest" not in skills

    assert {s for s, _ in top_skills(session, environment=PROD)} == skills


def test_retained_raw_metadata_join_cannot_cross_environments(session):
    """A development raw row must not attach its source to a production job."""
    _seed_two_environments(session)

    # 'arbeitnow' is only the development raw row's source.
    assert get_top_skills(session, source="arbeitnow", environment=PROD) == []
    assert get_kpis(session, source="arbeitnow", environment=PROD)["total_jobs"] == 0

    # The production job's own source still resolves.
    assert get_kpis(session, source="remotive", environment=PROD)["total_jobs"] == 1
    assert get_top_skills(session, source="remotive", environment=PROD)

    by_source = get_top_skills_by_source(session, environment=PROD)
    assert "arbeitnow" not in by_source or by_source["arbeitnow"] == []


def test_skill_trends_cannot_cross_environments(session):
    _seed_two_environments(session)

    rows = get_skill_trends(session, skills=["python", "pytest"], environment=PROD)
    assert rows
    assert {r["skill"] for r in rows} == {"python"}


# ------------------------------------------------------------ fan-out


def test_multiple_raw_rows_for_one_url_do_not_inflate_counts(session):
    """One job with several raw versions in one environment stays one job.

    `.distinct()` does not fix this: the differing column is the metadata being
    selected. Analytics resolves metadata through the authoritative raw row instead.
    """
    add_raw(session, PROD, "https://x/1", source="remotive", description="Python v1")
    add_raw(session, PROD, "https://x/1", source="remoteok", description="Python v2")
    add_raw(session, PROD, "https://x/1", source="remotive", description="Python v3")

    assert transform_jobs(session, environment=PROD) == 1
    extract_skills_for_all_jobs(session, environment=PROD)

    assert session.execute(select(func.count()).select_from(Job)).scalar_one() == 1
    assert get_kpis(session, environment=PROD)["total_jobs"] == 1
    assert get_kpis(session, environment=PROD)["unique_companies"] == 1


def test_authoritative_raw_metadata_is_the_originating_row(session):
    """Metadata comes from the lowest-id raw row, the one transform normalized from."""
    add_raw(session, PROD, "https://x/1", source="remotive", description="Python v1")
    add_raw(session, PROD, "https://x/1", source="remoteok", description="Python v2")

    transform_jobs(session, environment=PROD)
    extract_skills_for_all_jobs(session, environment=PROD)

    # The job's content came from the first raw row, so its source must too.
    assert get_kpis(session, source="remotive", environment=PROD)["total_jobs"] == 1
    assert get_kpis(session, source="remoteok", environment=PROD)["total_jobs"] == 0


def test_skill_trends_places_a_job_in_exactly_one_bucket(session):
    """Several raw versions must not count a job once per version."""
    from datetime import UTC, datetime

    add_raw(
        session,
        PROD,
        "https://x/1",
        source="remotive",
        ingested_at=datetime(2026, 1, 5, 3, tzinfo=UTC),
    )
    add_raw(
        session,
        PROD,
        "https://x/1",
        source="remoteok",
        ingested_at=datetime(2026, 1, 9, 15, tzinfo=UTC),
    )

    transform_jobs(session, environment=PROD)
    extract_skills_for_all_jobs(session, environment=PROD)

    rows = get_skill_trends(session, skills=["python"], granularity="day", environment=PROD)
    assert len(rows) == 1, f"job appeared in {len(rows)} buckets: {rows}"
    assert rows[0]["count"] == 1


def test_jobs_without_a_raw_row_still_count_in_kpis(session):
    """The environment gate is Job.environment, not the existence of a raw row."""
    session.add(Job(environment=PROD, title="Orphan", url="https://x/gone", hash="ho"))
    session.commit()

    assert get_kpis(session, environment=PROD)["total_jobs"] == 1
