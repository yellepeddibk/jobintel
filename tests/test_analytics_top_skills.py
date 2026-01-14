"""Tests for analytics top_skills function."""

from fixtures import seed_and_transform

from jobintel.analytics.top_skills import top_skills


def test_top_skills_returns_counts(session):
    """top_skills should return skill names with positive counts."""
    seed_and_transform(session, environment="test")

    rows = top_skills(session, limit=50)
    skills = {s for (s, _) in rows}

    # Our test data has Python in multiple jobs
    assert "python" in skills, "Python should be in top skills"
    assert all(n > 0 for (_, n) in rows), "All counts should be positive"
