"""Deterministic scoring. Pure functions: no database, no clock, no I/O.

Every function here takes plain values and returns plain values, so the ranking can
be tested, benchmarked and later compared against an improved version without a
database or a Streamlit runtime anywhere near it.

`today` is always a parameter. Nothing in this module calls `date.today()`: a scorer
that reads the clock cannot be tested for the boundary cases that matter, and a
benchmark whose results change overnight is not a benchmark.
"""

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

# Where a query token is found, and what that is worth. A title hit is the strongest
# signal a job board gives you; a description hit is the weakest because descriptions
# mention everything.
FIELD_WEIGHTS: dict[str, float] = {"title": 1.0, "company": 0.5, "description": 0.25}

# Scores are rounded before comparison so that ordering does not depend on float
# noise differing across platforms.
SCORE_PRECISION = 6

_TOKEN_SPLIT = re.compile(r"[^a-z0-9+#.]+")

# Location strings that mean "not tied to a place". Deliberately small and explicit
# rather than the dashboard's city heuristic, which is presentation-layer guesswork.
REMOTE_TOKENS = ("remote", "anywhere", "worldwide")


def tokenize(text: str | None) -> tuple[str, ...]:
    """Lowercase, split on non-alphanumerics, drop blanks, deduplicate in order.

    Order-preserving deduplication keeps the token list stable for the same input
    while making a repeated word count once, so "python python python" does not
    outscore "python".
    """
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
    """Token coverage across title, company and description.

    Each query token scores the best field it appears in, so a token in the title is
    not also credited for the description. Normalizing by token count makes this
    coverage rather than a raw count, which stops a long query from outscoring a
    short one purely on length.

    Matching is plain substring containment against the lowercased field, with no
    word boundaries. That is forgiving in the useful direction, so "engineer" matches
    "Engineering Manager" and "sql" matches "PostgreSQL", but it also matches inside
    unrelated words: "art" matches "Smart Start". Short tokens are therefore the
    weakest signal here. Word-boundary or stemmed matching is a deliberate candidate
    for a later version to be compared against this one.

    Returns (raw, normalized, present).
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
    """Fraction of preferred skills the job has.

    `has_skill_data` is whether the job has any skill rows at all, which is what
    `present` reports. A job with skills but none of the preferred ones is present
    and scores 0; a job with no extracted skills is simply absent. Both keep the
    weight in the denominator.

    Returns (raw, normalized, present, matched, missing).
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
    """A short deterministic ladder, first match wins.

    Exact match 1.0, substring either way 0.8, otherwise 0.0. Substring is checked in
    both directions so "Berlin" matches "Berlin, Germany" and vice versa.

    Returns (raw, normalized, present).
    """
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
    """Linear decay from `today` back to `horizon_days`.

    A job posted today scores 1.0, one at the horizon scores 0.0, and anything older
    stays at 0.0 rather than going negative. A future date is treated as zero days
    old rather than rejected, because a source sending tomorrow's date should not
    score better than one sending today's.

    Returns (raw, normalized, present).
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
    """Weight the active components and normalize by the weight the query activated.

    Only components the query asked for enter the denominator, which is what lets a
    text-only query reach exactly 1.0 instead of being capped by weights it never
    used. An active component whose job data is missing contributes 0 but keeps its
    weight in the denominator, so a job is never rewarded for having less data.

    With no active components the denominator is 0; score is 0.0 and ordering falls
    to the tie-break, which still returns a useful newest-first list.
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
    """A total order that does not depend on how rows reached us.

    Descending score, then descending posted_at with nulls last, then the job's
    stable content hash, then the id. The hash matters: `job_id` is an autoincrement
    surrogate, so ordering by it alone would make "same corpus, different insertion
    order" produce a different ranking and quietly invalidate any benchmark. The hash
    is derived from the job's own content, so equivalent corpora order identically.

    A null hash sorts as the empty string rather than raising: it is not reachable
    through the pipeline, but ordering must never crash on it.
    """
    posted_key = -float(posted_at.toordinal()) if posted_at is not None else float("inf")
    return (-score, posted_key, job_hash or "", job_id)
