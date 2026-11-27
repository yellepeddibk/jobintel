"""Tests for the RemoteOK job source. HTTP is mocked; nothing reaches remoteok.com."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from jobintel.etl.raw import compute_content_hash
from jobintel.etl.sources.registry import fetch_from_source
from jobintel.etl.sources.remoteok import REMOTEOK_API, RemoteOKSource, fetch_remoteok_jobs

# RemoteOK's API returns a list whose first element is a legal/metadata notice.
METADATA = {"legal": "API terms of service notice"}

FULL_JOB = {
    "id": "123456",
    "url": "https://remoteok.com/remote-jobs/123456",
    "position": "Senior Python Engineer",
    "company": "Acme",
    "location": "Worldwide",
    "description": "<p>Build data pipelines in Python.</p>",
    "date": "2026-01-05T10:00:00+00:00",
    "epoch": 1767607200,
    "tags": ["python", "backend"],
}


def _response(body, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} Server Error")
    return resp


@pytest.fixture
def mock_get():
    with patch("jobintel.etl.sources.remoteok.requests.get") as mock:
        yield mock


def test_skips_the_metadata_element(mock_get):
    mock_get.return_value = _response([METADATA, FULL_JOB])

    payloads = fetch_remoteok_jobs()

    assert len(payloads) == 1
    assert payloads[0]["external_id"] == "123456"


def test_normalizes_a_full_job(mock_get):
    mock_get.return_value = _response([METADATA, FULL_JOB])

    payload = fetch_remoteok_jobs()[0]

    assert payload == {
        "source": "remoteok",
        "external_id": "123456",
        "url": "https://remoteok.com/remote-jobs/123456",
        "title": "Senior Python Engineer",
        "company": "Acme",
        "location": "Worldwide",
        "description": "<p>Build data pipelines in Python.</p>",
        "posted_at": "2026-01-05T10:00:00+00:00",
        "tags": ["python", "backend"],
        "content_hash": payload["content_hash"],
    }


def test_fills_in_missing_fields(mock_get):
    sparse = {"id": 42, "title": "Data Analyst", "epoch": 1767607200}
    mock_get.return_value = _response([METADATA, sparse])

    payload = fetch_remoteok_jobs()[0]

    assert payload["url"] == "https://remoteok.com/remote-jobs/42"
    assert payload["title"] == "Data Analyst"  # "title" when there is no "position"
    assert payload["company"] == ""
    assert payload["location"] == "Remote"
    assert payload["description"] == ""
    assert payload["posted_at"] == 1767607200  # "epoch" when there is no "date"
    assert payload["external_id"] == "42"
    assert payload["tags"] == []


def test_position_wins_over_title(mock_get):
    job = {**FULL_JOB, "position": "From position", "title": "From title"}
    mock_get.return_value = _response([METADATA, job])

    assert fetch_remoteok_jobs()[0]["title"] == "From position"


def test_content_hash_is_the_standard_hash_of_the_payload(mock_get):
    mock_get.return_value = _response([METADATA, FULL_JOB])

    payload = fetch_remoteok_jobs()[0]
    without_hash = {k: v for k, v in payload.items() if k != "content_hash"}

    assert payload["content_hash"] == compute_content_hash(without_hash)
    assert len(payload["content_hash"]) == 64  # SHA-256 hex


@pytest.mark.parametrize(
    ("search", "expected_titles"),
    [
        ("python", ["Senior Python Engineer"]),  # title, case-insensitive
        ("ACME", ["Senior Python Engineer"]),  # company
        ("pipelines", ["Senior Python Engineer"]),  # description
        ("rust", []),
        (None, ["Senior Python Engineer", "Marketing Lead"]),
    ],
)
def test_search_filters_on_title_company_and_description(mock_get, search, expected_titles):
    other = {"id": "2", "position": "Marketing Lead", "company": "Globex", "description": "SEO"}
    mock_get.return_value = _response([METADATA, FULL_JOB, other])

    payloads = fetch_remoteok_jobs(search=search)

    assert [p["title"] for p in payloads] == expected_titles


def test_limit_caps_results_without_a_search(mock_get):
    jobs = [{**FULL_JOB, "id": str(i), "url": f"https://remoteok.com/{i}"} for i in range(5)]
    mock_get.return_value = _response([METADATA, *jobs])

    payloads = fetch_remoteok_jobs(limit=3)

    assert [p["external_id"] for p in payloads] == ["0", "1", "2"]


def _mixed_jobs():
    """Three non-matching jobs first, then three Python jobs."""
    other = [
        {"id": f"o{i}", "url": f"https://remoteok.com/o{i}", "position": "Marketing Lead"}
        for i in range(3)
    ]
    python = [
        {"id": f"p{i}", "url": f"https://remoteok.com/p{i}", "position": "Python Engineer"}
        for i in range(3)
    ]
    return [METADATA, *other, *python]


def test_search_is_applied_before_the_limit(mock_get):
    """The limit counts matching jobs, not the first jobs in the response."""
    mock_get.return_value = _response(_mixed_jobs())

    payloads = fetch_remoteok_jobs(search="python", limit=2)

    assert [p["external_id"] for p in payloads] == ["p0", "p1"]


def test_search_returns_every_match_when_under_the_limit(mock_get):
    mock_get.return_value = _response(_mixed_jobs())

    payloads = fetch_remoteok_jobs(search="python", limit=50)

    assert [p["external_id"] for p in payloads] == ["p0", "p1", "p2"]


def test_sends_user_agent_and_timeout(mock_get):
    mock_get.return_value = _response([METADATA])

    fetch_remoteok_jobs(timeout_s=12)

    mock_get.assert_called_once_with(
        REMOTEOK_API, headers={"User-Agent": "JobIntel/1.0"}, timeout=12
    )


def test_http_error_is_raised_not_swallowed(mock_get):
    """So run_ingest records the run as failed rather than as an empty success."""
    mock_get.return_value = _response(None, status=503)

    with pytest.raises(requests.HTTPError, match="503"):
        fetch_remoteok_jobs()


@pytest.mark.parametrize("body", [[], [METADATA], {"error": "unexpected"}, None])
def test_empty_or_unexpected_response_gives_no_jobs(mock_get, body):
    mock_get.return_value = _response(body)

    assert fetch_remoteok_jobs() == []


def test_source_fetch_passes_search_and_limit(mock_get):
    mock_get.return_value = _response([METADATA, FULL_JOB])

    source = RemoteOKSource()

    assert source.name == "remoteok"
    assert len(source.fetch(search="python", limit=10)) == 1
    assert source.fetch(search="rust", limit=10) == []


def test_registry_drops_a_job_without_a_title(mock_get):
    untitled = {"id": "9", "url": "https://remoteok.com/9"}
    mock_get.return_value = _response([METADATA, FULL_JOB, untitled])

    payloads, warnings = fetch_from_source("remoteok", "", 10)

    assert [p["external_id"] for p in payloads] == ["123456"]
    assert len(warnings) == 1
    assert "title" in warnings[0]
