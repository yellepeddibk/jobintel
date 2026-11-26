"""Tests for Arbeitnow job source."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from jobintel.etl.pipeline import run_ingest
from jobintel.etl.sources.arbeitnow import (
    MAX_RETRIES,
    ArbeitnowFetchError,
    ArbeitnowSource,
    _normalize_job,
    fetch_arbeitnow_jobs,
)
from jobintel.models import IngestRun, RawJob

# Sample API response data
SAMPLE_JOB = {
    "slug": "software-engineer-berlin-123456",
    "company_name": "TechCorp GmbH",
    "title": "Software Engineer",
    "description": "<p>Build amazing software.</p>",
    "remote": True,
    "url": "https://www.arbeitnow.com/jobs/companies/techcorp/software-engineer-123456",
    "tags": ["Remote", "Software Development"],
    "job_types": ["Full time"],
    "location": "Berlin",
    "created_at": 1700000000,  # Unix timestamp
}

SAMPLE_API_RESPONSE = {
    "data": [SAMPLE_JOB],
    "links": {
        "first": "https://arbeitnow.com/api/job-board-api?page=1",
        "last": None,
        "prev": None,
        "next": None,  # No next page
    },
    "meta": {
        "current_page": 1,
    },
}


class TestNormalizeJob:
    """Tests for the _normalize_job function."""

    def test_normalize_job_valid(self):
        """Test normalization of a valid job."""
        result = _normalize_job(SAMPLE_JOB)

        assert result is not None
        assert result["source"] == "arbeitnow"
        assert result["external_id"] == "software-engineer-berlin-123456"
        assert result["url"] == SAMPLE_JOB["url"]
        assert result["title"] == "Software Engineer"
        assert result["company"] == "TechCorp GmbH"
        assert result["location"] == "Berlin"
        assert result["description"] == "<p>Build amazing software.</p>"
        assert result["remote"] is True
        assert "Remote" in result["tags"]
        assert "Full time" in result["tags"]
        assert result["content_hash"] is not None
        assert result["posted_at"] is not None

    def test_normalize_job_missing_url(self):
        """Test normalization skips job without URL."""
        job = {**SAMPLE_JOB, "url": None}
        result = _normalize_job(job)
        assert result is None

    def test_normalize_job_missing_title(self):
        """Test normalization skips job without title."""
        job = {**SAMPLE_JOB, "title": None}
        result = _normalize_job(job)
        assert result is None

    def test_normalize_job_empty_url(self):
        """Test normalization skips job with empty URL."""
        job = {**SAMPLE_JOB, "url": ""}
        result = _normalize_job(job)
        assert result is None

    def test_normalize_job_no_tags(self):
        """Test normalization handles job without tags."""
        job = {**SAMPLE_JOB, "tags": None, "job_types": None}
        result = _normalize_job(job)

        assert result is not None
        assert result["tags"] is None

    def test_normalize_job_no_slug_uses_url_hash(self):
        """Test normalization uses URL hash when slug is missing."""
        job = {**SAMPLE_JOB, "slug": None}
        result = _normalize_job(job)

        assert result is not None
        assert result["external_id"] is not None
        assert len(result["external_id"]) == 16  # MD5 truncated to 16 chars

    def test_normalize_job_invalid_timestamp(self):
        """Test normalization handles invalid timestamp gracefully."""
        job = {**SAMPLE_JOB, "created_at": "invalid"}
        result = _normalize_job(job)

        assert result is not None
        assert result["posted_at"] is None


class TestFetchArbeitnowJobs:
    """Tests for the fetch_arbeitnow_jobs function."""

    @patch("jobintel.etl.sources.arbeitnow.requests.get")
    def test_fetch_success(self, mock_get):
        """Test successful fetch from Arbeitnow API."""
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_API_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        payloads = fetch_arbeitnow_jobs()

        assert len(payloads) == 1
        assert payloads[0]["source"] == "arbeitnow"
        assert payloads[0]["title"] == "Software Engineer"
        mock_get.assert_called_once()

    @patch("jobintel.etl.sources.arbeitnow.requests.get")
    def test_fetch_pagination(self, mock_get):
        """Test fetch handles pagination."""
        page1_response = {
            **SAMPLE_API_RESPONSE,
            "links": {"next": "https://arbeitnow.com/api/job-board-api?page=2"},
        }
        page2_response = {
            "data": [{**SAMPLE_JOB, "slug": "job-2", "title": "Backend Developer"}],
            "links": {"next": None},
        }

        mock_response1 = MagicMock()
        mock_response1.json.return_value = page1_response
        mock_response1.raise_for_status = MagicMock()

        mock_response2 = MagicMock()
        mock_response2.json.return_value = page2_response
        mock_response2.raise_for_status = MagicMock()

        mock_get.side_effect = [mock_response1, mock_response2]

        payloads = fetch_arbeitnow_jobs()

        assert len(payloads) == 2
        assert mock_get.call_count == 2

    @patch("jobintel.etl.sources.arbeitnow.requests.get")
    def test_fetch_api_error(self, mock_get):
        """A request error raises instead of looking like an empty result."""
        mock_get.side_effect = requests.RequestException("Network error")

        with pytest.raises(ArbeitnowFetchError, match="page 1 failed: Network error"):
            fetch_arbeitnow_jobs()

    @patch("jobintel.etl.sources.arbeitnow.requests.get")
    def test_fetch_empty_response(self, mock_get):
        """Test fetch handles empty data array."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [], "links": {}}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        payloads = fetch_arbeitnow_jobs()

        assert payloads == []


