"""Gold migration and XLSX answers preserve human work and source identity."""

from __future__ import annotations

import copy
import hashlib
import json
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from typer.testing import CliRunner

from job_intake import cli
from job_intake.annotation import cohorts
from job_intake.annotation import gold_workbook as gold
from job_intake.annotation.gold import DEFAULT_PROFILE_IDS, _snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.crm.xlsx_reader import MAIN_NS, PACKAGE_REL_NS, REL_NS, read_workbook
from job_intake.models.job import JobRecord

ROLES = [{"id": profile, "name": profile.replace("-", " ").title()}
         for profile in DEFAULT_PROFILE_IDS] + [{"id": "other", "name": "Other / unclear"}]
HEADERS = {
    "D": "Метка роли", "E": "Релевантность", "F": "Условия найма", "G": "Комментарий",
    "K": "job_uid", "L": "source_hash",
}


@pytest.fixture
def dataset():
    records = []
    cases = []
    for index in range(3):
        record = JobRecord(
            source="test", company="Example", title="Data Science Manager",
            original_url=f"https://example.test/{index}",
            description_clean=f"You will lead projects for team {index}.",
        )
        uid = f"uid-{index}"
        records.append((uid, record))
        cases.append({
            "schema": "vacancy-tags-v2", "job_uid": uid, "source_hash": source_hash(record),
            "text_version": TEXT_VERSION, "source": _snapshot(record),
            "label_scope": "blind_decisions", "labeled_at": None, "method": None,
            "tags": {}, "role_label": None, "relevance": None, "gate_outcome": None,
            "notes": "",
        })
    return records, cases


def workbook(tmp_path, cases, *, answers=None, formulas=None, headers=None):
    """Minimal OOXML fixture only; production authoring uses ArtifactTool."""
    answers = answers or {}
    formulas = formulas or {}
    path = tmp_path / "labels.xlsx"

    def tag(name):
        return f"{{{MAIN_NS}}}{name}"

    doc = ET.Element(tag("workbook"))
    sheets = ET.SubElement(doc, tag("sheets"))
    ET.SubElement(sheets, tag("sheet"), {
        "name": "Разметка", f"{{{REL_NS}}}id": "sheet1",
    })
    relations = ET.Element(f"{{{PACKAGE_REL_NS}}}Relationships")
    ET.SubElement(relations, f"{{{PACKAGE_REL_NS}}}Relationship", {
        "Id": "sheet1", "Target": "worksheets/sheet1.xml", "Type": f"{REL_NS}/worksheet",
    })
    sheet = ET.Element(tag("worksheet"))
    data = ET.SubElement(sheet, tag("sheetData"))
    values = {10: dict(HEADERS if headers is None else headers)}
    for number, case in enumerate(cases, 11):
        values[number] = {"A": number - 10, "K": case["job_uid"], "L": case["source_hash"],
                          **answers.get(number, {})}
    for number, columns in sorted(values.items()):
        row = ET.SubElement(data, tag("row"), {"r": str(number)})
        for column, value in columns.items():
            ref = f"{column}{number}"
            cell = ET.SubElement(row, tag("c"), {"r": ref})
            if ref in formulas:
                formula = ET.SubElement(cell, tag("f"), {"t": "shared", "si": "0"})
                formula.text = formulas[ref]
                ET.SubElement(cell, tag("v")).text = str(value)
            elif isinstance(value, str):
                cell.set("t", "inlineStr")
                ET.SubElement(ET.SubElement(cell, tag("is")), tag("t")).text = value
            elif value is not None:
                ET.SubElement(cell, tag("v")).text = str(value)
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", ET.tostring(doc))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(relations))
        archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(sheet))
    return path


def legacy(cases):
    result = copy.deepcopy(cases)
    for case in result:
        case["schema"] = "vacancy-tags-v1"
        case.pop("role_label")
        case["relevance"] = dict.fromkeys(DEFAULT_PROFILE_IDS)
    return result


def test_migrate_unanswered_cohort_preserves_order_source_and_original(dataset):
    _, cases = dataset
    original = legacy(cases)
    before = copy.deepcopy(original)
    assert gold.migrate_unlabeled_v1(original) == cases
    assert original == before


