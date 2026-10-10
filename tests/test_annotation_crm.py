from __future__ import annotations

import json
from copy import deepcopy

import pytest
from test_crm_ui import _run_javascript

from job_intake.annotation.schema import summarize_claims
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.crm.server import CRMService
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.storage.repository import JobRepository

DOM_SETUP = """
class FakeElement {
  constructor(tag) { this.tag = tag; this.children = []; this.ownText = ''; this.className = '';
    this.listeners = {}; this.open = false; this.parentNode = null; }
  set textContent(value) { this.ownText = String(value);
    this.children.forEach(child=>child.parentNode=null); this.children = []; }
  get textContent() { return this.ownText + this.children.map(child=>child.textContent).join(''); }
  set innerHTML(_value) { throw new Error('Unsafe HTML insertion attempted'); }
  insertAdjacentHTML() { throw new Error('Unsafe HTML insertion attempted'); }
  append(...children) { children.forEach(child=> { child.remove();
    child.parentNode = this; this.children.push(child); }); }
  remove() { if (this.parentNode) { this.parentNode.children =
    this.parentNode.children.filter(child=>child !== this); this.parentNode = null; } }
  replaceWith(...children) { const parent = this.parentNode; if (!parent) return;
    const index = parent.children.indexOf(this); this.remove();
    children.forEach(child=> { child.remove(); child.parentNode = parent; });
    parent.children.splice(index,0,...children); }
  addEventListener(event, listener) { this.listeners[event] = listener; }
}
document.createElement = tag => new FakeElement(tag);
document.createTextNode = text => { const node = new FakeElement('#text');
  node.textContent = text; return node; };
function tree(node) { return {tag:node.tag, text:node.ownText, allText:node.textContent,
  className:node.className, id:node.id || '', href:node.href || '', title:node.title || '',
  children:node.children.map(tree)}; }
"""


def claim(**changes):
    data = {
        "claim_id": "source:c1",
        "kind": "working_language",
        "value": "English",
        "review_status": "SUPPORTED",
        "review_method": "model",
        "requirement": "required",
        "polarity": "affirmative",
        "is_inference": False,
        "source_snippet": "The working language is English.",
        "source_field": "description_clean",
        "review_notes": "Directly stated in the vacancy description.",
    }
    data.update(changes)
    return data


def annotation(**changes):
    supported = claim()
    if changes.get("method") == "deterministic":
        supported["review_method"] = "local"
    data = {
        "version": "grounded-v1",
        "status": "reviewed",
        "method": "two_pass",
        "extract_model": "extract-model",
        "review_model": "review-model",
        "description_complete": True,
        "claims": [supported],
        "summary": {
            "knowns": [supported],
            "unknowns": ["hiring_location", "salary"],
            "contradictions": [],
            "supported_claim_ids": [supported["claim_id"]],
        },
        "issues": [],
        "coverage": {"chunks": 2, "completed_chunks": 2},
    }
    data.update(changes)
    return data


def render_annotation(data, assessment=None):
    return _run_javascript(
        DOM_SETUP
        + f"tree(renderJobAnnotation({json.dumps({'annotation': data})},"
        + f"{json.dumps(assessment)}))"
    )


def descendants(node):
    yield node
    for child in node["children"]:
        yield from descendants(child)


def headings(node):
    return [child["allText"] for child in descendants(node) if child["tag"] == "h4"]


def facts(node):
    return [child for child in descendants(node) if child["className"] == "annotation-fact"]


FOCUS_LABELS = {
    "project_management": "Руководство проектами",
    "people_interaction": "Работа с людьми",
    "decision_influence": "Влияние на решения",
    "business_economics": "Экономика бизнеса",
    "executive_finance_partnership": "Работа с руководителем бизнеса и финансами",
    "causal_inference": "Причинно-следственный анализ",
    "direct_reports": "Прямые подчинённые",
    "hands_on_analysis": "Личное выполнение анализа",
    "isolated_analysis": "Длительный анализ без работы с людьми",
    "working_english": "Рабочий английский",
}


def focus_dimension(priority, status, claim_ids=(), unresolved_claim_ids=()):
    return {
        "priority": priority, "status": status,
        "claim_ids": list(claim_ids), "unresolved_claim_ids": list(unresolved_claim_ids),
    }


