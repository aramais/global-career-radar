from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from job_intake.crm.ui import render_crm_page


def test_crm_page_is_self_contained_and_uses_persistent_api() -> None:
    page = render_crm_page()
    assert '<html lang="ru">' in page
    assert 'src="http' not in page
    assert 'href="http' not in page
    assert "'/api/state'" in page
    assert "'/api/applications/'" in page
    assert "headers['X-CSRF-Token']" in page
    assert "payload.expected_version = app.version" in page
    assert "error.status === 409" in page
    assert "innerHTML" not in page
    assert "localStorage" not in page


def test_import_ui_requires_preview_of_the_same_file() -> None:
    page = render_crm_page()
    assert "?dry_run=true" in page
    assert "checkedFile = file" in page
    assert "picker.files[0] !== checkedFile" in page
    assert "'Content-Type':'application/octet-stream'" in page
    assert "'X-File-Name':encodeURIComponent(file.name)" in page
    assert "Исходный файл останется без изменений" in page


def _run_javascript(expression: str) -> object:
    node = shutil.which("node")
    bundled = Path(
        "/Users/admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    )
    if node is None and bundled.is_file():
        node = str(bundled)
    if node is None:
        pytest.skip("Node is unavailable for browser helper checks")
    page = render_crm_page()
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    # Execute the actual helper code without installing event handlers or making requests.
    handlers = "document.querySelectorAll('.tab').forEach((tab) => tab.addEventListener"
    helpers = script.split(handlers, 1)[0]
    harness = (
        "const vm=require('node:vm');"
        "const context=vm.createContext({URL,URLSearchParams,document:{"
        "getElementById:()=>({value:''})}});"
        f"vm.runInContext({json.dumps(helpers)},context);"
        f"process.stdout.write(JSON.stringify(vm.runInContext({json.dumps(expression)},context)));"
    )
    result = subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_browser_links_reject_scripts_credentials_and_obfuscated_urls() -> None:
    assert _run_javascript(
        "['javascript:alert(1)','data:text/html,test','https://user:pass@example.com',"
        "'https://example.com/a b','https://example.com\\\\evil',"
        "'https://example.com/jobs/123'].map(safeURL)"
    ) == [None, None, None, None, None, "https://example.com/jobs/123"]


def test_unknown_dates_are_preserved_and_deadlines_use_server_date() -> None:
    assert _run_javascript(
        "state={as_of:'2026-10-02',options:{}};"
        "[dateText(null),dateText('2026-10-01T23:00:00+00:00'),"
        "dueState({next_action_due:null}),dueState({next_action_due:'2026-10-01'}),"
        "dueState({next_action_due:'2026-10-02'}),dueState({next_action_due:'2026-10-03'})]"
    ) == ["Дата неизвестна", "01.10.2026", "undated", "overdue", "today", "planned"]


def test_timestamp_dates_use_sao_paulo_day_even_in_another_browser_timezone() -> None:
    assert _run_javascript(
        "[dateOnly('2026-10-02T01:30:00+00:00'),"
        "dateOnly('2026-10-02T03:00:00+00:00'),dateOnly('2026-10-02'),"
        "dateOnly('not-a-date')]"
    ) == ["2026-10-01", "2026-10-02", "2026-10-02", ""]


def test_ui_enumerations_do_not_infer_referral_from_recruiter_contact() -> None:
    assert _run_javascript(
        "state={options:{channels:['unknown','cold','referral','recruiter']}};"
        "[label('channels','unknown'),label('channels','referral'),"
        "values('channels').map(item=>item.value)]"
    ) == [
        "Канал неизвестен",
        "Реферальный отклик",
        ["unknown", "cold", "referral", "recruiter"],
    ]


@pytest.fixture
def graded_jobs() -> list[dict]:
    return [
        {
            "job_uid": "b-high",
            "profiles": [{"id": "product", "tier": "B", "score": 21, "decision": "review"}],
        },
        {
            "job_uid": "unrated",
            "tier": "A",
            "fit_score": 99,  # Stale top-level values must not become current assessments.
            "profiles": [],
        },
        {
            "job_uid": "c-zero",
            "profiles": [{"id": "product", "tier": "C", "score": 0, "decision": "reject"}],
        },
        {
            "job_uid": "a-high",
            "profiles": [{"id": "product", "tier": "A", "score": 20.5, "decision": "pass"}],
        },
        {
            "job_uid": "b-low",
            "profiles": [{"id": "product", "tier": "B", "score": 6, "decision": "pass"}],
        },
        {
            "job_uid": "a-low",
            "profiles": [{"id": "product", "tier": "A", "score": 14, "decision": "pass"}],
        },
    ]


def _filtered_job_ids(jobs: list[dict], **options: object) -> object:
    return _run_javascript(
        f"filterAndSortJobs({json.dumps(jobs)}, {json.dumps(options)}).map(job=>job.job_uid)"
    )


def test_job_archive_filter_defaults_to_active_and_combines_with_scores():
    jobs = [
        {"job_uid": "active", "archived": False,
         "profiles": [{"id": "p", "tier": "A", "score": 20}]},
        {"job_uid": "archived", "archived": True,
         "profiles": [{"id": "p", "tier": "B", "score": 8}]},
    ]
    assert _filtered_job_ids(jobs) == ["active"]
    assert _filtered_job_ids(jobs, archive="archived") == ["archived"]
    assert _filtered_job_ids(jobs, archive="all") == ["active", "archived"]
    assert _filtered_job_ids(jobs, archive="archived", minScore=10) == []
    page = render_crm_page()
    assert 'id="jobArchiveFilter"' in page
    assert "job.archived ? 'Восстановить' : 'В архив'" in page
    assert "{archived,expected_version:job.archive_version}" in page


def test_bulk_selection_is_scoped_to_visible_results_and_current_archive_versions():
    assert _run_javascript(
        "const selected=new Map([['a',1],['b',1],['hidden',1],['stale',1]]);"
        "const visible=[{job_uid:'a',archive_version:1},{job_uid:'b',archive_version:1},"
        "{job_uid:'stale',archive_version:2},{job_uid:'unselected',archive_version:1}];"
        "[...retainedJobSelection(visible,selected)]"
    ) == [["a", 1], ["b", 1]]
    assert _run_javascript(
        "selectedJobs([{job_uid:'missing-version'}],new Map()).map(job=>job.job_uid)"
    ) == []


def test_bulk_payload_uses_only_selected_visible_jobs_that_need_the_target_state():
    assert _run_javascript(
        "const jobs=[{job_uid:'active',archived:false,archive_version:1},"
        "{job_uid:'archived',archived:true,archive_version:2},"
        "{job_uid:'untouched',archived:false,archive_version:1}];"
        "const selected=new Map([['active',1],['archived',2],['hidden',1]]);"
        "[jobArchivePayload(jobs,selected,true),jobArchivePayload(jobs,selected,false),"
        "jobArchivePayload(jobs,new Map(),true)]"
    ) == [
        {"archived": True, "jobs": [{"job_uid": "active", "expected_version": 1}]},
        {"archived": False, "jobs": [{"job_uid": "archived", "expected_version": 2}]},
        {"archived": True, "jobs": []},
    ]


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("tier-asc", ["a-high", "a-low", "b-high", "b-low", "c-zero", "unrated"]),
        ("tier-desc", ["c-zero", "b-high", "b-low", "a-high", "a-low", "unrated"]),
        ("score-desc", ["b-high", "a-high", "a-low", "b-low", "c-zero", "unrated"]),
        ("score-asc", ["c-zero", "b-low", "a-low", "a-high", "b-high", "unrated"]),
    ],
)
def test_job_sort_uses_numeric_scores_and_keeps_unrated_jobs_last(graded_jobs, sort, expected):
    assert _filtered_job_ids(graded_jobs, sort=sort) == expected


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"tier": "A"}, ["a-high", "a-low"]),
        ({"tier": "B", "minScore": 14}, ["b-high"]),
        ({"tier": "C"}, ["c-zero"]),
        ({"tier": "unrated"}, ["unrated"]),
        ({"minScore": "6", "maxScore": "20.5"}, ["a-high", "a-low", "b-low"]),
        ({"minScore": "20.5", "maxScore": "20.5"}, ["a-high"]),
        ({"minScore": "0", "maxScore": "0"}, ["c-zero"]),
        ({"minScore": "0", "maxScore": ""}, ["a-high", "a-low", "b-high", "b-low", "c-zero"]),
        ({"minScore": "22", "maxScore": "6"}, []),
    ],
)
def test_job_filters_combine_stored_tier_and_inclusive_uncapped_score_range(
    graded_jobs, options, expected
):
    assert _filtered_job_ids(graded_jobs, **options) == expected


