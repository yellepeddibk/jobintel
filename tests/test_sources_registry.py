"""Tests for source registry and validation."""

from unittest.mock import MagicMock, patch

import pytest

from jobintel.etl.sources.base import validate_payload, validate_payloads
from jobintel.etl.sources.registry import get_source, list_sources


def test_validate_payload_valid():
    """Test validation with valid payload."""
    payload = {
        "source": "test",
        "url": "https://example.com/job",
        "title": "Software Engineer",
        "company": "Acme Corp",
        "description": "Great job",
    }
    is_valid, error = validate_payload(payload)
    assert is_valid
    assert error == ""


def test_validate_payload_missing_required():
    """Test validation with missing required fields."""
    payload = {
        "source": "test",
        "company": "Acme Corp",
    }
    is_valid, error = validate_payload(payload)
    assert not is_valid
    assert "url" in error
    assert "title" in error


def test_validate_payloads_batch():
    """Test batch validation filters invalid payloads."""
    payloads = [
        {"source": "test", "url": "http://example.com/1", "title": "Job 1"},
        {"source": "test", "url": "http://example.com/2"},  # Missing title
        {"source": "test", "url": "http://example.com/3", "title": "Job 3"},
    ]

    valid, warnings = validate_payloads(payloads, "test")

    assert len(valid) == 2
    assert len(warnings) == 1
    assert "title" in warnings[0]


def test_list_sources_returns_registered():
    """Test list_sources returns registered source names."""
    # Sources are auto-registered on import
    sources = list_sources()
    assert isinstance(sources, list)
    assert "remotive" in sources
    assert "remoteok" in sources


def test_get_source_returns_valid_source():
    """Test get_source returns a valid source."""
    source = get_source("remotive")
    assert source.name == "remotive"
    assert hasattr(source, "fetch")


def test_get_source_unknown_raises():
    """Test get_source raises for unknown source."""
    with pytest.raises(ValueError, match="Unknown source"):
        get_source("nonexistent")


# One response per board, keyed by URL fragment. Each job mentions "test" to pass search.
_CANNED_RESPONSES = {
    "remotive.com": {
        "jobs": [
            {
                "id": 1,
                "url": "https://remotive.com/remote-jobs/1",
                "title": "Test Engineer",
                "company_name": "Acme",
                "candidate_required_location": "Worldwide",
                "publication_date": "2026-01-05T10:00:00",
                "description": "<p>test automation</p>",
            }
        ]
    },
    "arbeitnow.com": {
        "data": [
            {
                "slug": "test-engineer-1",
                "url": "https://www.arbeitnow.com/jobs/test-engineer-1",
                "title": "Test Engineer",
                "company_name": "Acme",
                "location": "Berlin",
                "description": "test automation",
                "created_at": 1767607200,
            }
        ],
        "links": {"next": None},
    },
    "remoteok.com": [
        {"legal": "API terms notice; the first element is metadata"},
        {
            "id": "1",
            "url": "https://remoteok.com/remote-jobs/1",
            "position": "Test Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "test automation",
            "epoch": 1767607200,
        },
    ],
}


def _fake_get(url, *args, **kwargs):
    for fragment, body in _CANNED_RESPONSES.items():
        if fragment in url:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = body
            return resp
    raise AssertionError(f"Unexpected URL requested: {url}")


def test_source_fetch_signature():
    """Every registered source accepts search and limit and returns a list (offline)."""
    with patch("requests.get", side_effect=_fake_get) as mock_get:
        for source_name in list_sources():
            mock_get.reset_mock()
            source = get_source(source_name)

            payloads = source.fetch(search="test", limit=1)

            assert isinstance(payloads, list)
            assert len(payloads) == 1
            assert payloads[0]["source"] == source_name
            # The adapter went through its real request path, not around it.
            assert mock_get.call_count == 1
