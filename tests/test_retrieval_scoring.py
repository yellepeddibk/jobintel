"""Pure scoring tests. No database, no clock, no Streamlit.

The point of a deterministic retrieval layer is that its ranking can be reasoned
about in isolation, so these tests never construct a session. Every date is injected.
"""

from __future__ import annotations

from datetime import date

import pytest

from jobintel.retrieval.contracts import (
    LOCATION,
    RECENCY,
    SKILLS,
    TEXT,
    RetrievalQuery,
)
from jobintel.retrieval.scoring import (
    DEFAULT_WEIGHTS,
    combine,
    is_remote,
    score_location,
    score_recency,
    score_skills,
    score_text,
    sort_key,
    tokenize,
)

TODAY = date(2026, 9, 7)


def build(parts, active):
    return combine(parts, active)


# ------------------------------------------------------------------ tokenize


def test_tokenize_lowercases_splits_and_deduplicates_in_order():
    assert tokenize("Senior Python Engineer, Python!") == ("senior", "python", "engineer")


def test_tokenize_handles_none_and_blank():
    assert tokenize(None) == ()
    assert tokenize("   ") == ()


def test_tokenize_keeps_technology_punctuation():
    assert tokenize("C++ and .NET") == ("c++", "and", ".net")


# ------------------------------------------------------------------ text


def test_text_perfect_title_match_normalizes_to_one():
    raw, normalized, present = score_text(
        tokenize("data engineer"), "Data Engineer", "Acme", "irrelevant"
    )
    assert raw == 2.0
    assert normalized == 1.0
    assert present is True


def test_text_field_precedence_title_beats_company_beats_description():
    _, title_norm, _ = score_text(("acme",), "Acme", None, None)
    _, company_norm, _ = score_text(("acme",), "Engineer", "Acme", None)
    _, description_norm, _ = score_text(("acme",), "Engineer", None, "we are Acme")
    assert title_norm == 1.0
    assert company_norm == 0.5
    assert description_norm == 0.25
    assert title_norm > company_norm > description_norm


def test_text_counts_each_token_once_at_its_best_field():
    """A token in both title and description is credited once, at the title."""
    raw, normalized, _ = score_text(("python",), "Python Engineer", None, "python python")
    assert raw == 1.0
    assert normalized == 1.0


def test_text_partial_coverage():
    _, normalized, _ = score_text(tokenize("data engineer"), "Data Scientist", None, None)
    assert normalized == 0.5


def test_text_absent_when_job_has_no_text_fields():
    _, normalized, present = score_text(("python",), "", None, None)
    assert normalized == 0.0
    assert present is False


# ------------------------------------------------------------------ skills


def test_skills_fraction_matched():
    raw, normalized, present, matched, missing = score_skills(
        ("python", "sql"), frozenset({"python"}), True
    )
    assert raw == 1.0
    assert normalized == 0.5
    assert present is True
    assert matched == ("python",)
    assert missing == ("sql",)


def test_skills_absent_when_job_has_no_skill_rows():
    _, normalized, present, matched, missing = score_skills(
        ("python",), frozenset(), False
    )
    assert normalized == 0.0
    assert present is False
    assert matched == ()
    assert missing == ("python",)


def test_skills_present_but_unmatched_scores_zero():
    """Having skills, just not the requested ones, is different from having none."""
    _, normalized, present, matched, _ = score_skills(
        ("python",), frozenset({"docker"}), True
    )
    assert normalized == 0.0
    assert present is True
    assert matched == ()


def test_skills_matched_and_missing_are_sorted_deterministically():
    _, _, _, matched, missing = score_skills(
        ("sql", "python", "docker"), frozenset({"sql", "python"}), True
    )
    assert matched == ("python", "sql")
    assert missing == ("docker",)


# ------------------------------------------------------------------ location


@pytest.mark.parametrize(
    ("preferred", "job_location", "expected"),
    [
        ("Berlin", "Berlin", 1.0),
        ("berlin", "BERLIN", 1.0),
        ("Berlin", "Berlin, Germany", 0.8),
        ("Berlin, Germany", "Berlin", 0.8),
        ("Berlin", "Munich", 0.0),
        ("remote", "Remote", 1.0),
        ("remote", "Remote, USA", 0.8),
    ],
)
def test_location_ladder(preferred, job_location, expected):
    _, normalized, present = score_location(preferred, job_location)
    assert normalized == expected
    assert present is True


