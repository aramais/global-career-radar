from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from job_intake.annotation.ai import ROLE_CLASSIFICATION_PROMPT
from job_intake.annotation.prefilter import prompts_version as prefilter_prompts_version
from job_intake.annotation.text import TEXT_VERSION
from job_intake.filtering import FilterRules, RuleEngine
from job_intake.scoring.pre_score import DeterministicScorer, SearchProfiles
from job_intake.utils.text import stable_hash

ROLE_POLICY_VERSION = "role-first-v2-title-occupations"


@dataclass(slots=True)
class SearchStream:
    id: str
    name: str
    context: str
    keywords: list[str]
    version: str
    rules: FilterRules
    scoring: SearchProfiles

    @property
    def engine(self) -> RuleEngine:
        return RuleEngine(self.rules)

    @property
    def scorer(self) -> DeterministicScorer:
        return DeterministicScorer(self.scoring, role_id=self.id)


def load_streams(rules: dict[str, Any], config: dict[str, Any]) -> list[SearchStream]:
    """Resolve role labels and their relevance criteria, retaining stable IDs."""
    definitions = config.get("streams")
    if definitions is None:
        definitions = [{"id": "default", "name": "Default", "scoring": config}]
        common_scoring: dict[str, Any] = {}
    else:
        if not isinstance(definitions, list):
            raise ValueError("search_profiles.streams must be a list")
        common_scoring = config.get("defaults", {})
    streams = []
    seen = set()
    for item in definitions:
        if not isinstance(item, dict):
            raise ValueError("Each search stream must be a mapping")
        profile_id = str(item.get("id", ""))
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", profile_id) or profile_id == "other":
            raise ValueError(f"Invalid stream id: {profile_id!r}")
        if profile_id in seen:
            raise ValueError(f"Duplicate stream id: {profile_id}")
        seen.add(profile_id)
        if not item.get("enabled", True):
            continue
        name = str(item.get("name", profile_id))
        context = str(item.get("context", ""))
        resolved_rules = {**rules, **item.get("rules", {})}
        resolved_scoring = {**common_scoring, **item.get("scoring", {})}
        scoring = SearchProfiles.from_mapping(resolved_scoring)
        if scoring.threshold_a < scoring.threshold_b:
            raise ValueError(f"Stream {profile_id}: threshold_a must be >= threshold_b")
        keywords = item.get("keywords", [])
        if not isinstance(keywords, list) or any(not isinstance(k, str) for k in keywords):
            raise ValueError(f"Stream {profile_id}: keywords must be a list of strings")
        version = stable_hash(
            json.dumps(
                {
                    "id": profile_id,
                    "name": name,
                    "context": context,
                    "rules": resolved_rules,
                    "scoring": resolved_scoring,
                    "text_processing_version": TEXT_VERSION,
                    "eligibility_policy_version": "eligibility-v3",
                    "prefilter_policy_version": prefilter_prompts_version(),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        )
        streams.append(
            SearchStream(
                profile_id,
                name,
                context,
                keywords,
                version,
                FilterRules.from_mapping(resolved_rules),
                scoring,
            )
        )
    if not streams:
        raise ValueError("Enable at least one search stream")
    # A neighbouring label can change classification without changing this role's
    # scoring rules. Invalidate every saved evaluation when the taxonomy changes.
    taxonomy = taxonomy_version(streams)
    for stream in streams:
        stream.version = stable_hash(stream.version + taxonomy)
    return streams


def role_taxonomy(streams: list[SearchStream]) -> list[dict[str, Any]]:
    """Occupation descriptors only: no candidate context, fit weights or thresholds."""
    return [
        {"id": stream.id, "name": stream.name,
         "titles": sorted(set(stream.keywords + stream.rules.positive_title_signals))}
        for stream in sorted(streams, key=lambda item: item.id) if stream.id != "other"
    ]


def taxonomy_version(streams: list[SearchStream]) -> str:
    return stable_hash(json.dumps(
        {"policy": ROLE_POLICY_VERSION, "text_version": TEXT_VERSION,
         "classification_prompt": ROLE_CLASSIFICATION_PROMPT, "labels": role_taxonomy(streams)},
        sort_keys=True, ensure_ascii=False,
    ))


def with_other_stream(
    streams: list[SearchStream], rules: dict[str, Any],
) -> list[SearchStream]:
    """Add the explicit outside/uncertain role; it never runs a relevance scorer."""
    if any(stream.id == "other" for stream in streams):
        return streams
    other = SearchStream(
        id="other", name="Other / uncertain role", context="", keywords=[],
        version=stable_hash(taxonomy_version(streams) + json.dumps({
            "rules": rules, "profiles": {s.id: s.version for s in streams},
        }, sort_keys=True)),
        rules=FilterRules.from_mapping(rules), scoring=SearchProfiles.from_mapping({}),
    )
    return [*streams, other]
