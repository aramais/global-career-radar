from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from job_intake.filtering import FilterRules, RuleEngine
from job_intake.scoring.pre_score import DeterministicScorer, SearchProfiles
from job_intake.utils.text import stable_hash


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
        return DeterministicScorer(self.scoring)


def load_streams(rules: dict[str, Any], config: dict[str, Any]) -> list[SearchStream]:
    """Resolve independent profiles; retain support for the original single-profile YAML."""
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
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", profile_id):
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
                    "text_processing_version": "grounded-v1",
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
    return streams
