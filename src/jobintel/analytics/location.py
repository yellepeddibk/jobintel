"""US location matching for the dashboard's location filter.

Matches the whole word us/usa/united states, a trailing ", XX" state code
(case-sensitive), or a known US city. Plain SQL, same on SQLite and Postgres.
Known limit: country codes that equal state codes ("Berlin, DE") still match.
"""

from __future__ import annotations

from sqlalchemy import String, and_, func, literal, or_
from sqlalchemy.sql.elements import ColumnElement

US_QUERIES = frozenset({"us", "usa", "united states"})

US_WORDS = ("us", "usa", "united states")

US_STATE_CODES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
)  # fmt: skip

# Matched as substrings.
US_CITIES = (
    "New York",
    "Los Angeles",
    "San Francisco",
    "Bay Area",
    "Chicago",
    "Boston",
    "Seattle",
    "Austin",
    "Denver",
    "Portland",
    "Miami",
    "Atlanta",
    "Dallas",
    "Houston",
    "Phoenix",
    "Philadelphia",
    "San Diego",
    "Washington",
)

# Word separators. Dots are removed instead, so "U.S." becomes "us".
_SEPARATORS = (",", "(", ")", "/", "-", "|", ";", ":")


def is_us_query(text: str) -> bool:
    """Whether the filter text asks for US locations rather than a substring."""
    return text.strip().lower() in US_QUERIES


def _padded_words(column: ColumnElement) -> ColumnElement:
    """' ' + lowercased location with separators as spaces and dots removed + ' '."""
    expr = func.replace(func.lower(column), ".", "", type_=String)
    for sep in _SEPARATORS:
        expr = func.replace(expr, sep, " ", type_=String)
    return literal(" ", String) + expr + literal(" ", String)


def us_location_clause(column: ColumnElement) -> ColumnElement[bool]:
    """A filter matching US locations in `column`. NULL locations never match."""
    words = _padded_words(column)
    trimmed = func.trim(column, type_=String)
    state_code = and_(
        trimmed.like("%, __"),
        # Equality is case-sensitive on both dialects, unlike SQLite's LIKE.
        func.substr(trimmed, func.length(trimmed) - 1, 2).in_(US_STATE_CODES),
    )
    return or_(
        *(words.like(f"% {word} %") for word in US_WORDS),
        state_code,
        *(column.ilike(f"%{city}%") for city in US_CITIES),
    )
