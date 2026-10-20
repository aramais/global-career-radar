from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
import yaml

from job_intake.annotation import cohorts
from job_intake.annotation.cohorts import SCHEMA, duplicate_group, register_cohort
from job_intake.labeling.rubric import (
    RubricError,
    load_precedents,
    load_rubric,
    parse_rubric,
)

ROOT = Path(__file__).parents[1]
RUBRIC = ROOT / "config" / "labeling_rubric.yaml"


def data():
    return yaml.safe_load(RUBRIC.read_text(encoding="utf-8"))


def test_config_is_the_literal_section_5_2_block_of_the_design_doc():
    doc = (ROOT / "docs" / "vacancy-labeling-architecture.md").read_text(encoding="utf-8")
    section = doc.split("### 5.2 `config/labeling_rubric.yaml`", 1)[1]
    block = re.search(r"```yaml\n(.*?)```", section, re.S)[1]
    assert RUBRIC.read_text(encoding="utf-8") == block


def test_confirmed_rubric_loads_with_proposed_strong_companies_ignored():
    rubric = load_rubric(RUBRIC)
    assert rubric.version == rubric.rubric_version == "labels-rubric-2026-10-20"
    assert rubric.status == "confirmed" and rubric.rubric_status == "confirmed"
    assert rubric.section_status == {"aggregation": "confirmed", "domain_preferences": "confirmed",
                                     "strong_companies": "proposed", "p2": "confirmed"}
    assert rubric.aggregation["base_by_function_core"]["analytics_for_decisions"] == 2
    assert rubric.aggregation["unfamiliar_domain_penalty"] == {"preferred": 1, "required": 2}
    assert rubric.liked_industries == {"marketplace_classifieds", "ecommerce_retail",
                                       "healthtech", "pharma_biotech"}
    assert "pharma_biotech" not in rubric.own_domains
    assert rubric.strong_company_names == ()
    assert list(rubric.anchors) == ["0", "1", "2", "3"]
    hashes = {rubric.prompt_rubric_hash, rubric.aggregation_hash}
    assert len(hashes) == 2 and all(re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes)


def test_proposed_names_change_nothing_until_confirmed():
    base = parse_rubric(data())
    proposed = data()
    proposed["strong_companies"]["names"] = ["Nubank S.A."]
    drafted = parse_rubric(proposed)
    assert drafted.strong_company_names == ()
    assert (drafted.prompt_rubric_hash, drafted.aggregation_hash) == (
        base.prompt_rubric_hash, base.aggregation_hash)
    proposed["strong_companies"]["status"] = "confirmed"
    confirmed = parse_rubric(proposed)
    assert confirmed.strong_company_names == ("Nubank S.A.",)
    assert confirmed.prompt_rubric_hash != base.prompt_rubric_hash
    assert confirmed.aggregation_hash != base.aggregation_hash


def test_three_versions_are_independent():
    base = parse_rubric(data())
    anchors = data()
    anchors["anchors"]["2"] = "Changed anchor."
    changed = parse_rubric(anchors)
    assert changed.prompt_rubric_hash != base.prompt_rubric_hash
    assert changed.aggregation_hash == base.aggregation_hash
    policy = data()
    policy["aggregation"]["other_role_max"] = 0
    changed = parse_rubric(policy)
    assert changed.aggregation_hash != base.aggregation_hash
    assert changed.prompt_rubric_hash == base.prompt_rubric_hash
    version = data()
    version["version"] = "labels-rubric-next"
    changed = parse_rubric(version)
    assert changed.version == "labels-rubric-next"
    assert (changed.prompt_rubric_hash, changed.aggregation_hash) == (
        base.prompt_rubric_hash, base.aggregation_hash)


def test_proposed_sections_are_not_applied():
    value = data()
    value["aggregation"]["status"] = "proposed"
    value["domain_preferences"]["status"] = "proposed"
    value["p2"]["status"] = "proposed"
    rubric = parse_rubric(value)
    assert rubric.aggregation is None and rubric.rubric_status == "proposed"
    assert rubric.domain_preferences is None and not rubric.liked_industries
    assert rubric.p2_rule is None


