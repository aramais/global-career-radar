"""Source-grounded discovery plans and a reproducible, local ranking baseline.

Human relevance is an input, never a prediction produced by this module. This
heuristic prioritizes review; its numbers are not calibrated probabilities.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from job_intake.annotation.gold import _snapshot, _source_error, validate_gold
from job_intake.annotation.text import source_hash
from job_intake.models.job import JobRecord, JobStatus
from job_intake.scoring.evidence import _duty_units
from job_intake.utils.text import canonicalize_url

ALGORITHM_VERSION = "positive-duties-tfidf-v1"
_CRITERIA = (
    "International data roles influencing business decisions, managing projects and "
    "working with people; direct reports are optional. English working language. "
    "Partnership with a business unit and P&L are preferences, not requirements. "
    "Brazil, LATAM and worldwide hiring are explored without rejecting role-training "
    "examples on hiring eligibility. Pricing and monetization are optional interests."
)
_CRITERIA_SOURCE = (
    "../aramais-vault/codex/projects/career-orientation/codex/areas/job-search-vector.md"
)
_STOP = set("""
    a an and are as at be been being by can do for from had has have how if in into
    is it its of on or our that the their them these they this those to us was we
    were what when where which who will with would you your yours about across all
    also both each ensure including key more most new other own provide role strong
    such team teams through work working within responsibilities successful success
    opportunity position job jobs careers career business data analytics analysis
    able ability experience required requirements qualifications years skills
