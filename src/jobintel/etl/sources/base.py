"""Base interface and validation for job sources."""

from typing import Any, Protocol


class JobSource(Protocol):
    """Protocol for job source implementations."""

    name: str

    def fetch(self, search: str, limit: int) -> list[dict[str, Any]]:
        """Return payload dicts: source, url, title required; company, location,
        description, posted_at, content_hash optional."""
        ...


REQUIRED_KEYS = ["source", "url", "title"]
OPTIONAL_KEYS = ["company", "location", "description", "posted_at", "content_hash"]


def validate_payload(payload: dict[str, Any]) -> tuple[bool, str]:
    """Return (is_valid, error_message); the message is empty when valid."""
    missing = [key for key in REQUIRED_KEYS if not payload.get(key)]

    if missing:
        return False, f"Missing required keys: {', '.join(missing)}"

    return True, ""


def validate_payloads(
    payloads: list[dict[str, Any]], source_name: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop invalid payloads: returns (valid_payloads, warnings)."""
    valid = []
    warnings = []

    for i, payload in enumerate(payloads):
        is_valid, error = validate_payload(payload)
        if is_valid:
            valid.append(payload)
        else:
            warnings.append(f"[{source_name}] Payload {i}: {error}")

    return valid, warnings
