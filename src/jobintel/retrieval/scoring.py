"""Pure scoring functions: no database, no clock (`today` is always passed in), no I/O."""

from __future__ import annotations

import re
from datetime import date

from jobintel.retrieval.contracts import (
    COMPONENT_ORDER,
    LOCATION,
    RECENCY,
    SKILLS,
    TEXT,
    ComponentScore,
    ScoreBreakdown,
)

DEFAULT_WEIGHTS: dict[str, float] = {
    TEXT: 0.40,
    SKILLS: 0.25,
    LOCATION: 0.20,
    RECENCY: 0.15,
}

# Title hits matter most; descriptions mention everything.
FIELD_WEIGHTS: dict[str, float] = {"title": 1.0, "company": 0.5, "description": 0.25}

SCORE_PRECISION = 6  # rounding keeps ordering stable across platforms

_TOKEN_SPLIT = re.compile(r"[^a-z0-9+#.]+")

REMOTE_TOKENS = ("remote", "anywhere", "worldwide")


def tokenize(text: str | None) -> tuple[str, ...]:
    """Lowercased, de-duplicated tokens in order, so a repeated word counts once."""
    if not text:
        return ()
    seen: dict[str, None] = {}
    for token in _TOKEN_SPLIT.split(text.lower()):
        if token:
            seen.setdefault(token, None)
    return tuple(seen)


def score_text(
    tokens: tuple[str, ...],
    title: str | None,
    company: str | None,
    description: str | None,
) -> tuple[float, float, bool]:
    """Query-token coverage, each token scored by its best field: (raw, normalized, present).

    Substring matching, so "sql" matches "PostgreSQL" but "art" also matches "Smart".
    """
    if not tokens:
        return 0.0, 0.0, False

    haystacks = {
        "title": (title or "").lower(),
        "company": (company or "").lower(),
        "description": (description or "").lower(),
    }
    present = any(haystacks.values())

    raw = 0.0
    for token in tokens:
        best = 0.0
        for field_name, weight in FIELD_WEIGHTS.items():
            if token and token in haystacks[field_name]:
                best = max(best, weight)
        raw += best
    return raw, raw / len(tokens), present


def score_skills(
    preferred: tuple[str, ...],
    job_skills: frozenset[str],
    has_skill_data: bool,
) -> tuple[float, float, bool, tuple[str, ...], tuple[str, ...]]:
    """Fraction of preferred skills the job has: (raw, normalized, present, matched, missing).

    `present` is whether the job has any skills at all.
    """
    if not preferred:
        return 0.0, 0.0, has_skill_data, (), ()
    matched = tuple(sorted(s for s in preferred if s in job_skills))
    missing = tuple(sorted(s for s in preferred if s not in job_skills))
    raw = float(len(matched))
    return raw, raw / len(preferred), has_skill_data, matched, missing


def is_remote(location: str | None) -> bool:
    """Whether a location string reads as remote."""
    if not location:
        return False
    lowered = location.lower()
    return any(token in lowered for token in REMOTE_TOKENS)


def score_location(
    preferred_location: str | None, job_location: str | None
) -> tuple[float, float, bool]:
    """Exact match 1.0, substring either way 0.8, else 0.0: (raw, normalized, present)."""
    if not preferred_location:
        return 0.0, 0.0, job_location is not None
    if job_location is None:
        return 0.0, 0.0, False

    want = preferred_location.strip().lower()
    have = job_location.strip().lower()
    if want == have:
        raw = 1.0
    elif want in have or have in want:
        raw = 0.8
    else:
        raw = 0.0
    return raw, raw, True


def score_recency(
    posted_at: date | None, today: date, horizon_days: int
) -> tuple[float, float, bool]:
    """Linear decay: 1.0 today, 0.0 at `horizon_days` or older: (raw, normalized, present).

    Future dates count as today, so they never outscore today.
    """
    if posted_at is None:
        return 0.0, 0.0, False
    age_days = max(0, (today - posted_at).days)
    raw = float(max(0, horizon_days - age_days))
    return raw, raw / horizon_days, True


def combine(
    parts: dict[str, tuple[float, float, bool]],
    active: tuple[str, ...],
    weights: dict[str, float] | None = None,
) -> ScoreBreakdown:
    """Weighted score normalized by the weight of the components the query activated.

    Missing job data scores 0 but keeps its weight, so less data never helps. With no
    active components the score is 0.0 and ordering falls to the tie-break.
    """
    weights = weights or DEFAULT_WEIGHTS
    components: list[ComponentScore] = []
    active_weight = 0.0
    weighted_sum = 0.0

    for name in COMPONENT_ORDER:
        raw, normalized, present = parts.get(name, (0.0, 0.0, False))
        is_active = name in active
        weight = weights[name]
        contribution = weight * normalized if is_active else 0.0
        if is_active:
            active_weight += weight
            weighted_sum += contribution
        components.append(
            ComponentScore(
                name=name,
                active=is_active,
                present=present,
                raw=raw,
                normalized=normalized if is_active else 0.0,
                weight=weight,
                contribution=contribution,
            )
        )

    score = (
        round(weighted_sum / active_weight, SCORE_PRECISION) if active_weight > 0 else 0.0
    )
    return ScoreBreakdown(
        components=tuple(components),
        active_weight=round(active_weight, SCORE_PRECISION),
        weighted_sum=round(weighted_sum, SCORE_PRECISION),
        score=score,
    )


def sort_key(
    score: float, posted_at: date | None, job_hash: str | None, job_id: int
) -> tuple[float, float, str, int]:
    """Total order: score desc, posted_at desc (nulls last), content hash, then id.

    The hash precedes the id so insertion order cannot change the ranking.
    """
    posted_key = -float(posted_at.toordinal()) if posted_at is not None else float("inf")
    return (-score, posted_key, job_hash or "", job_id)
