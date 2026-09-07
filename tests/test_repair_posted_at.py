"""Tests for the one-time posted_at / hash repair.

The repair rewrites stored data, so the tests care as much about what it refuses
to do as about what it does: dry runs must not write, preflight failures must
abort before any mutation, and a rerun must be a no-op.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from jobintel.etl.repair import (
    ProposedHashCollision,
    RepairError,
    UnresolvedAuthoritativeRawJob,
    apply_repair,
    plan_repair,
    resolve_authoritative_raw,
)
from jobintel.etl.transform import job_hash
from jobintel.models import Job, RawJob

PROD = "production"
DEV = "development"


def add_raw(session, url, *, environment=PROD, posted_at=None, source="remotive",
            title="Data Engineer", company="Acme", location="Remote"):
    raw = RawJob(
        source=source,
        environment=environment,
        payload_json={
            "url": url, "title": title, "company": company,
            "location": location, "posted_at": posted_at, "description": "Python",
        },
    )
    session.add(raw)
    session.flush()
    return raw


def add_job(session, url, *, environment=PROD, posted_at=None,
            title="Data Engineer", company="Acme", location="Remote", hash_=...):
    """A job as the broken pipeline would have written it: NULL date, dateless hash."""
    job = Job(
        environment=environment, url=url, title=title, company=company,
        location=location, posted_at=posted_at,
        hash=job_hash(title, company, location, posted_at) if hash_ is ... else hash_,
    )
    session.add(job)
    session.flush()
    return job


def broken_state(session):
    """Two production jobs written before the parser fix, plus a development one."""
    add_raw(session, "https://x/1", posted_at="2026-01-05T09:00:00+00:00")
    add_raw(session, "https://x/2", posted_at="2026-02-05T09:00:00+00:00",
            title="DevOps", company="Cloud", location="Berlin")
    add_job(session, "https://x/1")
    add_job(session, "https://x/2", title="DevOps", company="Cloud", location="Berlin")
    session.commit()


# ------------------------------------------------------- authoritative raw row


def test_authoritative_raw_row_is_the_lowest_id(session):
    first = add_raw(session, "https://x/1", posted_at="2026-01-05T09:00:00+00:00")
    add_raw(session, "https://x/1", posted_at="2026-09-09T09:00:00+00:00", source="remoteok")
    session.commit()

    resolved = resolve_authoritative_raw(session, PROD)
    assert resolved["https://x/1"].id == first.id

    add_job(session, "https://x/1")
    session.commit()
    plan = plan_repair(session, PROD)
    assert plan.proposals[0].proposed_posted_at == date(2026, 1, 5)


def test_environments_are_isolated(session):
    add_raw(session, "https://x/1", environment=PROD, posted_at="2026-01-05T09:00:00+00:00")
    add_raw(session, "https://x/1", environment=DEV, posted_at="2026-07-07T09:00:00+00:00")
    add_job(session, "https://x/1", environment=PROD)
    add_job(session, "https://x/1", environment=DEV)
    session.commit()

    apply_repair(session, PROD, dry_run=False)

    prod = session.execute(select(Job).where(Job.environment == PROD)).scalar_one()
    dev = session.execute(select(Job).where(Job.environment == DEV)).scalar_one()
    assert prod.posted_at == date(2026, 1, 5)
    assert dev.posted_at is None, "the development row must be untouched"

    apply_repair(session, DEV, dry_run=False)
    session.refresh(dev)
    assert dev.posted_at == date(2026, 7, 7)


def test_raw_rows_from_another_environment_are_not_used(session):
    """A development raw row must not supply a date to a production job."""
    add_raw(session, "https://x/1", environment=DEV, posted_at="2026-07-07T09:00:00+00:00")
    add_job(session, "https://x/1", environment=PROD)
    session.commit()

    with pytest.raises(UnresolvedAuthoritativeRawJob):
        plan_repair(session, PROD)


# ------------------------------------------------------------------ planning


def test_plan_backfills_dates_and_recomputes_hashes(session):
    broken_state(session)

    plan = plan_repair(session, PROD)

    assert plan.total == 2
    assert plan.posted_at_backfilled == 2
    assert plan.hashes_changed == 2
    assert plan.still_undated == 0
    for p in plan.proposals:
        assert p.current_posted_at is None
        assert p.proposed_posted_at is not None
        assert p.hash_changes


def test_proposed_hash_uses_the_exact_job_hash_function(session):
    broken_state(session)

    plan = plan_repair(session, PROD)
    first = plan.proposals[0]
    job = session.get(Job, first.job_id)
    assert first.proposed_hash == job_hash(
        job.title, job.company, job.location, first.proposed_posted_at
    )


def test_plan_is_read_only(session):
    broken_state(session)
    before = _snapshot(session)

    plan_repair(session, PROD)

    assert _snapshot(session) == before


def test_environment_must_be_explicit(session):
    broken_state(session)
    with pytest.raises(TypeError):
        apply_repair(session)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        plan_repair(session, "")


def test_job_without_a_url_keeps_its_date_and_does_not_abort(session):
    add_raw(session, "https://x/1", posted_at="2026-01-05T09:00:00+00:00")
    add_job(session, "https://x/1")
    add_job(session, None, title="No URL", company="Acme", location="Remote")
    session.commit()

    plan = plan_repair(session, PROD)
    urlless = next(p for p in plan.proposals if p.url is None)
    assert urlless.proposed_posted_at is None
    assert not urlless.hash_changes


# ----------------------------------------------------------------- dry run


def test_dry_run_changes_nothing(session):
    broken_state(session)
    before = _snapshot(session)

    result = apply_repair(session, PROD, dry_run=True)

    assert result.dry_run is True
    assert result.updated == 2
    assert _snapshot(session) == before, "a dry run must not write"


def test_dry_run_is_the_default(session):
    broken_state(session)
    before = _snapshot(session)
    apply_repair(session, PROD)
    assert _snapshot(session) == before


# ------------------------------------------------------------------- apply


def test_apply_updates_exactly_the_intended_rows(session):
    broken_state(session)
    untouched = add_job(session, "https://x/3", environment=DEV)
    session.commit()
    dev_hash_before = untouched.hash

    result = apply_repair(session, PROD, dry_run=False)

    assert result.dry_run is False
    assert result.examined == 2
    assert result.updated == 2
    assert result.posted_at_backfilled == 2

    jobs = {
        j.url: j
        for j in session.execute(select(Job).where(Job.environment == PROD)).scalars()
    }
    assert jobs["https://x/1"].posted_at == date(2026, 1, 5)
    assert jobs["https://x/2"].posted_at == date(2026, 2, 5)
    for j in jobs.values():
        assert j.hash == job_hash(j.title, j.company, j.location, j.posted_at)

    session.refresh(untouched)
    assert untouched.hash == dev_hash_before


def test_rerun_is_idempotent(session):
    broken_state(session)

    first = apply_repair(session, PROD, dry_run=False)
    after_first = _snapshot(session)

    second = apply_repair(session, PROD, dry_run=False)

    assert first.updated == 2
    assert second.updated == 0
    assert _snapshot(session) == after_first


def test_unparseable_raw_date_leaves_the_job_undated(session):
    add_raw(session, "https://x/1", posted_at="not a date")
    add_job(session, "https://x/1")
    session.commit()

    result = apply_repair(session, PROD, dry_run=False)

    assert result.still_undated == 1
    assert session.execute(select(Job.posted_at)).scalar_one() is None


# ------------------------------------------------------------- preflight aborts


def test_unresolved_authoritative_raw_row_aborts_before_writing(session):
    add_raw(session, "https://x/1", posted_at="2026-01-05T09:00:00+00:00")
    add_job(session, "https://x/1")
    # Distinct fields, so this row is valid on insert and the only problem the
    # repair can find is the missing raw row.
    add_job(session, "https://x/orphan", title="Orphan", company="Nowhere", location="Nowhere")
    session.commit()
    before = _snapshot(session)

    with pytest.raises(UnresolvedAuthoritativeRawJob, match="no authoritative raw row"):
        apply_repair(session, PROD, dry_run=False)

    session.rollback()
    assert _snapshot(session) == before, "nothing may be written when preflight fails"


def test_proposed_hash_collision_aborts_before_writing(session):
    """Two jobs whose proposed states would be identical must stop the repair."""
    add_raw(session, "https://x/1", posted_at="2026-01-05T09:00:00+00:00")
    add_raw(session, "https://x/2", posted_at="2026-01-05T09:00:00+00:00")
    # Same title/company/location, so once both get the same date they collide.
    add_job(session, "https://x/1")
    add_job(session, "https://x/2", posted_at=date(1999, 1, 1))
    session.commit()
    before = _snapshot(session)

    with pytest.raises(ProposedHashCollision, match="uq_jobs_environment_hash"):
        apply_repair(session, PROD, dry_run=False)

    session.rollback()
    assert _snapshot(session) == before


def test_collision_is_detected_during_a_dry_run_too(session):
    add_raw(session, "https://x/1", posted_at="2026-01-05T09:00:00+00:00")
    add_raw(session, "https://x/2", posted_at="2026-01-05T09:00:00+00:00")
    add_job(session, "https://x/1")
    add_job(session, "https://x/2", posted_at=date(1999, 1, 1))
    session.commit()

    with pytest.raises(ProposedHashCollision):
        apply_repair(session, PROD, dry_run=True)


def test_repair_errors_share_a_base_class(session):
    add_job(session, "https://x/orphan", title="Orphan", company="Nowhere", location="Nowhere")
    session.commit()
    with pytest.raises(RepairError):
        plan_repair(session, PROD)


# --------------------------------------------- transient uniqueness hazard


def test_survives_a_transient_hash_collision_during_the_rewrite(session):
    """One row's new hash equals another row's old hash.

    Rewriting hashes row by row would violate uq_jobs_environment_hash partway
    through even though the final state is clean, because unique constraints are
    checked per statement rather than deferred. The repair clears the hashes it is
    about to move before assigning the new ones, so no intermediate state conflicts.
    """
    shared = {"title": "Data Engineer", "company": "Acme", "location": "Remote"}
    jan, feb = date(2026, 1, 5), date(2026, 2, 5)

    # job A currently holds the hash that job B is about to be given.
    add_raw(session, "https://x/a", posted_at="2026-02-05T09:00:00+00:00", **shared)
    add_raw(session, "https://x/b", posted_at="2026-01-05T09:00:00+00:00", **shared)
    job_a = add_job(session, "https://x/a", posted_at=jan, **shared)
    job_b = add_job(session, "https://x/b", posted_at=None, **shared)
    session.commit()

    assert job_a.hash == job_hash(**shared, posted_at=jan)
    assert job_b.hash == job_hash(**shared, posted_at=None)

    plan = plan_repair(session, PROD)
    proposed = {p.url: p.proposed_hash for p in plan.proposals}
    assert proposed["https://x/b"] == job_a.hash, (
        "fixture must actually create the transient conflict"
    )

    apply_repair(session, PROD, dry_run=False)

    session.refresh(job_a)
    session.refresh(job_b)
    assert job_a.posted_at == feb
    assert job_b.posted_at == jan
    assert job_a.hash == job_hash(**shared, posted_at=feb)
    assert job_b.hash == job_hash(**shared, posted_at=jan)
    assert job_a.hash != job_b.hash


# --------------------------------------------------------------- rollback


def test_failure_during_apply_leaves_no_partial_state(session, monkeypatch):
    """An error mid-write must roll back everything, not leave half a repair."""
    broken_state(session)
    before = _snapshot(session)

    from jobintel.etl import repair as repair_module

    real_execute = session.execute
    calls = {"n": 0}

    def exploding_execute(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 2:  # after the clearing pass and the first row update
            raise RuntimeError("simulated failure mid-repair")
        return real_execute(*args, **kwargs)

    plan = repair_module.plan_repair(session, PROD)
    repair_module.verify_plan(plan)

    monkeypatch.setattr(session, "execute", exploding_execute)
    with pytest.raises(RuntimeError, match="simulated failure"):
        apply_repair(session, PROD, dry_run=False)
    monkeypatch.undo()

    assert _snapshot(session) == before, "a mid-write failure must roll back entirely"


def _snapshot(session):
    """Every field the repair could touch, for exact before/after comparison."""
    session.expire_all()
    return sorted(
        session.execute(
            select(Job.id, Job.environment, Job.url, Job.posted_at, Job.hash).order_by(Job.id)
        ).all()
    )
