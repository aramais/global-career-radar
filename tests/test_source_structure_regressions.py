"""Eligibility and duty scope must survive the actual adapter parsing path."""

import pytest
from bs4 import BeautifulSoup

from job_intake.adapters._parsing import html_text
from job_intake.adapters.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from job_intake.annotation.text import source_hash, source_units
from job_intake.filtering import FilterRules, RuleEngine
from job_intake.models.job import FilterDecision, JobRecord


def _parse_html(html: str, adapter: str = "greenhouse") -> JobRecord:
    if adapter == "ashby":
        return AshbyAdapter("ashby", {"board_name": "example"})._parse_job({
            "id": "42", "title": "Product Manager", "jobUrl": "https://example.com/42",
            "descriptionHtml": html, "location": "Brazil",
        }, "https://example.com/api")
    if adapter == "lever":
        return LeverAdapter("lever", {"site": "example"})._parse_job({
            "id": "42", "text": "Product Manager", "hostedUrl": "https://example.com/42",
            "description": html, "categories": {"location": "Brazil"},
        }, "https://example.com/api")
    return GreenhouseAdapter("greenhouse", {"board_token": "example"})._parse_job({
        "id": "42", "title": "Product Manager", "absolute_url": "https://example.com/42",
        "content": html, "location": {"name": "Brazil"},
    }, "https://example.com/api")


def _engine() -> RuleEngine:
    return RuleEngine(FilterRules.from_mapping({
        "positive_title_signals": ["product manager"],
        "target_geographies": ["Brazil"],
        "blocker_phrases": ["US work authorization required", "authorized to work in the U.S."],
    }))


@pytest.mark.parametrize("adapter", ["greenhouse", "ashby", "lever"])
@pytest.mark.parametrize("flattened", [False, True])
@pytest.mark.parametrize("html", [
    "<ul><li>No sponsorship provided</li><li>US work authorization required</li></ul>",
    "<p>No sponsorship provided</p><p>US work authorization required</p>",
    "<div>No sponsorship provided<br>US work authorization required</div>",
    "<p>No sponsorship provided</p><p>Must be authorized to work in the U.S.</p>",
])
def test_independent_authorization_assertion_is_not_negated_by_previous_block(
    adapter, flattened, html,
):
    record = _parse_html(html, adapter)
    if flattened:
        record.description_clean = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    else:
        assert "\n" in record.description_clean
    original_hash = source_hash(record)
    units = [unit for unit in source_units(record) if unit["field"] == "description_clean"]
    assert "".join(unit["text"] for unit in units) == record.description_clean
    authorization = next(unit for unit in units if "authorization" in unit["text"]
                         or "authorized" in unit["text"])
    assert "No sponsorship" not in authorization["text"]
    assert _engine().evaluate(record).decision == FilterDecision.REJECT
    assert source_hash(record) == original_hash


@pytest.mark.parametrize("html", [
    "<p>No <b>US work authorization</b> required</p>",
    "<p>US work authorization required is <em>optional</em></p>",
    "<p>Is US work authorization required?</p>",
])
def test_inline_formatting_preserves_negation_optional_and_question_context(html):
    record = _parse_html(html)
    result = _engine().evaluate(record)
    assert result.decision == FilterDecision.PASS
    assert result.blocker_signals == []


def test_ashby_plain_description_preserves_unpunctuated_list_boundaries():
    record = AshbyAdapter("ashby", {"board_name": "example"})._parse_job({
        "id": "42", "title": "Product Manager", "jobUrl": "https://example.com/42",
        "location": "Brazil", "descriptionPlain": (
            "Responsibilities\r\nOwn the roadmap\r\nRequirements\r\n"
            "No sponsorship provided\r\nUS work authorization required\r\nFINAL TAIL"
        ),
    }, "https://example.com/api")
    assert record.description_clean.endswith("US work authorization required\nFINAL TAIL")
    assert _engine().evaluate(record).decision == FilterDecision.REJECT
    units = source_units(record)
    assert next(unit for unit in units if "Own the roadmap" in unit["text"])["section"] == "role"
    assert next(unit for unit in units if "FINAL TAIL" in unit["text"])["section"] == "requirements"


