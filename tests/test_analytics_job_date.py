"""Every Analytics widget uses the same job date (posting date, else ingestion date)."""

from datetime import date, datetime, timedelta

import pytest

from jobintel.analytics.queries import (
    get_kpis,
    get_skill_trends,
    get_top_skills,
    get_top_skills_by_source,
)
from jobintel.models import Job, JobSkill, RawJob

ENV = "test"
TODAY = date.today()


def _at_noon(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time()).replace(hour=12)


def _add_job(session, n: int, *, posted: date | None, ingested: date, source="remotive"):
    url = f"https://example.invalid/{n}"
    session.add(
        RawJob(
            source=source,
            payload_json={"url": url},
            ingested_at=_at_noon(ingested),
            environment=ENV,
        )
    )
    job = Job(
        environment=ENV,
        title=f"Engineer {n}",
        company=f"Company {n}",
        url=url,
        hash=f"hash-{n}",
        posted_at=posted,
    )
    session.add(job)
    session.flush()
    session.add(JobSkill(job_id=job.id, skill="python"))
    session.commit()


def _trend_total(session, **kwargs) -> int:
    return sum(
        t["count"] for t in get_skill_trends(session, skills=["python"], environment=ENV, **kwargs)
    )


def _widget_counts(session, date_from: date) -> dict:
    kpis = get_kpis(session, date_from=date_from, environment=ENV)
    top = dict(get_top_skills(session, date_from=date_from, environment=ENV))
    by_source = get_top_skills_by_source(session, date_from=date_from, environment=ENV)
    return {
        "kpi_total": kpis["total_jobs"],
        "kpi_companies": kpis["unique_companies"],
        "top_skills": top.get("python", 0),
        "by_source": sum(dict(skills).get("python", 0) for skills in by_source.values()),
        "trends": _trend_total(session, date_from=date_from),
    }


def test_one_range_selects_the_same_jobs_on_every_widget(session):
    """Posted 40 days ago but fetched yesterday: outside "Last 30 days" everywhere."""
    _add_job(session, 1, posted=TODAY - timedelta(days=40), ingested=TODAY - timedelta(days=1))
    _add_job(session, 2, posted=TODAY - timedelta(days=5), ingested=TODAY - timedelta(days=1))

    counts = _widget_counts(session, date_from=TODAY - timedelta(days=30))

    assert counts == {
        "kpi_total": 1,
        "kpi_companies": 1,
        "top_skills": 1,
        "by_source": 1,
        "trends": 1,
    }


def test_trends_group_by_posting_date_not_ingestion(session):
    """Fetched in one run, posted on two days: two daily buckets."""
    fetched = TODAY - timedelta(days=1)
    _add_job(session, 1, posted=TODAY - timedelta(days=3), ingested=fetched)
    _add_job(session, 2, posted=TODAY - timedelta(days=2), ingested=fetched)

    trends = get_skill_trends(session, skills=["python"], granularity="day", environment=ENV)

    assert len({t["bucket"] for t in trends}) == 2
    assert {t["count"] for t in trends} == {1}


def test_job_without_posting_date_counts_by_ingestion_date(session):
    """No posting date: counted on the day it was fetched, not dropped."""
    _add_job(session, 1, posted=None, ingested=TODAY - timedelta(days=2))

    counts = _widget_counts(session, date_from=TODAY - timedelta(days=7))
    assert counts == {
        "kpi_total": 1,
        "kpi_companies": 1,
        "top_skills": 1,
        "by_source": 1,
        "trends": 1,
    }
    assert get_kpis(session, environment=ENV)["jobs_last_7d"] == 1

    old = _widget_counts(session, date_from=TODAY - timedelta(days=1))
    assert old["kpi_total"] == 0
    assert old["trends"] == 0


def test_source_filter_still_applies_with_a_date_range(session):
    _add_job(session, 1, posted=TODAY - timedelta(days=2), ingested=TODAY, source="remotive")
    _add_job(session, 2, posted=TODAY - timedelta(days=2), ingested=TODAY, source="arbeitnow")

    since = TODAY - timedelta(days=7)
    assert get_kpis(session, source="remotive", date_from=since, environment=ENV)["total_jobs"] == 1
    assert dict(get_top_skills(session, source="remotive", date_from=since, environment=ENV)) == {
        "python": 1
    }
    assert _trend_total(session, source="remotive", date_from=since) == 1


@pytest.mark.parametrize("granularity", ["day", "week"])
def test_trends_accept_day_and_week(session, granularity):
    _add_job(session, 1, posted=TODAY - timedelta(days=2), ingested=TODAY)

    assert _trend_total(session, granularity=granularity) == 1