@pytest.mark.parametrize("change", [
    lambda value: value.update(unknown=1),
    lambda value: value.pop("gate_note"),
    lambda value: value["anchors"].pop("3"),
    lambda value: value["aggregation"]["base_by_function_core"].pop("unspecified"),
    lambda value: value["aggregation"].update(min=3, max=0),
    lambda value: value["aggregation"].update(upgrade_to_3_if_any=["seniority"]),
    lambda value: value["domain_preferences"]["disliked_industries"].append("healthtech"),
    lambda value: value["domain_preferences"]["own_domains"].append("banking"),
    lambda value: value["strong_companies"].update(status="draft"),
])
def test_invalid_rubric_is_refused_with_a_code(change):
    value = data()
    change(value)
    with pytest.raises(RubricError) as error:
        parse_rubric(value)
    assert error.value.code == "invalid_rubric"


def precedent(index, relevance):
    return {
        "job_uid": f"uid-{index}", "source_hash": f"{index:064x}",
        "cohort_id": "calibration-2026-10-17", "title": "Analytics Manager",
        "lines": ["[description_clean:000004|role] Lead pricing analytics."],
        "labels": {"role_label": "analytics-leadership", "relevance": relevance,
                   "function_core": "analytics_for_decisions",
                   "domain_fit": {"requirement": "none", "industry": "fintech"},
                   "preferred_focus": ["experimentation_causal"]},
        "reason": "Analytics core informs pricing decisions.",
    }


def manifest(rows, role="calibration"):
    return {
        "schema": SCHEMA, "cohort_id": "calibration-2026-10-17", "dataset_role": role,
        "rubric_version": "labels-rubric-2026-10-20", "created_at": "2026-10-17T12:00:00+00:00",
        "members": [{"job_uid": row["job_uid"], "source_hash": row["source_hash"],
                     "duplicate_group": duplicate_group({"title": row["job_uid"]})}
                    for row in rows],
    }


@pytest.fixture
def precedents(tmp_path):
    rows = [precedent(index, index % 4) for index in range(8)]
    registry = tmp_path / "cohorts.jsonl"
    register_cohort(registry, manifest(rows))
    path = tmp_path / "precedents.jsonl"

    def write(values):
        path.write_text("".join(json.dumps(row) + "\n" for row in values))
        return path

    write(rows)
    return rows, path, registry, write


def test_precedents_validate_cohort_balance_and_privacy(precedents):
    rows, path, registry, write = precedents
    assert load_precedents(path, registry=registry,
                           role_ids=["analytics-leadership"]) == rows
    with_notes = copy.deepcopy(rows)
    with_notes[0]["notes"] = "personal gold comment"
    with pytest.raises(RubricError, match="invalid_precedent"):
        load_precedents(write(with_notes), registry=registry)
    with pytest.raises(RubricError, match="precedent_count"):
        load_precedents(write(rows[:5]), registry=registry)
    unbalanced = [precedent(index, 2) for index in range(8)]
    with pytest.raises(RubricError, match="precedent_balance"):
        load_precedents(write(unbalanced), registry=registry)
    with pytest.raises(RubricError, match="precedent_cohort"):
        load_precedents(write(rows), registry=registry.with_name("missing.jsonl"))
    foreign = copy.deepcopy(rows)
    foreign[0]["source_hash"] = "f" * 64
    with pytest.raises(RubricError, match="precedent_cohort"):
        load_precedents(write(foreign), registry=registry)


@pytest.mark.parametrize("role", [
    "", "  ", "\t", "\n", "new_role\n", "NEW_ROLE", "_bad", "-bad", "bad id", "bad.id",
    None, 0, False, [], {},
])
@pytest.mark.parametrize("with_taxonomy", [False, True])
def test_registered_precedents_refuse_invalid_role_syntax(precedents, role, with_taxonomy):
    rows, _, registry, write = precedents
    invalid = copy.deepcopy(rows)
    invalid[0]["labels"]["role_label"] = role
    options = {"role_ids": ["analytics-leadership"]} if with_taxonomy else {}
    with pytest.raises(RubricError, match=r"invalid_precedent: line 1: labels\.role_label.*syntax"):
        load_precedents(write(invalid), registry=registry, **options)


@pytest.mark.parametrize("role", ["bad id", "new_role\n", "_bad", "Analytics"])
def test_registered_precedents_refuse_invalid_syntax_even_if_allowlisted(precedents, role):
    rows, _, registry, write = precedents
    invalid = copy.deepcopy(rows)
    invalid[0]["labels"]["role_label"] = role
    with pytest.raises(RubricError, match=r"invalid_precedent: line 1: labels\.role_label.*syntax"):
        load_precedents(write(invalid), registry=registry,
                        role_ids=["analytics-leadership", role])


