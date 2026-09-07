"""Tests for ETL transform step."""

import hashlib
from datetime import UTC, date, datetime

import pytest
from fixtures import TEST_JOB_DUPLICATE, seed_test_data
from sqlalchemy import select

from jobintel.etl.raw import upsert_raw_job
from jobintel.etl.transform import _safe_date, job_hash, transform_jobs
from jobintel.models import Job, RawJob


def test_transform_dedupes_by_url(session):
    """Transform should deduplicate jobs by URL."""
    # Insert test data (4 unique jobs)
    seed_test_data(session, environment="test")

    # Insert a duplicate (same URL as first job)
    upsert_raw_job(session, TEST_JOB_DUPLICATE, environment="test")
    session.commit()

    inserted = transform_jobs(session, environment="test")

    # Should only create 4 unique jobs (duplicate URL skipped)
    assert inserted == 4

    jobs = session.query(Job).all()
    assert len(jobs) == 4

    # Verify no duplicate URLs
    urls = [j.url for j in jobs]
    assert len(urls) == len(set(urls)), "Jobs should have unique URLs"


class TestSafeDate:
    """_safe_date must accept what the adapters actually emit.

    It previously used date.fromisoformat, which rejects every datetime string the
    adapters produce, so posted_at was NULL for effectively every real posting and
    the date component of job_hash collapsed to an empty string.
    """

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # Real adapter output.
            ("2026-01-08T00:00:00+00:00", date(2026, 1, 8)),  # arbeitnow
            ("2024-01-15T12:00:00", date(2024, 1, 15)),  # remotive
            (1767830400, date(2026, 1, 8)),  # remoteok epoch
            # Other shapes a source could reasonably send.
            ("2026-01-10", date(2026, 1, 10)),
            ("2024-01-15T12:00:00Z", date(2024, 1, 15)),
            ("2024-01-15 12:00:00", date(2024, 1, 15)),
            ("2024-01-15T12:00:00-05:00", date(2024, 1, 15)),
            (1767830400.0, date(2026, 1, 8)),
            ("1767830400", date(2026, 1, 8)),
            (date(2025, 3, 4), date(2025, 3, 4)),
            (datetime(2025, 3, 4, 9, 30), date(2025, 3, 4)),
            (datetime(2025, 3, 4, 9, 30, tzinfo=UTC), date(2025, 3, 4)),
        ],
    )
    def test_accepts_supported_values(self, value, expected):
        assert _safe_date(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            "not a date",
            "2026-13-45",  # impossible calendar date
            "2026-01-08T99:99:99",
            [],
            {},
            10**20,  # absurd epoch, out of range
            -(10**20),
            "-",
            ".",
        ],
    )
    def test_rejects_unusable_values(self, value):
        assert _safe_date(value) is None

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_is_never_an_epoch(self, value):
        """bool subclasses int, so an unguarded epoch branch turns True into 1970-01-01."""
        assert _safe_date(value) is None

    def test_is_deterministic(self):
        value = "2026-01-08T00:00:00+00:00"
        assert len({_safe_date(value) for _ in range(5)}) == 1


class TestJobHashDateSensitivity:
    """The date component must actually distinguish otherwise-identical postings."""

    def test_different_dates_produce_different_hashes(self):
        a = job_hash("Data Engineer", "Acme", "Remote", date(2026, 1, 5))
        b = job_hash("Data Engineer", "Acme", "Remote", date(2026, 2, 5))
        assert a != b

    def test_same_date_and_fields_still_collide(self):
        """Deduplication must not be weakened by the parser fix."""
        a = job_hash("Data Engineer", "Acme", "Remote", date(2026, 1, 5))
        b = job_hash(" data ENGINEER ", "ACME ", " remote", date(2026, 1, 5))
        assert a == b

    def test_hash_serialization_is_unchanged(self):
        """Pin the format: the key set and separator are persistent data behavior."""
        expected = hashlib.sha256(
            b"data engineer|acme|remote|2026-01-05"
        ).hexdigest()
        assert job_hash("Data Engineer", "Acme", "Remote", date(2026, 1, 5)) == expected

    def test_missing_date_still_hashes_to_empty_component(self):
        expected = hashlib.sha256(b"data engineer|acme|remote|").hexdigest()
        assert job_hash("Data Engineer", "Acme", "Remote", None) == expected


def test_repostings_are_no_longer_suppressed_by_unparseable_dates(session):
    """The regression the parser fix exists to remove.

    Three genuine repostings of one role, each with a different date, arriving as
    the ISO datetimes every adapter emits. Under the old parser all three dates
    became None, every hash collapsed to the same value, and two real postings were
    silently dropped.
    """
    for i, posted in enumerate(
        ["2026-01-05T09:00:00+00:00", "2026-02-05T09:00:00+00:00", "2026-03-05T09:00:00+00:00"]
    ):
        session.add(
            RawJob(
                source="remotive",
                environment="test",
                payload_json={
                    "url": f"https://example.com/repost/{i}",
                    "title": "Data Engineer",
                    "company": "Acme",
                    "location": "Remote",
                    "posted_at": posted,
                    "description": "Python",
                },
            )
        )
    session.commit()

    assert transform_jobs(session, environment="test") == 3

    jobs = session.execute(select(Job).where(Job.environment == "test")).scalars().all()
    assert sorted(j.posted_at for j in jobs) == [
        date(2026, 1, 5),
        date(2026, 2, 5),
        date(2026, 3, 5),
    ]
    assert len({j.hash for j in jobs}) == 3, "each posting must hash distinctly"


def test_identical_postings_on_the_same_date_still_dedupe(session):
    """The fix must not weaken deduplication for genuine duplicates."""
    for i in range(3):
        session.add(
            RawJob(
                source="remotive",
                environment="test",
                payload_json={
                    "url": f"https://example.com/dupe/{i}",
                    "title": "Data Engineer",
                    "company": "Acme",
                    "location": "Remote",
                    "posted_at": "2026-01-05T09:00:00+00:00",
                    "description": "Python",
                },
            )
        )
    session.commit()

    assert transform_jobs(session, environment="test") == 1
