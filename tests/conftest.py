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