def test_job_filters_and_card_assessment_use_the_selected_profile_not_the_api_best():
    jobs = [
        {
            "job_uid": "product-fit",
            "tier": "A",
            "fit_score": 25,
            "profiles": [
                {"id": "product", "tier": "A", "score": 25, "decision": "pass"},
                {"id": "analytics", "tier": "C", "score": 0, "decision": "reject"},
            ],
        },
        {
            "job_uid": "analytics-fit",
            "profiles": [
                {"id": "product", "tier": "C", "score": 2, "decision": "review"},
                {"id": "analytics", "tier": "B", "score": 9, "decision": "review"},
            ],
        },
        {"job_uid": "no-analytics", "profiles": [{"id": "product", "tier": "A", "score": 50}]},
    ]
    assert _filtered_job_ids(jobs, profileId="analytics", tier="B", minScore=6) == [
        "analytics-fit"
    ]
    assert _filtered_job_ids(jobs, profileId="analytics", sort="score-asc") == [
        "product-fit",
        "analytics-fit",
    ]
    assert _run_javascript(
        f"jobAssessment({json.dumps(jobs[0])}, 'analytics')"
    ) == jobs[0]["profiles"][1]
    assert _filtered_job_ids(jobs, tier="A") == ["no-analytics", "product-fit"]