def focus_rows(rendered):
    return {
        row["children"][0]["allText"]: row
        for row in descendants(rendered) if row["className"] == "annotation-focus-row"
    }


@pytest.mark.parametrize("kind", list(FOCUS_LABELS)[:-1])
def test_career_focus_claim_kinds_have_plain_russian_labels(kind):
    rendered = _run_javascript(DOM_SETUP + f"tree(annotationFact({json.dumps(claim(kind=kind))}))")
    assert rendered["children"][0]["allText"] == FOCUS_LABELS[kind] + ": "
    assert kind not in rendered["allText"]


def test_search_focus_shows_required_unknowns_preferences_and_linked_source_evidence():
    project = claim(
        claim_id="vacancy:project", kind="project_management", value="Lead pricing projects",
        source_snippet="You will lead pricing projects.",
    )
    pending = claim(
        claim_id="vacancy:people", kind="people_interaction", value="Work with Finance",
        review_status="NEEDS_VERIFICATION", source_snippet="You may work with Finance.",
    )
    economic = claim(
        claim_id="vacancy:economics", kind="business_economics", value="Analyze unit P&L",
        source_snippet="You will analyze the unit's P&L.",
    )
    data = annotation(claims=[project, pending, economic], summary={
        "knowns": [project, economic], "unknowns": [], "contradictions": [],
        "search_focus": {
            "required_confirmed": False,
            "unconfirmed_required": ["people_interaction", "decision_influence", "working_english"],
            "explicit_mismatches": [], "confirmed_preferences": ["business_economics"],
            "dimensions": {
                "project_management": focus_dimension(
                    "required", "confirmed", [project["claim_id"]],
                ),
                "people_interaction": focus_dimension(
                    "required", "unknown", unresolved_claim_ids=[pending["claim_id"]],
                ),
                "decision_influence": focus_dimension("required", "unknown"),
                "working_english": focus_dimension("required", "unknown"),
                "business_economics": focus_dimension(
                    "preferred", "confirmed", [economic["claim_id"]],
                ),
            },
        },
    })
    rendered = render_annotation(data)
    assert "Условия выбранного поиска" in headings(rendered)
    assert "Обязательные условия без подтверждения: Работа с людьми; Влияние на решения; " in (
        rendered["allText"]
    )
    assert "Рабочий английский" in rendered["allText"]
    assert "Подтверждённые предпочтения: Экономика бизнеса" in rendered["allText"]
    assert "Обязательные условия поиска подтверждены источником." not in rendered["allText"]
    rows = focus_rows(rendered)
    assert "Непроверенные цитаты" in rows["Работа с людьми"]["allText"]
    assert "Не подтверждено" in rows["Влияние на решения"]["allText"]
    assert not any("green" in node["className"] for node in descendants(rows["Работа с людьми"]))
    rendered_facts = facts(rendered)
    assert len(rendered_facts) == 3
    anchors = {fact["id"] for fact in rendered_facts}
    links = [node for node in descendants(rendered) if node["tag"] == "a"]
    assert len(links) == 3
    assert {link["href"][1:] for link in links} == anchors
    assert {link["title"] for link in links} == {
        "Утверждение: " + row["claim_id"] for row in (project, pending, economic)
    }
    assert project["source_snippet"] in rendered["allText"]
    assert pending["source_snippet"] in rendered["allText"]