@pytest.mark.parametrize("field,value", [
    ("relevance", {**dict.fromkeys(DEFAULT_PROFILE_IDS), "product-management": 0}),
    ("relevance", {"product-management": None}),
    ("tags", {"work_mode": {"value": "unspecified", "quote": ""}}),
    ("gate_outcome", "unknown"), ("notes", "Human work"), ("method", "blind"),
    ("labeled_at", "2026-10-17"), ("schema", "unexpected"),
])
def test_migration_refuses_human_work_or_unknown_contract(dataset, field, value):
    _, cases = dataset
    cases = legacy(cases)
    cases[0][field] = value
    before = copy.deepcopy(cases)
    with pytest.raises(ValueError):
        gold.migrate_unlabeled_v1(cases)
    assert cases == before


def test_blank_roundtrip_preserves_all_rows_tags_and_provenance(dataset, tmp_path):
    _, cases = dataset
    cases[2].update(role_label="other", relevance=0, gate_outcome="unknown",
                    method="blind", labeled_at="2026-10-17", notes="Existing answer")
    path = workbook(tmp_path, cases[:2])
    merged, changed = gold.import_workbook(cases, path, ROLES)
    assert merged == cases
    assert changed == 0


def test_import_reordered_zero_answer_updates_only_matching_uid(dataset, tmp_path):
    _, cases = dataset
    before = copy.deepcopy(cases)
    path = workbook(tmp_path, [cases[1], cases[0]], answers={11: {
        "D": ROLES[3]["name"], "E": 0, "F": "Неясно", "G": "Read the source & confirmed",
    }})
    merged, changed = gold.import_workbook(cases, path, ROLES,
                                          method="blind", labeled_at="2026-10-17")
    assert changed == 1
    assert merged[0] == cases[0] and merged[2] == cases[2]
    assert merged[1] == {**cases[1], "role_label": "data-science-management", "relevance": 0,
                         "gate_outcome": "unknown", "notes": "Read the source & confirmed",
                         "method": "blind", "labeled_at": "2026-10-17"}
    assert cases == before


@pytest.mark.parametrize("formula", ["1+1", "", None])
def test_formula_with_cached_value_including_empty_shared_formula_is_rejected(
    dataset, tmp_path, formula,
):
    _, cases = dataset
    path = workbook(tmp_path, cases, answers={11: {"E": 2}}, formulas={"E11": formula})
    assert read_workbook(path).sheets[0].rows[11]["E"].formula is not None
    with pytest.raises(ValueError, match="Formulas"):
        gold.import_workbook(cases, path, ROLES, method="blind", labeled_at="2026-10-17")


@pytest.mark.parametrize("answer,error", [
    ({"K": ""}, "job_uid"), ({"K": " uid-0"}, "job_uid"),
    ({"K": "unknown"}, "Unknown job_uid"), ({"L": "bad"}, "source_hash"),
    ({"L": ""}, "source_hash"), ({"D": "invented"}, "role_label"),
    ({"E": "0.0"}, "integer"), ({"E": 4}, "integer"),
    ({"F": "maybe"}, "gate_outcome"),
])
def test_invalid_identity_hash_and_answers_fail_without_changing_gold(
    dataset, tmp_path, answer, error,
):
    _, cases = dataset
    before = copy.deepcopy(cases)
    path = workbook(tmp_path, cases, answers={11: answer})
    with pytest.raises(ValueError, match=error):
        gold.import_workbook(cases, path, ROLES, method="blind", labeled_at="2026-10-17")
    assert cases == before


def test_duplicate_uid_and_missing_or_duplicate_header_are_rejected(dataset, tmp_path):
    _, cases = dataset
    path = workbook(tmp_path, [cases[0], cases[0]])
    with pytest.raises(ValueError, match="duplicate job_uid"):
        gold.import_workbook(cases, path, ROLES)
    for headers in ({k: v for k, v in HEADERS.items() if k != "D"},
                    {**HEADERS, "M": "role_label"}):
        path = workbook(tmp_path, cases, headers=headers)
        with pytest.raises(ValueError, match="role_label header"):
            gold.import_workbook(cases, path, ROLES)


def test_existing_answer_cannot_be_overwritten_and_blank_does_not_erase(dataset, tmp_path):
    _, cases = dataset
    cases[0].update(role_label="other", relevance=0, method="blind", labeled_at="2026-10-17")
    path = workbook(tmp_path, cases, answers={11: {"E": 1}})
    with pytest.raises(ValueError, match="Conflicting existing relevance"):
        gold.import_workbook(cases, path, ROLES, method="blind", labeled_at="2026-10-17")
    path = workbook(tmp_path, cases, answers={11: {"E": 0}})
    assert gold.import_workbook(cases, path, ROLES) == (cases, 0)