def _response(body=None, status=200, json_error=None):
    resp = MagicMock()
    resp.status_code = status
    if status >= 400 and status != 429:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} Server Error")
    if json_error is not None:
        resp.json.side_effect = json_error
    else:
        resp.json.return_value = body
    return resp


PAGE_WITH_NEXT = {
    "data": [SAMPLE_JOB],
    "links": {"next": "https://arbeitnow.com/api/job-board-api?page=2"},
}


@patch("jobintel.etl.sources.arbeitnow.time.sleep")
@patch("jobintel.etl.sources.arbeitnow.requests.get")
class TestFetchFailures:
    """A failed or partial fetch must raise, never return a short list."""

    def test_later_page_failure_discards_earlier_pages(self, mock_get, _sleep):
        mock_get.side_effect = [_response(PAGE_WITH_NEXT), _response(status=500)]

        with pytest.raises(ArbeitnowFetchError, match="page 2 failed: 500 Server Error"):
            fetch_arbeitnow_jobs(limit=100)
        assert mock_get.call_count == 2

    def test_exhausted_rate_limit_raises(self, mock_get, _sleep):
        mock_get.return_value = _response(status=429)

        with pytest.raises(ArbeitnowFetchError, match=r"page 1 still rate limited \(429\)"):
            fetch_arbeitnow_jobs()
        assert mock_get.call_count == MAX_RETRIES

    def test_invalid_json_raises(self, mock_get, _sleep):
        mock_get.return_value = _response(json_error=ValueError("Expecting value"))

        with pytest.raises(ArbeitnowFetchError, match="page 1 returned invalid JSON"):
            fetch_arbeitnow_jobs()

    def test_error_keeps_original_exception_as_cause(self, mock_get, _sleep):
        original = requests.ConnectionError("connection reset")
        mock_get.side_effect = original

        with pytest.raises(ArbeitnowFetchError) as exc_info:
            fetch_arbeitnow_jobs()
        assert exc_info.value.__cause__ is original

    def test_rate_limit_then_success_still_returns_jobs(self, mock_get, _sleep):
        mock_get.side_effect = [_response(status=429), _response(SAMPLE_API_RESPONSE)]

        payloads = fetch_arbeitnow_jobs()

        assert len(payloads) == 1
        assert mock_get.call_count == 2

    def test_reaching_limit_does_not_fetch_or_fail_on_the_next_page(self, mock_get, _sleep):
        mock_get.side_effect = [_response(PAGE_WITH_NEXT), _response(status=500)]

        payloads = fetch_arbeitnow_jobs(limit=1)

        assert len(payloads) == 1
        assert mock_get.call_count == 1


@patch("jobintel.etl.sources.arbeitnow.time.sleep")
@patch("jobintel.etl.sources.arbeitnow.requests.get")
def test_failed_fetch_is_recorded_as_failed_run(mock_get, _sleep, session):
    """End to end through the registry: the run is failed, not success with fewer jobs."""
    mock_get.side_effect = [_response(PAGE_WITH_NEXT), _response(status=500)]

    with pytest.raises(ArbeitnowFetchError):
        run_ingest(session, "arbeitnow", "", 200, environment="test")

    run = session.query(IngestRun).one()
    assert run.status == "failed"
    assert run.error == "Arbeitnow page 2 failed: 500 Server Error"
    assert run.finished_at is not None
    # Page 1's job was discarded with the failed fetch.
    assert session.query(RawJob).count() == 0


class TestArbeitnowSource:
    """Tests for the ArbeitnowSource class."""

    def test_source_name(self):
        """Test source has correct name."""
        source = ArbeitnowSource()
        assert source.name == "arbeitnow"

    @patch("jobintel.etl.sources.arbeitnow.requests.get")
    def test_source_fetch(self, mock_get):
        """Test source.fetch() delegates to fetch_arbeitnow_jobs."""
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_API_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        source = ArbeitnowSource()
        payloads = source.fetch(search="", limit=100)

        assert len(payloads) == 1
        assert payloads[0]["source"] == "arbeitnow"

    @patch("jobintel.etl.sources.arbeitnow.requests.get")
    def test_source_fetch_with_search(self, mock_get):
        """Test source.fetch() with search filter."""
        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_API_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        source = ArbeitnowSource()

        # Search for "Software" should match
        payloads = source.fetch(search="Software", limit=100)
        assert len(payloads) == 1

        # Search for "Nonexistent" should not match
        payloads = source.fetch(search="Nonexistent", limit=100)
        assert len(payloads) == 0