def test_focus_absence_and_conflict_are_visible_while_reports_and_analysis_are_neutral():
    data = annotation(summary={
        "knowns": [], "unknowns": [], "contradictions": [],
        "search_focus": {
            "required_confirmed": False, "confirmed_preferences": [],
            "unconfirmed_required": ["project_management", "people_interaction"],
            "explicit_mismatches": ["project_management", "isolated_analysis"],
            "dimensions": {
                "project_management": focus_dimension("required", "explicitly_absent"),
                "people_interaction": focus_dimension("required", "conflicting"),
                "isolated_analysis": focus_dimension("excluded", "confirmed"),
                "direct_reports": focus_dimension("informational", "explicitly_absent"),
                "hands_on_analysis": focus_dimension("informational", "confirmed"),
            },
        },
    })
    rendered = render_annotation(data)
    assert "Явные расхождения с поиском: Руководство проектами; " in rendered["allText"]
    rows = focus_rows(rendered)
    assert "Явно отсутствует" in rows["Руководство проектами"]["allText"]
    assert "Есть противоречие" in rows["Работа с людьми"]["allText"]
    for label in ("Руководство проектами", "Работа с людьми", FOCUS_LABELS["isolated_analysis"]):
        assert any("red" in node["className"] for node in descendants(rows[label]))
    assert "Прямые подчинённые не обязательны" in rows["Прямые подчинённые"]["allText"]
    assert "Самостоятельный анализ допустим" in rows["Личное выполнение анализа"]["allText"]
    for label in ("Прямые подчинённые", "Личное выполнение анализа"):
        assert "Для уточнения" in rows[label]["allText"]
        assert not any(
            color in node["className"]
            for node in descendants(rows[label]) for color in ("red", "green", "amber")
        )


def test_focus_evidence_links_are_safe_and_missing_claims_do_not_create_dead_links():
    identifier = '<img src=x onerror="steal()">#source'
    supported = claim(claim_id=identifier, kind="project_management")
    data = annotation(claims=[supported], summary={
        "knowns": [supported], "unknowns": [], "contradictions": [],
        "search_focus": {
            "required_confirmed": True, "unconfirmed_required": [],
            "explicit_mismatches": [], "confirmed_preferences": [],
            "dimensions": {
                "project_management": focus_dimension("required", "confirmed", [identifier]),
                "business_economics": focus_dimension("preferred", "confirmed", ["missing"]),
            },
        },
    })
    rendered = render_annotation(data)
    links = [node for node in descendants(rendered) if node["tag"] == "a"]
    assert len(links) == 1
    assert links[0]["title"] == "Утверждение: " + identifier
    assert links[0]["href"] == "#" + facts(rendered)[0]["id"]
    assert '<img' not in links[0]["href"]
    assert "Цитата недоступна" in rendered["allText"]
    assert all(node["tag"] not in {"img", "script", "iframe"} for node in descendants(rendered))
    assert "Обязательные условия поиска подтверждены источником." in rendered["allText"]
    result = _run_javascript(
        DOM_SETUP + f"const job={json.dumps({'annotation': data})};"
        "[tree(renderJobAnnotation(job,null)),tree(renderJobAnnotation(job,null))]"
    )
    assert facts(result[0])[0]["id"] != facts(result[1])[0]["id"]


def test_annotation_fact_renders_untrusted_value_quote_and_notes_as_literal_text():
    malicious = {
        "value": '<img src=x onerror="steal()">',
        "source_snippet": "<script>steal()</script>Applicants must reside in Brazil.",
        "review_notes": '<a href="javascript:steal()">Review</a>',
        "source_field": '<iframe src="attacker.example">',
    }
    rendered = _run_javascript(
        DOM_SETUP + f"tree(annotationFact({json.dumps(claim(**malicious))}))"
    )
    assert all(value in rendered["allText"] for value in malicious.values())
    tags = {node["tag"] for node in descendants(rendered)}
    assert not tags.intersection({"script", "img", "a", "iframe"})
    assert [node["allText"] for node in descendants(rendered) if node["tag"] == "blockquote"] == [
        malicious["source_snippet"]
    ]


def test_confirmed_and_uncertain_claims_are_shown_once_in_separate_sections():
    supported = claim()
    pending = claim(
        claim_id="source:c2", kind="salary", value="USD 100,000",
        review_status="NEEDS_VERIFICATION", source_snippet="Compensation varies by location.",
    )
    data = annotation(claims=[supported, pending])
    rendered = render_annotation(data)
    assert headings(rendered) == [
        "Факты с цитатами из источника",
        "Не подтверждено источником",
        "Спорные и непроверенные утверждения",
    ]
    rendered_facts = facts(rendered)
    assert len(rendered_facts) == 2
    assert "English" in rendered_facts[0]["allText"]
    assert "Есть подтверждение" in rendered_facts[0]["allText"]
    assert "USD 100,000" in rendered_facts[1]["allText"]
    assert "Нужна проверка" in rendered_facts[1]["allText"]
    assert "Страны найма" in rendered["allText"]
    assert "Оплата" in rendered["allText"]


