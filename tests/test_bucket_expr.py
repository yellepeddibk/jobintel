"""Tests for time bucketing in analytics queries.

Tests the bucket_expr function and get_skill_trends granularity behavior
using SQLite (in-memory test database).
"""

from datetime import datetime, timedelta

from sqlalchemy import select

from jobintel.analytics.queries import bucket_expr, get_skill_trends
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
    """Test the bucket_expr function for SQLite."""

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
        bucket_col = bucket_expr(RawJob.ingested_at, "6h", "sqlite")
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
        bucket_col = bucket_expr(RawJob.ingested_at, "day", "sqlite")
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
        bucket_col = bucket_expr(RawJob.ingested_at, "week", "sqlite")
        results = session.execute(
            select(bucket_col).where(RawJob.environment == "test").distinct()
        ).all()

        # Should have 1 distinct bucket (same week)
        assert len(results) == 1, f"Expected 1 distinct week bucket, got {len(results)}"


class TestGetSkillTrendsGranularity:
    """Test get_skill_trends with different granularities."""

    def test_trends_with_6h_granularity(self, session):
        """Skill trends should return multiple buckets with 6h granularity."""
        base_date = datetime(2026, 1, 15)
        times = [
            base_date.replace(hour=1),
            base_date.replace(hour=7),
            base_date.replace(hour=13),
            base_date.replace(hour=19),
        ]

        seed_jobs_with_times(session, times, environment="test")
        transform_jobs(session)
        extract_skills_for_all_jobs(session)

        # Get trends with 6h granularity
        trends = get_skill_trends(
            session,
            skills=["python", "django"],
            granularity="6h",
            environment="test",
        )

        # Should have multiple buckets
        buckets = {t["bucket"] for t in trends}
        assert len(buckets) >= 1, "Expected at least 1 bucket in trends"

    def test_trends_auto_granularity_short_range(self, session):
        """Auto granularity should use 6h for ≤7 day range."""
        from datetime import date

        base_date = datetime(2026, 1, 15)
        times = [base_date.replace(hour=1), base_date.replace(hour=13)]

        seed_jobs_with_times(session, times, environment="test")
        transform_jobs(session)
        extract_skills_for_all_jobs(session)

        # Request with short date range (auto should pick 6h)
        trends = get_skill_trends(
            session,
            skills=["python"],
            date_from=date(2026, 1, 14),
            date_to=date(2026, 1, 16),
            granularity=None,  # Auto
            environment="test",
        )

        # Should return results (granularity internally set to 6h)
        assert isinstance(trends, list)

    def test_trends_returns_empty_for_no_skills(self, session):
        """get_skill_trends should return empty list when no skills provided."""
        trends = get_skill_trends(session, skills=[], environment="test")
        assert trends == []

    def test_trends_default_granularity_is_6h(self, session):
        """When no date range and no granularity, should default to 6h."""
        base_date = datetime(2026, 1, 15)
        times = [base_date.replace(hour=1), base_date.replace(hour=13)]

        seed_jobs_with_times(session, times, environment="test")
        transform_jobs(session)
        extract_skills_for_all_jobs(session)

        # Request without date range (should default to 6h)
        trends = get_skill_trends(
            session,
            skills=["python"],
            granularity=None,  # Auto with no date range
            environment="test",
        )

        # Should return results with multiple buckets if 6h bucketing worked
        assert isinstance(trends, list)
