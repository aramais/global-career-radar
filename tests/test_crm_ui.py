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
