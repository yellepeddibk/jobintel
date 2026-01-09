"""Base interface and validation for job sources."""

from typing import Any, Protocol


class JobSource(Protocol):
    """Protocol for job source implementations."""

    name: str

    def fetch(self, search: str, limit: int) -> list[dict[str, Any]]:
        """Fetch jobs from the source.

        Returns list of standardized payload dicts with keys:
        - source: str (required)
        - url: str (required)
        - title: str (required)
        - company: str (optional)
        - location: str (optional)
        - description: str (optional)
        - posted_at: str | date (optional, ISO format if string)
        - content_hash: str (optional, will be computed if missing)
        """
        ...


REQUIRED_KEYS = ["source", "url", "title"]
OPTIONAL_KEYS = ["company", "location", "description", "posted_at", "content_hash"]


def validate_payload(payload: dict[str, Any]) -> tuple[bool, str]:
    """Validate payload has required fields.

    Returns (is_valid, error_message).
    If valid, error_message is empty.
    If invalid, error_message describes what's missing.
    """
    missing = [key for key in REQUIRED_KEYS if not payload.get(key)]

    if missing:
        return False, f"Missing required keys: {', '.join(missing)}"

    return True, ""


def validate_payloads(
    payloads: list[dict[str, Any]], source_name: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate a batch of payloads, filtering out invalid ones.

    Returns (valid_payloads, warnings).
    """
    valid = []
    warnings = []

    for i, payload in enumerate(payloads):
        is_valid, error = validate_payload(payload)
        if is_valid:
            valid.append(payload)
        else:
            warnings.append(f"[{source_name}] Payload {i}: {error}")

    return valid, warnings
