"""Deterministic job retrieval.

Filters in SQL, ranks in Python, and returns a fully reconstructible score for every
result. No embeddings and no model: this layer must be understandable, testable and
benchmarkable on its own before anything probabilistic is built on top of it.
"""

from jobintel.retrieval.contracts import (
    ComponentScore,
    Evidence,
    RetrievalQuery,
    RetrievalResult,
    RetrievedJob,
    ScoreBreakdown,
)
from jobintel.retrieval.scoring import DEFAULT_WEIGHTS
from jobintel.retrieval.service import retrieve

__all__ = [
    "DEFAULT_WEIGHTS",
    "ComponentScore",
    "Evidence",
    "RetrievalQuery",
    "RetrievalResult",
    "RetrievedJob",
    "ScoreBreakdown",
    "retrieve",
]
