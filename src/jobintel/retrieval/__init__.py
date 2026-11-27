"""Deterministic job retrieval: filter in SQL, rank in Python, explain every score."""

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