def test_contradictions_are_excluded_from_confirmed_facts_even_if_claim_status_is_supported():
    positive = claim(claim_id="source:positive", value="English", polarity="affirmative")
    negative = claim(
        claim_id="source:negative", value="English", polarity="negative",
        requirement="not_required", source_snippet="English is not required.",
    )
    data = annotation(
        claims=[positive, negative],
        summary={
            "knowns": [], "unknowns": ["working_language"],
            "contradictions": [{
                "kind": "working_language", "value": "English",
                "claim_ids": ["source:positive", "source:negative"],
            }],
        },
    )
    rendered = render_annotation(data)
    assert "Противоречия в источнике" in headings(rendered)
    assert "Подтверждённых фактов пока нет." in rendered["allText"]
    assert len(facts(rendered)) == 2
    assert all(
        "Исключено из подтверждённых: противоречие" in fact["allText"]
        for fact in facts(rendered)
    )
    for fact in facts(rendered):
        status_badge = next(
            node for node in descendants(fact) if node["className"].startswith("badge")
        )
        assert "red" in status_badge["className"]


def test_preference_negation_and_inference_are_visible_without_becoming_requirements():
    data = claim(
        requirement="preferred", polarity="negative", is_inference=True,
        review_status="WEAKLY_SUPPORTED",
    )
    rendered = _run_javascript(DOM_SETUP + f"tree(annotationFact({json.dumps(data)}))")
    assert "Предпочтительно" in rendered["allText"]
    assert "Отрицание / исключение" in rendered["allText"]
    assert "Вывод требует проверки" in rendered["allText"]
    assert "Слабое подтверждение" in rendered["allText"]
    assert "Обязательно" not in rendered["allText"]


@pytest.mark.parametrize("status", ["UNSUPPORTED", "NEEDS_VERIFICATION", "UNREVIEWED"])
def test_unresolved_claims_have_no_green_supported_badge(status):
    rendered = _run_javascript(
        DOM_SETUP + f"tree(annotationFact({json.dumps(claim(review_status=status))}))"
    )
    assert not any("green" in node["className"] for node in descendants(rendered))


def test_local_annotations_explicitly_say_models_did_not_review():
    rendered = render_annotation(annotation(method="deterministic", status="local"))
    assert "Проверка моделями не выполнялась." in rendered["allText"]
    assert "проверены второй моделью" not in rendered["allText"]
    assert "extract-model → review-model" not in rendered["allText"]


@pytest.mark.parametrize(("method", "expected", "other"), [
    ("local", "Проверено локально", "Проверено второй моделью"),
    ("model", "Проверено второй моделью", "Проверено локально"),
])
def test_each_supported_claim_shows_its_actual_review_provenance(method, expected, other):
    rendered = _run_javascript(
        DOM_SETUP + f"tree(annotationFact({json.dumps(claim(review_method=method))}))"
    )
    assert expected in rendered["allText"]
    assert other not in rendered["allText"]


def test_partial_annotation_and_incomplete_description_do_not_claim_finished_review():
    data = annotation(
        status="partial", description_complete=False,
        issues=["chunk-2: review unavailable"],
    )
    rendered = render_annotation(data, {
        "fit_reason": "Target role; verify residency.",
        "risks": ["geography:eligibility_unconfirmed"],
        "blocker_signals": ["authorization_required"],
    })
    assert "Независимая проверка выполнена частично" in rendered["allText"]
    assert "Факты извлечены из текста и проверены второй моделью." not in rendered["allText"]
    assert "Источник содержит неполное описание" in rendered["allText"]
    assert "geography:eligibility_unconfirmed" in rendered["allText"]
    assert "authorization_required" in rendered["allText"]
    assert "Target role; verify residency." in rendered["allText"]
    assert "chunk-2: review unavailable" in rendered["allText"]


def test_absent_annotation_says_it_has_not_run():
    rendered = render_annotation({})
    assert rendered["allText"] == "Разметка ещё не выполнена."
    assert facts(rendered) == []


