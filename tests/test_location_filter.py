"""The dashboard's US location filter, run as real SQL.

Uses the shared session fixture, so it runs on SQLite and, in CI, on PostgreSQL.
"""

import pytest
from sqlalchemy import select

from jobintel.analytics.location import is_us_query, us_location_clause
from jobintel.models import Job

US_LOCATIONS = [
    "United States",
    "USA",
    "USA Only",
    "US",
    "Remote - US",
    "Remote (US)",
    "US, Canada",
    "Remote, US/Canada",
    "USA timezones",
    "U.S.",
    "U.S.A.",
    "us-remote",
    "San Francisco, CA",
    "Raleigh, NC",
    "Columbus, OH",
    "Austin, TX",
    "Washington, DC",
    "New York",
]

NOT_US_LOCATIONS = [
    # Contain "us" inside a word: the old "%US%" rule matched all of these.
    "Australia",
    "Austria",
    "Russia",
    "Belarus",
    "Cyprus",
    "Mauritius",
    "Busan",
    "Remote - Brussels",
    # End in ", " plus two letters that are not a state code: the old "%, __" rule
    # matched these.
    "London, UK",
    "Toronto, ON",
    "Vancouver, BC",
    "Paris, FR",
    # Neither.
    "Sydney, NSW",
    "Worldwide",
    "Europe",
    "Remote",
    "Americas",
    "North America",
    "Berlin",
]


@pytest.fixture
def jobs_with_locations(session):
    locations = US_LOCATIONS + NOT_US_LOCATIONS + [None]
    for i, location in enumerate(locations):
        session.add(
            Job(
                environment="test",
                title=f"Engineer {i}",
                location=location,
                url=f"https://example.invalid/{i}",
                hash=f"hash-{i}",
            )
        )
    session.commit()
    return session


def _matched(session) -> set:
    return set(
        session.execute(select(Job.location).where(us_location_clause(Job.location))).scalars()
    )


def test_us_filter_matches_us_locations(jobs_with_locations):
    matched = _matched(jobs_with_locations)

    assert set(US_LOCATIONS) <= matched


def test_us_filter_does_not_match_other_locations(jobs_with_locations):
    matched = _matched(jobs_with_locations)

    assert matched.isdisjoint(NOT_US_LOCATIONS)
    assert None not in matched


def test_us_filter_matches_exactly_the_us_list(jobs_with_locations):
    assert _matched(jobs_with_locations) == set(US_LOCATIONS)


def test_state_codes_are_case_sensitive(session):
    """Lowercase two-letter endings are ordinary words, not state codes."""
    session.add(Job(environment="test", title="A", location="Remote, or", url="u1", hash="h1"))
    session.add(Job(environment="test", title="B", location="Remote, OR", url="u2", hash="h2"))
    session.commit()

    assert _matched(session) == {"Remote, OR"}


def test_known_limit_country_code_that_is_also_a_state_code(session):
    """Documented limit: 'DE' is Germany and Delaware, so this still matches."""
    session.add(Job(environment="test", title="A", location="Berlin, DE", url="u1", hash="h1"))
    session.commit()

    assert _matched(session) == {"Berlin, DE"}


@pytest.mark.parametrize("text", ["US", "us", " USA ", "United States", "united states"])
def test_us_queries(text):
    assert is_us_query(text)


@pytest.mark.parametrize("text", ["CA", "Remote", "New York", "U.S.", "America"])
def test_other_queries_are_substring_searches(text):
    assert not is_us_query(text)
