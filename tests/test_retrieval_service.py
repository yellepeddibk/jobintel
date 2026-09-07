"""End-to-end retrieval against SQLite.

These cover the parts pure scoring cannot: what SQL filters out, what it must not
filter out, that source resolution neither drops nor duplicates a job, and that
ordering does not depend on how rows reached us.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from jobintel.models import Base, Job, JobSkill, RawJob
from jobintel.retrieval import RetrievalQuery, retrieve
from jobintel.retrieval.candidates import ID_BATCH

TODAY = date(2026, 9, 7)
PROD = "production"
DEV = "development"


def add_job(
    session, *, url, title, hash_, environment=PROD, company="Acme",
    location="Remote", posted_at=TODAY, description="python and sql", skills=(),
):
    job = Job(
        environment=environment, url=url, title=title, company=company,
        location=location, posted_at=posted_at, description=description, hash=hash_,
    )
    session.add(job)
    session.flush()
    for skill in skills:
        session.add(JobSkill(job_id=job.id, skill=skill))
    return job


def add_raw(session, *, url, source="remotive", environment=PROD):
    raw = RawJob(
        source=source, environment=environment,
        payload_json={"url": url, "title": "t", "description": "d"},
    )
    session.add(raw)
    session.flush()
    return raw


def ids(result):
    return [j.job_id for j in result.jobs]


# ------------------------------------------------------ preferred vs required


def test_preferred_skill_changes_rank_but_never_filters(session):
    add_job(session, url="u/1", title="Engineer", hash_="h1", skills=())
    add_job(session, url="u/2", title="Engineer", hash_="h2", skills=("python",))
    session.commit()

    result = retrieve(
        session,
        RetrievalQuery(environment=PROD, preferred_skills=("python",)),
        today=TODAY,
    )

    assert len(result.jobs) == 2, "a missing preferred skill must not exclude a job"
    assert result.jobs[0].title == "Engineer"
    assert result.jobs[0].matched_preferred_skills == ("python",)
    assert result.jobs[0].score > result.jobs[1].score
    assert result.jobs[1].missing_preferred_skills == ("python",)


def test_required_skill_filters_and_does_not_inflate_score_among_survivors(session):
    add_job(session, url="u/1", title="Engineer", hash_="h1", skills=("python",))
    add_job(session, url="u/2", title="Engineer", hash_="h2", skills=())
    session.commit()

    result = retrieve(
        session,
        RetrievalQuery(environment=PROD, required_skills=("python",)),
        today=TODAY,
    )

    assert ids(result) and len(result.jobs) == 1
    assert result.jobs[0].url == "u/1"
    # Required skills activate nothing, so there is no ranking signal at all.
    assert result.jobs[0].breakdown.active_weight == 0.0
    assert result.jobs[0].score == 0.0


def test_required_and_preferred_skills_compose(session):
    add_job(session, url="u/1", title="E", hash_="h1", skills=("python",))
    add_job(session, url="u/2", title="E", hash_="h2", skills=("python", "sql"))
    add_job(session, url="u/3", title="E", hash_="h3", skills=("sql",))
    session.commit()

    result = retrieve(
        session,
        RetrievalQuery(
            environment=PROD, required_skills=("python",), preferred_skills=("sql",)
        ),
        today=TODAY,
    )

    assert [j.url for j in result.jobs] == ["u/2", "u/1"], "u/3 lacks the required skill"
    assert result.jobs[0].score > result.jobs[1].score


# ------------------------------------------------------------ no truncation


def test_relevant_older_job_survives_many_newer_irrelevant_jobs(session):
    """Recency must never be used to pre-select candidates before ranking."""
    for i in range(60):
        add_job(
            session, url=f"noise/{i}", title="Warehouse Associate", hash_=f"n{i:03d}",
            description="forklift", posted_at=date(2026, 9, 1),
        )
    add_job(
        session, url="gem", title="Senior Data Engineer", hash_="gem",
        description="python", posted_at=date(2024, 1, 1),
    )
    session.commit()

    result = retrieve(
        session,
        RetrievalQuery(environment=PROD, text="senior data engineer", limit=5),
        today=TODAY,
    )

    assert result.total_candidates == 61, "every filter-passing row must be scored"
    assert result.jobs[0].url == "gem"


def test_total_candidates_is_not_capped(session):
    for i in range(40):
        add_job(session, url=f"u/{i}", title="E", hash_=f"h{i:03d}")
    session.commit()

    result = retrieve(session, RetrievalQuery(environment=PROD, limit=3), today=TODAY)

    assert result.total_candidates == 40
    assert result.returned == 3


# ------------------------------------------------------------ determinism


def test_repeated_retrieval_is_identical(session):
    for i in range(10):
        add_job(session, url=f"u/{i}", title="Data Engineer", hash_=f"h{i:03d}")
    session.commit()

    query = RetrievalQuery(environment=PROD, text="data engineer", limit=10)
    first = retrieve(session, query, today=TODAY)
    second = retrieve(session, query, today=TODAY)

    assert [j.to_dict() for j in first.jobs] == [j.to_dict() for j in second.jobs]


def test_ordering_is_invariant_to_insertion_order(session):
    """Same corpus, different insertion order, identical ranking.

    Compared by the stable content hash rather than by job_id, because job_id is an
    autoincrement surrogate that necessarily differs between the two builds.
    """
    corpus = [
        ("a", "Data Engineer", "hash-a", date(2026, 9, 1)),
        ("b", "Data Engineer", "hash-b", date(2026, 9, 1)),
        ("c", "Data Engineer", "hash-c", date(2026, 9, 1)),
        ("d", "Senior Data Engineer", "hash-d", date(2026, 8, 1)),
    ]
    query = RetrievalQuery(environment=PROD, text="data engineer", limit=10)

    def ranked(order):
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        local = sessionmaker(bind=engine)()
        for url, title, hash_, posted in order:
            add_job(local, url=url, title=title, hash_=hash_, posted_at=posted)
        local.commit()
        result = retrieve(local, query, today=TODAY)
        out = [(j.url, j.score) for j in result.jobs]
        local.close()
        engine.dispose()
        return out

    assert ranked(corpus) == ranked(list(reversed(corpus)))


# ------------------------------------------------------------ environment


def test_no_cross_environment_leakage(session):
    add_job(session, url="shared", title="Data Engineer", hash_="hp", environment=PROD)
    add_job(session, url="shared", title="Data Engineer", hash_="hd", environment=DEV)
    session.commit()

    prod = retrieve(session, RetrievalQuery(environment=PROD, text="data"), today=TODAY)
    dev = retrieve(session, RetrievalQuery(environment=DEV, text="data"), today=TODAY)

    assert [j.environment for j in prod.jobs] == [PROD]
    assert [j.environment for j in dev.jobs] == [DEV]
    assert prod.total_candidates == 1
    assert dev.total_candidates == 1


# ------------------------------------------------------------ sources


def test_source_filter_uses_the_authoritative_raw_row(session):
    add_raw(session, url="u/1", source="remotive")
    add_raw(session, url="u/1", source="remoteok")  # later id, must not win
    add_raw(session, url="u/2", source="arbeitnow")
    add_job(session, url="u/1", title="E", hash_="h1")
    add_job(session, url="u/2", title="E", hash_="h2")
    session.commit()

    remotive = retrieve(
        session, RetrievalQuery(environment=PROD, sources=("remotive",)), today=TODAY
    )
    remoteok = retrieve(
        session, RetrievalQuery(environment=PROD, sources=("remoteok",)), today=TODAY
    )

    assert [j.url for j in remotive.jobs] == ["u/1"]
    assert remotive.jobs[0].source == "remotive"
    assert remoteok.jobs == (), "the lower-id raw row is authoritative"


def test_source_filter_does_not_cross_environments(session):
    add_raw(session, url="u/1", source="arbeitnow", environment=DEV)
    add_raw(session, url="u/1", source="remotive", environment=PROD)
    add_job(session, url="u/1", title="E", hash_="h1", environment=PROD)
    session.commit()

    result = retrieve(
        session, RetrievalQuery(environment=PROD, sources=("arbeitnow",)), today=TODAY
    )
    assert result.jobs == ()


def test_multiple_raw_versions_do_not_fan_a_job_out(session):
    for _ in range(3):
        add_raw(session, url="u/1", source="remotive")
    add_job(session, url="u/1", title="E", hash_="h1")
    session.commit()

    result = retrieve(
        session, RetrievalQuery(environment=PROD, sources=("remotive",)), today=TODAY
    )

    assert result.total_candidates == 1
    assert len(result.jobs) == 1


def test_unresolved_optional_source_yields_none_without_dropping_the_job(session):
    add_job(session, url="orphan", title="Data Engineer", hash_="h1")  # no raw row
    session.commit()

    result = retrieve(session, RetrievalQuery(environment=PROD, text="data"), today=TODAY)

    assert len(result.jobs) == 1, "a job with no resolvable source must still be returned"
    assert result.jobs[0].source is None


# ------------------------------------------------------------ location


def test_required_location_filters_while_preferred_location_only_scores(session):
    add_job(session, url="u/1", title="E", hash_="h1", location="Berlin, Germany")
    add_job(session, url="u/2", title="E", hash_="h2", location="Munich, Germany")
    session.commit()

    filtered = retrieve(
        session, RetrievalQuery(environment=PROD, required_location="Berlin"), today=TODAY
    )
    preferred = retrieve(
        session, RetrievalQuery(environment=PROD, preferred_location="Berlin"), today=TODAY
    )

    assert [j.url for j in filtered.jobs] == ["u/1"]
    assert len(preferred.jobs) == 2, "a preference must not exclude"
    assert preferred.jobs[0].url == "u/1"
    assert preferred.jobs[0].score > preferred.jobs[1].score


def test_remote_only_filters(session):
    add_job(session, url="u/1", title="E", hash_="h1", location="Remote")
    add_job(session, url="u/2", title="E", hash_="h2", location="Berlin, Germany")
    session.commit()

    result = retrieve(session, RetrievalQuery(environment=PROD, remote_only=True), today=TODAY)
    assert [j.url for j in result.jobs] == ["u/1"]


# ------------------------------------------------------------ recency


def test_posted_within_days_filters_independently_of_prefer_recent(session):
    add_job(session, url="new", title="E", hash_="h1", posted_at=date(2026, 9, 1))
    add_job(session, url="old", title="E", hash_="h2", posted_at=date(2025, 1, 1))
    session.commit()

    filtered = retrieve(
        session,
        RetrievalQuery(environment=PROD, posted_within_days=30, prefer_recent=False),
        today=TODAY,
    )
    assert [j.url for j in filtered.jobs] == ["new"]
    assert filtered.jobs[0].breakdown.active_weight == 0.0, "the filter is not a signal"

    ranked = retrieve(
        session,
        RetrievalQuery(environment=PROD, prefer_recent=True),
        today=TODAY,
    )
    assert [j.url for j in ranked.jobs] == ["new", "old"], "both kept, only ordered"


# ------------------------------------------------------------ edge cases


def test_empty_signal_query_returns_ordered_results_with_zero_scores(session):
    add_job(session, url="u/1", title="E", hash_="h1", posted_at=date(2026, 1, 1))
    add_job(session, url="u/2", title="E", hash_="h2", posted_at=date(2026, 9, 1))
    session.commit()

    result = retrieve(session, RetrievalQuery(environment=PROD), today=TODAY)

    assert [j.url for j in result.jobs] == ["u/2", "u/1"], "newest first via tie-break"
    assert all(j.score == 0.0 for j in result.jobs)
    assert all(j.breakdown.active_weight == 0.0 for j in result.jobs)


def test_null_location_and_posted_at_do_not_crash(session):
    add_job(session, url="u/1", title="Data Engineer", hash_="h1", location=None, posted_at=None)
    session.commit()

    result = retrieve(
        session,
        RetrievalQuery(
            environment=PROD, text="data engineer", preferred_location="Berlin",
            prefer_recent=True,
        ),
        today=TODAY,
    )

    job = result.jobs[0]
    assert job.breakdown.component("location").present is False
    assert job.breakdown.component("recency").present is False
    assert job.breakdown.active_weight == pytest.approx(0.75)
    assert job.score == pytest.approx(0.4 / 0.75)


def test_job_with_no_skills_is_scored_not_skipped(session):
    add_job(session, url="u/1", title="E", hash_="h1", skills=())
    session.commit()

    result = retrieve(
        session, RetrievalQuery(environment=PROD, preferred_skills=("python",)), today=TODAY
    )
    assert len(result.jobs) == 1
    assert result.jobs[0].breakdown.component("skills").present is False


# ------------------------------------------------------------ hydration


def test_hydration_returns_faithful_evidence_from_stored_fields(session):
    description = "We need someone strong in python and comfortable with docker."
    add_job(
        session, url="u/1", title="Senior Data Engineer", hash_="h1",
        description=description, skills=("python",),
    )
    session.commit()

    result = retrieve(
        session,
        RetrievalQuery(environment=PROD, text="data engineer", preferred_skills=("python",)),
        today=TODAY,
    )

    job = result.jobs[0]
    assert job.title == "Senior Data Engineer"
    assert job.evidence[0].field == "title"
    assert job.evidence[0].excerpt == "Senior Data Engineer"
    for span in job.evidence[1:]:
        assert span.field == "description"
        assert span.excerpt in description, "excerpts must be verbatim substrings"


def test_evidence_is_bounded(session):
    add_job(
        session, url="u/1", title="Engineer", hash_="h1",
        description="python sql docker aws pandas pytest ci postgres " * 5,
        skills=("python", "sql", "docker", "aws", "pandas"),
    )
    session.commit()

    result = retrieve(
        session,
        RetrievalQuery(
            environment=PROD, text="python sql docker aws pandas",
            preferred_skills=("python", "sql", "docker", "aws", "pandas"),
        ),
        today=TODAY,
    )
    assert len(result.jobs[0].evidence) <= 4


def test_job_skills_are_reported_for_returned_jobs(session):
    add_job(session, url="u/1", title="E", hash_="h1", skills=("docker", "python"))
    session.commit()

    result = retrieve(session, RetrievalQuery(environment=PROD), today=TODAY)
    assert result.jobs[0].job_skills == ("docker", "python")


def test_result_serializes_losslessly(session):
    add_job(session, url="u/1", title="Data Engineer", hash_="h1", skills=("python",))
    session.commit()

    result = retrieve(
        session,
        RetrievalQuery(environment=PROD, text="data", preferred_skills=("python",)),
        today=TODAY,
    )
    payload = result.to_dict()

    assert payload["total_candidates"] == 1
    assert payload["returned"] == 1
    assert payload["scored_on"] == TODAY.isoformat()
    assert payload["jobs"][0]["breakdown"]["active_weight"] == 0.65


def test_limit_is_respected(session):
    for i in range(5):
        add_job(session, url=f"u/{i}", title="E", hash_=f"h{i}")
    session.commit()

    result = retrieve(session, RetrievalQuery(environment=PROD, limit=2), today=TODAY)
    assert result.returned == 2
    assert result.total_candidates == 5


def test_today_defaults_to_the_current_date_at_the_boundary(session):
    """The service may resolve the clock; nothing downstream may."""
    add_job(session, url="u/1", title="E", hash_="h1", posted_at=date(2026, 1, 1))
    session.commit()

    result = retrieve(session, RetrievalQuery(environment=PROD, prefer_recent=True))
    assert result.scored_on == date.today()


# ------------------------------------------------ query-count regressions
#
# Skill loading is conditional, and the whole point is that a query which does not
# rank on skills must not pay to load them for the entire corpus. These tests watch
# the statements actually issued rather than trusting the code to be shaped right.

SKILL_SELECT = "job_skills.job_id, job_skills.skill"
LARGE_CORPUS = 1200


@contextmanager
def watch_skill_queries(session):
    """Capture every JobSkill-loading statement and its bound id count.

    Matched on load_skills' exact select list rather than the table name, so the
    correlated EXISTS a required_skills filter emits, which selects only
    job_skills.job_id, is not mistaken for a skill load.
    """
    captured: list[int] = []

    def hook(conn, cursor, statement, parameters, context, executemany):
        if SKILL_SELECT in statement:
            captured.append(len(parameters) if parameters is not None else 0)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", hook)
    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", hook)


@pytest.fixture(scope="module")
def large_corpus():
    """1,200 production jobs, every other one carrying a skill. Read-only."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    for i in range(LARGE_CORPUS):
        add_job(
            session, url=f"u/{i}", title="Data Engineer", hash_=f"h{i:06d}",
            posted_at=date(2026, 9, 1), description="python and sql",
            skills=("python",) if i % 2 == 0 else (),
        )
    session.commit()
    yield session
    session.close()
    engine.dispose()