@pytest.mark.parametrize("flattened", [False, True])
def test_actual_dom_heading_does_not_bind_to_requirements_inside_earlier_duty(flattened):
    raw = (
        "<h2>Responsibilities</h2><p>Gather requirements from stakeholders</p>"
        "<p>Own business decisions</p><strong>Requirements</strong>"
        "<p>English required</p>"
    )
    record = _parse_html(raw)
    if flattened:
        # Existing saved records retain their exact historical source snapshot.
        record.description_clean = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    original = record.description_clean
    original_hash = source_hash(record)
    units = [unit for unit in source_units(record) if unit["field"] == "description_clean"]
    assert next(unit for unit in units if "Gather requirements" in unit["text"])["section"] == (
        "role"
    )
    assert next(unit for unit in units if "Own business" in unit["text"])["section"] == "role"
    assert next(unit for unit in units if "English required" in unit["text"])["section"] == (
        "requirements"
    )
    assert "".join(unit["text"] for unit in units) == original
    assert source_hash(record) == original_hash


@pytest.mark.parametrize("flattened", [False, True])
def test_bold_requirements_word_within_duty_is_not_a_heading(flattened):
    raw = (
        "<h2>Responsibilities</h2><p>Gather <strong>requirements</strong> from partners.</p>"
        "<p>Own business decisions.</p><h2>Qualifications</h2><p>English required.</p>"
    )
    record = _parse_html(raw)
    if flattened:
        record.description_clean = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    units = source_units(record)
    assert next(unit for unit in units if "Gather" in unit["text"])["section"] == "role"
    assert next(unit for unit in units if "Own business" in unit["text"])["section"] == "role"
    assert next(unit for unit in units if "English required" in unit["text"])["section"] == (
        "requirements"
    )


def test_html_inline_punctuation_and_encoded_headings_retain_source_structure():
    html = (
        "&lt;h2&gt;Responsibilities&lt;/h2&gt;"
        "&lt;p&gt;Own &lt;b&gt;decisions&lt;/b&gt;.&lt;/p&gt;"
    )
    record = _parse_html(html)
    assert record.description_clean == "Responsibilities\nOwn decisions."
    assert source_units(record)[-1]["section"] == "role"
    assert html_text("<p>Use <b>SQL</b>, Python and <a href='/'>data</a>.</p>") == (
        "Use SQL, Python and data."
    )


@pytest.mark.parametrize("adapter", ["greenhouse", "ashby", "lever"])
@pytest.mark.parametrize("flattened", [False, True])
@pytest.mark.parametrize("country", ["Ireland", "Brazil"])
def test_scoped_location_and_authorization_survive_adapter_units_engine(
    adapter, flattened, country,
):
    location = "This role is based in Ireland. " if country == "Ireland" else ""
    raw = (
        "<h2>Job Description</h2><p>Lead platforms that support our global operations.</p>"
        "<h2>Required Skills &amp; Experience</h2><ul><li>Fluency in English is required.</li>"
        "<li><strong>Authorization + location requirements:</strong> " + location
        + f"Candidates must have the right to work in {country}. "
        "Delivery Associates will not sponsor applicants for work visas.</li></ul>"
        "<h2>What We Offer</h2><p>Remote working environment with opportunities for global "
        "collaboration.</p>"
    )
    record = _parse_html(raw, adapter)
    if flattened:
        record.description_clean = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    original_hash = source_hash(record)
    units = source_units(record)
    assert any("right to work in " + country in unit["text"] for unit in units)
    engine = RuleEngine(FilterRules.from_mapping({
        "recall_first": True, "positive_title_signals": ["product manager"],
        "target_geographies": ["Brazil", "worldwide", "Global"],
        "required_languages": ["English"],
    }))
    evaluation = engine.evaluate(record)
    assert evaluation.decision == (FilterDecision.REJECT if country == "Ireland"
                                   else FilterDecision.REVIEW)
    assert evaluation.eligibility_status == ("ineligible" if country == "Ireland" else "unknown")
    assert "authorization:work_permission_unconfirmed" in evaluation.risks
    assert "authorization:sponsorship_unconfirmed" in evaluation.risks
    if country == "Ireland":
        assert "geo_blocker:this role is based in ireland" in evaluation.blocker_signals
    assert source_hash(record) == original_hash


def test_legacy_flattened_inline_heading_is_anchored_to_paragraph_start():
    raw = (
        "<h2>Responsibilities</h2><p>Gather requirements: understand stakeholders.</p>"
        "<p><b>Requirements:</b> English required.</p>"
    )
    record = _parse_html(raw)
    record.description_clean = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    units = source_units(record)
    assert next(unit for unit in units if "Gather requirements" in unit["text"])["section"] == (
        "role"
    )
    assert next(unit for unit in units if "English required" in unit["text"])["section"] == (
        "requirements"
    )
