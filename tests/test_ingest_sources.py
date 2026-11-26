"""run_ingest_sources: one failing source must not stop the others.

fetch_from_source is mocked, so no network is used; data goes to the in-memory
SQLite engine from conftest.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jobintel.etl import pipeline
from jobintel.etl.pipeline import run_ingest_sources
from jobintel.models import IngestRun, Job

SOURCES = ["remotive", "arbeitnow", "remoteok"]
ENV = "test"


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine)


def _payload(source: str) -> dict:
    return {
        "source": source,
        "url": f"https://example.invalid/{source}/1",
        "title": f"{source} engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "python and sql",
        "posted_at": "2026-01-05",
    }


def _fetch_failing_for(*failing: str):
    def fake_fetch(source_name, search, limit):
        if source_name in failing:
            raise RuntimeError(f"{source_name} upstream unavailable")
        return [_payload(source_name)], []

    return fake_fetch


def _runs(session_factory) -> dict[str, IngestRun]:
    with session_factory() as s:
        return {r.source: r for r in s.execute(select(IngestRun)).scalars()}


@pytest.mark.parametrize("failing", SOURCES)
def test_one_failing_source_does_not_stop_the_others(session_factory, failing):
    fake = _fetch_failing_for(failing)
    with patch.object(pipeline, "fetch_from_source", side_effect=fake) as mock_fetch:
        outcomes = run_ingest_sources(session_factory, SOURCES, "", 10, environment=ENV)

    assert [c.args[0] for c in mock_fetch.call_args_list] == SOURCES
    assert [o.source for o in outcomes] == SOURCES
    for outcome in outcomes:
        if outcome.source == failing:
            assert not outcome.ok
            assert outcome.result is None
            assert str(outcome.error) == f"{failing} upstream unavailable"
        else:
            assert outcome.ok
            assert outcome.error is None
            assert outcome.result.fetched == 1

    runs = _runs(session_factory)
    assert runs[failing].status == "failed"
    assert runs[failing].error == f"{failing} upstream unavailable"
    for source in SOURCES:
        if source != failing:
            assert runs[source].status == "success"

    with session_factory() as s:
        urls = set(s.execute(select(Job.url).where(Job.environment == ENV)).scalars())
    assert urls == {_payload(s)["url"] for s in SOURCES if s != failing}


def test_all_sources_succeed(session_factory):
    with patch.object(pipeline, "fetch_from_source", side_effect=_fetch_failing_for()):
        outcomes = run_ingest_sources(session_factory, SOURCES, "", 10, environment=ENV)

    assert all(o.ok for o in outcomes)
    assert {r.status for r in _runs(session_factory).values()} == {"success"}


def test_a_source_that_breaks_its_session_does_not_stop_the_next(session_factory):
    """A database error mid-ETL leaves that session needing a rollback.

    Each source gets a fresh session, so the next one is unaffected. What the broken
    source itself records is out of scope here.
    """
    real_etl = pipeline.run_etl_from_payloads

    def etl(session, payloads, environment=None):
        if payloads and payloads[0]["source"] == "arbeitnow":
            session.add(Job(title="no environment"))  # violates NOT NULL on flush
            session.flush()
        return real_etl(session, payloads, environment=environment)

    with (
        patch.object(pipeline, "fetch_from_source", side_effect=_fetch_failing_for()),
        patch.object(pipeline, "run_etl_from_payloads", side_effect=etl),
    ):
        outcomes = run_ingest_sources(session_factory, SOURCES, "", 10, environment=ENV)

    assert [o.ok for o in outcomes] == [True, False, True]
    assert _runs(session_factory)["remoteok"].status == "success"


def test_keyboard_interrupt_is_not_swallowed(session_factory):
    def interrupt(source_name, search, limit):
        raise KeyboardInterrupt

    with patch.object(pipeline, "fetch_from_source", side_effect=interrupt) as mock_fetch:
        with pytest.raises(KeyboardInterrupt):
            run_ingest_sources(session_factory, SOURCES, "", 10, environment=ENV)

    assert mock_fetch.call_count == 1