def test_text_only_query_does_not_load_skills_for_every_candidate(large_corpus):
    """A text-only query ranks nothing on skills, so it must not pay for them."""
    query = RetrievalQuery(environment=PROD, text="data engineer", limit=20)

    with watch_skill_queries(large_corpus) as batches:
        result = retrieve(large_corpus, query, today=TODAY)

    assert result.total_candidates == LARGE_CORPUS
    assert len(batches) == 1, f"expected one top-k skill query, got {len(batches)}"
    assert batches[0] <= query.limit, "skill loading must be scoped to the top-k"
    assert len(batches) < LARGE_CORPUS // ID_BATCH, "no candidate-wide skill batches"


def test_text_only_query_still_reports_correct_job_skills(large_corpus):
    """Skipping the candidate-wide load must not empty job_skills."""
    result = retrieve(
        large_corpus,
        RetrievalQuery(environment=PROD, text="data engineer", limit=20),
        today=TODAY,
    )

    by_url = {j.url: j for j in result.jobs}
    for url, job in by_url.items():
        expected = ("python",) if int(url.split("/")[1]) % 2 == 0 else ()
        assert job.job_skills == expected, f"{url} reported {job.job_skills}"
    assert any(j.job_skills for j in result.jobs), "fixture must include skilled jobs"


