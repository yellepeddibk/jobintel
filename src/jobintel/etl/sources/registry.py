"""Source registry for job sources."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jobintel.etl.sources.base import JobSource

# Registry will be populated as sources are imported
_SOURCES: dict[str, Any] = {}
_initialized = False


def register_source(source: JobSource) -> None:
    """Register a job source (idempotent)."""
    name = source.name
    if name in _SOURCES:
        return
    _SOURCES[name] = source


def _init_sources() -> None:
    """Lazy initialization - import source modules to trigger registration."""
    global _initialized
    if _initialized:
        return

    # Direct module imports to avoid circular dependency through __init__.py
    from jobintel.etl.sources.arbeitnow import ArbeitnowSource  # noqa: F401
    from jobintel.etl.sources.remoteok import RemoteOKSource  # noqa: F401
    from jobintel.etl.sources.remotive import RemotiveSource  # noqa: F401

    _initialized = True


def list_sources() -> list[str]:
    """Get list of available source names."""
    _init_sources()
    return sorted(_SOURCES.keys())


def get_source(name: str) -> JobSource:
    """Get a source by name."""
    _init_sources()
    if name not in _SOURCES:
        raise ValueError(f"Unknown source: {name}. Available: {list(_SOURCES.keys())}")
    return _SOURCES[name]


def fetch_from_source(
    source_name: str, search: str, limit: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch and validate payloads from a source.

    Returns (valid_payloads, warnings).
    """
    from jobintel.etl.sources.base import validate_payloads

    _init_sources()
    source = get_source(source_name)
    payloads = source.fetch(search, limit)
    return validate_payloads(payloads, source_name)
