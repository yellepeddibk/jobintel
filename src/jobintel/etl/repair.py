"""Backfill Job.posted_at from the authoritative raw row and recompute Job.hash.

Fixes rows written before `_safe_date` parsed adapter datetimes. Planned and verified
in full before writing. Never runs `transform_jobs`; run the pipeline separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

# Reuse the pipeline's parser so the repair cannot drift from it.
from jobintel.etl.transform import _safe_date, job_hash
from jobintel.models import Job, RawJob


class RepairError(RuntimeError):
    """Base class for conditions that stop the repair before it writes."""


class UnresolvedAuthoritativeRawJob(RepairError):
    """A job has a URL but no raw row to derive its posted_at from."""


class ProposedHashCollision(RepairError):
    """Two jobs in one environment would end up sharing a hash."""


class ProposedUrlCollision(RepairError):
    """Two jobs in one environment would end up sharing a URL."""


@dataclass(frozen=True)
class JobProposal:
    """The complete proposed end state for one job."""

    job_id: int
    environment: str
    url: str | None
    current_posted_at: date | None
    proposed_posted_at: date | None
    current_hash: str | None
    proposed_hash: str

    @property
    def posted_at_changes(self) -> bool:
        return self.current_posted_at != self.proposed_posted_at

    @property
    def hash_changes(self) -> bool:
        return self.current_hash != self.proposed_hash

    @property
    def changes(self) -> bool:
        return self.posted_at_changes or self.hash_changes


@dataclass
class RepairPlan:
    """Everything the repair intends to do, computed without writing."""

    environment: str
    proposals: list[JobProposal] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.proposals)

    @property
    def changing(self) -> list[JobProposal]:
        return [p for p in self.proposals if p.changes]

    @property
    def posted_at_backfilled(self) -> int:
        return sum(
            1 for p in self.proposals
            if p.current_posted_at is None and p.proposed_posted_at is not None
        )

    @property
    def hashes_changed(self) -> int:
        return sum(1 for p in self.proposals if p.hash_changes)

    @property
    def still_undated(self) -> int:
        return sum(1 for p in self.proposals if p.proposed_posted_at is None)


@dataclass
class RepairResult:
    """What actually happened."""

    environment: str
    dry_run: bool
    examined: int
    updated: int
    posted_at_backfilled: int
    hashes_changed: int
    still_undated: int


def resolve_authoritative_raw(session: Session, environment: str) -> dict[str, RawJob]:
    """Map each URL in one environment to its lowest-id raw row (the job's source row)."""
    authoritative: dict[str, RawJob] = {}
    rows = session.execute(
        select(RawJob).where(RawJob.environment == environment).order_by(RawJob.id)
    ).scalars()
    for raw in rows:
        url = (raw.payload_json or {}).get("url")
        if not url:
            continue
        if url not in authoritative:
            authoritative[url] = raw
    return authoritative


def plan_repair(session: Session, environment: str) -> RepairPlan:
    """Compute the proposed state for one environment without writing.

    Raises UnresolvedAuthoritativeRawJob if a job's URL has no raw row. Jobs without a
    URL keep their posted_at; their hash is still recomputed.
    """
    if not environment:
        raise ValueError("environment is required and must not be empty")

    authoritative = resolve_authoritative_raw(session, environment)

    jobs = (
        session.execute(
            select(Job).where(Job.environment == environment).order_by(Job.id)
        )
        .scalars()
        .all()
    )

    plan = RepairPlan(environment=environment)
    unresolved: list[int] = []

    for job in jobs:
        if job.url:
            raw = authoritative.get(job.url)
            if raw is None:
                unresolved.append(job.id)
                continue
            proposed_posted_at = _safe_date((raw.payload_json or {}).get("posted_at"))
        else:
            proposed_posted_at = job.posted_at

        plan.proposals.append(
            JobProposal(
                job_id=job.id,
                environment=job.environment,
                url=job.url,
                current_posted_at=job.posted_at,
                proposed_posted_at=proposed_posted_at,
                current_hash=job.hash,
                proposed_hash=job_hash(
                    job.title, job.company, job.location, proposed_posted_at
                ),
            )
        )

    if unresolved:
        raise UnresolvedAuthoritativeRawJob(
            f"{len(unresolved)} job(s) in environment {environment!r} have a URL with "
            f"no authoritative raw row: ids {sorted(unresolved)[:20]}"
            f"{' ...' if len(unresolved) > 20 else ''}. Refusing to guess at a date."
        )
    return plan


def verify_plan(plan: RepairPlan) -> None:
    """Raise if the proposed state would break either unique constraint.

    URLs are not changed, but are checked so pre-existing duplicates stop the repair.
    """
    hash_owners: dict[str, list[int]] = {}
    url_owners: dict[str, list[int]] = {}
    for p in plan.proposals:
        hash_owners.setdefault(p.proposed_hash, []).append(p.job_id)
        if p.url is not None:
            url_owners.setdefault(p.url, []).append(p.job_id)

    hash_dupes = {h: ids for h, ids in hash_owners.items() if len(ids) > 1}
    if hash_dupes:
        detail = ", ".join(
            f"{h[:12]}...={sorted(ids)}" for h, ids in list(hash_dupes.items())[:10]
        )
        raise ProposedHashCollision(
            f"{len(hash_dupes)} proposed (environment, hash) collision group(s) in "
            f"{plan.environment!r}, covering {sum(len(v) for v in hash_dupes.values())} "
            f"row(s): {detail}. Applying this would violate uq_jobs_environment_hash."
        )

    url_dupes = {u: ids for u, ids in url_owners.items() if len(ids) > 1}
    if url_dupes:
        raise ProposedUrlCollision(
            f"{len(url_dupes)} duplicate URL(s) already present in {plan.environment!r}, "
            f"covering {sum(len(v) for v in url_dupes.values())} row(s). This violates "
            "uq_jobs_environment_url independently of the repair."
        )


def apply_repair(
    session: Session,
    environment: str,
    *,
    dry_run: bool = True,
) -> RepairResult:
    """Plan, verify, then write unless dry_run. One environment, one transaction.

    `environment` has no default so a repair can never fall back to production.
    Re-running after a successful repair is a no-op.
    """
    plan = plan_repair(session, environment)
    verify_plan(plan)

    changing = plan.changing
    result = RepairResult(
        environment=environment,
        dry_run=dry_run,
        examined=plan.total,
        updated=len(changing),
        posted_at_backfilled=plan.posted_at_backfilled,
        hashes_changed=plan.hashes_changed,
        still_undated=plan.still_undated,
    )
    if dry_run or not changing:
        return result

    try:
        # Clear changing hashes first: Postgres checks uniqueness per statement, so a
        # new hash could otherwise collide with another row's old one mid-update.
        session.execute(
            update(Job)
            .where(Job.id.in_([p.job_id for p in changing if p.hash_changes]))
            .values(hash=None)
        )
        for p in sorted(changing, key=lambda x: x.job_id):
            session.execute(
                update(Job)
                .where(Job.id == p.job_id)
                .values(posted_at=p.proposed_posted_at, hash=p.proposed_hash)
            )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return result
