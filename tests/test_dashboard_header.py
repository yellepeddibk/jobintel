"""Dashboard header: environment banner outside production, freshness for everyone."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from streamlit.testing.v1 import AppTest

import jobintel.db as db
from jobintel.analytics.queries import get_last_updated
from jobintel.core.config import database_host, is_local_database, settings
from jobintel.models import Base, IngestRun

DASHBOARD = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"

# Display-only URLs: the engine is already bound elsewhere, so these are never used to
# connect. They decide what the header says and prove the password is never shown.
LOCAL_URL = "postgresql+psycopg://jobintel:s3cret-pw@127.0.0.1:5433/jobintel"
REMOTE_URL = "postgresql+psycopg://jobintel:s3cret-pw@db.remote.invalid:5432/jobintel"


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///./jobintel.db",
        "sqlite+pysqlite:///:memory:",
        LOCAL_URL,
        "postgresql+psycopg://u:p@localhost/jobintel",
        "postgresql+psycopg://u:p@[::1]:5432/jobintel",
        "postgresql+psycopg:///jobintel",  # no host: local socket
    ],
)
def test_local_databases(url):
    assert is_local_database(url)


@pytest.mark.parametrize("url", [REMOTE_URL, "not a url"])
def test_non_local_databases(url):
    assert not is_local_database(url)


def test_database_host_has_no_credentials():
    assert database_host(REMOTE_URL) == "db.remote.invalid"
    assert database_host("sqlite:///./jobintel.db") == ""


def _run(env: str, status: str, finished: datetime | None, source="remotive") -> IngestRun:
    return IngestRun(
        source=source,
        environment=env,
        status=status,
        started_at=finished or datetime(2026, 11, 1, tzinfo=UTC),
        finished_at=finished,
    )


# --------------------------------------------------------------- get_last_updated


def test_last_updated_is_the_latest_successful_run(session):
    session.add_all(
        [
            _run("test", "success", datetime(2026, 11, 15, 0, 5, tzinfo=UTC)),
            _run("test", "success", datetime(2026, 11, 22, 0, 4, tzinfo=UTC), "arbeitnow"),
            # Ignored: a later failure, a running run, and another environment.
            _run("test", "failed", datetime(2026, 11, 23, 0, 1, tzinfo=UTC)),
            _run("test", "running", None),
            _run("production", "success", datetime(2026, 11, 25, 0, 2, tzinfo=UTC)),
        ]
    )
    session.commit()

    last = get_last_updated(session, environment="test")

    assert last.replace(tzinfo=None) == datetime(2026, 11, 22, 0, 4)


def test_last_updated_is_none_without_a_successful_run(session):
    session.add(_run("test", "failed", datetime(2026, 11, 23, tzinfo=UTC)))
    session.commit()

    assert get_last_updated(session, environment="test") is None


# --------------------------------------------------------------- dashboard render


@pytest.fixture
def runs_db(tmp_path, monkeypatch):
    """File-backed so the AppTest script thread sees the same data."""
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'header.db').as_posix()}")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(db, "SessionLocal", factory)
    yield factory
    engine.dispose()


def _render(monkeypatch, env: str, database_url: str = LOCAL_URL) -> AppTest:
    monkeypatch.setattr(settings, "ENV", env)
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    at = AppTest.from_file(str(DASHBOARD), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _captions(at: AppTest) -> list[str]:
    return [c.value for c in at.caption]


def _banners(at: AppTest) -> list[str]:
    return [i.value for i in at.info if "environment" in i.value]


def _remote_warnings(at: AppTest) -> list[str]:
    return [e.value for e in at.error if "not on this machine" in e.value]


def _all_text(at: AppTest) -> str:
    groups = (at.caption, at.markdown, at.info, at.success, at.warning, at.error)
    return "\n".join(str(el.value) for group in groups for el in group)


@pytest.mark.parametrize(("env", "label"), [("development", "Development"), ("test", "Test")])
def test_non_production_shows_an_environment_banner(monkeypatch, runs_db, env, label):
    at = _render(monkeypatch, env)

    assert _banners(at) == [f"**{label} environment**"]
    assert _remote_warnings(at) == []
    assert "s3cret-pw" not in _all_text(at)


def test_remote_database_warning_names_the_host_not_the_password(monkeypatch, runs_db):
    at = _render(monkeypatch, "development", database_url=REMOTE_URL)

    warnings = _remote_warnings(at)
    assert len(warnings) == 1
    assert "db.remote.invalid" in warnings[0]
    assert "s3cret-pw" not in _all_text(at)


def test_production_shows_no_banner_and_no_database_details(monkeypatch, runs_db):
    at = _render(monkeypatch, "production", database_url=REMOTE_URL)

    assert _banners(at) == []
    assert _remote_warnings(at) == []
    assert "db.remote.invalid" not in _all_text(at)


@pytest.mark.parametrize("env", ["production", "development"])
def test_freshness_line_shows_the_latest_successful_ingest(monkeypatch, runs_db, env):
    with runs_db() as s:
        s.add(_run(env, "success", datetime(2026, 11, 22, 0, 4, tzinfo=UTC)))
        s.add(_run(env, "failed", datetime(2026, 11, 23, 0, 1, tzinfo=UTC)))
        s.commit()

    at = _render(monkeypatch, env)

    assert "Data last updated: 2026-11-22 00:04 UTC" in _captions(at)


def test_freshness_line_without_any_successful_ingest(monkeypatch, runs_db):
    at = _render(monkeypatch, "production")

    assert "Data last updated: no successful ingest yet." in _captions(at)