def test_location_absent_when_job_has_none():
    _, normalized, present = score_location("Berlin", None)
    assert normalized == 0.0
    assert present is False


@pytest.mark.parametrize(
    ("location", "expected"),
    [("Remote", True), ("Remote, USA", True), ("Anywhere", True),
     ("Worldwide", True), ("Berlin, Germany", False), (None, False)],
)
def test_is_remote(location, expected):
    assert is_remote(location) is expected


# ------------------------------------------------------------------ recency


def test_recency_today_scores_one():
    _, normalized, present = score_recency(TODAY, TODAY, 180)
    assert normalized == 1.0
    assert present is True


def test_recency_decays_linearly():
    _, normalized, _ = score_recency(date(2026, 6, 9), TODAY, 180)  # 90 days old
    assert normalized == pytest.approx(0.5)


def test_recency_at_and_beyond_horizon_is_zero():
    _, at_horizon, _ = score_recency(date(2026, 3, 11), TODAY, 180)
    _, beyond, _ = score_recency(date(2020, 1, 1), TODAY, 180)
    assert at_horizon == pytest.approx(0.0, abs=0.01)
    assert beyond == 0.0


def test_recency_future_date_treated_as_today():
    _, normalized, _ = score_recency(date(2027, 1, 1), TODAY, 180)
    assert normalized == 1.0


def test_recency_absent_when_posted_at_is_null():
    _, normalized, present = score_recency(None, TODAY, 180)
    assert normalized == 0.0
    assert present is False


# ------------------------------------------- combine and active normalization


def test_text_only_query_can_reach_exactly_one():
    """The reason the denominator is query-shaped rather than the full weight sum."""
    breakdown = build({TEXT: (2.0, 1.0, True)}, (TEXT,))
    assert breakdown.active_weight == 0.4
    assert breakdown.score == 1.0


def test_text_plus_preferred_skills_uses_active_weight_of_065():
    breakdown = build(
        {TEXT: (1.0, 1.0, True), SKILLS: (1.0, 1.0, True)}, (TEXT, SKILLS)
    )
    assert breakdown.active_weight == 0.65
    assert breakdown.score == 1.0


def test_all_four_active_uses_active_weight_of_one():
    breakdown = build(
        {
            TEXT: (1.0, 1.0, True),
            SKILLS: (1.0, 1.0, True),
            LOCATION: (1.0, 1.0, True),
            RECENCY: (180.0, 1.0, True),
        },
        (TEXT, SKILLS, LOCATION, RECENCY),
    )
    assert breakdown.active_weight == 1.0
    assert breakdown.score == 1.0


def test_active_component_with_missing_job_data_stays_in_the_denominator():
    """Missing data must score zero, not shrink the denominator and inflate the rest."""
    breakdown = build(
        {TEXT: (1.0, 1.0, True), LOCATION: (0.0, 0.0, False)}, (TEXT, LOCATION)
    )
    assert breakdown.active_weight == 0.6
    assert breakdown.component(LOCATION).present is False
    assert breakdown.component(LOCATION).contribution == 0.0
    assert breakdown.score == pytest.approx(0.4 / 0.6)


def test_inactive_component_is_excluded_from_the_denominator():
    breakdown = build({TEXT: (1.0, 1.0, True), LOCATION: (1.0, 1.0, True)}, (TEXT,))
    assert breakdown.active_weight == 0.4
    location = breakdown.component(LOCATION)
    assert location.active is False
    assert location.contribution == 0.0
    assert breakdown.score == 1.0


def test_empty_signal_query_scores_zero_without_dividing_by_zero():
    breakdown = build({}, ())
    assert breakdown.active_weight == 0.0
    assert breakdown.weighted_sum == 0.0
    assert breakdown.score == 0.0


def test_breakdown_arithmetic_is_reconstructible():
    breakdown = build(
        {TEXT: (1.0, 0.5, True), SKILLS: (1.0, 0.5, True), RECENCY: (90.0, 0.5, True)},
        (TEXT, SKILLS, RECENCY),
    )
    active = [c for c in breakdown.components if c.active]
    assert sum(c.contribution for c in active) == pytest.approx(breakdown.weighted_sum)
    assert sum(c.weight for c in active) == pytest.approx(breakdown.active_weight)
    assert breakdown.score == pytest.approx(breakdown.weighted_sum / breakdown.active_weight)


