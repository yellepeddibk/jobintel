"""Pytest configuration - isolate tests from dev/prod database.

MUST be loaded before any jobintel imports to ensure settings picks up test env vars.
"""

import os

# Force tests to use isolated in-memory SQLite
# This MUST happen before importing any jobintel modules
os.environ["ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

import socket

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jobintel.models import Base

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "", None}
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _is_local(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    return host in _LOCAL_HOSTS or (isinstance(host, str) and host.startswith("127."))


def _refuse(target) -> None:
    raise RuntimeError(
        f"Tests must not use the network (attempted to reach {target!r}). "
        "Mock the adapter's requests.get, or fetch_from_source for pipeline tests."
    )


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Fail any test that tries to reach a host other than this machine.

    Name lookup is blocked as well as connecting, because a lookup is itself a
    network request. Loopback and Unix sockets stay allowed, since local machinery
    such as an event loop's internal socket pair uses them.
    """

    def getaddrinfo(host, *args, **kwargs):
        if not _is_local(host):
            _refuse(host)
        return _real_getaddrinfo(host, *args, **kwargs)

    def connect(self, address):
        if not isinstance(address, (str, bytes)) and not _is_local(address[0]):
            _refuse(address)
        return _real_connect(self, address)

    def connect_ex(self, address):
        if not isinstance(address, (str, bytes)) and not _is_local(address[0]):
            _refuse(address)
        return _real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


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


# Optional: when set, the shared `engine` fixture and the empty-database migration tests
# run on this PostgreSQL server instead of SQLite. CI sets it in a separate job.
TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

# Postgres sessions give up on a blocked lock instead of hanging the run, for example
# if a test leaves a transaction open while the next one drops the tables.
POSTGRES_CONNECT_ARGS = {"options": "-c lock_timeout=10000"}


def check_test_postgres_url(url: str) -> None:
    """Refuse any test database that is not local and clearly named for tests.

    The fixtures drop and recreate tables and whole databases, so this is what stops
    a mistyped or copied URL from pointing them at a real database.
    """
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    problems = []
    if not parsed.drivername.startswith("postgresql"):
        problems.append("it is not a PostgreSQL URL")
    if parsed.host not in ("localhost", "127.0.0.1"):
        problems.append(f"host {parsed.host!r} is not localhost or 127.0.0.1")
    if not (parsed.database or "").startswith("jobintel_test"):
        problems.append(f"database {parsed.database!r} does not start with 'jobintel_test'")
    if problems:
        raise pytest.UsageError("Refusing TEST_POSTGRES_URL: " + "; ".join(problems) + ".")


def pytest_configure(config):
    if TEST_POSTGRES_URL:
        check_test_postgres_url(TEST_POSTGRES_URL)


def pytest_report_header(config):
    if TEST_POSTGRES_URL:
        return "database fixtures: PostgreSQL (TEST_POSTGRES_URL)"
    return "database fixtures: SQLite"


DB_KINDS = ["sqlite"] + (["postgresql"] if TEST_POSTGRES_URL else [])


@pytest.fixture(params=DB_KINDS)
def make_database(request, tmp_path):
    """Factory for empty databases of one kind, removed after the test.

    Parametrized: SQLite always, plus PostgreSQL when TEST_POSTGRES_URL is set. SQLite
    databases are files in tmp_path. PostgreSQL databases are created on the
    TEST_POSTGRES_URL server with a unique jobintel_test_ name and dropped afterwards.
    """
    import uuid

    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    created: list[str] = []
    admin = None
    if request.param == "postgresql":
        admin = create_engine(make_url(TEST_POSTGRES_URL), isolation_level="AUTOCOMMIT")

    def make(label: str) -> str:
        if admin is None:
            return f"sqlite+pysqlite:///{(tmp_path / f'{label}.db').as_posix()}"
        name = f"jobintel_test_{label}_{uuid.uuid4().hex[:8]}"
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        return make_url(TEST_POSTGRES_URL).set(database=name).render_as_string(hide_password=False)

    yield make

    if admin is not None:
        with admin.connect() as conn:
            for name in created:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def empty_db(make_database, monkeypatch):
    """An empty database from make_database, plus an Alembic Config pointed at it.

    Patches the settings singleton because alembic/env.py reads DATABASE_URL from it.
    """
    from pathlib import Path

    from alembic.config import Config

    from jobintel.core import config as jobintel_config

    url = make_database("empty")
    monkeypatch.setattr(jobintel_config.settings, "DATABASE_URL", url, raising=False)

    # Config without an .ini file: env.py then skips fileConfig, which would otherwise
    # reconfigure logging for the rest of the test session.
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))

    eng = create_engine(url)
    yield eng, cfg
    eng.dispose()


@pytest.fixture
def engine():
    """A fresh, empty schema for each test.

    In-memory SQLite by default. With TEST_POSTGRES_URL set, the tables are dropped and
    recreated in that database instead, so each test still starts empty.
    """
    if TEST_POSTGRES_URL:
        eng = create_engine(TEST_POSTGRES_URL, connect_args=POSTGRES_CONNECT_ARGS)
        Base.metadata.drop_all(bind=eng)
        Base.metadata.create_all(bind=eng)
        yield eng
        eng.dispose()
        return

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