def test_new_answers_need_explicit_human_provenance(dataset, tmp_path):
    _, cases = dataset
    path = workbook(tmp_path, cases, answers={11: {"E": 0}})
    with pytest.raises(ValueError, match="explicit --method and --labeled-at"):
        gold.import_workbook(cases, path, ROLES)


def test_workbook_payload_uses_existing_selection_order_not_jsonl_order(dataset, tmp_path):
    _, cases = dataset
    path = workbook(tmp_path, [cases[2], cases[0]], headers={"K": "ID для переноса"})
    payload = gold.workbook_data(cases, ROLES, selection_xlsx=path)
    assert payload["cases"] == [cases[2], cases[0]]
    assert payload["header_row"] == 10 and payload["first_data_row"] == 11
    assert payload["roles"] == ROLES


def test_import_finds_identity_by_header_after_columns_move(dataset, tmp_path):
    _, cases = dataset
    headers = {key: value for key, value in HEADERS.items() if key not in ("K", "L")}
    path = workbook(tmp_path, cases, headers={**headers, "I": "job_uid", "J": "source_hash"},
                    answers={number: {"I": case["job_uid"], "J": case["source_hash"]}
                             for number, case in enumerate(cases, 11)})
    assert gold.import_workbook(cases, path, ROLES) == (cases, 0)


@pytest.fixture
def cli_context(tmp_path, monkeypatch, dataset):
    records, cases = dataset
    config = tmp_path / "config" / "settings.yaml"
    config.parent.mkdir()
    config.write_text("protected config")
    database = tmp_path / "jobs.db"
    database.write_bytes(b"protected database")
    settings = AppConfig(
        database_url=f"sqlite:///{database}", log_level="INFO", sources=[],
        rules_path=config.parent / "rules.yaml",
        search_profiles_path=config.parent / "profiles.yaml",
        company_watchlist_path=config.parent / "companies.yaml", export_dir=tmp_path,
        telegram=TelegramConfig(), llm=LLMConfig(),
    )
    monkeypatch.setattr(cli, "load_app_config", lambda _: settings)
    monkeypatch.setattr(gold, "configured_roles", lambda _: ROLES)
    monkeypatch.setattr(gold, "read_saved_records", lambda *a, **kw: (database, records, {}))
    source = tmp_path / "v1.jsonl"
    source.write_text(gold.jsonl_content(legacy(cases)))
    canonical = tmp_path / "v2.jsonl"
    canonical.write_text(gold.jsonl_content(cases))
    return config, database, source, canonical, cases


def test_cli_migrate_and_import_are_read_only_and_never_clobber(cli_context, tmp_path):
    config, database, source, canonical, cases = cli_context
    original = source.read_bytes(), canonical.read_bytes(), database.read_bytes()
    out = tmp_path / "migrated.jsonl"
    args = ["gold", "migrate", "--config", str(config), "--source", str(source), "--out", str(out)]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert [json.loads(line) for line in out.read_text().splitlines()] == cases
    assert CliRunner().invoke(cli.app, args).exit_code == 2
    path = workbook(tmp_path, cases, answers={11: {"E": 0}})
    imported = tmp_path / "imported.jsonl"
    result = CliRunner().invoke(cli.app, [
        "gold", "import-xlsx", "--config", str(config), "--workbook", str(path),
        "--gold", str(canonical), "--out", str(imported),
        "--method", "blind", "--labeled-at", "2026-10-17",
    ])
    assert result.exit_code == 0, result.output
    assert json.loads(imported.read_text().splitlines()[0])["relevance"] == 0
    assert original == (source.read_bytes(), canonical.read_bytes(), database.read_bytes())


@pytest.mark.parametrize("target", ["database", "canonical", "existing"])
def test_cli_input_collisions_and_existing_destinations_fail_before_reads(
    cli_context, tmp_path, monkeypatch, target,
):
    config, database, _, canonical, _ = cli_context
    existing = tmp_path / "existing.jsonl"
    existing.write_text("preserved human work")
    out = {"database": database, "canonical": canonical, "existing": existing}[target]
    before = out.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("Must fail before opening the source database")

    monkeypatch.setattr(gold, "read_saved_records", forbidden)
    result = CliRunner().invoke(cli.app, [
        "gold", "import-xlsx", "--config", str(config), "--workbook", "unused.xlsx",
        "--gold", str(canonical), "--out", str(out),
    ])
    assert result.exit_code == 2, result.output
    assert out.read_bytes() == before


