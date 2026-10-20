"""Lossless gold preparation and explicit human XLSX import, without model calls.

The JSONL remains canonical. XLSX is an editable view over a selected cohort;
blank cells never erase answers and old per-profile ratings never select a role.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from job_intake.annotation.benchmark import read_jsonl
from job_intake.annotation.cohorts import write_new_text as publish_new_text
from job_intake.annotation.eval_runner import read_saved_records
from job_intake.annotation.gold import DEFAULT_PROFILE_IDS, GOLD_SCHEMA, _snapshot, validate_gold
from job_intake.annotation.text import source_hash
from job_intake.config.settings import AppConfig, load_yaml_mapping
from job_intake.crm.xlsx_reader import Cell, Sheet, read_workbook
from job_intake.profiles import load_streams

SHEET_NAME = "Разметка"
HEADER_ROW = 10
FIRST_DATA_ROW = 11
_HEADERS = {
    "job_uid": {"job_uid", "ID для переноса"},
    "source_hash": {"source_hash"},
    "role_label": {"role_label", "Метка роли"},
    "relevance": {"relevance", "Релевантность"},
    "gate_outcome": {"gate_outcome", "Условия найма", "Условия\nнайма"},
    "notes": {"notes", "Комментарий", "Комментарий\n(необязательно)"},
}
_GATES = {
    "eligible": "eligible", "ineligible": "ineligible", "unknown": "unknown",
    "Подходят": "eligible", "Не подходят": "ineligible", "Неясно": "unknown",
}


def ensure_new_outputs(outputs: list[Path]) -> None:
    """Check every destination before work starts; the writer also refuses races."""
    paths = [path.expanduser().parent.resolve() / path.expanduser().name for path in outputs]
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("Gold destinations must be distinct")
    for path in paths:
        if path.exists() or path.is_symlink():
            raise ValueError(f"Gold output already exists; choose a new path: {path}")


def write_new_text(output: Path, content: str) -> Path:
    """Gold preflight with the common path-first, identity-owned publisher."""
    output = output.expanduser()
    path = output.parent.resolve() / output.name
    ensure_new_outputs([path])
    return publish_new_text(path, content)


def jsonl_content(cases: list[dict]) -> str:
    return "".join(json.dumps(case, ensure_ascii=False, allow_nan=False) + "\n" for case in cases)


def migrate_unlabeled_v1(cases: list[dict]) -> list[dict]:
    """Convert only unanswered v1 templates; human decisions require manual review."""
    if not cases:
        raise ValueError("Cannot migrate an empty gold file")
    migrated = copy.deepcopy(cases)
    for case in migrated:
        if case.get("schema") != "vacancy-tags-v1" or "role_label" in case:
            raise ValueError("Migration accepts only vacancy-tags-v1 templates")
        ratings = case.get("relevance")
        if not isinstance(ratings, dict) or set(ratings) != set(DEFAULT_PROFILE_IDS) \
                or any(value is not None for value in ratings.values()):
            raise ValueError("Human v1 ratings require manual migration; "
                             "automatic argmax is forbidden")
        if any(case.get(field) for field in (
            "tags", "gate_outcome", "method", "labeled_at", "notes",
        )):
            raise ValueError("Human v1 labels, notes and provenance require manual migration")
        case.update(schema=GOLD_SCHEMA, role_label=None, relevance=None)
    return migrated


def migration_companion_paths(path: Path) -> tuple[Path, Path]:
    return path.with_suffix(".manifest.json"), path.with_name(path.stem + "-reading.md")


def _digest(value: object) -> str:
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _reading_body(cases: list[dict]) -> str:
    """Source-only fallback for a cohort with no historical reading document."""
    sections = []
    for number, case in enumerate(cases, 1):
        source = case["source"]
        text = source.get("description_clean") or source.get("description_raw") or ""
        metadata = {key: value for key, value in source.items()
                    if key not in ("description_clean", "description_raw")}
        # A source may itself contain Markdown fences. Use longer fences rather
        # than changing any source text or allowing it to escape its code block.
        fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", text)), default=0))
        sections.append(
            f"## {number}. {source.get('title', '')}\n\n"
            f"Объём разметки: `{case.get('label_scope', '')}` · job_uid: `{case['job_uid']}`\n\n"
            "```json\n" + json.dumps(metadata, ensure_ascii=False, indent=2)
            + f"\n```\n\n{fence}text\n{text}\n{fence}\n\n"
        )
    return "".join(sections)


def _verified_historical_reading(path: Path, cases: list[dict]) -> str:
    """Keep the original source sections and their order after checking every case."""
    content = path.read_text(encoding="utf-8")
    start = re.search(r"(?m)^## 1\. ", content)
    if start is None:
        raise ValueError("Historical reading has no numbered source sections")
    body = content[start.start():]
    sections = re.split(r"(?m)^## \d+\. ", body)[1:]
    by_uid = {case["job_uid"]: case for case in cases}
    seen = set()
    for section in sections:
        identifier = re.search(r"Объём разметки: `([^`]+)` · job_uid: `([^`]+)`", section)
        metadata = re.search(r"```json\n(.*?)\n```", section, re.S)
        text = re.search(r"```text\n(.*?)\n```", section, re.S)
        if identifier is None or metadata is None or text is None:
            raise ValueError("Historical reading has malformed source sections")
        scope, uid = identifier.groups()
        if uid not in by_uid or uid in seen or scope != by_uid[uid].get("label_scope"):
            raise ValueError("Historical reading cohort or scope differs from JSONL")
        seen.add(uid)
        source = by_uid[uid]["source"]
        facts = json.loads(metadata[1])
        expected_text = source.get("description_clean") or source.get("description_raw") or ""
        if not isinstance(facts, dict) or not set(facts) <= set(source) \
                or any(source.get(key) != value for key, value in facts.items()) \
                or text[1] != expected_text:
            raise ValueError("Historical reading source differs from canonical JSONL")
    if seen != set(by_uid):
        raise ValueError("Historical reading does not contain the complete gold cohort")
    return body


def migration_companions(source: Path, out: Path, cases: list[dict]) -> dict[Path, str]:
    """Build auditable migration provenance and blind reading, never altering originals.

    This also supports creating companions for an already migrated JSONL after
    the caller verifies its bytes match ``jsonl_content(cases)`` exactly.
    """
    original = read_jsonl(source)
    if migrate_unlabeled_v1(original) != cases:
        raise ValueError("Migration companions must match the exact migrated source cohort")
    manifest_path, reading_path = migration_companion_paths(out)
    prior_manifest, prior_reading = migration_companion_paths(source)
    body = _verified_historical_reading(prior_reading, cases) if prior_reading.exists() \
        else _reading_body(cases)
    prior_provenance = None
    if prior_manifest.exists():
        prior_provenance = json.loads(prior_manifest.read_text(encoding="utf-8"))
        if not isinstance(prior_provenance, dict) or set(prior_provenance.get("selection", {})) \
                != {case["job_uid"] for case in cases}:
            raise ValueError("Historical sampling manifest cohort differs from JSONL")

    def reference(path: Path) -> dict:
        return {"path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    baseline_fields = ("job_uid", "source_hash", "text_version", "source", "label_scope")
    baseline = [{field: case[field] for field in baseline_fields} for case in cases]
    reading = (
        "# Вакансии для человеческой разметки: одна роль и одна релевантность\n\n"
        f"{len(cases)} исходников без оценок pipeline. Метки заполняйте в `{out.name}`; "
        "протокол — docs/vacancy-gold-labeling.md.\n\n"
        "Сначала выберите одну `role_label` по основной профессии вакансии, затем одну "
        "`relevance` от 0 до 3 по критериям выбранной роли. Заполните `gate_outcome` "
        "отдельно от соответствия роли, укажите фактические `method` и `labeled_at`. "
        "Не оценивайте вакансию по остальным профессиям.\n\n"
        "Начните с `blind_decisions`; до завершения слепой части не открывайте "
        "manifest или сохранённые оценки. Источники и области разметки сохранены. "
        "Человеческие метки ещё не заполнены.\n\n" + body
    )
    manifest = {
        "schema": GOLD_SCHEMA, "manifest_type": "migration-v1-to-v2",
        "created_at": datetime.now(UTC).isoformat(), "database_access": "read_only",
        "api_calls": 0, "source_jsonl": reference(source),
        "source_sampling_manifest": reference(prior_manifest) if prior_manifest.exists() else None,
        "source_reading": reference(prior_reading) if prior_reading.exists() else None,
        "target_jsonl": {"path": str(out.resolve()), "sha256": hashlib.sha256(
            jsonl_content(cases).encode("utf-8")).hexdigest()},
        "target_reading": {"path": str(reading_path.resolve()), "sha256": hashlib.sha256(
            reading.encode("utf-8")).hexdigest()},
        "case_count": len(cases), "human_labeled_count": 0,
        "baseline_sha256": _digest(baseline),
        "ordered_job_uids_sha256": _digest([case["job_uid"] for case in cases]),
        "cohort": [{field: case[field] for field in baseline_fields if field != "source"}
                   for case in cases],
        "preserved": dict.fromkeys(baseline_fields, True),
        "reading_order": "historical" if prior_reading.exists() else "jsonl",
        "sampling_provenance": prior_provenance,
        "limitations": [
            "No resampling or model calls; no human labels were inferred.",
            "Historical sampling provenance may contain saved pipeline scores; "
            "open it only after blind labeling.",
            "Model quality remains unmeasured until human decisions are complete.",
        ],
    }
    return {manifest_path: json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            reading_path: reading}


def configured_roles(settings: AppConfig) -> list[dict[str, str]]:
    streams = load_streams(load_yaml_mapping(settings.rules_path),
                           load_yaml_mapping(settings.search_profiles_path))
    return [{"id": stream.id, "name": stream.name} for stream in streams] + [
        {"id": "other", "name": "Other / unclear"},
    ]


def validate_current_cases(
    settings: AppConfig, cases: list[dict], roles: list[dict[str, str]],
) -> None:
    """Validate templates/answers against unchanged current sources, read-only."""
    if not cases or any(not isinstance(case.get("job_uid"), str) for case in cases):
        raise ValueError("Gold must contain vacancy rows with job_uid")
    _, rows, problems = read_saved_records(
        settings.database_url, limit=None, job_uids=[case["job_uid"] for case in cases],
        include_archived=True,
    )
    if any(item["severity"] == "error" and item["code"] != "invalid_annotation_json"
           for items in problems.values() for item in items):
        raise ValueError("Gold sources contain invalid saved records")
    from job_intake.annotation.source_compat import check_source_compatibility, read_source_history

    records = dict(rows)
    history = read_source_history(settings.database_url, [
        case for case in cases if case["source_hash"] != source_hash(records[case["job_uid"]])
        and case.get("source") in (None, _snapshot(records[case["job_uid"]]))
    ])
    report = validate_gold(rows, cases, profile_ids=[role["id"] for role in roles],
                           source_history=history)
    if not report["valid"]:
        raise ValueError("Invalid gold: " + "; ".join(
            error["code"] + ": " + error["message"] for error in report["errors"]
        ))
    records = dict(rows)
    proofs = {case["job_uid"]: check_source_compatibility(
        case, records[case["job_uid"]], history=history.get(case["job_uid"]),
    ) for case in cases}
    if any(not proof["compatible"] for proof in proofs.values()):
        raise ValueError("Gold source hash is stale; review source drift before importing answers")
    if any(case.get("source") != _snapshot(records[case["job_uid"]]) and (
        proofs[case["job_uid"]]["method"] == "exact_public_hash"
    ) for case in cases):
        raise ValueError("Gold source snapshot is missing or differs from the current source")


def _sheet(path: Path) -> Sheet:
    matches = [sheet for sheet in read_workbook(path).sheets if sheet.name == SHEET_NAME]
    if len(matches) != 1:
        raise ValueError(f"Workbook must contain the {SHEET_NAME!r} sheet")
    sheet = matches[0]
    if any(cell.formula is not None for row in sheet.rows.values() for cell in row.values()):
        raise ValueError("Formulas are forbidden on the gold labeling sheet")
    return sheet


def _columns(sheet: Sheet, required: set[str]) -> dict[str, str]:
    result = {}
    for name in required:
        columns = [column for column, cell in sheet.rows.get(HEADER_ROW, {}).items()
                   if cell.value in _HEADERS[name]]
        if len(columns) != 1:
            raise ValueError(f"Expected one {name} header on row {HEADER_ROW}")
        result[name] = columns[0]
    return result


def _data_rows(sheet: Sheet, uid_column: str) -> list[tuple[int, dict[str, Cell]]]:
    rows = []
    seen = set()
    for number, row in sorted(sheet.rows.items()):
        if number < FIRST_DATA_ROW or not any(cell.value.strip() for cell in row.values()):
            continue
        uid = row.get(uid_column, Cell("")).value
        if not uid or uid != uid.strip() or uid in seen:
            raise ValueError(f"Missing, malformed or duplicate job_uid at row {number}")
        seen.add(uid)
        rows.append((number, row))
    if not rows:
        raise ValueError("Workbook has no vacancy rows")
    return rows


def workbook_data(
    cases: list[dict], roles: list[dict[str, str]], *, selection_xlsx: Path | None = None,
) -> dict:
    """Prepare source-only data for the ArtifactTool workbook builder."""
    selected = cases
    if selection_xlsx is not None:
        sheet = _sheet(selection_xlsx)
        columns = _columns(sheet, {"job_uid"})
        by_uid = {case["job_uid"]: case for case in cases}
        selected = []
        for _, row in _data_rows(sheet, columns["job_uid"]):
            uid = row[columns["job_uid"]].value
            if uid not in by_uid:
                raise ValueError("Selection workbook contains an unknown job_uid")
            selected.append(by_uid[uid])
    return {
        "schema": "vacancy-gold-workbook-v2", "gold_schema": GOLD_SCHEMA,
        "sheet_name": SHEET_NAME, "header_row": HEADER_ROW,
        "first_data_row": FIRST_DATA_ROW, "roles": copy.deepcopy(roles),
        "cases": copy.deepcopy(selected),
    }


def import_workbook(
    cases: list[dict], workbook: Path, roles: list[dict[str, str]], *,
    method: str | None = None, labeled_at: str | None = None,
) -> tuple[list[dict], int]:
    """Merge explicit answers by UID; blanks preserve canonical values and provenance."""
    sheet = _sheet(workbook)
    columns = _columns(sheet, set(_HEADERS))
    result = copy.deepcopy(cases)
    by_uid = {case["job_uid"]: case for case in result}
    role_names = {role["id"]: role["id"] for role in roles}
    for role in roles:
        if role["name"] in role_names and role_names[role["name"]] != role["id"]:
            raise ValueError("Role display names are ambiguous; use distinct names")
        role_names[role["name"]] = role["id"]
    changed = 0
    for number, row in _data_rows(sheet, columns["job_uid"]):
        uid = row[columns["job_uid"]].value
        if uid not in by_uid:
            raise ValueError(f"Unknown job_uid at row {number}")
        case = by_uid[uid]
        if row.get(columns["source_hash"], Cell("")).value != case["source_hash"]:
            raise ValueError(f"Missing or mismatched source_hash at row {number}")
        updates = {}
        for field in ("role_label", "relevance", "gate_outcome", "notes"):
            value = row.get(columns[field], Cell("")).value
            if not value.strip():
                continue
            if field == "role_label":
                if value not in role_names:
                    raise ValueError(f"Unknown role_label at row {number}")
                value = role_names[value]
            elif field == "relevance":
                if value not in ("0", "1", "2", "3"):
                    raise ValueError(f"relevance must be an integer 0–3 at row {number}")
                value = int(value)
            elif field == "gate_outcome":
                if value not in _GATES:
                    raise ValueError(f"Unknown gate_outcome at row {number}")
                value = _GATES[value]
            old = case[field]
            if old not in (None, "") and old != value:
                raise ValueError(f"Conflicting existing {field} answer at row {number}")
            if old != value:
                updates[field] = value
        if not updates:
            continue
        if method not in ("blind", "assisted") or not labeled_at:
            raise ValueError("New human answers require explicit --method and --labeled-at")
        for field, value in (("method", method), ("labeled_at", labeled_at)):
            if case[field] is not None and case[field] != value:
                raise ValueError(f"Conflicting existing {field} provenance at row {number}")
        case.update(updates, method=method, labeled_at=labeled_at)
        changed += 1
    return result, changed