def test_suppressed_prefilter_is_preliminary_and_keeps_source_evidence_visible():
    snippet = "Your work consists only of maintaining daily dashboard reports."
    data = {"prefilter": {
        "routing": "suppressed", "decision": "drop", "reason_code": "isolated_reporting",
        "reason": "Only routine reporting is described.",
        "evidence": [{"unit_id": "description:1", "source_snippet": snippet}],
    }}
    rendered = render_annotation(data)
    assert headings(rendered) == ["Предварительный фильтр"]
    assert "Предварительно отфильтровано" in rendered["allText"]
    assert "Подробная AI-проверка пропущена" in rendered["allText"]
    assert "сохранена для ручной проверки (REVIEW, категория C)" in rendered["allText"]
    assert "Only routine reporting is described." in rendered["allText"]
    assert facts(rendered) == []
    assert not any("green" in node["className"] for node in descendants(rendered))
    link = next(node for node in descendants(rendered) if node["tag"] == "a")
    quote = next(node for node in descendants(rendered) if node["tag"] == "blockquote")
    assert quote["allText"] == snippet
    assert link["href"] == "#" + quote["id"]
    assert link["title"] == "Фрагмент источника: description:1"


@pytest.mark.parametrize(("decision", "expected"), [
    ("keep", "Вероятно подходит по предварительному отбору."),
    ("uncertain", "Соответствие пока неясно; вакансия передана на подробную проверку."),
])
def test_prefilter_full_review_distinguishes_probable_and_uncertain_and_links_claim_source(
    decision, expected,
):
    supported = claim(unit_id="description:1")
    data = annotation(claims=[supported], summary={
        "knowns": [supported], "unknowns": [], "contradictions": [],
    }, prefilter={
        "routing": "full_review", "decision": decision, "reason": "Review actual duties.",
        "evidence": [{"unit_id": "description:1", "source_snippet": supported["source_snippet"]}],
    })
    rendered = render_annotation(data)
    assert "Передано на подробную AI-проверку" in rendered["allText"]
    assert expected in rendered["allText"]
    link = next(node for node in descendants(rendered) if node["tag"] == "a")
    assert link["href"] == "#" + facts(rendered)[0]["id"]
    assert "Факты извлечены из текста и проверены второй моделью." in rendered["allText"]


@pytest.mark.parametrize("routing", ["suppressed", "local_reject"])
def test_skipped_full_review_does_not_present_previous_claims_as_current_verified_facts(routing):
    data = annotation(prefilter={
        "routing": routing, "decision": "drop", "reason": "Different filter result.",
        "evidence": [{"unit_id": "description:1", "source_snippet": "Relevant source quote."}],
    })
    rendered = render_annotation(data)
    assert facts(rendered) == []
    assert "Факты извлечены из текста и проверены второй моделью." not in rendered["allText"]
    assert "Факты с цитатами из источника" not in headings(rendered)
    assert (
        "Подробная проверка условий вакансии для этого предварительного отбора не выполнялась."
    ) in rendered["allText"]
    link = next(node for node in descendants(rendered) if node["tag"] == "a")
    assert any(node["id"] == link["href"][1:] for node in descendants(rendered))


def test_prefilter_model_text_and_source_identifiers_are_safe_and_private_stages_are_hidden():
    malicious = '<img src=x onerror="steal()"><script>steal()</script>'
    identifier = 'javascript:steal()#<iframe>'
    data = {"prefilter": {
        "routing": "suppressed", "decision": malicious, "reason_code": malicious,
        "reason": malicious, "evidence": [{"unit_id": identifier, "source_snippet": malicious}],
        "stages": {"raw": "private draft"}, "usage": [{"raw": "private usage"}],
    }}
    rendered = render_annotation(data)
    assert malicious in rendered["allText"]
    assert "private draft" not in rendered["allText"]
    assert "private usage" not in rendered["allText"]
    assert all(node["tag"] not in {"img", "script", "iframe"} for node in descendants(rendered))
    link = next(node for node in descendants(rendered) if node["tag"] == "a")
    assert link["href"].startswith("#annotation-evidence-")
    assert identifier not in link["href"]
    assert link["title"] == "Фрагмент источника: " + identifier
    quote = next(node for node in descendants(rendered) if node["tag"] == "blockquote")
    assert link["href"] == "#" + quote["id"]


