"""Retrieval orchestration: score every candidate from a light row, then hydrate the top-k.

Candidate skills are loaded only when preferred_skills ranks something.
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

MAX_EVIDENCE_SPANS = 4
EVIDENCE_WINDOW = 240


@dataclass(frozen=True)
class _Scored:
    """A scored candidate, without its description."""

    job_id: int
    environment: str
    breakdown: ScoreBreakdown
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    posted_at: date | None
    job_hash: str | None


def _excerpt(text: str, term: str) -> str | None:
    """Verbatim window of `text` around the first occurrence of `term`, or None."""
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
    """Title plus description windows for matched skills, then query tokens."""
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
    """Rank jobs for one environment. Pass `today` for a reproducible run."""
    resolved_today = today if today is not None else date.today()
    active = query.active_components
    tokens = tokenize(query.text)

    candidates = list(iter_candidates(session, query, today=resolved_today))

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

    # Reuse skills already loaded for scoring; otherwise load them for the top-k only.
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