""".split())  # noqa: SIM905 -- Keep the word list readable.
_TOKEN = re.compile(r"[a-z][a-z0-9]*(?:[-'][a-z0-9]+)*", re.I)
# These are search vocabulary expansions, not claims about a candidate or seed.
_CONCEPTS = {
    "pricing": (r"\b(?:pricing|price|prices)\b", "pricing strategy"),
    "monetization": (r"\b(?:monetiz\w*|revenue)\b", "monetization revenue"),
    "experimentation": (r"\b(?:experiment\w*|causal|a/b)\b", "experimentation causal inference"),
    "economics": (r"\b(?:economics|profit|p&l|financial|finance|margin)\b",
                  "unit economics finance"),
    "decisions": (r"\b(?:decision\w*|recommend\w*|strateg\w*)\b", "business decisions strategy"),
    "projects": (r"\b(?:project\w*|program\w*|roadmap\w*|prioriti\w*)\b",
                 "cross functional projects"),
    "people": (r"\b(?:stakeholder\w*|collaborat\w*|partner\w*|mentor\w*|coach\w*)\b",
               "stakeholder partnership"),
    "product": (r"\b(?:product\w*|customer\w*|marketplace\w*)\b", "product insights"),
}
_ROLES = ("analytics lead", "decision science manager", "strategic analytics",
          "business analytics manager", "product insights lead", "analytics project lead")


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key in human labels")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON value in human labels: " + value)


def _record(snapshot: dict) -> JobRecord:
    if message := _source_error(snapshot):
        raise ValueError("Invalid immutable source snapshot: " + message)
    values = copy.deepcopy(snapshot)
    for field in ("source", "company", "title", "original_url", "description_raw",
                  "description_clean"):
        values[field] = values.get(field) or ""
    try:
        values["status"] = JobStatus(values.get("status") or "open")
        if values.get("posted_at") is not None:
            values["posted_at"] = datetime.fromisoformat(values["posted_at"].replace("Z", "+00:00"))
        return JobRecord(**values)
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid immutable source snapshot: " + str(error)) from error


def _load_labels(path: Path, source_records: dict[str, JobRecord] | None = None) -> list[dict]:
    try:
        rows = [json.loads(line, object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
                for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read human labels {path}: {error}") from error
    if not rows:
        raise ValueError(f"Human labels are empty: {path}")
    records = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("source"), dict):
            raise ValueError("Every discovery input row requires an immutable source snapshot")
        record = _record(row["source"])
        if source_records is not None and row.get("job_uid") in source_records:
            record = source_records[row["job_uid"]]
            if _snapshot(record) != row["source"]:
                raise ValueError("Immutable source snapshot mismatch: " + str(row.get("job_uid")))
        if row.get("source_hash") != source_hash(record):
            raise ValueError(
                "Immutable source_hash mismatch: " + str(row.get("job_uid"))
                + ". Existing gold may require read-only source_records because its snapshot "
                  "omits metadata that source_hash includes."
            )
        records.append((row.get("job_uid"), record))
    report = validate_gold(records, rows)
    if not report["valid"]:
        messages = "; ".join(f"{item['job_uid']}: {item['code']}" for item in report["errors"])
        raise ValueError("Invalid human discovery labels: " + messages)
    # Do not return rebound labels: preserve the exact human source snapshots.
    return rows


def _duties(record: JobRecord) -> list[str]:
    """Use the same conservative current-role scopes as role-fit evidence."""
    return list(dict.fromkeys(text for _, text in _duty_units(record)))


def _terms(text: str) -> list[str]:
    aliases = {
        "decisions": "decision", "decision-making": "decision", "projects": "project",
        "stakeholders": "stakeholder", "experiments": "experiment",
        "experimentation": "experiment", "experimental": "experiment",
        "collaboration": "collaborate", "collaborating": "collaborate",
        "partners": "partner", "partnering": "partner", "leads": "lead",
        "leading": "lead", "manages": "manage", "managing": "manage",
        "products": "product", "customers": "customer", "insights": "insight",
    }
    return [aliases.get(term, term) for match in _TOKEN.finditer(text.casefold())
            if len(term := match[0]) > 2 and term not in _STOP]


def _concepts(text: str) -> list[str]:
    return [name for name, (pattern, _) in _CONCEPTS.items() if re.search(pattern, text, re.I)]


def _phrases(duties: list[str], prohibited: set[str], document_frequency: Counter) -> list[str]:
    phrases: dict[str, float] = {}
    for duty in duties:
        tokens = list(_TOKEN.finditer(duty))
        for size in (2, 3, 4):
            for start in range(len(tokens) - size + 1):
                selected = tokens[start:start + size]
                phrase = duty[selected[0].start():selected[-1].end()]
                words = [match[0].casefold() for match in selected]
                meaningful = _terms(phrase)
                if (len(phrase) > 48 or len(meaningful) < 2 or words[0] in _STOP
                        or words[-1] in _STOP or prohibited.intersection(words)):
                    continue
                if not re.fullmatch(r"[A-Za-z0-9 &'/-]+", phrase):
                    continue
                # A phrase is quoted only if it is a literal span of a duty.
                weight = sum(1 / max(document_frequency[term], 1) for term in set(meaningful))
                weight += 4 * len(_concepts(phrase))
                phrases.setdefault(phrase, weight / math.sqrt(size))
    return sorted(phrases, key=lambda phrase: (-phrases[phrase], phrase.casefold()))[:18]


def _seed_queries(seed: dict, document_frequency: Counter) -> list[str]:
    duties = seed["duties"]
    text = "\n".join(duties)
    company = seed["source"].get("company") or ""
    prohibited = {token[0].casefold() for token in _TOKEN.finditer(company)}
    phrases = _phrases(duties, prohibited, document_frequency)
    concepts = _concepts(text)
    queries = []
    # Vocabulary expansion is intentionally unquoted, to avoid treating synonyms
    # as exact source evidence or forcing exact seed-title matches.
    for index, concept in enumerate(concepts):
        vocabulary = _CONCEPTS[concept][1]
        queries.append(f"{_ROLES[index % len(_ROLES)]} {vocabulary} jobs")
        for following in concepts[index + 1:]:
            queries.append(f"analytics {_CONCEPTS[concept][1]} {_CONCEPTS[following][1]} jobs")
    for index, phrase in enumerate(phrases):
        # Broad content words first; quoted exact phrases are a separate,
        # narrower probe rather than the only way to find similar work.
        queries.append(f"{phrase} analytics leadership jobs")
        queries.append(f'{_ROLES[index % len(_ROLES)]} "{phrase}" jobs')
    title = " ".join((seed["source"].get("title") or "").casefold().split())
    return [query for query in dict.fromkeys(queries)
            if len(query) <= 128
            and not (title and title in query.casefold())
            and not prohibited.intersection(
                match[0].casefold() for match in _TOKEN.finditer(query))]


def _exploration_queries() -> list[str]:
    themes = (
        "business decisions cross functional projects",
        "strategic decisions stakeholder partnership",
        "project ownership product decisions",
        "decision support business unit partnership",
        "experimentation project leadership",
        "commercial decisions finance partnership",
        "insights business recommendations project management",
        "product strategy stakeholder collaboration",
    )
    # No single industry, preferred domain, title or country is mandatory.
    contexts = ("jobs", "English remote jobs", "Brazil English jobs", "LATAM remote jobs")
    return [f"{role} {theme} {context}"
            for context in contexts for theme in themes for role in _ROLES[:3]]


def build_plan(
    gold_path: Path, *, holdout_path: Path | None = None, max_queries: int = 80,
    exploration_fraction: float = .2, feedback_path: Path | None = None,
    source_records: dict[str, JobRecord] | None = None,
) -> dict:
    """Plan a bounded, round-robin search from human positives, without writes.

    ``feedback_path`` is a separate newly labeled batch in the human gold JSONL
    format. Holdout sources may overlap gold, but never supply seeds or ranking
    counterexamples. All known source URLs are excluded from new successes.
    Existing gold snapshots omit some original metadata; ``source_records`` can
    supply the original records read through the existing read-only DB loader.
    Their full source hash AND whitelisted snapshot must match the human labels.
    """
    if type(max_queries) is not int or not 1 <= max_queries <= 80:
        raise ValueError("max_queries must be an integer from 1 to 80")
    if (type(exploration_fraction) not in (int, float)
            or not math.isfinite(exploration_fraction) or not 0 <= exploration_fraction <= 1):
        raise ValueError("exploration_fraction must be a finite number from 0 to 1")
    gold = _load_labels(gold_path, source_records)
    holdout = _load_labels(holdout_path, source_records) if holdout_path is not None else []
    feedback = _load_labels(feedback_path, source_records) if feedback_path is not None else []
    holdout_uids = {row["job_uid"] for row in holdout}
    # URL overlap catches a held-out vacancy that was assigned a different ID.
    holdout_urls = {canonicalize_url(row["source"].get(field))
                    for row in holdout for field in ("original_url", "apply_url")}
    holdout_urls.discard("")
    seen = {}
    for row in [*gold, *holdout, *feedback]:
        uid = row["job_uid"]
        if uid in seen and seen[uid]["source_hash"] != row["source_hash"]:
            raise ValueError("Conflicting immutable source for duplicate job_uid: " + uid)
        if uid in seen and any(
            seen[uid].get(field) is not None and row.get(field) is not None
            and seen[uid][field] != row[field]
            for field in ("relevance", "role_label", "gate_outcome")
        ):
            raise ValueError("Conflicting human labels for duplicate job_uid: " + uid)
        seen.setdefault(uid, row)
    known_gold = {row["job_uid"] for row in gold}
    train = [*gold, *[row for row in feedback if row["job_uid"] not in known_gold]]
    seeds, counterexamples, warnings = [], [], []
    for row in sorted(train, key=lambda item: item["job_uid"]):
        urls = {canonicalize_url(row["source"].get(field))
                for field in ("original_url", "apply_url")} - {""}
        if row["job_uid"] in holdout_uids or urls.intersection(holdout_urls):
            continue
        relevance = row.get("relevance")
        if type(relevance) is not int or relevance not in (0, 2, 3):
            continue
        if row.get("method") not in ("blind", "assisted") or not row.get("labeled_at"):
            raise ValueError("Discovery seeds and counterexamples require human provenance")
        duties = _duties(_record(row["source"]))
        if not duties:
            warnings.append(f"No attributable role duties; skipped {row['job_uid']}")
            continue
        entry = {key: copy.deepcopy(row.get(key))
                 for key in ("job_uid", "source_hash", "relevance", "role_label", "source")}
        entry["snapshot_hash"] = source_hash(_record(row["source"]))
        entry["source_binding"] = (
            "snapshot_verified" if entry["snapshot_hash"] == row["source_hash"]
            else "sqlite_verified"
        )
        entry["duties"] = duties
        entry["seed_group"] = next(iter(_concepts("\n".join(duties))), "general")
        (seeds if relevance >= 2 else counterexamples).append(entry)
    if not seeds:
        raise ValueError("No validated human relevance 2/3 seeds with attributable role duties")
    frequency = Counter(term for seed in seeds for term in set(_terms("\n".join(seed["duties"]))))
    query_rows, seen_queries = [], set()

    def append(query: str, kind: str, seed_ids: list[str]) -> bool:
        key = " ".join(query.casefold().split())
        if key in seen_queries or len(query) > 128:
            if key in seen_queries and seed_ids:
                existing = next(row for row in query_rows
                                if " ".join(row["query"].casefold().split()) == key)
                existing["seed_ids"] = sorted(set(existing["seed_ids"]) | set(seed_ids))
            return False
        seen_queries.add(key)
        query_rows.append({"id": f"q-{len(query_rows) + 1:04d}", "query": query,
                           "seed_ids": seed_ids, "kind": kind})
        return True

    exploration_count = min(max_queries, round(max_queries * exploration_fraction))
    seed_limit = max_queries - exploration_count
    queues = [(seed["job_uid"], _seed_queries(seed, frequency)) for seed in seeds]
    # Round-robin prevents the longest positive source consuming the whole plan.
    while len(query_rows) < seed_limit and any(queue for _, queue in queues):
        for uid, queue in queues:
            if queue and len(query_rows) < seed_limit:
                append(queue.pop(0), "seed", [uid])
    for query in _exploration_queries():
        if sum(row["kind"] == "explore" for row in query_rows) >= exploration_count:
            break
        append(query, "explore", [])
    if len(query_rows) < max_queries:
        warnings.append("Not enough distinct grounded queries to fill the requested query budget")
    return {
        "schema": "discovery-plan-v1", "created_at": datetime.now(UTC).isoformat(),
        "algorithm_version": ALGORITHM_VERSION, "seeds": seeds,
        "counterexamples": counterexamples, "queries": query_rows,
        "excluded_job_uids": sorted(seen),
        "excluded_urls": sorted({url for row in seen.values()
                                 for field in ("original_url", "apply_url")
                                 if (url := canonicalize_url(row["source"].get(field)))}),
        "holdout_job_uids": sorted(holdout_uids), "criteria_summary": _CRITERIA,
        "criteria_source": _CRITERIA_SOURCE, "criteria_as_of": "2026-10-16",
        "parameters": {"max_queries": max_queries,
                       "exploration_fraction": exploration_fraction},
        "warnings": warnings,
        "limitations": [
            "Local duty similarity is an uncalibrated heuristic priority, not a relevance label.",
            "Human precision and recall are unknown until independent human review.",
            "Counterexamples are recorded for review; this baseline is not a trained classifier.",
            "English lexical matching can miss paraphrases and unrecognized duty headings.",
        ],
    }


def rank_candidate(record: JobRecord, plan: dict) -> dict:
    """Return an explainable review priority using only candidate role duties."""
    if plan.get("schema") != "discovery-plan-v1" or not plan.get("seeds"):
        raise ValueError("A discovery-plan-v1 with positive seeds is required")
    duties = _duties(record)
    if not duties:
        return {"priority": 0.0, "seed_ids": [], "evidence": []}
    seeds = plan["seeds"]
    documents = [_terms("\n".join(seed["duties"])) for seed in seeds]
    frequency = Counter(term for document in documents for term in set(document))
    candidate_terms = _terms("\n".join(duties))

    def vector(terms: list[str]) -> dict[str, float]:
        return {term: (1 + math.log(count)) * (1 + math.log((len(seeds) + 1)
                                                         / (frequency[term] + 1)))
                for term, count in Counter(terms).items()}

    candidate = vector(candidate_terms)
    norm = math.sqrt(sum(value * value for value in candidate.values()))
    similarities = []
    for seed, terms in zip(seeds, documents, strict=True):
        seed_vector = vector(terms)
        seed_norm = math.sqrt(sum(value * value for value in seed_vector.values()))
        similarity = (sum(value * seed_vector.get(term, 0) for term, value in candidate.items())
                      / (norm * seed_norm) if norm and seed_norm else 0)
        similarities.append((similarity, seed))
    similarities.sort(key=lambda item: (-item[0], item[1]["job_uid"]))
    if not similarities[0][0]:
        return {"priority": 0.0, "seed_ids": [], "evidence": []}
    selected, groups = [], set()
    for similarity, seed in similarities:
        group = seed.get("seed_group", seed.get("role_label") or "general")
        if similarity >= similarities[0][0] * .5 and group not in groups:
            selected.append(seed)
            groups.add(group)
        if len(selected) == 3:
            break
    shared = set(candidate).intersection(term for seed in selected
                                         for term in _terms("\n".join(seed["duties"])))
    ranked_duties = sorted(enumerate(duties), key=lambda item: (
        -len(set(_terms(item[1])).intersection(shared)), item[0],
    ))
    evidence = [duty for _, duty in ranked_duties if set(_terms(duty)).intersection(shared)][:4]
    coverage = len(shared) / max(len(set(candidate_terms)), 1)
    priority = min(100, 85 * similarities[0][0] + 15 * coverage)
    return {"priority": round(priority, 4), "seed_ids": [seed["job_uid"] for seed in selected],
            "evidence": evidence}
