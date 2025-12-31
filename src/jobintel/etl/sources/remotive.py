from __future__ import annotations

import html as _html
import re
from typing import Any

import requests

REMOTIVE_ENDPOINT = "https://remotive.com/api/remote-jobs"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    return _html.unescape(_TAG_RE.sub(" ", s)).replace("\xa0", " ").strip()


def fetch_remotive_jobs(
    *,
    search: str | None = None,
    category: str | None = None,
    company_name: str | None = None,
    limit: int = 100,
    timeout_s: int = 30,
) -> list[dict[str, Any]]:
    """Fetch jobs from Remotive and return payloads in JobIntel raw format."""
    params: dict[str, Any] = {"limit": int(limit)}
    if search:
        params["search"] = search
    if category:
        params["category"] = category
    if company_name:
        params["company_name"] = company_name

    resp = requests.get(REMOTIVE_ENDPOINT, params=params, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    jobs = data.get("jobs", []) or []

    payloads: list[dict[str, Any]] = []
    for j in jobs[:limit]:
        payloads.append(
            {
                "source": "remotive",
                "external_id": str(j.get("id")),
                "url": j.get("url"),
                "title": j.get("title"),
                "company": j.get("company_name"),
                "location": j.get("candidate_required_location"),
                "posted_at": j.get("publication_date"),
                "description": _strip_html(j.get("description") or ""),
                "tags": j.get("tags"),
                "job_type": j.get("job_type"),
                "category": j.get("category"),
            }
        )
    return payloads
