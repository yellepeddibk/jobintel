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
