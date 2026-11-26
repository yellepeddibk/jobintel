"""The conftest network guard blocks outbound access and allows local sockets."""

import socket

import pytest
import requests


def test_hostname_lookup_is_blocked():
    with pytest.raises(RuntimeError, match="Tests must not use the network"):
        requests.get("https://example.invalid/", timeout=1)


def test_direct_ip_connection_is_blocked():
    # 203.0.113.0/24 is reserved for documentation; the guard refuses before any
    # packet is sent either way.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        with pytest.raises(RuntimeError, match="Tests must not use the network"):
            s.connect(("203.0.113.1", 80))


def test_loopback_connection_is_allowed():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(server.getsockname())
