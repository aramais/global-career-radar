from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from job_intake.labeling.aggregate import (
    AGGREGATION_VERSION,
    normalize_company,
    p2_candidate,
    rules_relevance,
)
from job_intake.labeling.rubric import load_rubric, parse_rubric

RUBRIC_PATH = Path(__file__).parents[1] / "config" / "labeling_rubric.yaml"
RUBRIC = load_rubric(RUBRIC_PATH)
TARGET = "analytics-leadership"
PREFERRED = ["pricing_monetization", "experimentation_causal", "unit_economics_pnl_finance",
             "marketplace_economics"]


def tags(function_core="analytics_for_decisions", *, industry=None, requirement="none",
         required_domain=None, focus=()):
    result = {"function_core": {"value": function_core, "evidence": ["d:1"]},
              "preferred_focus": {"values": list(focus), "evidence": []}}
    if industry is not None:
        result["domain_fit"] = {"industry": industry, "requirement": requirement,
                                "required_domain": required_domain, "evidence": []}
    return result


def rubric_with(**sections):
    value = yaml.safe_load(RUBRIC_PATH.read_text(encoding="utf-8"))
    for name, changes in sections.items():
        value[name].update(changes)
    return parse_rubric(value)


@pytest.mark.parametrize("function_core,expected", [
    ("analytics_for_decisions", 2), ("analytics_reporting", 1), ("solo_analysis", 0),
    ("product_without_analytics", 0), ("non_analytics", 0), ("unspecified", 1),
])
def test_each_base_row(function_core, expected):
    assert rules_relevance(TARGET, tags(function_core), RUBRIC) == expected


def test_missing_function_core_counts_as_unspecified():
    assert rules_relevance(TARGET, {}, RUBRIC) == 1


def test_other_role_ceiling_blocks_the_upgrade():
    assert rules_relevance("other", tags(), RUBRIC) == 1
    assert rules_relevance("other", tags(focus=["pricing_monetization"],
                                         industry="healthtech"), RUBRIC) == 1
    assert rules_relevance("other", tags("non_analytics"), RUBRIC) == 0


def test_upgrade_to_3_needs_base_2_and_one_condition():
    assert rules_relevance(TARGET, tags(focus=["experimentation_causal"]), RUBRIC) == 3
    assert rules_relevance(TARGET, tags(industry="marketplace_classifieds"), RUBRIC) == 3
    assert rules_relevance(TARGET, tags(industry="fintech"), RUBRIC) == 2
    assert rules_relevance(TARGET, tags("analytics_reporting", focus=["pricing_monetization"],
                                        industry="healthtech"), RUBRIC) == 1


def test_strong_company_upgrades_only_from_a_confirmed_list():
    names = {"names": ["Nubank S.A."]}
    proposed = rubric_with(strong_companies=names)
    confirmed = rubric_with(strong_companies={**names, "status": "confirmed"})
    assert rules_relevance(TARGET, tags(), proposed, company="Nubank") == 2
    assert rules_relevance(TARGET, tags(), confirmed, company="nubank ltda.") == 3
    assert rules_relevance(TARGET, tags(), confirmed, company="Other Bank") == 2
    assert rules_relevance(TARGET, tags(), confirmed) == 2


def test_disliked_industry_penalty():
    assert rules_relevance(TARGET, tags(industry="gaming_betting"), RUBRIC) == 1
    assert rules_relevance(TARGET, tags(industry="cybersecurity",
                                        focus=["pricing_monetization"]), RUBRIC) == 2


def test_unfamiliar_domain_penalties_and_own_domain():
    def relevance(requirement, domain):
        return rules_relevance(TARGET, tags(industry="fintech", requirement=requirement,
                                            required_domain=domain), RUBRIC)

    assert relevance("preferred", "fintech") == 1
    assert relevance("required", "fintech") == 0
    assert relevance("required", "mobility") == 2
    assert relevance("preferred", "ecommerce_retail") == 2
    assert relevance("required", "other") == 0
    assert relevance("preferred", None) == 1


def test_result_is_clamped_to_rubric_range():
    assert rules_relevance(TARGET, tags("non_analytics", industry="gaming_betting",
                                        requirement="required",
                                        required_domain="gaming_betting"), RUBRIC) == 0
    assert rules_relevance(TARGET, tags(industry="ecommerce_retail",
                                        focus=PREFERRED), RUBRIC) == 3


@pytest.mark.parametrize("company,role,label,expected", [
    # Gold 2026-10-17: biotech is a liked industry, pharma knowledge "preferred" costs 1.
    ("argenx", "analytics-leadership",
     tags(industry="pharma_biotech", requirement="preferred", required_domain="pharma_biotech"),
     2),
    # Gold: "minus 2 for required pharma data" — the pharma practice is the industry.
    ("Tiger Analytics Inc.", "analytics-leadership",
     tags(industry="pharma_biotech", requirement="required", required_domain="pharma_biotech"),
     1),
    # Gold: "just analytics in betting".
    ("BVGroup", "analytics-leadership", tags(industry="gaming_betting"), 1),
    # Gold: required esports experience in a disliked industry; the US gate is separate.
    ("PrizePicks", "data-science-management",
     tags(industry="gaming_betting", requirement="required", required_domain="gaming_betting"),
     0),
])
def test_gold_control_cases(company, role, label, expected):
    assert rules_relevance(role, label, RUBRIC, company=company) == expected


def test_proposed_domain_preferences_neither_upgrade_nor_penalize():
    rubric = rubric_with(domain_preferences={"status": "proposed"})
    assert rules_relevance(TARGET, tags(industry="healthtech"), rubric) == 2
    assert rules_relevance(TARGET, tags(industry="gaming_betting", requirement="required",
                                        required_domain="gaming_betting"), rubric) == 2


def test_unconfirmed_aggregation_is_refused():
    with pytest.raises(ValueError, match="aggregation_not_confirmed"):
        rules_relevance(TARGET, tags(), rubric_with(aggregation={"status": "proposed"}))


@pytest.mark.parametrize("name,expected", [
    ("Tiger Analytics Inc.", "tiger analytics"), ("Acme, Inc", "acme"), ("Nubank S.A.", "nubank"),
    ("Nubank SA", "nubank"), ("Raízen Ltda.", "raizen"), ("Zalando SE GmbH", "zalando se"),
    ("Mercado-Libre LLC", "mercado libre"), ("ARGENX", "argenx"), ("  Foo   Ltd  ", "foo"),
    ("Mosa", "mosa"), (None, ""),
])
def test_normalize_company(name, expected):
    assert normalize_company(name) == expected


@pytest.mark.parametrize("function_core,relevance,gate,expected", [
    ("analytics_for_decisions", 1, "unknown", True),
    ("analytics_reporting", 1, "eligible", True),
    ("analytics_for_decisions", 2, "ineligible", True),
    ("analytics_reporting", 3, "ineligible", True),
    ("analytics_for_decisions", 2, "unknown", False),
    ("analytics_for_decisions", 0, "ineligible", False),
    ("product_without_analytics", 1, "unknown", False),
    ("non_analytics", 2, "ineligible", False),
    ("analytics_for_decisions", None, "ineligible", False),
])
def test_p2_candidate(function_core, relevance, gate, expected):
    assert p2_candidate(function_core, relevance, gate) is expected


def test_aggregation_version_is_stable():
    assert AGGREGATION_VERSION == "labels-aggregation-v1"