def test_local_prefilter_rejection_displays_blockers_as_plain_text_without_duplicating_them():
    blocker = '<img src=x onerror="steal()">'
    data = {"prefilter": {
        "routing": "local_reject", "decision": "drop", "reason": "Explicit location restriction.",
        "blockers": [blocker, "status:closed"], "evidence": [],
    }}
    rendered = render_annotation(data, {"blocker_signals": [blocker]})
    assert "Явные ограничения вакансии" in rendered["allText"]
    assert "Explicit location restriction." in rendered["allText"]
    assert "status:closed" in rendered["allText"]
    assert rendered["allText"].count(blocker) == 1
    assert facts(rendered) == []
    assert all(node["tag"] not in {"img", "script"} for node in descendants(rendered))


def test_annotation_details_lazy_load_once_without_duplicate_evidence():
    job = {"job_uid": "fixture:job1"}
    detail = {**job, "annotation": annotation(), "profiles": []}
    result = _run_javascript(
        DOM_SETUP + "(async()=> { const requests=[]; globalThis.fetch=async(path,options)=> {"
        "requests.push({path,method:options.method,credentials:options.credentials});"
        f"return {{ok:true,json:async()=>({json.dumps(detail)})}}; }};"
        f"const details=jobAnnotationDetails({json.dumps(job)},null);"
        "const before=tree(details), beforeRequests=requests.length; details.open=true;"
        "const first=details.listeners.toggle(); await details.listeners.toggle(); await first;"
        "const afterFirst=tree(details); await details.listeners.toggle();"
        "return {before,beforeRequests,afterFirst,afterSecond:tree(details),requests}; })()",
        await_result=True,
    )
    assert result["before"]["allText"] == "Разметка и подтверждения"
    assert result["beforeRequests"] == 0
    assert result["requests"] == [{
        "path": "/api/jobs/fixture%3Ajob1", "method": "GET", "credentials": "same-origin",
    }]
    assert result["afterFirst"] == result["afterSecond"]
    assert len(facts(result["afterFirst"])) == 1
    assert "The working language is English." in result["afterFirst"]["allText"]
    assert "Загрузка…" not in result["afterFirst"]["allText"]


@pytest.fixture
def service(tmp_path):
    rules = tmp_path / "rules.yaml"
    profiles = tmp_path / "profiles.yaml"
    rules.write_text("{}", encoding="utf-8")
    profiles.write_text("streams:\n  - id: product\n    name: Product\n", encoding="utf-8")
    return CRMService(AppConfig(
        database_url=f"sqlite:///{tmp_path / 'jobs.db'}", log_level="WARNING",
        rules_path=rules, search_profiles_path=profiles,
        company_watchlist_path=tmp_path / "companies.yaml", export_dir=tmp_path,
        sources=[], telegram=TelegramConfig(), llm=LLMConfig(),
    ))


def save_job(service, job_annotation):
    stream = service.streams[0]
    item = EvaluatedJob(
        record=JobRecord(
            source="fixture", source_job_id="job1", company="Example", title="Product Manager",
            original_url="https://example.com/jobs/job1", annotation=job_annotation,
        ),
        evaluation=JobEvaluation(
            decision=FilterDecision.REVIEW, tier=JobTier.B, fit_score=12,
            fit_reason="Verify hiring location", risks=["geography:eligibility_unconfirmed"],
            blocker_signals=[],
        ),
        profile_id=stream.id, profile_name=stream.name, profile_version=stream.version,
    )
    with service.database.session() as session:
        JobRepository(session).upsert_evaluations([item])
        session.commit()


def test_state_is_compact_and_job_detail_exposes_only_public_annotation_evidence(service):
    data = annotation()
    public = deepcopy(data)
    data.update({
        "stages": {"chunk1": {"extraction": "raw draft not for CRM", "review": "raw review"}},
        "units": [{"text": "full source units duplicated privately"}],
        "cache_key": "cache-hash", "cache_dir": "/private/example-cache",
        "source_hash": "content-hash", "usage": [{"input_tokens": 100}],
        "unrecognized_field": "must not be included",
    })
    save_job(service, data)
    compact = service.state({})["jobs"][0]
    assert "annotation" not in compact
    assert "description" not in compact
    assert "fit_reason" not in compact["profiles"][0]
    assert "risks" not in compact["profiles"][0]
    job = service.job(compact["job_uid"])
    assert job["annotation"] == public
    assert job["annotation"]["summary"]["knowns"][0]["source_snippet"] == (
        "The working language is English."
    )
    assert job["annotation"]["summary"]["knowns"][0]["review_method"] == "model"
    assert job["profiles"][0]["fit_reason"] == "Verify hiring location"
    assert job["profiles"][0]["risks"] == ["geography:eligibility_unconfirmed"]
    serialized = json.dumps(job)
    for private in (
        "raw draft not for CRM", "raw review", "full source units duplicated privately",
        "/private/example-cache", "cache-hash", "content-hash", "must not be included",
    ):
        assert private not in serialized