@pytest.mark.parametrize("role", [
    "analytics-leadership", "new_role", "new--role", "new-role-", "0", "r0", "other",
])
@pytest.mark.parametrize("with_taxonomy", [False, True])
def test_registered_precedents_accept_shared_role_syntax(precedents, role, with_taxonomy):
    rows, _, registry, write = precedents
    valid = copy.deepcopy(rows)
    valid[0]["labels"]["role_label"] = role
    enabled = ["analytics-leadership", *([] if role == "other" else [role])]
    options = {"role_ids": enabled} if with_taxonomy else {}
    assert load_precedents(write(valid), registry=registry, **options) == valid


@pytest.mark.parametrize("role", ["new_role", "new--role", "new-role-", "0", "r0"])
def test_registered_precedents_still_require_enabled_membership_when_supplied(precedents, role):
    rows, _, registry, write = precedents
    valid_syntax = copy.deepcopy(rows)
    valid_syntax[0]["labels"]["role_label"] = role
    path = write(valid_syntax)
    assert load_precedents(path, registry=registry) == valid_syntax
    with pytest.raises(RubricError, match=r"labels\.role_label must be an enabled stream ID"):
        load_precedents(path, registry=registry, role_ids=["analytics-leadership"])


@pytest.mark.parametrize("domain", [
    {"requirement": "required", "industry": "fintech", "required_domain": "other"},
    {"requirement": "preferred", "industry": "fintech", "required_domain": "other"},
    *({"requirement": "required", "industry": "fintech", "required_domain": "other",
       "domain": value} for value in (None, "", "   ", "x" * 61, [], {"arbitrary": "value"})),
    {"requirement": "required", "industry": "fintech"},
    {"requirement": "preferred", "industry": "fintech", "required_domain": None},
    {"requirement": "required", "industry": "fintech", "required_domain": "unknown"},
    {"requirement": "none", "industry": "fintech", "required_domain": "fintech"},
    {"requirement": "none", "industry": "fintech", "required_domain": "other",
     "domain": "A domain"},
    {"requirement": "none", "industry": "fintech", "domain": {"arbitrary": "value"}},
    {"requirement": "none", "industry": "unknown"},
    {"requirement": "unknown", "industry": "fintech"},
    {"requirement": "none"},
    {"industry": "fintech"},
    {"requirement": "none", "industry": "fintech", "extra": "unexpected"},
    None,
])
def test_registered_precedents_refuse_invalid_domain_facts(precedents, domain):
    rows, _, registry, write = precedents
    invalid = copy.deepcopy(rows)
    invalid[0]["labels"]["domain_fit"] = domain
    with pytest.raises(RubricError, match=r"invalid_precedent: line 1: labels\.domain_fit"):
        load_precedents(write(invalid), registry=registry,
                        role_ids=["analytics-leadership"])


@pytest.mark.parametrize("domain", [
    {"requirement": "none", "industry": "fintech"},
    {"requirement": "none", "industry": "fintech", "required_domain": None},
    {"requirement": "preferred", "industry": "fintech", "required_domain": "fintech"},
    {"requirement": "required", "industry": "consulting_outsourcing",
     "required_domain": "pharma_biotech"},
    {"requirement": "preferred", "industry": "fintech", "required_domain": "other",
     "domain": "A specific domain"},
    {"requirement": "required", "industry": "fintech", "required_domain": "other",
     "domain": "x" * 60},
])
def test_registered_precedents_preserve_valid_domain_facts(precedents, domain):
    rows, _, registry, write = precedents
    valid = copy.deepcopy(rows)
    valid[0]["labels"]["domain_fit"] = domain
    assert load_precedents(write(valid), registry=registry,
                           role_ids=["analytics-leadership"]) == valid


def test_precedents_from_holdout_are_refused(tmp_path, precedents, monkeypatch):
    rows, _, _, write = precedents
    holdout = tmp_path / "holdout.jsonl"
    register_cohort(holdout, manifest(rows, role="holdout"))
    with pytest.raises(RubricError, match="precedent_cohort"):
        load_precedents(write(rows), registry=holdout)

    def overlap(*args, **kwargs):
        raise ValueError("Holdout and its duplicate mirrors cannot be training examples")

    monkeypatch.setattr(cohorts, "validate_registered_seed_usage", overlap)
    _, path, registry, _ = precedents
    write(rows)
    with pytest.raises(RubricError, match="precedent_holdout"):
        load_precedents(path, registry=registry)
