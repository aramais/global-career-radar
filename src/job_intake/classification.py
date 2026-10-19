"""Choose one advertised occupation before evaluating relevance to that role.

Consumes source title/duties and role names/examples, never candidate preferences,
scoring weights, eligibility, or the previously best score.
"""

from __future__ import annotations

import json
import re
from typing import Any

from job_intake.annotation.ai import ROLE_CLASSIFICATION_PROMPT, AnnotationModelClient
from job_intake.annotation.text import (
    SOURCE_UNITS_VERSION,
    asserted_phrase_hits,
    normalize_quote,
    role_description,
    source_hash,
    source_identity,
    source_units,
)
from job_intake.config.settings import AnnotationConfig
from job_intake.models.job import JobRecord
from job_intake.occupation_titles import _title_matches, _title_text
from job_intake.profiles import (
    SearchStream,
    role_taxonomy,
    taxonomy_version,
    with_other_stream,
)
from job_intake.storage.dedup import JobDeduplicator
from job_intake.utils.text import normalize_text, stable_hash

CLASSIFICATION_INPUT_VERSION = "role-classification-input-v1"


def _duties(record: JobRecord) -> str:
    """Current-role evidence, excluding company and candidate-qualification sections."""
    scoped = role_description(record, exclude_boilerplate=True)
    return "\n".join(
        unit["text"] for unit in source_units(record)
        if unit["field"] in {"description_clean", "description_raw"}
        and unit["section"] not in {"company", "requirements"}
        and unit["text"] in scoped
    )


