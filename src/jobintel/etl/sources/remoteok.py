"""RemoteOK job source."""

from __future__ import annotations

from typing import Any

import requests

from jobintel.etl.raw import compute_content_hash
from jobintel.etl.sources.registry import register_source

REMOTEOK_API = "https://remoteok.com/api"


def fetch_remoteok_jobs(
    search: str | None = None, limit: int = 100, timeout_s: int = 30
) -> list[dict[str, Any]]:
    """Fetch jobs from RemoteOK and normalize to JobIntel format.

    RemoteOK API returns jobs with different field names, so we normalize them.
    """
    # RemoteOK API requires user agent
    headers = {"User-Agent": "JobIntel/1.0"}

    resp = requests.get(REMOTEOK_API, headers=headers, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()

    # RemoteOK returns list where first item is metadata
    jobs = data[1:] if isinstance(data, list) and len(data) > 1 else []

    payloads: list[dict[str, Any]] = []
    for j in jobs[:limit]:
        # Normalize RemoteOK fields to our standard format
        url = j.get("url") or f"https://remoteok.com/remote-jobs/{j.get('id')}"
        title = j.get("position") or j.get("title") or ""
        company = j.get("company") or ""
        location = j.get("location") or "Remote"
        description = j.get("description") or ""
        posted_at = j.get("date") or j.get("epoch")  # RemoteOK uses epoch timestamp

        # Apply search filter client-side (RemoteOK API doesn't support search param)
        if search:
            search_lower = search.lower()
            searchable = f"{title} {company} {description}".lower()
            if search_lower not in searchable:
                continue

        payload = {
            "source": "remoteok",
            "external_id": str(j.get("id", "")),
            "url": url,
            "title": title,
            "company": company,
            "location": location,
            "description": description,
            "posted_at": posted_at,
            "tags": j.get("tags", []),
        }

        # Compute content hash for deduplication
        payload["content_hash"] = compute_content_hash(payload)
        payloads.append(payload)

    return payloads


class RemoteOKSource:
    """RemoteOK job source."""

    name = "remoteok"

    def fetch(self, search: str, limit: int) -> list[dict[str, Any]]:
        """Fetch jobs from RemoteOK."""
        return fetch_remoteok_jobs(search=search, limit=limit)


# Auto-register on import
register_source(RemoteOKSource())
