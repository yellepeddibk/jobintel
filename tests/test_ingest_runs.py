"""Tests for ingest run logging."""

from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from jobintel.db import SessionLocal, init_db
from jobintel.etl.pipeline import run_ingest
from jobintel.models import IngestRun, Job


@pytest.fixture(autouse=True)
def setup_db():
    """Ensure tables exist before each test."""
    init_db()
    yield
    # Clean up ingest_runs after each test
    with SessionLocal() as s:
        s.query(IngestRun).delete()
        s.commit()


def test_successful_ingest_logs_run():
    """Test that a successful ingest creates an IngestRun record."""
    fake_payloads = [
        {
            "id": "test-123",
            "source": "remotive",
            "title": "Test Engineer",
            "company_name": "Test Co",
            "candidate_required_location": "Remote",
            "url": "https://example.com/test-123",
            "description": "Testing with Python and pytest",
            "publication_date": "2024-01-15",
        }
    ]

    with patch("jobintel.etl.pipeline.fetch_from_source") as mock_fetch:
        mock_fetch.return_value = (fake_payloads, [])

        with SessionLocal() as session:
            result = run_ingest(session, "remotive", "test", 10)

            assert result.fetched == 1

            # Check that an IngestRun record was created
            run = session.query(IngestRun).order_by(IngestRun.id.desc()).first()
            assert run is not None
            assert run.source == "remotive"
            assert run.search == "test"
            assert run.limit == 10
            assert run.status == "success"
            assert run.fetched == 1
            assert run.finished_at is not None
            assert run.error is None


def test_successful_ingest_with_warnings_logs_run():
    """Test that warnings are captured in the IngestRun record."""
    fake_payloads = [
        {
            "id": "test-456",
            "source": "remotive",
            "title": "Test Dev",
            "company_name": "Test Inc",
            "candidate_required_location": "Remote",
            "url": "https://example.com/test-456",
            "description": "JavaScript development",
            "publication_date": "2024-01-16",
        }
    ]
    warnings = ["Skipped 2 invalid payloads"]

    with patch("jobintel.etl.pipeline.fetch_from_source") as mock_fetch:
        mock_fetch.return_value = (fake_payloads, warnings)

        with SessionLocal() as session:
            result = run_ingest(session, "remotive", "dev", 20)

            assert result.warnings == warnings

            run = session.query(IngestRun).order_by(IngestRun.id.desc()).first()
            assert run is not None
            assert run.status == "success"
            assert run.warnings == warnings


def test_failed_ingest_logs_error():
    """Test that a failed ingest logs the error."""
    with patch("jobintel.etl.pipeline.fetch_from_source") as mock_fetch:
        mock_fetch.side_effect = ValueError("API connection failed")

        with SessionLocal() as session:
            with pytest.raises(ValueError, match="API connection failed"):
                run_ingest(session, "remotive", "test", 10)

            # Check that an IngestRun record was created with failed status
            run = session.query(IngestRun).order_by(IngestRun.id.desc()).first()
            assert run is not None
            assert run.source == "remotive"
            assert run.status == "failed"
            assert run.error == "API connection failed"
            assert run.finished_at is not None


def test_ingest_with_empty_search():
    """Test that empty search is stored as None."""
    with patch("jobintel.etl.pipeline.fetch_from_source") as mock_fetch:
        mock_fetch.return_value = ([], [])

        with SessionLocal() as session:
            run_ingest(session, "remotive", "", 10)

            run = session.query(IngestRun).order_by(IngestRun.id.desc()).first()
            assert run is not None
            assert run.search is None


def _etl_with_flush_error(session, payloads, environment=None):
    """Stand-in ETL that fails the way a constraint violation mid-ETL does."""
    session.add(Job(title="no environment"))  # violates NOT NULL on flush
    session.flush()


def test_database_error_mid_etl_is_recorded_and_reraised(engine):
    """A failed flush leaves the session needing a rollback before it can record."""
    factory = sessionmaker(bind=engine)

    with (
        patch("jobintel.etl.pipeline.fetch_from_source", return_value=([], [])),
        patch("jobintel.etl.pipeline.run_etl_from_payloads", side_effect=_etl_with_flush_error),
    ):
        with factory() as session:
            # The original error, not a PendingRollbackError raised while recording it.
            with pytest.raises(IntegrityError, match="NOT NULL"):
                run_ingest(session, "remotive", "", 10, environment="test")
            # The caller's session is usable again afterwards.
            assert session.execute(select(1)).scalar() == 1

    with factory() as s:
        run = s.execute(select(IngestRun)).scalar_one()
    assert run.status == "failed"
    assert "NOT NULL" in run.error
    assert run.finished_at is not None


def test_original_error_survives_when_recording_the_failure_fails(engine, caplog):
    """If the database cannot record the failure, the real cause still propagates."""
    factory = sessionmaker(bind=engine)

    with factory() as session:
        real_commit = session.commit
        commits = {"n": 0}

        def commit_then_fail():
            commits["n"] += 1
            if commits["n"] > 1:  # the first commit creates the 'running' row
                raise OperationalError("COMMIT", {}, Exception("database is unreachable"))
            real_commit()

        session.commit = commit_then_fail
        with patch(
            "jobintel.etl.pipeline.fetch_from_source", side_effect=ValueError("upstream down")
        ):
            with pytest.raises(ValueError, match="upstream down"):
                run_ingest(session, "remotive", "", 10, environment="test")

    assert "Could not record failed ingest run" in caplog.text
    assert "database is unreachable" in caplog.text
