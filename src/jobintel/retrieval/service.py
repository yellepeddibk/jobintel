"""Orchestration: filter in SQL, score in Python, hydrate the top-k.

The shape is score-then-hydrate. Every candidate is scored from a lightweight row
that is discarded immediately, then only the jobs actually being returned are fetched
again for their full fields and evidence. That keeps memory bounded by the number of
candidates rather than by the size of every description, and keeps evidence work
proportional to `limit` instead of to the corpus.

Skill loading follows the same principle and is conditional. When `preferred_skills`
participates in scoring, every candidate's skills are loaded in bounded batches
because ranking needs them, and the returned jobs reuse that data rather than
refetching it. When it does not, no candidate-wide load happens at all and skills are
fetched only for the returned top-k, purely to populate `job_skills`. No query is
issued per candidate in either case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from jobintel.retrieval.candidates import (
    count_candidates,
    hydrate,
    iter_candidates,
    load_skills,
)
from jobintel.retrieval.contracts import (
    LOCATION,
    RECENCY,
    SKILLS,
    TEXT,
    Evidence,
    RetrievalQuery,
    RetrievalResult,
    RetrievedJob,
    ScoreBreakdown,
)
from jobintel.retrieval.scoring import (
    combine,
    score_location,
    score_recency,
    score_skills,
    score_text,
    sort_key,
    tokenize,
)

# Evidence stays small on purpose. This is grounding for a later analyst, not a
# summarizer: a bounded number of literal windows, each capped in length.
MAX_EVIDENCE_SPANS = 4
EVIDENCE_WINDOW = 240


@dataclass(frozen=True)
class _Scored:
    """What survives scoring. No description: it is dropped with the candidate row."""

    job_id: int
    environment: str
    breakdown: ScoreBreakdown
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    posted_at: date | None
    job_hash: str | None


def _excerpt(text: str, term: str) -> str | None:
    """A literal window of `text` around the first occurrence of `term`.

    Returns a verbatim substring. Nothing is appended, trimmed to a sentence, or
    rewritten, so an excerpt can always be found character-for-character in the
    stored field.
    """
    if not text or not term:
        return None
    position = text.lower().find(term.lower())
    if position < 0:
        return None
    half = EVIDENCE_WINDOW // 2
    start = max(0, position - half)
    end = min(len(text), position + len(term) + half)
    return text[start:end].strip() or None


def _build_evidence(
    query: RetrievalQuery, title: str, description: str | None,
    matched_skills: tuple[str, ...],
) -> tuple[Evidence, ...]:
    """Title plus a few description windows, in a deterministic order.

    Matched skills come before query tokens because a skill match is the more
    specific claim, and both are iterated in a fixed order so the same job always
    produces the same evidence.
    """
    spans: list[Evidence] = []
    if title:
        spans.append(Evidence(field="title", excerpt=title))

    if description:
        seen: set[str] = set()
        terms = list(matched_skills) + list(tokenize(query.text))
        for term in terms:
            if len(spans) >= MAX_EVIDENCE_SPANS:
                break
            excerpt = _excerpt(description, term)
            if excerpt and excerpt not in seen:
                seen.add(excerpt)
                spans.append(Evidence(field="description", excerpt=excerpt))
    return tuple(spans)


def retrieve(
    session: Session,
    query: RetrievalQuery,
    *,
    today: date | None = None,
) -> RetrievalResult:
    """Rank jobs for one environment, deterministically.

    `today` is resolved once here, at the service boundary, and then passed
    explicitly to filtering and scoring. Nothing downstream reads the clock, so a
    caller that pins `today` gets a fully reproducible run.
    """
    resolved_today = today if today is not None else date.today()
    active = query.active_components
    tokens = tokenize(query.text)

    # Pass 1: score every candidate, holding no descriptions.
    candidates = list(iter_candidates(session, query, today=resolved_today))

    # Candidate-wide skills are loaded only when they actually rank something. A
    # text-only or filter-only query does not need them to order anything, and
    # loading them for the whole corpus would cost a batch per 400 candidates to
    # produce a field only the returned handful ever shows.
    skills_by_job: dict[int, frozenset[str]] = {}
    if query.preferred_skills and candidates:
        skills_by_job = load_skills(session, [c.job_id for c in candidates])

    scored: list[_Scored] = []
    for row in candidates:
        job_skills = skills_by_job.get(row.job_id, frozenset())
        has_skill_data = row.job_id in skills_by_job

        text_part = score_text(tokens, row.title, row.company, row.description)
        skill_raw, skill_norm, skill_present, matched, missing = score_skills(
            query.preferred_skills, job_skills, has_skill_data
        )
        location_part = score_location(query.preferred_location, row.location)
        recency_part = score_recency(
            row.posted_at, resolved_today, query.recency_horizon_days
        )

        breakdown = combine(
            {
                TEXT: text_part,
                SKILLS: (skill_raw, skill_norm, skill_present),
                LOCATION: location_part,
                RECENCY: recency_part,
            },
            active,
        )
        scored.append(
            _Scored(
                job_id=row.job_id,
                environment=row.environment,
                breakdown=breakdown,
                matched_skills=matched,
                missing_skills=missing,
                posted_at=row.posted_at,
                job_hash=row.job_hash,
            )
        )

    scored.sort(
        key=lambda s: sort_key(s.breakdown.score, s.posted_at, s.job_hash, s.job_id)
    )
    top = scored[: query.limit]
    top_ids = [s.job_id for s in top]

    # Pass 2: hydrate only what is being returned. Skills for the returned jobs come
    # from the candidate-wide load when scoring already did it, and are otherwise
    # fetched for just these ids, so job_skills is always populated and the data is
    # never loaded twice.
    top_skills = (
        {job_id: skills_by_job.get(job_id, frozenset()) for job_id in top_ids}
        if query.preferred_skills
        else load_skills(session, top_ids)
    )
    hydrated = hydrate(session, top_ids, query.environment)

    jobs = tuple(
        RetrievedJob(
            job_id=s.job_id,
            environment=s.environment,
            score=s.breakdown.score,
            breakdown=s.breakdown,
            job_skills=tuple(sorted(top_skills.get(s.job_id, frozenset()))),
            matched_preferred_skills=s.matched_skills,
            missing_preferred_skills=s.missing_skills,
            title=(row := hydrated.get(s.job_id, {})).get("title", "") or "",
            company=row.get("company"),
            location=row.get("location"),
            url=row.get("url"),
            posted_at=row.get("posted_at"),
            source=row.get("source"),
            evidence=_build_evidence(
                query, row.get("title") or "", row.get("description"), s.matched_skills
            ),
        )
        for s in top
    )

    return RetrievalResult(
        query=query,
        jobs=jobs,
        total_candidates=len(candidates),
        scored_on=resolved_today,
    )


__all__ = ["retrieve", "count_candidates"]
