"""ETL orchestration shared by the dashboard and CLI scripts."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from jobintel.core.config import settings
from jobintel.etl.raw import upsert_raw_job
from jobintel.etl.skills import extract_skills_for_all_jobs
from jobintel.etl.sources.registry import fetch_from_source
from jobintel.etl.transform import transform_jobs
from jobintel.models import IngestRun

logger = logging.getLogger(__name__)


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


def run_etl_from_payloads(
    session: Session,
    payloads: list[dict],
    environment: str | None = None,
) -> EtlResult:
    """Store payloads in raw_jobs, transform them into jobs, and extract skills."""
    env = environment or settings.ENV
    inserted_raw = 0
    for payload in payloads:
        if upsert_raw_job(session, payload, environment=env):
            inserted_raw += 1

    inserted_jobs = transform_jobs(session, environment=env)
    inserted_skills = extract_skills_for_all_jobs(session, environment=env)

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
    environment: str | None = None,
) -> IngestResult:
    """Fetch one source and run ETL, recording the run in ingest_runs. Re-raises on failure."""
    env = environment or settings.ENV

    run = IngestRun(
        source=source_name,
        search=search if search else None,
        limit=limit,
        environment=env,
        status="running",
        started_at=datetime.now(UTC),
    )
    session.add(run)
    session.commit()
    run_id = run.id  # read now; it may be unreadable after a failed rollback

    try:
        payloads, warnings = fetch_from_source(source_name, search, limit)
        etl_result = run_etl_from_payloads(session, payloads, environment=env)

        run.status = "success"
        run.finished_at = datetime.now(UTC)
        run.fetched = len(payloads)
        run.inserted_raw = etl_result.inserted_raw
        run.inserted_jobs = etl_result.inserted_jobs
        run.inserted_skills = etl_result.inserted_skills
        run.warnings = warnings if warnings else None
        session.commit()

        return IngestResult(
            fetched=len(payloads),
            inserted_raw=etl_result.inserted_raw,
            inserted_jobs=etl_result.inserted_jobs,
            inserted_skills=etl_result.inserted_skills,
            warnings=warnings,
        )
    except Exception as e:
        # Roll back first: a failed flush leaves the session unusable. If recording
        # fails too, log it and still raise the original error.
        try:
            session.rollback()
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error = str(e)
            session.commit()
        except Exception:
            logger.exception("Could not record failed ingest run %s", run_id)
            session.rollback()
        raise


@dataclass
class SourceOutcome:
    """What happened to one source in a multi-source ingest."""

    source: str
    result: IngestResult | None = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def run_ingest_sources(
    session_factory: Callable[[], Session],
    sources: Sequence[str],
    search: str,
    limit: int,
    environment: str | None = None,
) -> list[SourceOutcome]:
    """Run `run_ingest` per source, each in a fresh session, continuing past failures.

    Exceptions are returned in the outcomes, not raised; the caller decides how to report.
    """
    outcomes: list[SourceOutcome] = []
    for source in sources:
        try:
            with session_factory() as session:
                result = run_ingest(session, source, search, limit, environment=environment)
        except Exception as e:
            outcomes.append(SourceOutcome(source=source, error=e))
        else:
            outcomes.append(SourceOutcome(source=source, result=result))
    return outcomes


def run_postprocess(session: Session, environment: str | None = None) -> tuple[int, int]:
    """Re-run transform and skills for one environment: (inserted_jobs, inserted_skills)."""
    env = environment or settings.ENV
    inserted_jobs = transform_jobs(session, environment=env)
    inserted_skills = extract_skills_for_all_jobs(session, environment=env)
    return inserted_jobs, inserted_skills
