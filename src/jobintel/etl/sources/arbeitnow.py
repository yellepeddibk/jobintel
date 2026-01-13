"""Arbeitnow job source implementation.

Arbeitnow is a German job board API providing jobs primarily in Germany.
API Docs: https://arbeitnow.com/api/job-board-api

The API returns JSON with a 'data' array containing job listings.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

import requests

from jobintel.etl.sources.registry import register_source

logger = logging.getLogger(__name__)

ARBEITNOW_API_URL = "https://arbeitnow.com/api/job-board-api"
# Rate limit: wait between API requests to avoid 429 errors
REQUEST_DELAY_SECONDS = 2.5
# Retry settings for 429 rate limit errors
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5.0


def _fetch_page_with_retry(page: int) -> dict[str, Any] | None:
    """Fetch a single page from Arbeitnow API with retry on 429.

    Returns the JSON response dict, or None if all retries failed.
    """
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                ARBEITNOW_API_URL,
                params={"page": page},
                timeout=30,
            )
            if resp.status_code == 429:
                wait_time = RETRY_BACKOFF_SECONDS * (attempt + 1)
                logger.warning(
                    "Arbeitnow rate limited (429) on page %d, retry %d/%d in %.1fs",
                    page,
                    attempt + 1,
                    MAX_RETRIES,
                    wait_time,
                )
                time.sleep(wait_time)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning("Arbeitnow API request failed on page %d: %s", page, e)
            return None
        except ValueError as e:
            logger.warning("Arbeitnow API returned invalid JSON on page %d: %s", page, e)
            return None
    logger.warning("Arbeitnow API exhausted retries on page %d", page)
    return None


def fetch_arbeitnow_jobs(search: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Fetch jobs from Arbeitnow API and normalize to canonical payload format.

    Args:
        search: Optional search filter (applied client-side).
        limit: Maximum number of jobs to return.

    Returns:
        List of job payloads conforming to the canonical schema.
    """
    payloads: list[dict[str, Any]] = []
    page = 1
    max_pages = 10  # Safety limit to prevent infinite loops

    while page <= max_pages and len(payloads) < limit:
        data = _fetch_page_with_retry(page)
        if data is None:
            break

        jobs = data.get("data", [])
        if not jobs:
            break

        for job in jobs:
            if len(payloads) >= limit:
                break

            payload = _normalize_job(job)
            if not payload:
                continue

            # Apply search filter client-side (API doesn't support search param well)
            if search:
                search_lower = search.lower()
                title = payload["title"]
                company = payload.get("company", "")
                description = payload.get("description", "")
                searchable = f"{title} {company} {description}".lower()
                if search_lower not in searchable:
                    continue

            payloads.append(payload)

        # Check for next page
        links = data.get("links", {})
        if not links.get("next"):
            break

        page += 1
        # Rate limit to avoid 429 errors
        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info("Fetched %d jobs from Arbeitnow", len(payloads))
    return payloads


def _normalize_job(job: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a single Arbeitnow job to canonical payload format.

    Args:
        job: Raw job dict from Arbeitnow API.

    Returns:
        Normalized payload dict, or None if required fields are missing.
    """
    slug = job.get("slug")
    url = job.get("url")
    title = job.get("title")

    # Skip if missing required fields
    if not url or not title:
        logger.debug("Skipping job with missing url or title: %s", slug)
        return None

    # Use slug as external_id, fall back to URL hash
    external_id = slug or hashlib.md5(url.encode()).hexdigest()[:16]

    # Parse posted_at timestamp (Unix timestamp)
    posted_at = None
    created_at = job.get("created_at")
    if created_at:
        try:
            posted_at = datetime.fromtimestamp(created_at, tz=UTC).isoformat()
        except (ValueError, OSError, TypeError) as e:
            logger.debug("Failed to parse created_at for %s: %s", slug, e)

    # Build description from HTML content
    description = job.get("description", "")

    # Combine tags and job_types for tags field
    tags = job.get("tags", []) or []
    job_types = job.get("job_types", []) or []
    all_tags = tags + job_types

    # Generate content hash for deduplication
    content_for_hash = f"{title}|{job.get('company_name', '')}|{description}"
    content_hash = hashlib.md5(content_for_hash.encode()).hexdigest()

    return {
        "source": "arbeitnow",
        "external_id": external_id,
        "url": url,
        "title": title,
        "company": job.get("company_name"),
        "location": job.get("location"),
        "description": description,
        "posted_at": posted_at,
        "tags": all_tags if all_tags else None,
        "remote": job.get("remote", False),
        "content_hash": content_hash,
    }


class ArbeitnowSource:
    """Arbeitnow job source for the registry."""

    name = "arbeitnow"

    def fetch(self, search: str = "", limit: int = 100) -> list[dict[str, Any]]:
        """Fetch jobs from Arbeitnow."""
        return fetch_arbeitnow_jobs(search=search or None, limit=limit)


# Auto-register with the source registry
register_source(ArbeitnowSource())
