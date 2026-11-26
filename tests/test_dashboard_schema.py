"""The production dashboard must not change the database structure.

Renders app/dashboard.py in-process with Streamlit's AppTest against the in-memory
SQLite database conftest configures. No network and no external database.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import jobintel.db as db
from jobintel.core.config import settings

DASHBOARD = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"


@pytest.fixture
def init_db_calls(monkeypatch):
    calls = []
    real_init_db = db.init_db

    def recording_init_db(*args, **kwargs):
        calls.append((args, kwargs))
        return real_init_db(*args, **kwargs)

    # The dashboard imports init_db from jobintel.db on every run.
    monkeypatch.setattr(db, "init_db", recording_init_db)
    return calls


def _render(monkeypatch, env: str) -> AppTest:
    monkeypatch.setattr(settings, "ENV", env)
    at = AppTest.from_file(str(DASHBOARD), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_production_dashboard_does_not_initialize_the_schema(monkeypatch, init_db_calls):
    at = _render(monkeypatch, "production")

    assert init_db_calls == []
    assert any("Database connected" in s.value for s in at.success)


def test_development_dashboard_still_creates_missing_tables(monkeypatch, init_db_calls):
    _render(monkeypatch, "development")

    assert init_db_calls == [((), {})]
