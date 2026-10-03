from __future__ import annotations

import json
from copy import deepcopy

import pytest
from test_crm_ui import _run_javascript

from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.crm.server import CRMService
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.storage.repository import JobRepository

DOM_SETUP = """
class FakeElement {
  constructor(tag) { this.tag = tag; this.children = []; this.ownText = ''; this.className = '';
    this.listeners = {}; this.open = false; }
  set textContent(value) { this.ownText = String(value); this.children = []; }
  get textContent() { return this.ownText + this.children.map(child=>child.textContent).join(''); }
  set innerHTML(_value) { throw new Error('Unsafe HTML insertion attempted'); }
  insertAdjacentHTML() { throw new Error('Unsafe HTML insertion attempted'); }
  append(...children) { this.children.push(...children); }
  addEventListener(event, listener) { this.listeners[event] = listener; }
}
document.createElement = tag => new FakeElement(tag);
document.createTextNode = text => { const node = new FakeElement('#text');
  node.textContent = text; return node; };
function tree(node) { return {tag:node.tag, text:node.ownText, allText:node.textContent,
  className:node.className, children:node.children.map(tree)}; }
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


def test_annotation_details_lazy_load_once_without_duplicate_evidence():
    job = {"annotation": annotation()}
    result = _run_javascript(
        DOM_SETUP + f"const details = jobAnnotationDetails({json.dumps(job)},null);"
        "const before = tree(details); details.open=true; details.listeners.toggle();"
        "const afterFirst = tree(details); details.listeners.toggle();"
        "[before,afterFirst,tree(details)]"
    )
    assert result[0]["allText"] == "Разметка и подтверждения"
    assert result[1] == result[2]
    assert len(facts(result[1])) == 1


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


def test_state_api_exposes_facts_quotes_and_profile_evidence_but_omits_raw_stages(service):
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
    job = service.state({})["jobs"][0]
    assert job["annotation"] == public
    assert job["annotation"]["summary"]["knowns"][0]["source_snippet"] == (
        "The working language is English."
    )
    assert job["annotation"]["summary"]["knowns"][0]["review_method"] == "model"
    assert job["profiles"][0]["fit_reason"] == "Verify hiring location"
    assert job["profiles"][0]["risks"] == ["geography:eligibility_unconfirmed"]
    serialized = json.dumps(job["annotation"])
    for private in (
        "raw draft not for CRM", "/private/example-cache", "cache-hash", "content-hash"
    ):
        assert private not in serialized


def test_state_without_annotation_uses_empty_object_instead_of_fabricating_support(service):
    save_job(service, {})
    assert service.state({})["jobs"][0]["annotation"] == {}


def test_api_preserves_quote_and_notes_markup_as_data_for_text_only_ui(service):
    bad = claim(
        value='<img onerror="evil()">', source_snippet="<script>evil()</script>",
        review_notes='<a href="javascript:evil()">evil</a>',
    )
    data = annotation(claims=[bad], summary={
        "knowns": [bad], "unknowns": [], "contradictions": [],
    })
    save_job(service, data)
    returned = service.state({})["jobs"][0]["annotation"]
    assert returned["claims"][0] == bad
    rendered = render_annotation(returned)
    assert bad["source_snippet"] in rendered["allText"]
    assert all(node["tag"] not in {"img", "script", "a"} for node in descendants(rendered))