class RoleClassifier:
    def __init__(
        self, streams: list[SearchStream], annotation_config: AnnotationConfig | None = None,
    ) -> None:
        self.streams = with_other_stream(streams, {})
        self.by_id = {stream.id: stream for stream in self.streams}
        self.taxonomy = role_taxonomy(self.streams)
        self.version = taxonomy_version(self.streams)
        self.config = annotation_config
        self.client = AnnotationModelClient(annotation_config) if annotation_config else None
        spec = annotation_config.resolved_stage("extract") if annotation_config else None
        self.model_signature = stable_hash(json.dumps({
            "source_units_version": SOURCE_UNITS_VERSION,
            **{key: getattr(spec, key)
               for key in ("provider", "model", "base_url", "reasoning_effort",
                           "max_output_tokens")},
        }, sort_keys=True)) if spec else None
        self.stats = {"calls": 0, "cache_hits": 0, "errors": 0}

    def _title_matches(self, value: str) -> dict[str, list[str]]:
        return _title_matches(value, self.taxonomy)

    def _local(self, record: JobRecord) -> tuple[str, str, str]:
        matches = self._title_matches(record.title)
        found = [label for label, phrases in matches.items() if phrases]
        if len(found) == 1:
            return found[0], "title", "Advertised title: " + record.title
        if not found:
            # Literal role declarations can resolve vague headings. Collaborators'
            # titles or qualifications must not define the advertised occupation.
            declarations = []
            for unit in source_units(record):
                if unit["field"] not in {"description_clean", "description_raw"}:
                    continue
                if unit["section"] in {"company", "requirements"}:
                    continue
                declarations.extend(re.findall(
                    r"\b(?:you(?: will|['’]ll) (?:work|serve|act) as (?:a|an|the)|"
                    r"this (?:role|position) is (?:a|an)|^as (?:a|an|the))\s+([^.!?\n]+)",
                    unit["text"].strip(), re.I,
                ))
            declared = set()
            for text in declarations:
                for label, phrases in self._title_matches(text.split(",", 1)[0]).items():
                    if asserted_phrase_hits(_title_text(text), phrases):
                        declared.add(label)
            if len(declared) == 1:
                return declared.pop(), "duties", "Explicit role declaration in responsibilities."
        if len(self.taxonomy) == 1 and self.taxonomy[0]["id"] == "default":
            return "default", "single_label", "Single default role configured."
        reason = "Ambiguous role across configured labels." if found else (
            "No supported match to a configured role; review occupation manually."
        )
        return "other", "uncertain" if found else "outside_taxonomy", reason

    def classify(
        self, record: JobRecord, *, use_ai: bool = False, previous: dict | None = None,
    ) -> tuple[SearchStream, dict[str, Any]]:
        content_hash = JobDeduplicator().build_identity(record).content_hash
        digest = source_hash(record)
        duties = _duties(record)
        visible_source = normalize_quote(record.title + "\n" + duties[:24000])
        prompt = ROLE_CLASSIFICATION_PROMPT + json.dumps({
            "labels": self.taxonomy, "title": record.title, "duties": duties[:24000],
        }, ensure_ascii=False, sort_keys=True)
        input_hash = stable_hash(json.dumps({
            "version": CLASSIFICATION_INPUT_VERSION,
            "source_identity": source_identity(record), "prompt": prompt,
        }, ensure_ascii=False, sort_keys=True))
        current = (
            isinstance(previous, dict)
            and previous.get("version") == self.version
            and (
                previous.get("classification_input_version") == CLASSIFICATION_INPUT_VERSION
                and previous.get("classification_input_hash") == input_hash
                or "classification_input_hash" not in previous
                and previous.get("source_hash") == digest
                and previous.get("content_hash") == content_hash
            )
            and isinstance(previous.get("label"), str)
            and previous["label"] in self.by_id
            and isinstance(previous.get("reason"), str) and bool(previous["reason"].strip())
            and previous.get("method") == "model"
            and previous.get("model_config") == self.model_signature
            and isinstance(previous.get("status"), str)
            and previous["status"] in {"classified", "outside_taxonomy"}
            and isinstance(previous.get("quote"), str)
            and (previous["label"] == "other" or (
                bool(normalize_text(previous["quote"]))
                and normalize_quote(previous["quote"]) in visible_source
            ))
        )
        if current:
            self.stats["cache_hits"] += 1
            rebound = dict(previous)
            rebound.update(source_hash=digest, content_hash=content_hash)
            return self.by_id[previous["label"]], rebound
        label, status, reason = self._local(record)
        result: dict[str, Any] = {
            "label": label, "name": self.by_id[label].name, "version": self.version,
            "source_hash": digest, "content_hash": content_hash,
            "method": "local", "status": status, "reason": reason,
        }
        # An explicit AI run can resolve vague/hybrid titles by responsibilities.
        if label == "other" and use_ai and self.client is not None:
            self.stats["calls"] += 1
            try:
                payload, _usage = self.client.generate(self.config.extract_model, prompt)
                if (
                    not isinstance(payload, dict)
                    or set(payload) != {"role_label", "reason", "quote"}
                    or not isinstance(payload["role_label"], str)
                    or payload["role_label"] not in self.by_id
                    or not isinstance(payload["reason"], str) or not payload["reason"].strip()
                    or not isinstance(payload["quote"], str)
                ):
                    raise ValueError("Invalid classification")
                quote = payload["quote"].strip()
                if payload["role_label"] != "other" and (
                    not normalize_text(quote) or normalize_quote(quote) not in visible_source
                ):
                    raise ValueError("Classification lacks source evidence")
                label = payload["role_label"]
                result.update(
                    label=label, name=self.by_id[label].name, method="model",
                    status="classified" if label != "other" else "outside_taxonomy",
                    reason=payload["reason"].strip(), quote=quote,
                    model=self.config.extract_model,
                    provider=self.config.resolved_stage("extract").provider,
                    model_config=self.model_signature,
                    classification_input_version=CLASSIFICATION_INPUT_VERSION,
                    classification_input_hash=input_hash,
                )
            except Exception as exc:  # Provider errors never leak source/secrets.
                self.stats["errors"] += 1
                result["error"] = type(exc).__name__
                result["status"] = "needs_review"
        return self.by_id[result["label"]], result
