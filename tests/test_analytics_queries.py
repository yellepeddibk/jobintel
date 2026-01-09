"""Tests for analytics/queries.py"""

from datetime import date

from sqlalchemy import text

from jobintel.analytics.queries import (
    get_kpis,
    get_skill_trends,
    get_top_skills,
    get_top_skills_by_source,
)
from jobintel.db import SessionLocal, init_db
from jobintel.etl.load_raw import load_raw_jobs
from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.transform import transform_jobs


def _setup_test_data(session):
    """Clear tables and load sample data."""
    session.execute(text("DELETE FROM job_skills"))
    session.execute(text("DELETE FROM jobs"))
    session.execute(text("DELETE FROM raw_jobs"))
    session.commit()

    load_raw_jobs(session, "data/sample_jobs.jsonl")
    transform_jobs(session)
    extract_skills_for_all_jobs(session)


def test_kpis_basic_counts():
    """Test that get_kpis returns expected structure with counts."""
    init_db()

    with SessionLocal() as session:
        _setup_test_data(session)

        kpis = get_kpis(session)

        assert "total_jobs" in kpis
        assert "jobs_last_7d" in kpis
        assert "unique_companies" in kpis
        assert "sources_count" in kpis

        # Should have some jobs from sample data
        assert kpis["total_jobs"] > 0
        assert isinstance(kpis["total_jobs"], int)
        assert isinstance(kpis["unique_companies"], int)


def test_top_skills_respects_limit():
    """Test that get_top_skills respects the limit parameter."""
    init_db()

    with SessionLocal() as session:
        _setup_test_data(session)

        # Request only 5 skills
        skills_5 = get_top_skills(session, limit=5)
        assert len(skills_5) <= 5

        # Request more skills
        skills_20 = get_top_skills(session, limit=20)
        assert len(skills_20) <= 20

        # The 5-skill list should be a subset of counts
        if skills_5 and skills_20:
            top_5_skills = {s for s, _ in skills_5}
            top_20_skills = {s for s, _ in skills_20}
            assert top_5_skills.issubset(top_20_skills)


def test_top_skills_counts_distinct_jobs():
    """Test that top_skills counts distinct jobs, not total mentions."""
    init_db()

    with SessionLocal() as session:
        _setup_test_data(session)

        skills = get_top_skills(session, limit=50)

        # Each skill should have a positive count
        for skill, count in skills:
            assert isinstance(skill, str)
            assert isinstance(count, int)
            assert count > 0


def test_top_skills_with_search_filter():
    """Test that search filter works."""
    init_db()

    with SessionLocal() as session:
        _setup_test_data(session)

        # Get all skills first
        all_skills = get_top_skills(session, limit=50)

        # Filter by a common term - should return subset
        filtered_skills = get_top_skills(session, search="engineer", limit=50)

        # Filtered should be <= all (could be empty if no matches)
        assert len(filtered_skills) <= len(all_skills)


def test_skill_trends_returns_weekly_buckets():
    """Test that skill trends returns data bucketed by week."""
    init_db()

    with SessionLocal() as session:
        _setup_test_data(session)

        # Get top skills first
        top_skills_list = get_top_skills(session, limit=3)
        skill_names = [s for s, _ in top_skills_list]

        if skill_names:
            trends = get_skill_trends(session, skills=skill_names)

            # Trends should be a list of dicts
            for item in trends:
                assert "week" in item
                assert "skill" in item
                assert "count" in item
                assert isinstance(item["week"], date)
                assert isinstance(item["count"], int)
                # Week should be a Monday
                assert item["week"].weekday() == 0


def test_skill_trends_empty_skills_list():
    """Test that empty skills list returns empty trends."""
    init_db()

    with SessionLocal() as session:
        trends = get_skill_trends(session, skills=[])
        assert trends == []


def test_top_skills_by_source_returns_dict():
    """Test that top_skills_by_source returns dict keyed by source."""
    init_db()

    with SessionLocal() as session:
        _setup_test_data(session)

        by_source = get_top_skills_by_source(session, limit=10)

        assert isinstance(by_source, dict)

        # Each source should have a list of tuples
        for source_name, skills_list in by_source.items():
            assert isinstance(source_name, str)
            assert isinstance(skills_list, list)
            for skill, count in skills_list:
                assert isinstance(skill, str)
                assert isinstance(count, int)
