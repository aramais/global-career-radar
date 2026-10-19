from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from job_intake.annotation.ai import (
    ROLE_CLASSIFICATION_PROMPT,
)
from job_intake.annotation.ai import (
    prompts_version as annotation_prompts_version,
)
from job_intake.annotation.prefilter import prompts_version as prefilter_prompts_version
from job_intake.annotation.text import SOURCE_UNITS_VERSION
from job_intake.filtering import FilterRules, RuleEngine
from job_intake.scoring.pre_score import DeterministicScorer, SearchProfiles
from job_intake.utils.text import stable_hash

ROLE_POLICY_VERSION = "role-first-v3-title-boundaries"
SCORING_POLICY_VERSION = "role-fit-policy-v2-raw-caps"
ELIGIBILITY_POLICY_VERSION = "eligibility-v5-scoped-location-permission"


@dataclass(slots=True)
class SearchStream:
    id: str
    name: str
    context: str
    keywords: list[str]
    version: str
    rules: FilterRules
    scoring: SearchProfiles
    titles: list[str] | None = None
    query_version: str = ""
    version_components: dict[str, str] = field(default_factory=dict)

    @property
    def engine(self) -> RuleEngine:
        return RuleEngine(self.rules)

    @property
    def scorer(self) -> DeterministicScorer:
        return DeterministicScorer(self.scoring, role_id=self.id)


def profile_query_order(streams: list[SearchStream]) -> list[str]:
    """Give each stream a query before the next one from a broad stream, deduplicated."""
    groups = [deque(stream.keywords) for stream in streams]
    seen: set[str] = set()
    result = []
    while any(groups):
        for group in groups:
            if group:
                query = group.popleft()
                if query not in seen:
                    result.append(query)
                    seen.add(query)
    return result


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
        keywords = item.get("search_queries", item.get("keywords", []))
        if not isinstance(keywords, list) or any(not isinstance(k, str) for k in keywords):
            raise ValueError(f"Stream {profile_id}: keywords must be a list of strings")
        titles = item.get("titles", item.get("keywords", []))
        if not isinstance(titles, list) or any(not isinstance(t, str) for t in titles):
            raise ValueError(f"Stream {profile_id}: titles must be a list of strings")
        scoring_version = stable_hash(
            json.dumps(
                {
                    "id": profile_id,
                    "name": name,
                    "context": context,
                    "rules": resolved_rules,
                    "scoring": resolved_scoring,
                    "text_processing_version": SOURCE_UNITS_VERSION,
                    "eligibility_policy_version": ELIGIBILITY_POLICY_VERSION,
                    "scoring_policy_version": SCORING_POLICY_VERSION,
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
                scoring_version,
                FilterRules.from_mapping(resolved_rules),
                scoring,
                titles=titles,
                query_version=stable_hash(json.dumps(sorted(set(keywords)))),
                version_components={
                    "scoring": scoring_version,
                    "classification_prompt": stable_hash(ROLE_CLASSIFICATION_PROMPT),
                    "annotation": annotation_prompts_version(),
                    "prefilter": prefilter_prompts_version(),
                    "source_units": SOURCE_UNITS_VERSION,
                },
            )
        )
    if not streams:
        raise ValueError("Enable at least one search stream")
    # A neighbouring label can change classification without changing this role's
    # scoring rules. Invalidate every saved evaluation when the taxonomy changes.
    taxonomy = taxonomy_version(streams)
    for stream in streams:
        stream.version_components.update(taxonomy=taxonomy, query=stream.query_version)
        stream.version = stable_hash(stream.version + taxonomy)
    return streams


def role_taxonomy(streams: list[SearchStream]) -> list[dict[str, Any]]:
    """Occupation descriptors only: no candidate context, fit weights or thresholds."""
    return [
        {"id": stream.id, "name": stream.name,
         "titles": sorted(set(
             (stream.titles if stream.titles is not None else stream.keywords)
             + stream.rules.positive_title_signals
         ))}
        for stream in sorted(streams, key=lambda item: item.id) if stream.id != "other"
    ]


def taxonomy_version(streams: list[SearchStream]) -> str:
    return stable_hash(json.dumps(
        {"policy": ROLE_POLICY_VERSION, "text_version": SOURCE_UNITS_VERSION,
         "labels": role_taxonomy(streams)},
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
        version_components={
            "taxonomy": taxonomy_version(streams),
            "scoring": stable_hash(json.dumps(rules, sort_keys=True)),
            "annotation": annotation_prompts_version(),
            "classification_prompt": stable_hash(ROLE_CLASSIFICATION_PROMPT),
            "prefilter": prefilter_prompts_version(),
            "source_units": SOURCE_UNITS_VERSION,
        },
    )
    return [*streams, other]


def stale_version_reasons(
    previous: dict[str, str] | None, current: dict[str, str],
    *, stage: str = "evaluation",
) -> list[str]:
    """Explain exactly which contract changed; search-only edits never stale decisions.

    Missing component manifests on historical rows remain unknown, rather than
    being reverse-engineered from a composite version.
    """
    stages = {
        "classification": {"taxonomy", "classification_prompt", "source_units"},
        "scoring": {"scoring", "taxonomy", "source_units"},
        "annotation": {"annotation", "source_units"},
        "prefilter": {"prefilter", "source_units"},
        "evaluation": {"scoring", "taxonomy", "classification_prompt", "annotation",
                       "prefilter", "source_units"},
    }
    if stage not in stages:
        raise ValueError(f"Unknown version stage: {stage}")
    if not previous:
        return ["version_components:legacy_unknown"]
    return [f"version:{name}:changed" for name in sorted(stages[stage])
            if name in current and previous.get(name) != current[name]]