def test_best_profile_prioritizes_tier_then_eligibility_then_score_and_has_stable_ties():
    profiles = [
        {"id": "high-score-review", "tier": "B", "score": 30, "decision": "review"},
        {"id": "c", "tier": "B", "score": 12, "decision": "pass"},
        {"id": "b", "tier": "B", "score": 12, "decision": "pass"},
        {"id": "a", "tier": "A", "score": 14, "decision": "pass"},
    ]
    assert _run_javascript(
        f"jobAssessment({json.dumps({'profiles': profiles})}, '').id"
    ) == "a"
    assert _run_javascript(
        f"jobAssessment({json.dumps({'profiles': profiles[:-1]})}, '').id"
    ) == "b"
    tied_jobs = [
        {"job_uid": "z", "profiles": [profiles[-1]]},
        {"job_uid": "a", "profiles": [profiles[-1]]},
    ]
    assert _filtered_job_ids(tied_jobs) == ["a", "z"]


@pytest.mark.parametrize("missing_score", [None, "", "not-a-number"])
def test_missing_profile_scores_are_not_coerced_to_zero_or_included_in_numeric_ranges(
    missing_score,
):
    jobs = [
        {"job_uid": "missing", "profiles": [{"id": "p", "tier": "C", "score": missing_score}]},
        {"job_uid": "zero", "profiles": [{"id": "p", "tier": "C", "score": 0}]},
    ]
    assert _filtered_job_ids(jobs, sort="score-asc") == ["zero", "missing"]
    assert _filtered_job_ids(jobs, minScore=0, maxScore=0) == ["zero"]
