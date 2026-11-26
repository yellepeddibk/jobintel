"""Pytest configuration - isolate tests from dev/prod database.

MUST be loaded before any jobintel imports to ensure settings picks up test env vars.
"""

import os

# Force tests to use isolated in-memory SQLite
# This MUST happen before importing any jobintel modules
os.environ["ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jobintel.models import Base


@pytest.fixture(autouse=True)
def _restore_streamlit_error_details():
    """Undo the process-wide option a production dashboard render sets.

    app/dashboard.py calls st.set_option("client.showErrorDetails", "none") when
    ENV=production, and Streamlit config is global to the process, so without this a
    render in one test would change how later tests display exceptions.
    """
    from streamlit import config as st_config

    before = st_config.get_option("client.showErrorDetails")
    yield
    st_config.set_option("client.showErrorDetails", before)


@pytest.fixture
def engine():
    """Create a fresh in-memory SQLite engine for each test."""
    eng = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    """Create a session bound to the in-memory engine."""
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()
