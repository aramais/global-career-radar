from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from job_intake.annotation.rules_diff import run_rules_diff, write_rules_diff_report
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig

PROJECT = Path(__file__).resolve().parents[1]


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args], check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _commit(repository: Path) -> str:
    _git(repository, "add", "src", "config")
    _git(repository, "-c", "user.name=Test", "-c", "user.email=test@example.test",
         "commit", "-m", "Offline deterministic baseline")
    return _git(repository, "rev-parse", "HEAD")


@pytest.fixture
def policy_repository(tmp_path):
    repository = tmp_path / "policy"
    repository.mkdir()
    shutil.copytree(
        PROJECT / "src", repository / "src", ignore=shutil.ignore_patterns("__pycache__"),
    )
    (repository / "config").mkdir()
    rules = {
        "positive_title_signals": ["Analytics Manager"],
        "positive_description_signals": [], "target_geographies": ["Brazil"],
        "required_languages": ["English"], "recall_first": True,
    }
    profiles = {
        "streams": [{"id": "analytics", "name": "Analytics", "scoring": {
            "title_weights": {"Analytics Manager": 10}, "threshold_a": 14, "threshold_b": 8,
        }}],
    }
    (repository / "config/rules.yaml").write_text(yaml.safe_dump(rules))
    (repository / "config/search_profiles.yaml").write_text(yaml.safe_dump(profiles))
    _git(repository, "init", "-b", "main")
    commit = _commit(repository)
    database = repository / "jobs.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE jobs (job_uid TEXT PRIMARY KEY, source TEXT, company TEXT, "
            "title TEXT, original_url TEXT, description_raw TEXT, description_clean TEXT, "
            "source_metadata TEXT, annotation TEXT, updated_at TEXT, archived_at TEXT, "
            "status TEXT, location_text TEXT, remote_text TEXT)"
        )
        for uid, archived in [("stable-job", None), ("archived-job", "2026-10-12")]:
            connection.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, "test", "Example", "Analytics Manager", "https://example.test/job",
                 "", "Lead projects with business teams.",
                 json.dumps({"working_language": "English"}), "{}", "2026-10-13", archived,
                 "open", "Brazil", "Remote"),
            )
    config = AppConfig(
        database_url=f"sqlite:///{database}", log_level="INFO", sources=[],
        rules_path=repository / "config/rules.yaml",
        search_profiles_path=repository / "config/search_profiles.yaml",
        company_watchlist_path=repository / "config/company_watchlist.yaml",
        export_dir=tmp_path, telegram=TelegramConfig(), llm=LLMConfig(),
    )
    return repository, config, commit


def _run(fixture, baseline=None):
    repository, config, commit = fixture
    return run_rules_diff(config, baseline=baseline or commit, repository=repository)


def test_identical_git_revision_has_parity_and_keeps_database_read_only(policy_repository):
    repository, config, commit = policy_repository
    database = repository / "jobs.db"
    before = database.read_bytes()
    files_before = {str(path): path.read_bytes() for path in (repository / "src").rglob("*.py")}
    report = _run(policy_repository)
    assert report["changes"] == report["flips"] == []
    assert report["counts"]["evaluations_before"] == report["counts"]["evaluations_after"] == 1
    assert report["baseline"]["commit"] == commit
    assert report["baseline"]["code_fingerprint"] == report["current"]["code_fingerprint"]
    assert report["baseline"]["config_fingerprints"] == report["current"]["config_fingerprints"]
    assert report["baseline"]["evaluations"] == report["current"]["evaluations"]
    assert report["api_calls"] == 0 and report["database_access"] == "read_only"
    assert report["source_snapshot"]["jobs"] == 1
    assert database.read_bytes() == before
    assert files_before == {
        str(path): path.read_bytes() for path in (repository / "src").rglob("*.py")
    }
    assert config.rules_path.read_text().startswith("positive_description_signals:")


def test_git_baseline_uses_its_scorer_code_instead_of_current_code(policy_repository):
    repository, _, _ = policy_repository
    scorer = repository / "src/job_intake/scoring/pre_score.py"
    scorer.write_text(scorer.read_text().replace(
        "score += self._source_weight(source)", "score += self._source_weight(source) + 12.0",
    ))
    report = _run(policy_repository)
    assert report["counts"]["score_changes"] == report["counts"]["tier_flips"] == 1
    change = report["flips"][0]
    assert change["job_uid"] == "stable-job" and change["profile_id"] == "analytics"
    assert change["before"]["score"] == 10 and change["before"]["tier"] == "B"
    assert change["after"]["score"] == 22 and change["after"]["tier"] == "A"
    assert report["baseline"]["code_fingerprint"] != report["current"]["code_fingerprint"]
    assert report["counts"]["decision_flips"] == 0