def test_import_rejects_stale_source_and_invalid_label_date(cli_context, tmp_path, monkeypatch):
    config, database, _, canonical, cases = cli_context
    path = workbook(tmp_path, cases, answers={11: {"E": 0}})
    args = ["gold", "import-xlsx", "--config", str(config), "--workbook", str(path),
            "--gold", str(canonical), "--out", str(tmp_path / "out.jsonl"),
            "--method", "blind", "--labeled-at", "yesterday"]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 2 and "invalid_labeled_at" in result.output
    assert not (tmp_path / "out.jsonl").exists()
    record = JobRecord(**cases[0]["source"])
    record.description_clean += " Changed source."
    records = [(case["job_uid"], record) for case in cases]
    monkeypatch.setattr(gold, "read_saved_records", lambda *a, **kw: (database, records, {}))
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 2 and "stale" in result.output
    assert not (tmp_path / "out.jsonl").exists()


@pytest.mark.parametrize("mutation", ["tampered", "missing"])
def test_payload_and_import_reject_inconsistent_snapshot_even_with_current_hash(
    cli_context, tmp_path, mutation,
):
    config, _, _, canonical, cases = cli_context
    if mutation == "tampered":
        cases[0]["source"]["description_clean"] = "Accidentally edited source text."
    else:
        cases[0].pop("source")
    canonical.write_text(gold.jsonl_content(cases))
    path = workbook(tmp_path, cases)
    for command, args in (
        ("workbook-data", []), ("import-xlsx", ["--workbook", str(path)]),
    ):
        out = tmp_path / f"{command}.json"
        result = CliRunner().invoke(cli.app, [
            "gold", command, "--config", str(config), "--gold", str(canonical),
            "--out", str(out), *args,
        ])
        assert result.exit_code == 2 and "snapshot" in result.output
        assert not out.exists()


@pytest.mark.parametrize("suffix", [".jsonl", ".manifest.json"])
def test_gold_sample_refuses_existing_gold_or_manifest_before_sampling(
    cli_context, tmp_path, monkeypatch, suffix,
):
    config, _, _, _, _ = cli_context
    out = tmp_path / "sample.jsonl"
    protected = out.with_suffix(suffix)
    protected.write_text("human labels or sampling provenance")

    def forbidden(*args, **kwargs):
        raise AssertionError("Must fail before resampling")

    monkeypatch.setattr("job_intake.annotation.gold.sample_gold", forbidden)
    result = CliRunner().invoke(cli.app, [
        "gold", "sample", "--config", str(config), "--out", str(out),
    ])
    assert result.exit_code == 2 and "already exists" in result.output
    assert protected.read_text() == "human labels or sampling provenance"


def test_atomic_create_refuses_existing_output_without_temp_files(tmp_path):
    path = tmp_path / "gold.jsonl"
    gold.write_new_text(path, "human labels")
    with pytest.raises(ValueError, match="already exists"):
        gold.write_new_text(path, "replacement")
    assert path.read_text() == "human labels"
    assert sorted(tmp_path.iterdir()) == [path]


def test_atomic_create_race_preserves_competing_output_and_cleans_temp_files(tmp_path, monkeypatch):
    path = tmp_path / "gold.jsonl"

    def competing_writer(source, destination):
        destination.write_text("competing human labels")
        raise FileExistsError("Created after preflight")

    monkeypatch.setattr(cohorts.os, "link", competing_writer)
    with pytest.raises(FileExistsError):
        gold.write_new_text(path, "new data")
    assert path.read_text() == "competing human labels"
    assert sorted(tmp_path.iterdir()) == [path]


def historical_reading(cases):
    return "# Historical blind reading\n\n" + "".join(
        f"## {number}. {case['source']['title']}\n\n"
        f"Объём разметки: `{case['label_scope']}` · job_uid: `{case['job_uid']}`\n\n"
        "```json\n" + json.dumps({"company": case["source"]["company"]})
        + "\n```\n\n```text\n" + case["source"]["description_clean"] + "\n```\n\n"
        for number, case in enumerate(cases, 1)
    )


