"""Arbeitnow source: a mostly German job board (https://arbeitnow.com/api/job-board-api)."""

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
REQUEST_DELAY_SECONDS = 2.5  # between pages, to avoid 429s
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5.0


class ArbeitnowFetchError(RuntimeError):
    """A page failed, so the whole fetch failed (a partial fetch must not look successful)."""


def _fetch_page_with_retry(page: int) -> dict[str, Any]:
    """Fetch one page, retrying on 429. Raises ArbeitnowFetchError on any failure."""
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
            raise ArbeitnowFetchError(f"Arbeitnow page {page} failed: {e}") from e
        except ValueError as e:
            raise ArbeitnowFetchError(f"Arbeitnow page {page} returned invalid JSON: {e}") from e
    raise ArbeitnowFetchError(
        f"Arbeitnow page {page} still rate limited (429) after {MAX_RETRIES} attempts"
    )


def fetch_arbeitnow_jobs(search: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Fetch up to `limit` normalized jobs, filtering by `search` client-side.

    Raises ArbeitnowFetchError if any needed page fails; collected pages are discarded.
    """
    payloads: list[dict[str, Any]] = []
    page = 1
    max_pages = 10

    while page <= max_pages and len(payloads) < limit:
        data = _fetch_page_with_retry(page)

        jobs = data.get("data", [])
        if not jobs:
            break

        for job in jobs:
            if len(payloads) >= limit:
                break

            payload = _normalize_job(job)
            if not payload:
                continue

            if search:
                search_lower = search.lower()
                title = payload["title"]
                company = payload.get("company", "")
                description = payload.get("description", "")
                searchable = f"{title} {company} {description}".lower()
                if search_lower not in searchable:
                    continue

            payloads.append(payload)

        links = data.get("links", {})
        if not links.get("next"):
            break

        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info("Fetched %d jobs from Arbeitnow", len(payloads))
    return payloads


def _normalize_job(job: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Arbeitnow job to the canonical payload, or None without url or title."""
    slug = job.get("slug")
    url = job.get("url")
    title = job.get("title")

    if not url or not title:
        logger.debug("Skipping job with missing url or title: %s", slug)
        return None

    external_id = slug or hashlib.md5(url.encode()).hexdigest()[:16]

    posted_at = None
    created_at = job.get("created_at")
    if created_at:
        try:
            posted_at = datetime.fromtimestamp(created_at, tz=UTC).isoformat()
        except (ValueError, OSError, TypeError) as e:
            logger.debug("Failed to parse created_at for %s: %s", slug, e)

    description = job.get("description", "")

    tags = job.get("tags", []) or []
    job_types = job.get("job_types", []) or []
    all_tags = tags + job_types

    # Arbeitnow's own MD5 hash; changing it would re-insert every stored row.
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


register_source(ArbeitnowSource())