def test_git_baseline_uses_its_rules_and_reports_blocker_reasons(policy_repository):
    _, config, _ = policy_repository
    rules = yaml.safe_load(config.rules_path.read_text())
    rules["company_blacklist"] = ["Example"]
    config.rules_path.write_text(yaml.safe_dump(rules))
    report = _run(policy_repository)
    assert report["counts"]["decision_flips"] == report["counts"]["tier_flips"] == 1
    change = report["flips"][0]
    assert change["before"]["decision"] == "pass"
    assert change["after"]["decision"] == "reject"
    assert change["after"]["score"] == 0
    assert "company_blacklist:Example" in change["after"]["blocker_signals"]
    assert "blocker_signals" in change["changed_fields"]
    assert report["decision_tier_transitions"] == {"pass/B → reject/C": 1}


def test_git_baseline_uses_its_profile_thresholds(policy_repository):
    _, config, _ = policy_repository
    profiles = yaml.safe_load(config.search_profiles_path.read_text())
    profiles["streams"][0]["scoring"].update(threshold_a=40, threshold_b=30)
    config.search_profiles_path.write_text(yaml.safe_dump(profiles))
    report = _run(policy_repository)
    assert report["flips"][0]["before"]["tier"] == "B"
    assert report["flips"][0]["after"]["tier"] == "C"
    assert report["counts"]["score_changes"] == 0
    assert report["baseline"]["profiles"] != report["current"]["profiles"]


def test_changed_profile_versions_without_changed_outcomes_are_not_flips(policy_repository):
    _, config, _ = policy_repository
    profiles = yaml.safe_load(config.search_profiles_path.read_text())
    profiles["streams"][0]["context"] = "Metadata-only context change"
    config.search_profiles_path.write_text(yaml.safe_dump(profiles))
    report = _run(policy_repository)
    assert report["baseline"]["profiles"] != report["current"]["profiles"]
    assert report["changes"] == []


def test_added_profiles_are_reported_without_losing_stable_job_ids(policy_repository):
    _, config, _ = policy_repository
    profiles = yaml.safe_load(config.search_profiles_path.read_text())
    profiles["streams"].append({"id": "second", "scoring": {"threshold_a": 20, "threshold_b": 8}})
    config.search_profiles_path.write_text(yaml.safe_dump(profiles))
    report = _run(policy_repository)
    assert report["counts"]["profiles_added"] == ["second"]
    assert report["current"]["policy"] == "single-role-v1"
    assert len(report["current"]["evaluations"]) == 1
    assert report["current"]["evaluations"][0]["job_uid"] == "stable-job"
    assert report["current"]["evaluations"][0]["profile_id"] == "other"
    assert all(change["profile_id"] != "second" for change in report["changes"])
    assert report["counts"]["decision_flips"] == 0


def test_rules_diff_report_is_a_reusable_snapshot_on_exact_same_inputs(policy_repository, tmp_path):
    report = _run(policy_repository)
    snapshot = tmp_path / "baseline.json"
    snapshot.write_text(json.dumps(report))
    _, config, _ = policy_repository
    profiles = yaml.safe_load(config.search_profiles_path.read_text())
    profiles["streams"][0]["scoring"]["title_weights"]["Analytics Manager"] = 20
    config.search_profiles_path.write_text(yaml.safe_dump(profiles))
    comparison = _run(policy_repository, str(snapshot))
    assert comparison["baseline"]["kind"] == "snapshot"
    assert comparison["flips"][0]["before"]["score"] == 10
    assert comparison["flips"][0]["after"]["score"] == 20


def test_snapshot_does_not_compare_different_source_inputs(policy_repository, tmp_path):
    report = _run(policy_repository)
    snapshot = tmp_path / "baseline.json"
    snapshot.write_text(json.dumps(report))
    repository, _, _ = policy_repository
    with sqlite3.connect(repository / "jobs.db") as connection:
        connection.execute("UPDATE jobs SET description_clean='Changed source'")
    with pytest.raises(ValueError, match="same saved"):
        _run(policy_repository, str(snapshot))


@pytest.mark.parametrize("ref", [
    "--help", "HEAD; touch marker", "HEAD\nmain", "HEAD:config/rules.yaml",
])
def test_git_refs_are_validated_without_shell_execution(policy_repository, ref):
    with pytest.raises(ValueError, match="safe Git"):
        _run(policy_repository, ref)


@pytest.mark.parametrize("code", [
    'import socket\nsocket.getaddrinfo("example.test", 443)\n',
    'import sqlite3\nsqlite3.connect("side-effect.db")\n',
    'import subprocess\nsubprocess.run(["true"])\n',
    'open(".env").read()\n',
])
def test_baseline_worker_blocks_network_database_subprocess_and_dotenv(policy_repository, code):
    repository, _, _ = policy_repository
    initialization = repository / "src/job_intake/__init__.py"
    initialization.write_text(code + initialization.read_text())
    commit = _commit(repository)
    with pytest.raises(ValueError, match="PermissionError"):
        _run(policy_repository, commit)
    assert not (repository / "side-effect.db").exists()