def test_compact_state_omits_annotation_and_empty_detail_does_not_fabricate_support(service):
    save_job(service, {})
    compact = service.state({})["jobs"][0]
    assert "annotation" not in compact
    assert service.job(compact["job_uid"])["annotation"] == {}


def test_job_detail_preserves_source_grounded_focus_summary_and_evidence_links(service):
    project = claim(
        claim_id="source:focus", kind="project_management", requirement="informational",
        value="Lead analytics projects", source_snippet="You will lead analytics projects.",
    )
    summary = summarize_claims([project])
    data = annotation(claims=[project], summary=summary)
    save_job(service, data)
    compact = service.state({})["jobs"][0]
    assert "annotation" not in compact
    returned = service.job(compact["job_uid"])["annotation"]
    assert returned["summary"]["search_focus"] == summary["search_focus"]
    rendered = render_annotation(returned)
    row = focus_rows(rendered)["Руководство проектами"]
    assert "Есть подтверждение" in row["allText"]
    link = next(node for node in descendants(row) if node["tag"] == "a")
    assert link["href"] == "#" + facts(rendered)[0]["id"]
    assert project["source_snippet"] in rendered["allText"]
    assert "Обязательные условия без подтверждения" in rendered["allText"]


def test_job_detail_exposes_prefilter_decision_and_quotes_without_private_stage_data(service):
    public_prefilter = {
        "routing": "suppressed", "decision": "drop", "reason_code": "isolated_reporting",
        "reason": "Only daily reporting is described.",
        "evidence": [{
            "unit_id": "description:1",
            "source_snippet": '<script>example()</script>Only daily reporting.',
        }],
    }
    private_prefilter = deepcopy(public_prefilter)
    private_prefilter.update({
        "stages": {"screen": {"draft": "private screen draft"}},
        "usage": [{"raw": "private token accounting"}],
        "cache_key": "private prefilter cache", "unrecognized": "private unknown field",
    })
    private_prefilter["evidence"][0]["source_unit"] = "private complete unit"
    save_job(service, annotation(prefilter=private_prefilter))
    compact = service.state({})["jobs"][0]
    assert "annotation" not in compact
    returned = service.job(compact["job_uid"])["annotation"]
    assert "prefilter" in returned
    for key, value in public_prefilter.items():
        assert returned["prefilter"][key] == value
    serialized = json.dumps(returned["prefilter"])
    for private in (
        "private screen draft", "private token accounting", "private prefilter cache",
        "private unknown field", "private complete unit",
    ):
        assert private not in serialized
    rendered = render_annotation(returned)
    assert "Предварительно отфильтровано" in rendered["allText"]
    assert public_prefilter["evidence"][0]["source_snippet"] in rendered["allText"]
    assert all(node["tag"] != "script" for node in descendants(rendered))


def test_api_preserves_quote_and_notes_markup_as_data_for_text_only_ui(service):
    bad = claim(
        value='<img onerror="evil()">', source_snippet="<script>evil()</script>",
        review_notes='<a href="javascript:evil()">evil</a>',
    )
    data = annotation(claims=[bad], summary={
        "knowns": [bad], "unknowns": [], "contradictions": [],
    })
    save_job(service, data)
    compact = service.state({})["jobs"][0]
    assert "annotation" not in compact
    returned = service.job(compact["job_uid"])["annotation"]
    assert returned["claims"][0] == bad
    rendered = render_annotation(returned)
    assert bad["source_snippet"] in rendered["allText"]
    assert all(node["tag"] not in {"img", "script", "a"} for node in descendants(rendered))
