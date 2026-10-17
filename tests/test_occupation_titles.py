from __future__ import annotations

import pytest

from job_intake.occupation_titles import title_weight_matches


@pytest.mark.parametrize(("title", "role", "phrase"), [
    ("Manager, Analytics", "analytics-leadership", "analytics manager"),
    ("Manager of Analytics", "analytics-leadership", "analytics manager"),
    ("Manager: Commercial Analytics", "analytics-leadership", "analytics manager"),
    ("Analytics & Insights Manager", "analytics-leadership", "analytics manager"),
    ("Head of Commercial Analytics & Insights", "analytics-leadership", "head of analytics"),
    ("Senior Manager, Data Science", "data-science-management", "data science manager"),
    ("Senior Manager, Product Data Science", "data-science-management", "data science manager"),
    ("Senior Manager, Product Data Science & Analytics", "data-science-management",
     "data science manager"),
    ("Data Science & Analytics Manager", "data-science-management", "data science manager"),
    ("Manager Business Performance Analytics", "business-analytics", "business analytics manager"),
    ("Head of Business Performance Analytics", "business-analytics", "head of business analytics"),
    ("Business Performance Analytics Lead", "business-analytics", "business analytics lead"),
    ("Product Analytics Chapter Lead", "analytics-leadership", "product analytics lead"),
    ("Product Analytics Team Lead", "analytics-leadership", "product analytics lead"),
    ("Manager of Product", "product-management", "product manager"),
    ("Product Head", "product-management", "head of product"),
])
def test_reordered_titles_reuse_only_the_configured_phrase_and_weight(title, role, phrase):
    assert title_weight_matches(title, {phrase: 7.25}, role) == [(phrase, 7.25)]


@pytest.mark.parametrize(("title", "role", "weights"), [
    ("Product Manager, Analytics", "analytics-leadership", {"analytics manager": 8}),
    ("Product Manager, Data Science", "data-science-management", {"data science manager": 8}),
    ("Engineering Manager - Commercial Analytics", "analytics-leadership",
     {"analytics manager": 8}),
    ("Marketing Manager, Analytics", "analytics-leadership", {"analytics manager": 8}),
    ("Manager, Analytics Engineering", "analytics-leadership", {"analytics manager": 8}),
    ("Manager, Business Analytics Engineering", "business-analytics",
     {"business analytics manager": 8, "business analytics": 2}),
    ("Business Analytics Platform Manager", "business-analytics",
     {"business analytics manager": 8}),
    ("Head of Data Products", "analytics-leadership", {"head of data": 4}),
    ("Head of Data Engineering", "analytics-leadership", {"head of data": 4}),
    ("Head of Data Science", "analytics-leadership", {"head of data": 4}),
    ("Head of Product Design", "product-management", {"head of product": 3}),
    ("Product Lead Designer", "product-management", {"product lead": 3}),
    ("Manager of Data Science Production Engineering", "data-science-management",
     {"data science manager": 8}),
    ("Director of Analytics", "analytics-leadership", {"analytics manager": 8}),
    ("Head of Analytics", "analytics-leadership", {"analytics manager": 8}),
    ("Commercial Analytics Lead", "analytics-leadership", {"product analytics lead": 8}),
    ("Manager of Analytics", "analytics-leadership", {"business analytics manager": 8}),
    ("Business Analytics Manager", "business-analytics",
     {"business performance analytics manager": 8}),
    ("Manager of Analytics", "data-science-management", {"data science manager": 8}),
    ("Manager of Data Science", "analytics-leadership", {"analytics manager": 8}),
    ("Director Product Analytics", "product-management", {"head of product": 8}),
])
def test_titles_do_not_borrow_other_occupations_domains_or_leadership_levels(title, role, weights):
    assert title_weight_matches(title, weights, role) == []


@pytest.mark.parametrize("role", [None, "custom-role", "other"])
def test_unrecognized_roles_keep_literal_signals_without_new_syntax_aliases(role):
    weights = {"analytics manager": 8, "analytics": 2}
    assert title_weight_matches("Manager, Analytics", weights, role) == [("analytics", 2)]
    assert title_weight_matches("Analytics Manager", weights, role) == list(weights.items())


def test_all_candidates_are_returned_once_in_configuration_order_for_scorer_aggregation():
    weights = {"product analytics": 2, "product analytics lead": 8, "analytics manager": 8}
    assert title_weight_matches(
        "Product Analytics Chapter Lead / Product Analytics Lead", weights, "analytics-leadership",
    ) == [("product analytics", 2), ("product analytics lead", 8)]


def test_no_weight_is_invented_when_the_equivalent_phrase_is_not_configured():
    assert title_weight_matches("Manager, Analytics", {"head of analytics": 8},
                                "analytics-leadership") == []
    assert title_weight_matches("Manager, Analytics", {}, "analytics-leadership") == []


def test_literal_occurrences_are_guarded_individually_in_hybrid_titles():
    assert title_weight_matches(
        "Head of Data Products / Head of Data", {"head of data": 4}, "analytics-leadership",
    ) == [("head of data", 4)]
    assert title_weight_matches(
        "Head of Product Design / Product Manager", {"head of product": 3, "product manager": 4},
        "product-management",
    ) == [("product manager", 4)]


def test_literal_custom_weights_keep_case_key_value_and_token_boundaries():
    weights = {"HTML": 2, "ml": 8, "": 100, "A/B testing": -1.25}
    assert title_weight_matches("HTML Engineer - A/B Testing", weights, None) == [
        ("HTML", 2), ("A/B testing", -1.25),
    ]


def test_custom_role_does_not_gain_domain_modifier_or_reordered_aliases():
    assert title_weight_matches(
        "Head of Commercial Analytics", {"head of analytics": 8}, "bespoke-analytics",
    ) == []


def test_weighted_domain_must_appear_even_when_generic_role_grammar_accepts_the_title():
    weights = {"Product Analytics Lead": 8, "Commercial Analytics Lead": 5}
    assert title_weight_matches("Lead, Commercial Analytics", weights, "analytics-leadership") == [
        ("Commercial Analytics Lead", 5),
    ]