def test_breakdown_always_reports_all_four_components_in_fixed_order():
    breakdown = build({TEXT: (1.0, 1.0, True)}, (TEXT,))
    assert [c.name for c in breakdown.components] == [TEXT, SKILLS, LOCATION, RECENCY]


def test_scoring_is_repeatable():
    parts = {TEXT: (1.0, 0.5, True), RECENCY: (90.0, 0.5, True)}
    first = build(parts, (TEXT, RECENCY))
    second = build(parts, (TEXT, RECENCY))
    assert first == second


def test_default_weights_are_the_agreed_values():
    assert DEFAULT_WEIGHTS == {"text": 0.40, "skills": 0.25, "location": 0.20, "recency": 0.15}


# ------------------------------------------------------------------ sort key


def test_sort_key_orders_by_score_then_recency_then_hash_then_id():
    keys = [
        sort_key(0.5, date(2026, 1, 1), "b", 1),
        sort_key(0.9, date(2020, 1, 1), "z", 99),
        sort_key(0.5, date(2026, 6, 1), "a", 50),
    ]
    assert sorted(keys) == [keys[1], keys[2], keys[0]]


def test_sort_key_puts_null_posted_at_last_within_equal_scores():
    dated = sort_key(0.5, date(2020, 1, 1), "a", 1)
    undated = sort_key(0.5, None, "a", 2)
    assert sorted([undated, dated]) == [dated, undated]


def test_sort_key_uses_hash_before_id_so_ordering_survives_reinsertion():
    """job_id is a surrogate; the hash is derived from content and is stable."""
    high_id_stable_hash = sort_key(0.5, date(2026, 1, 1), "aaa", 999)
    low_id_later_hash = sort_key(0.5, date(2026, 1, 1), "bbb", 1)
    assert sorted([low_id_later_hash, high_id_stable_hash]) == [
        high_id_stable_hash,
        low_id_later_hash,
    ]


def test_sort_key_handles_null_hash_without_crashing():
    assert sort_key(0.5, date(2026, 1, 1), None, 7) == (-0.5, -date(2026, 1, 1).toordinal(), "", 7)


# ------------------------------------------------- query validation contract


def test_query_normalizes_skills_case_and_deduplicates_deterministically():
    query = RetrievalQuery(
        environment="production", preferred_skills=("SQL", "python", " Python ", "sql")
    )
    assert query.preferred_skills == ("python", "sql")


def test_query_rejects_unknown_skills_with_a_clear_error():
    with pytest.raises(ValueError, match="unknown preferred_skills"):
        RetrievalQuery(environment="production", preferred_skills=("kubernetes",))
    with pytest.raises(ValueError, match="unknown required_skills"):
        RetrievalQuery(environment="production", required_skills=("terraform",))


def test_query_normalizes_blank_text_and_locations_to_none():
    query = RetrievalQuery(
        environment="production", text="   ", preferred_location="  ", required_location=""
    )
    assert query.text is None
    assert query.preferred_location is None
    assert query.required_location is None


def test_query_normalizes_and_deduplicates_sources_without_lowercasing():
    query = RetrievalQuery(
        environment="production", sources=(" remotive ", "remotive", "arbeitnow")
    )
    assert query.sources == ("arbeitnow", "remotive")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"environment": "  "},
        {"environment": "production", "limit": 0},
        {"environment": "production", "limit": -1},
        {"environment": "production", "recency_horizon_days": 0},
        {"environment": "production", "posted_within_days": 0},
    ],
)
def test_query_rejects_malformed_values(kwargs):
    with pytest.raises(ValueError):
        RetrievalQuery(**kwargs)


def test_active_components_reflect_only_what_the_query_asked_for():
    assert RetrievalQuery(environment="p").active_components == ()
    assert RetrievalQuery(environment="p", text="x").active_components == (TEXT,)
    assert RetrievalQuery(
        environment="p", required_skills=("python",)
    ).active_components == (), "required skills are a filter, not a ranking signal"
    assert RetrievalQuery(
        environment="p", text="x", preferred_skills=("python",),
        preferred_location="Berlin", prefer_recent=True,
    ).active_components == (TEXT, SKILLS, LOCATION, RECENCY)


def test_query_round_trips_through_to_dict():
    query = RetrievalQuery(environment="production", text="data", preferred_skills=("python",))
    payload = query.to_dict()
    assert payload["environment"] == "production"
    assert payload["preferred_skills"] == ["python"]
