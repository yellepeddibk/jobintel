"""Location matching for the dashboard's "Location contains" filter.

Typing US, USA or United States switches the filter to `us_location_clause`, which
matches a US location by any of:

- a whole word "us", "usa" or "united states", after lowercasing, turning separators
  into spaces and dropping dots, so "Remote - US", "USA Only" and "U.S.A." match but
  "Australia", "Russia" and "Brussels" do not;
- a trailing ", XX" where XX is one of the 50 state codes or DC, compared
  case-sensitively, so "Austin, TX" matches but "London, UK" and "Toronto, ON" do not;
- one of a fixed list of major US city names.

Everything is plain SQL (lower, replace, trim, substr, length, LIKE) that behaves the
same on SQLite and PostgreSQL.

Known limits: a country code that is also a state code still matches, for example
"Berlin, DE" (Delaware), "Toronto, CA" (California) or "Pune, IN" (Indiana). Regions
such as "Americas" or "North America" do not match, because they are not only the US,
and neither does a US city outside the list without a state code.
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

# Major US cities (tech hubs and common job markets), matched as substrings.
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

# Characters treated as word separators. Dots are removed rather than replaced, so
# "U.S." becomes "us".
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