def test_empty_signal_query_does_not_load_skills_for_every_candidate(large_corpus):
    query = RetrievalQuery(environment=PROD, limit=20)

    with watch_skill_queries(large_corpus) as batches:
        result = retrieve(large_corpus, query, today=TODAY)

    assert result.total_candidates == LARGE_CORPUS
    assert len(batches) == 1
    assert batches[0] <= query.limit
    assert all(j.score == 0.0 for j in result.jobs)


def test_empty_signal_query_still_reports_correct_job_skills(large_corpus):
    result = retrieve(large_corpus, RetrievalQuery(environment=PROD, limit=20), today=TODAY)
    for job in result.jobs:
        expected = ("python",) if int(job.url.split("/")[1]) % 2 == 0 else ()
        assert job.job_skills == expected


def test_preferred_skills_query_loads_candidate_skills_in_bounded_batches(large_corpus):
    """Scoring needs every candidate's skills, so the batches are expected here."""
    query = RetrievalQuery(
        environment=PROD, text="data engineer", preferred_skills=("python",), limit=20
    )

    with watch_skill_queries(large_corpus) as batches:
        result = retrieve(large_corpus, query, today=TODAY)

    expected_batches = -(-LARGE_CORPUS // ID_BATCH)  # ceil
    assert len(batches) == expected_batches, f"expected {expected_batches}, got {len(batches)}"
    assert all(size <= ID_BATCH for size in batches), "each batch stays bounded"
    assert len(batches) < LARGE_CORPUS, "nothing resembling a query per candidate"
    assert result.total_candidates == LARGE_CORPUS


def test_preferred_skills_query_does_not_reload_top_k_skills(large_corpus):
    """The returned jobs reuse the candidate-wide data instead of refetching it."""
    query = RetrievalQuery(
        environment=PROD, preferred_skills=("python",), limit=20
    )

    with watch_skill_queries(large_corpus) as batches:
        result = retrieve(large_corpus, query, today=TODAY)

    expected_batches = -(-LARGE_CORPUS // ID_BATCH)
    assert len(batches) == expected_batches, (
        f"{len(batches)} skill queries for {expected_batches} candidate batches "
        "means the top-k was reloaded"
    )
    assert all(j.job_skills == ("python",) for j in result.jobs)
    assert all(j.matched_preferred_skills == ("python",) for j in result.jobs)


def test_conditional_skill_loading_does_not_change_ranking(large_corpus):
    """Scores and ordering must be identical to what a full load would produce."""
    query = RetrievalQuery(environment=PROD, text="data engineer", limit=20)
    result = retrieve(large_corpus, query, today=TODAY)

    ordered = [(j.url, j.score) for j in result.jobs]
    assert ordered == [(j.url, j.score) for j in retrieve(large_corpus, query, today=TODAY).jobs]
    assert all(score == 1.0 for _, score in ordered), "title matches every token"


def test_skills_component_present_is_false_when_the_query_does_not_rank_on_skills(
    large_corpus,
):
    """Documented consequence of not loading what is not needed.

    `present` is only meaningful for an active component. With skills inactive the
    data is never fetched, so it reports False regardless of the job, which costs
    nothing because an inactive component touches neither score nor denominator.
    """
    result = retrieve(
        large_corpus, RetrievalQuery(environment=PROD, text="data", limit=5), today=TODAY
    )
    skilled = [j for j in result.jobs if j.job_skills]
    assert skilled, "fixture must include a job that does have skills"
    for job in skilled:
        component = job.breakdown.component("skills")
        assert component.active is False
        assert component.present is False
        assert component.contribution == 0.0
