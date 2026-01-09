"""Tests for source registry and validation."""

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


def test_source_fetch_signature():
    """Test that registered sources have correct fetch signature."""
    for source_name in list_sources():
        source = get_source(source_name)
        # Should have fetch method that accepts search and limit
        payloads = source.fetch(search="test", limit=1)
        assert isinstance(payloads, list)
