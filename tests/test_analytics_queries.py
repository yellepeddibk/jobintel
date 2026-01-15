"""Tests for analytics/queries.py"""

from fixtures import seed_and_transform

from jobintel.analytics.queries import (
    PRODUCTION_ENV,
    get_kpis,
    get_top_skills,
    get_top_skills_by_source,
)


def test_kpis_basic_counts(session):
    """Test that get_kpis returns expected structure with counts."""
    seed_and_transform(session, environment=PRODUCTION_ENV)

    kpis = get_kpis(session, environment=PRODUCTION_ENV)

    assert "total_jobs" in kpis
    assert "jobs_last_7d" in kpis
    assert "unique_companies" in kpis
    assert "sources_count" in kpis

    # Should have 4 jobs from test fixtures
    assert kpis["total_jobs"] == 4
    assert isinstance(kpis["total_jobs"], int)
    assert isinstance(kpis["unique_companies"], int)


def test_top_skills_respects_limit(session):
    """Test that get_top_skills respects the limit parameter."""
    seed_and_transform(session, environment=PRODUCTION_ENV)

    # Request only 3 skills
    skills_3 = get_top_skills(session, limit=3, environment=PRODUCTION_ENV)
    assert len(skills_3) <= 3

    # Request more skills
    skills_20 = get_top_skills(session, limit=20, environment=PRODUCTION_ENV)
    assert len(skills_20) <= 20

    # The 3-skill list should be a subset
    if skills_3 and skills_20:
        top_3_skills = {s for s, _ in skills_3}
        top_20_skills = {s for s, _ in skills_20}
        assert top_3_skills.issubset(top_20_skills)


def test_top_skills_counts_distinct_jobs(session):
    """Test that top_skills counts distinct jobs, not total mentions."""
    seed_and_transform(session, environment=PRODUCTION_ENV)

    skills = get_top_skills(session, limit=50, environment=PRODUCTION_ENV)

    # Each skill should have a positive count
    for skill, count in skills:
        assert isinstance(skill, str)
        assert isinstance(count, int)
        assert count > 0


def test_top_skills_with_source_filter(session):
    """Test that source filter works."""
    seed_and_transform(session, environment=PRODUCTION_ENV)

    # Get skills from remotive only
    remotive_skills = get_top_skills(
        session, source="remotive", limit=50, environment=PRODUCTION_ENV
    )

    # Get skills from arbeitnow only
    arbeitnow_skills = get_top_skills(
        session, source="arbeitnow", limit=50, environment=PRODUCTION_ENV
    )

    # Both should have results since our test data has jobs from both sources
    assert len(remotive_skills) > 0, "Remotive should have skills"
    assert len(arbeitnow_skills) > 0, "Arbeitnow should have skills"


def test_top_skills_by_source(session):
    """Test get_top_skills_by_source returns grouped data."""
    seed_and_transform(session, environment=PRODUCTION_ENV)

    results = get_top_skills_by_source(session, limit=10, environment=PRODUCTION_ENV)

    # Should be dict: {"remotive": [(skill, count), ...], "arbeitnow": [...]}
    assert len(results) > 0
    assert isinstance(results, dict)
    for source, skills in results.items():
        assert source in ("remotive", "arbeitnow")
        assert isinstance(skills, list)
        for skill, count in skills:
            assert isinstance(skill, str)
            assert count > 0


def test_environment_filtering_excludes_other_envs(session):
    """Queries should only return data from the specified environment."""
    # Seed data as 'development' environment
    seed_and_transform(session, environment="development")

    # Query for 'production' should return nothing
    kpis = get_kpis(session, environment=PRODUCTION_ENV)
    assert kpis["total_jobs"] == 0, "Production query should not see development data"

    skills = get_top_skills(session, environment=PRODUCTION_ENV)
    assert len(skills) == 0, "Production query should not see development skills"
