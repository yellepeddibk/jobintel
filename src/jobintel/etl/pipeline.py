"""Centralized ETL pipeline runner.

Provides shared orchestration logic for both the Streamlit dashboard and CLI scripts.
Keeps pipeline behavior consistent across all entrypoints.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from jobintel.etl.raw import upsert_raw_job
from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.sources.registry import fetch_from_source
from jobintel.etl.transform import transform_jobs


@dataclass
class EtlResult:
    """Result from running ETL on payloads."""

    inserted_raw: int = 0
    inserted_jobs: int = 0
    inserted_skills: int = 0


@dataclass
class IngestResult:
    """Result from a full ingest operation (fetch + ETL)."""

    fetched: int = 0
    inserted_raw: int = 0
    inserted_jobs: int = 0
    inserted_skills: int = 0
    warnings: list[str] = field(default_factory=list)


def run_etl_from_payloads(session: Session, payloads: list[dict]) -> EtlResult:
    """Run ETL pipeline on a list of raw payloads.

    Steps:
        1. Upsert each payload into raw_jobs (idempotent)
        2. Transform raw_jobs into normalized jobs
        3. Extract skills from jobs into job_skills

    Args:
        session: SQLAlchemy session (ETL functions handle commits internally)
        payloads: List of raw job payloads to process

    Returns:
        EtlResult with counts of inserted records
    """
    inserted_raw = 0
    for payload in payloads:
        if upsert_raw_job(session, payload):
            inserted_raw += 1

    inserted_jobs = transform_jobs(session)
    inserted_skills = extract_skills_for_all_jobs(session)

    return EtlResult(
        inserted_raw=inserted_raw,
        inserted_jobs=inserted_jobs,
        inserted_skills=inserted_skills,
    )


def run_ingest(
    session: Session,
    source_name: str,
    search: str,
    limit: int,
) -> IngestResult:
    """Run full ingest pipeline: fetch from source + ETL.

    Steps:
        1. Fetch jobs from the specified source via registry
        2. Run ETL pipeline on fetched payloads

    Args:
        session: SQLAlchemy session (ETL functions handle commits internally)
        source_name: Name of the source to fetch from (e.g., "remotive", "remoteok")
        search: Search query string
        limit: Maximum number of jobs to fetch

    Returns:
        IngestResult with counts and any validation warnings
    """
    # Fetch from source
    payloads, warnings = fetch_from_source(source_name, search, limit)

    # Run ETL on fetched payloads
    etl_result = run_etl_from_payloads(session, payloads)

    return IngestResult(
        fetched=len(payloads),
        inserted_raw=etl_result.inserted_raw,
        inserted_jobs=etl_result.inserted_jobs,
        inserted_skills=etl_result.inserted_skills,
        warnings=warnings,
    )


def run_postprocess(session: Session) -> tuple[int, int]:
    """Run transform and skills extraction only (no fetch/raw upsert).

    Useful for reprocessing existing raw_jobs data.

    Args:
        session: SQLAlchemy session (transform and skills functions commit internally)

    Returns:
        Tuple of (inserted_jobs, inserted_skills)
    """
    inserted_jobs = transform_jobs(session)
    inserted_skills = extract_skills_for_all_jobs(session)
    return inserted_jobs, inserted_skills
