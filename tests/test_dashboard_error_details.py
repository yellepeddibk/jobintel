"""The production dashboard never renders exception details (AppTest, offline)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from streamlit import config as st_config
from streamlit.testing.v1 import AppTest

import jobintel.analytics.queries as queries
import jobintel.db as db
from jobintel.core.config import settings
from jobintel.models import Base, IngestRun

DASHBOARD = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"

# Stands in for what real exception text can carry, such as a database host.
MARKER = "db-host-do-not-show.internal:5432"


def _render(monkeypatch: pytest.MonkeyPatch, env: str) -> AppTest:
    # The script re-reads settings and imports on every run, so patches apply.
    monkeypatch.setattr(settings, "ENV", env)
    return AppTest.from_file(str(DASHBOARD), default_timeout=60).run()


def _rendered_text(at: AppTest) -> str:
    """Every piece of text a viewer could read, including exception elements."""
    parts: list[str] = []
    for group in (at.error, at.warning, at.info, at.success, at.markdown, at.caption):
        parts.extend(str(el.value) for el in group)
    for exc in at.exception:
        parts.append(exc.proto.type)
        parts.append(exc.proto.message)
        parts.extend(exc.stack_trace)
    return "\n".join(parts)


def _error_messages(at: AppTest) -> list[str]:
    return [el.value for el in at.error]


def _fail_kpis(*args, **kwargs):
    raise RuntimeError(f"could not connect to {MARKER}")


def _fail_connect(*args, **kwargs):
    """Stands in for SessionLocal when the database cannot be reached."""
    raise RuntimeError(f'connection to server at "{MARKER}" failed')


def test_production_hides_query_exception_details(monkeypatch, caplog):
    monkeypatch.setattr(queries, "get_kpis", _fail_kpis)

    at = _render(monkeypatch, "production")

    assert "Failed to load KPIs." in _error_messages(at)
    assert MARKER not in _rendered_text(at)
    assert not at.exception
    # The detail is not lost: it goes to the server log instead.
    assert MARKER in caplog.text


def test_development_shows_query_exception_details(monkeypatch):
    monkeypatch.setattr(queries, "get_kpis", _fail_kpis)

    at = _render(monkeypatch, "development")

    assert "Failed to load KPIs." in _error_messages(at)
    assert any(MARKER in exc.proto.message for exc in at.exception)


def test_production_hides_connection_failure_details(monkeypatch):
    monkeypatch.setattr(db, "SessionLocal", _fail_connect)

    at = _render(monkeypatch, "production")

    assert any("Database connection failed" in m for m in _error_messages(at))
    assert MARKER not in _rendered_text(at)
    assert not at.exception


def test_development_shows_connection_failure_details(monkeypatch):
    monkeypatch.setattr(db, "SessionLocal", _fail_connect)

    at = _render(monkeypatch, "development")

    assert any(MARKER in exc.proto.message for exc in at.exception)


@pytest.fixture
def failed_run_db(tmp_path, monkeypatch):
    """One failed run per environment, file-backed so AppTest's thread sees it."""
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'runs.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as s:
        for env in ("production", "development"):
            s.add(
                IngestRun(
                    source="remotive",
                    environment=env,
                    status="failed",
                    started_at=datetime(2026, 1, 4, tzinfo=UTC),
                    finished_at=datetime(2026, 1, 4, 0, 1, tzinfo=UTC),
                    error=f"OperationalError: {MARKER}",
                )
            )
        s.commit()
    monkeypatch.setattr(db, "SessionLocal", session_factory)
    yield
    engine.dispose()


def test_production_hides_stored_run_error_text(monkeypatch, failed_run_db):
    at = _render(monkeypatch, "production")

    assert "Error details are in the ingestion workflow logs." in _error_messages(at)
    assert MARKER not in _rendered_text(at)


def test_development_shows_stored_run_error_text(monkeypatch, failed_run_db):
    at = _render(monkeypatch, "development")

    assert f"OperationalError: {MARKER}" in _error_messages(at)


def test_production_hides_uncaught_exception_details(monkeypatch):
    _render(monkeypatch, "production")

    assert st_config.get_option("client.showErrorDetails") == "none"


def test_development_leaves_uncaught_exception_details_alone(monkeypatch):
    before = st_config.get_option("client.showErrorDetails")

    _render(monkeypatch, "development")

    assert st_config.get_option("client.showErrorDetails") == before
