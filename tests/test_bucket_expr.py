"""Tests for time bucketing in analytics queries.

Tests the bucket_expr function and get_skill_trends granularity behavior
using SQLite (in-memory test database).
"""

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from jobintel.analytics.queries import auto_granularity, bucket_expr, get_skill_trends
from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.transform import transform_jobs
from jobintel.models import RawJob


def seed_jobs_with_times(session, times: list[datetime], environment: str = "test") -> int:
    """Seed RawJob records with specific ingested_at times.

    Creates jobs with skills for trends testing.
    """
    inserted = 0
    for i, ts in enumerate(times):
        raw = RawJob(
            source="test_source",
            payload_json={
                "url": f"https://test.com/job/{i}",
                "title": f"Test Job {i}",
                "company": "TestCorp",
                "location": "Remote",
                "description": "Python developer with Django and PostgreSQL experience.",
                "tags": ["python", "django"],
                "posted_at": ts.date().isoformat(),
            },
            ingested_at=ts,
            environment=environment,
        )
        session.add(raw)
        inserted += 1

    session.commit()
    return inserted


class TestBucketExpr:
    """Test bucket_expr on whichever database the session fixture provides."""

    def test_bucket_6h_creates_4_buckets_per_day(self, session):
        """6h bucketing should create 4 distinct buckets per day (00, 06, 12, 18)."""
        # Create jobs at hours 01, 07, 13, 19 (should map to buckets 00, 06, 12, 18)
        base_date = datetime(2026, 1, 15)
        times = [
            base_date.replace(hour=1),   # -> 00:00
            base_date.replace(hour=7),   # -> 06:00
            base_date.replace(hour=13),  # -> 12:00
            base_date.replace(hour=19),  # -> 18:00
        ]

        inserted = seed_jobs_with_times(session, times, environment="test")
        assert inserted == 4, f"Expected 4 jobs, got {inserted}"

        # Verify we have 4 RawJob rows
        raw_jobs = session.execute(select(RawJob)).scalars().all()
        assert len(raw_jobs) >= 4, "Need at least 4 RawJob rows seeded for bucketing tests"

        # Query with bucket_expr for 6h
        bucket_col = bucket_expr(RawJob.ingested_at, "6h", session.bind.dialect.name)
        results = session.execute(
            select(bucket_col).where(RawJob.environment == "test").distinct()
        ).all()

        # Should have 4 distinct buckets
        assert len(results) == 4, f"Expected 4 distinct 6h buckets, got {len(results)}"

    def test_bucket_day_groups_all_to_one(self, session):
        """Day bucketing should group all same-day records into one bucket."""
        base_date = datetime(2026, 1, 15)
        times = [
            base_date.replace(hour=1),
            base_date.replace(hour=7),
            base_date.replace(hour=13),
            base_date.replace(hour=19),
        ]

        seed_jobs_with_times(session, times, environment="test")

        # Query with bucket_expr for day
        bucket_col = bucket_expr(RawJob.ingested_at, "day", session.bind.dialect.name)
        results = session.execute(
            select(bucket_col).where(RawJob.environment == "test").distinct()
        ).all()

        # Should have 1 distinct bucket (same day)
        assert len(results) == 1, f"Expected 1 distinct day bucket, got {len(results)}"

    def test_bucket_week_groups_same_week(self, session):
        """Week bucketing should group records in the same week."""
        # Create jobs on Mon, Wed, Fri of the same week
        base_monday = datetime(2026, 1, 12)  # A Monday
        times = [
            base_monday,
            base_monday + timedelta(days=2),  # Wednesday
            base_monday + timedelta(days=4),  # Friday
        ]

        seed_jobs_with_times(session, times, environment="test")

        # Query with bucket_expr for week
        bucket_col = bucket_expr(RawJob.ingested_at, "week", session.bind.dialect.name)
        results = session.execute(
            select(bucket_col).where(RawJob.environment == "test").distinct()
        ).all()

        # Should have 1 distinct bucket (same week)
        assert len(results) == 1, f"Expected 1 distinct week bucket, got {len(results)}"


class TestGetSkillTrendsGranularity:
    """Test get_skill_trends with different granularities."""

    def test_trends_refuse_6h_granularity(self, session):
        """Trends are charted by date, which has no time of day, so 6h is refused."""
        with pytest.raises(ValueError, match="granularity"):
            get_skill_trends(session, skills=["python"], granularity="6h", environment="test")

    def test_trends_auto_granularity_short_range(self, session):
        """Auto granularity should use daily buckets for a short range."""
        times = [datetime(2026, 1, 15, 1), datetime(2026, 1, 16, 13)]

        seed_jobs_with_times(session, times, environment="test")
        transform_jobs(session, environment="test")
        extract_skills_for_all_jobs(session, environment="test")

        trends = get_skill_trends(
            session,
            skills=["python"],
            date_from=date(2026, 1, 14),
            date_to=date(2026, 1, 16),
            granularity=None,  # Auto
            environment="test",
        )

        # Posted on two different days: two daily buckets.
        assert len({t["bucket"] for t in trends}) == 2

    def test_trends_auto_with_open_end_uses_the_range_to_today(self, session):
        """A "Last 30 days" range has no end date; Auto must still pick daily."""
        days = [date.today() - timedelta(days=n) for n in (2, 3)]
        times = [datetime.combine(d, datetime.min.time()).replace(hour=12) for d in days]

        seed_jobs_with_times(session, times, environment="test")
        transform_jobs(session, environment="test")
        extract_skills_for_all_jobs(session, environment="test")

        trends = get_skill_trends(
            session,
            skills=["python"],
            date_from=date.today() - timedelta(days=30),
            granularity=None,
            environment="test",
        )

        # Daily: one bucket per posting day.
        assert len({t["bucket"] for t in trends}) == 2

    def test_trends_returns_empty_for_no_skills(self, session):
        """get_skill_trends should return empty list when no skills provided."""
        trends = get_skill_trends(session, skills=[], environment="test")
        assert trends == []

    def test_trends_all_time_auto_is_weekly(self, session):
        """With no date range ("All Time"), Auto groups weekly."""
        # Monday and Wednesday of the same week.
        times = [datetime(2026, 1, 12, 12), datetime(2026, 1, 14, 12)]

        seed_jobs_with_times(session, times, environment="test")
        transform_jobs(session, environment="test")
        extract_skills_for_all_jobs(session, environment="test")

        trends = get_skill_trends(
            session,
            skills=["python"],
            granularity=None,  # Auto with no date range
            environment="test",
        )

        # Weekly, not daily: both jobs share one bucket.
        assert len({t["bucket"] for t in trends}) == 1


TODAY = date(2026, 11, 26)


@pytest.mark.parametrize(
    ("date_from", "date_to", "expected"),
    [
        # The dashboard's ranges: a start date and no end date.
        (TODAY - timedelta(days=7), None, "day"),
        (TODAY - timedelta(days=30), None, "day"),
        (TODAY - timedelta(days=90), None, "week"),
        # All Time: no range at all.
        (None, None, "week"),
        # Explicit ranges, at the 60-day boundary.
        (date(2026, 1, 1), date(2026, 1, 8), "day"),
        (date(2026, 1, 1), date(2026, 3, 2), "day"),
        (date(2026, 1, 1), date(2026, 3, 3), "week"),
    ],
)
def test_auto_granularity(date_from, date_to, expected):
    assert auto_granularity(date_from, date_to, today=TODAY) == expected
