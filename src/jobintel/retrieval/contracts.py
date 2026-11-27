"""Immutable, serializable query and result types for retrieval.

`preferred_*` fields only affect the score; `required_*`, `remote_only`, `sources` and
`posted_within_days` only filter. No field does both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from jobintel.etl.skills import known_skills

# In ScoreBreakdown order.
TEXT = "text"
SKILLS = "skills"
LOCATION = "location"
RECENCY = "recency"
COMPONENT_ORDER = (TEXT, SKILLS, LOCATION, RECENCY)


def _clean_optional_text(value: str | None) -> str | None:
    """Strip, and treat a blank-only string as absent."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_skills(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    """Lowercase, de-duplicate and sort; raise on skills extraction can never produce."""
    cleaned = {v.strip().lower() for v in values if v and v.strip()}
    vocabulary = known_skills()
    unknown = sorted(cleaned - vocabulary)
    if unknown:
        raise ValueError(
            f"unknown {label}: {unknown}. Known skills are {sorted(vocabulary)}. "
            "Skill extraction defines this vocabulary; retrieval cannot match a "
            "name it can never have stored."
        )
    return tuple(sorted(cleaned))


def _normalize_sources(values: tuple[str, ...]) -> tuple[str, ...]:
    """Strip, de-duplicate and sort. Not lowercased: names are stored verbatim."""
    return tuple(sorted({v.strip() for v in values if v and v.strip()}))


@dataclass(frozen=True)
class RetrievalQuery:
    """What a caller asks for. Validated and normalized on construction."""

    environment: str

    # Ranking preferences. Each activates one score component.
    text: str | None = None
    preferred_skills: tuple[str, ...] = ()
    preferred_location: str | None = None
    prefer_recent: bool = False
    recency_horizon_days: int = 180

    # Hard filters. None of these affect the score.
    required_skills: tuple[str, ...] = ()
    required_location: str | None = None
    remote_only: bool = False
    sources: tuple[str, ...] = ()
    posted_within_days: int | None = None

    limit: int = 20

    def __post_init__(self) -> None:
        environment = (self.environment or "").strip()
        if not environment:
            raise ValueError("environment is required and must not be blank")
        if self.limit <= 0:
            raise ValueError(f"limit must be > 0, got {self.limit}")
        if self.recency_horizon_days <= 0:
            raise ValueError(
                f"recency_horizon_days must be > 0, got {self.recency_horizon_days}"
            )
        if self.posted_within_days is not None and self.posted_within_days <= 0:
            raise ValueError(
                f"posted_within_days must be > 0 when supplied, got {self.posted_within_days}"
            )

        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "text", _clean_optional_text(self.text))
        object.__setattr__(
            self, "preferred_location", _clean_optional_text(self.preferred_location)
        )
        object.__setattr__(
            self, "required_location", _clean_optional_text(self.required_location)
        )
        object.__setattr__(
            self,
            "preferred_skills",
            _normalize_skills(tuple(self.preferred_skills), "preferred_skills"),
        )
        object.__setattr__(
            self,
            "required_skills",
            _normalize_skills(tuple(self.required_skills), "required_skills"),
        )
        object.__setattr__(self, "sources", _normalize_sources(tuple(self.sources)))

    # Active because the query asked, never because a job has the data.
    @property
    def active_components(self) -> tuple[str, ...]:
        active = []
        if self.text:
            active.append(TEXT)
        if self.preferred_skills:
            active.append(SKILLS)
        if self.preferred_location:
            active.append(LOCATION)
        if self.prefer_recent:
            active.append(RECENCY)
        return tuple(active)

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "text": self.text,
            "preferred_skills": list(self.preferred_skills),
            "preferred_location": self.preferred_location,
            "prefer_recent": self.prefer_recent,
            "recency_horizon_days": self.recency_horizon_days,
            "required_skills": list(self.required_skills),
            "required_location": self.required_location,
            "remote_only": self.remote_only,
            "sources": list(self.sources),
            "posted_within_days": self.posted_within_days,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class ComponentScore:
    """One score component and its contribution.

    `active`: the query asked for it. `present`: the job had the data (meaningful
    only when active, since inactive data is never loaded).
    """

    name: str
    active: bool
    present: bool
    raw: float
    normalized: float
    weight: float
    contribution: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "active": self.active,
            "present": self.present,
            "raw": self.raw,
            "normalized": self.normalized,
            "weight": self.weight,
            "contribution": self.contribution,
        }


@dataclass(frozen=True)
class ScoreBreakdown:
    """score == round(weighted_sum / active_weight, 6) when active_weight > 0, else 0.0."""

    components: tuple[ComponentScore, ...]
    active_weight: float
    weighted_sum: float
    score: float

    def component(self, name: str) -> ComponentScore:
        for c in self.components:
            if c.name == name:
                return c
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "components": [c.to_dict() for c in self.components],
            "active_weight": self.active_weight,
            "weighted_sum": self.weighted_sum,
            "score": self.score,
        }


@dataclass(frozen=True)
class Evidence:
    """A verbatim substring of a stored field, never rewritten."""

    field: str
    excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "excerpt": self.excerpt}


@dataclass(frozen=True)
class RetrievedJob:
    """One ranked job with its score breakdown, skills and evidence."""

    job_id: int
    environment: str
    score: float
    breakdown: ScoreBreakdown

    job_skills: tuple[str, ...] = ()
    matched_preferred_skills: tuple[str, ...] = ()
    missing_preferred_skills: tuple[str, ...] = ()

    title: str = ""
    company: str | None = None
    location: str | None = None
    url: str | None = None
    posted_at: date | None = None
    source: str | None = None
    evidence: tuple[Evidence, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "environment": self.environment,
            "score": self.score,
            "breakdown": self.breakdown.to_dict(),
            "job_skills": list(self.job_skills),
            "matched_preferred_skills": list(self.matched_preferred_skills),
            "missing_preferred_skills": list(self.missing_preferred_skills),
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "source": self.source,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass(frozen=True)
class RetrievalResult:
    """Ordered results; `total_candidates` counts every row that passed the filters."""

    query: RetrievalQuery
    jobs: tuple[RetrievedJob, ...] = field(default_factory=tuple)
    total_candidates: int = 0
    scored_on: date | None = None

    @property
    def returned(self) -> int:
        return len(self.jobs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.to_dict(),
            "total_candidates": self.total_candidates,
            "returned": self.returned,
            "scored_on": self.scored_on.isoformat() if self.scored_on else None,
            "jobs": [j.to_dict() for j in self.jobs],
        }