def test_workers_do_not_inherit_api_keys_or_initialize_clients(policy_repository, monkeypatch):
    repository, _, _ = policy_repository
    monkeypatch.setenv("GEMINI_API_KEY", "private-sentinel-key")
    initialization = repository / "src/job_intake/__init__.py"
    initialization.write_text(
        'import os\nassert not os.environ.get("GEMINI_API_KEY")\n'
        + initialization.read_text(),
    )
    ai = repository / "src/job_intake/annotation/ai.py"
    content = ai.read_text()
    # Merely importing historical AI definitions is necessary for saved-fact
    # constraints, but creating a model client is forbidden in this evaluation.
    content += (
        '\ndef forbidden_client(*args, **kwargs):\n'
        '    raise AssertionError("Model client initialized")\n'
        'AnnotationModelClient.__init__ = forbidden_client\n'
    )
    ai.write_text(content)
    commit = _commit(repository)
    report = _run(policy_repository, commit)
    assert report["changes"] == []
    assert "private-sentinel-key" not in json.dumps(report)


def test_missing_database_is_not_created(policy_repository):
    repository, config, commit = policy_repository
    missing = repository / "missing.db"
    config.database_url = f"sqlite:///{missing}"
    with pytest.raises(ValueError, match="will not create"):
        run_rules_diff(config, baseline=commit, repository=repository)
    assert not missing.exists()


def test_symlinked_baseline_code_is_rejected(policy_repository):
    repository, _, _ = policy_repository
    scorer = repository / "src/job_intake/scoring/pre_score.py"
    scorer.unlink()
    scorer.symlink_to("tiering.py")
    commit = _commit(repository)
    with pytest.raises(ValueError, match="regular files"):
        _run(policy_repository, commit)


def test_source_snapshot_fingerprint_covers_saved_annotation_facts(policy_repository):
    initial = _run(policy_repository)
    repository, _, _ = policy_repository
    with sqlite3.connect(repository / "jobs.db") as connection:
        connection.execute("UPDATE jobs SET annotation=?", (json.dumps({"status": "partial"}),))
    changed = _run(policy_repository)
    assert initial["source_snapshot"]["fingerprint"] != changed["source_snapshot"]["fingerprint"]
    assert len(initial["source_snapshot"]["fingerprint"]) == len(hashlib.sha256().hexdigest())


def test_json_and_markdown_report_preserve_flip_details_and_protect_inputs(
    policy_repository, tmp_path,
):
    repository, config, _ = policy_repository
    rules = yaml.safe_load(config.rules_path.read_text())
    rules["company_blacklist"] = ["Example"]
    config.rules_path.write_text(yaml.safe_dump(rules))
    report = _run(policy_repository)
    destination = tmp_path / "drift.json"
    protected = [repository / "jobs.db", config.rules_path, config.search_profiles_path]
    json_path, markdown_path = write_rules_diff_report(
        report, destination, protected_paths=protected,
    )
    assert json.loads(json_path.read_text())["flips"] == report["flips"]
    markdown = markdown_path.read_text()
    assert "stable-job" in markdown and "analytics" in markdown
    assert "pass/B" in markdown and "reject/C" in markdown
    assert "company_blacklist:Example" in markdown
    assert "API calls: 0; SQLite: read-only" in markdown
    assert not list(tmp_path.glob("*.tmp"))
    before = config.rules_path.read_bytes()
    with pytest.raises(ValueError, match="must not overwrite"):
        write_rules_diff_report(report, config.rules_path, protected_paths=protected)
    assert config.rules_path.read_bytes() == before
    with pytest.raises(ValueError, match="must not overwrite"):
        write_rules_diff_report(report, destination.with_suffix(".md"), protected_paths=[])


def test_markdown_output_collision_is_checked_before_json_is_written(policy_repository, tmp_path):
    report = _run(policy_repository)
    output = tmp_path / "drift.json"
    markdown = output.with_suffix(".md")
    markdown.write_text("Input evidence")
    with pytest.raises(ValueError, match="must not overwrite"):
        write_rules_diff_report(report, output, protected_paths=[markdown])
    assert not output.exists() and markdown.read_text() == "Input evidence"


def test_snapshot_rejects_missing_vacancy_profile_outcomes(policy_repository, tmp_path):
    report = _run(policy_repository)
    report["current"]["evaluations"] = []
    snapshot = tmp_path / "baseline.json"
    snapshot.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="exactly one selected role"):
        _run(policy_repository, str(snapshot))


def test_legacy_git_baseline_preserves_matrix_without_fabricating_role(policy_repository):
    repository, config, _ = policy_repository
    classifier = repository / "src/job_intake/classification.py"
    original = classifier.read_text()
    classifier.unlink()
    profiles = yaml.safe_load(config.search_profiles_path.read_text())
    profiles["streams"].append({"id": "second", "scoring": {"threshold_a": 20, "threshold_b": 8}})
    config.search_profiles_path.write_text(yaml.safe_dump(profiles))
    baseline = _commit(repository)
    classifier.write_text(original)
    report = _run(policy_repository, baseline)
    assert report["baseline"]["policy"] == "multi-profile-legacy"
    assert report["current"]["policy"] == "single-role-v1"
    assert report["counts"]["evaluations_before"] == 2
    assert report["counts"]["evaluations_after"] == 1
    assert report["counts"]["classification_policy_changed"] is True