def test_migration_companions_preserve_sampling_provenance_and_blind_source_order(
    cli_context, tmp_path,
):
    _, _, source, _, cases = cli_context
    old_manifest, old_reading = gold.migration_companion_paths(source)
    provenance = {"schema": "vacancy-tags-v1", "created_at": "2026-10-13T15:00:00Z",
                  "selection": {case["job_uid"]: {"saved_fit_score": "SECRET_OLD_SCORE"}
                                for case in cases}}
    old_manifest.write_text(json.dumps(provenance))
    original_reading = historical_reading(list(reversed(cases)))
    old_reading.write_text(original_reading)
    originals = source.read_bytes(), old_manifest.read_bytes(), old_reading.read_bytes()
    out = tmp_path / "migrated.jsonl"
    new_manifest, new_reading = gold.migration_companion_paths(out)
    companions = gold.migration_companions(source, out, cases)
    manifest = json.loads(companions[new_manifest])
    assert manifest["sampling_provenance"] == provenance
    assert manifest["source_sampling_manifest"]["sha256"] == hashlib.sha256(
        old_manifest.read_bytes()).hexdigest()
    assert manifest["target_jsonl"]["sha256"] == hashlib.sha256(
        gold.jsonl_content(cases).encode()).hexdigest()
    assert manifest["case_count"] == 3 and manifest["human_labeled_count"] == 0
    assert manifest["api_calls"] == 0 and manifest["database_access"] == "read_only"
    assert [row["job_uid"] for row in manifest["cohort"]] == [case["job_uid"] for case in cases]
    reading = companions[new_reading]
    assert reading[reading.index("## 1."):] == original_reading[original_reading.index("## 1."):]
    assert "role_label" in reading and "relevance" in reading
    assert "SECRET_OLD_SCORE" not in reading and "saved_fit_score" not in reading
    assert originals == (source.read_bytes(), old_manifest.read_bytes(), old_reading.read_bytes())


@pytest.mark.parametrize("companion", ["manifest", "reading"])
def test_migration_preflights_all_companions_before_source_database_reads(
    cli_context, tmp_path, monkeypatch, companion,
):
    config, _, source, _, _ = cli_context
    out = tmp_path / "new.jsonl"
    paths = gold.migration_companion_paths(out)
    protected = paths[0 if companion == "manifest" else 1]
    protected.write_text("preserved artifact")

    def forbidden(*args, **kwargs):
        raise AssertionError("Must fail before reading source database")

    monkeypatch.setattr(gold, "read_saved_records", forbidden)
    result = CliRunner().invoke(cli.app, [
        "gold", "migrate", "--config", str(config), "--source", str(source), "--out", str(out),
    ])
    assert result.exit_code == 2 and "already exists" in result.output
    assert not out.exists() and protected.read_text() == "preserved artifact"


@pytest.mark.parametrize("tamper", ["cohort", "source", "scope"])
def test_inconsistent_historical_reading_or_provenance_blocks_migration(
    cli_context, tmp_path, tamper,
):
    config, _, source, _, cases = cli_context
    prior_manifest, prior_reading = gold.migration_companion_paths(source)
    prior_manifest.write_text(json.dumps({"selection": {case["job_uid"]: {} for case in cases}}))
    text = historical_reading(cases)
    if tamper == "cohort":
        prior_manifest.write_text(json.dumps({"selection": {"unrelated": {}}}))
    elif tamper == "source":
        text = text.replace("You will lead projects for team 0.", "Changed source.")
    else:
        text = text.replace("`blind_decisions`", "`constraints`", 1)
    prior_reading.write_text(text)
    out = tmp_path / "new.jsonl"
    result = CliRunner().invoke(cli.app, [
        "gold", "migrate", "--config", str(config), "--source", str(source), "--out", str(out),
    ])
    assert result.exit_code == 2, result.output
    assert not out.exists()
    assert not any(path.exists() for path in gold.migration_companion_paths(out))


@pytest.mark.parametrize("command", ["migrate", "workbook-data", "import-xlsx"])
def test_gold_command_help_is_available_without_opening_database(command):
    result = CliRunner().invoke(cli.app, ["gold", command, "--help"])
    assert result.exit_code == 0, result.output
    assert "--config" in result.output
