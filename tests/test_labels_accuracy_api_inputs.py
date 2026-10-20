"""Public accuracy API shares the CLI's input boundary before any analysis."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from test_frozen_evaluation import cohort
from test_labels_accuracy_outputs import reports as reports
from test_labels_cli import snapshot
from test_labels_cli import stored as stored

from job_intake.annotation import accuracy


def arguments(reports, current, variant):
    return {
        "config": current["settings"], "variant": variant,
        "gold": reports["v2"] if variant == "saved" else reports["v3"],
        "predictions": reports["predictions"] if variant == "labels" else None,
        "prices": Path(__file__).parents[1] / "config/eval_prices.yaml"
        if variant == "saved" else None,
    }


@pytest.mark.parametrize("variant", ["rules", "labels", "saved"])
@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("parent_alias", [False, True])
def test_api_refuses_absent_sqlite_companion_before_reading_sources(
    stored, reports, tmp_path, monkeypatch, variant, suffix, parent_alias,
):
    database, _, _, current = stored
    parent = tmp_path / "alias" if parent_alias else database.parent
    if parent_alias:
        parent.symlink_to(database.parent, target_is_directory=True)
    output = parent / (database.name + suffix)
    before = snapshot(database)

    def forbidden(*args, **kwargs):
        pytest.fail("Protected outputs must be refused before gold or SQLite reads")

    monkeypatch.setattr(accuracy, "_accuracy_cases", forbidden)
    with pytest.raises(ValueError, match="must not overwrite inputs"):
        accuracy.run_accuracy(**arguments(reports, current, variant), output=output)
    assert snapshot(database) == before and not output.exists()
    assert not output.with_suffix(".md").exists()


def test_api_journal_refusal_preserves_native_sqlite_read_only_availability(stored, reports):
    database, _, _, current = stored
    before = snapshot(database)
    with pytest.raises(ValueError, match="must not overwrite inputs"):
        accuracy.run_accuracy(
            **arguments(reports, current, "rules"), output=Path(str(database) + "-journal"),
        )
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert snapshot(database) == before
    assert not Path(str(database) + "-journal").exists()


@pytest.mark.parametrize("variant,field", [
    *[(variant, field) for variant in ("rules", "labels", "saved")
      for field in ("gold", "rules", "profiles")],
    ("labels", "predictions"), ("saved", "prices"),
])
def test_api_protects_all_declared_inputs_before_analysis(
    stored, reports, monkeypatch, variant, field,
):
    _, _, _, current = stored
    args = arguments(reports, current, variant)
    paths = {
        "gold": args["gold"], "rules": current["settings"].rules_path,
        "profiles": current["settings"].search_profiles_path,
        "predictions": reports["predictions"],
        "prices": Path(__file__).parents[1] / "config/eval_prices.yaml",
    }
    output = paths[field]
    before = snapshot(output)
    monkeypatch.setattr(accuracy, "_accuracy_cases", lambda *a, **k:
                        pytest.fail("API must guard inputs before analysis"))
    with pytest.raises(ValueError, match="must not overwrite inputs"):
        accuracy.run_accuracy(**args, output=output)
    assert snapshot(output) == before


def test_api_protects_an_absent_cohort_journal_before_analysis(
    stored, reports, tmp_path, monkeypatch,
):
    _, _, _, current = stored
    journal = tmp_path / "exposure.json"
    manifest = cohort()
    manifest["exposure_log"] = str(journal)
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps(manifest))
    before = snapshot(path)
    alias = tmp_path / "journal-alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(accuracy, "_accuracy_cases", lambda *a, **k:
                        pytest.fail("Journal must be protected before cohort analysis"))
    with pytest.raises(ValueError, match="must not overwrite inputs"):
        accuracy.run_accuracy(**arguments(reports, current, "rules"),
                              cohort=path, output=alias / journal.name)
    assert snapshot(path) == before and not journal.exists()


@pytest.mark.parametrize("variant", ["rules", "labels", "saved"])
def test_api_new_pair_has_the_existing_format_and_keeps_inputs(stored, reports, tmp_path, variant):
    _, _, _, current = stored
    before = {path: snapshot(path) for path in reports["inputs"]}
    output = tmp_path / f"api-{variant}.json"
    report = accuracy.run_accuracy(**arguments(reports, current, variant), output=output)
    assert json.loads(output.read_text()) == report
    assert output.with_suffix(".md").read_text() == accuracy.accuracy_markdown(report)
    assert all(snapshot(path) == value for path, value in before.items())
