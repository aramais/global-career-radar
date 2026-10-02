import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from job_intake.review.report import render_html_report


def _profile(profile_id="analytics", **changes):
    fields = {
        "profile_id": profile_id,
        "profile_name": "Analytics Leadership",
        "profile_version": "version-1",
        "content_hash": "content-v1",
        "decision": "pass",
        "fit_score": 20.0,
        "tier": "A",
        "matched_signals": ["title:head of analytics"],
        "blocker_signals": [],
        "fit_reason": "Leadership and experimentation fit.",
        "risks": ["Check hiring from Brazil."],
        "audit_log": ["Profile-specific rules applied."],
    }
    return SimpleNamespace(**{**fields, **changes})


def _job(**changes):
    fields = {
        "company": "Example",
        "title": "Head of Analytics",
        "source": "test",
        "content_hash": "content-v1",
        "original_url": "https://example.com/job?team=analytics&lang=en",
        "apply_url": "https://example.com/apply",
        "location_text": "São Paulo or remote",
        "remote_text": "Remote",
        "employment_type": "Full-time",
        "salary_text": "$120,000",
        "timezone_text": "Americas",
        "description_clean": "Own experimentation.\nFull description with hiring conditions.",
        "description_raw": "",
        "first_seen_at": datetime(2026, 10, 1, 12, tzinfo=UTC),
        "updated_at": datetime(2026, 10, 4, 12, tzinfo=UTC),
        "tier": "A",
        "filter_decision": "pass",
        "fit_score": 20.0,
        "matched_signals": [],
        "detected_blockers": [],
        "fit_reason": "Legacy evaluation.",
        "risks": [],
        "audit_log": [],
    }
    return SimpleNamespace(**{**fields, **changes})


def _render(tmp_path, jobs, **kwargs):
    target = tmp_path / "reports" / "review.html"
    assert render_html_report(jobs, target, **kwargs) == target
    html = target.read_text(encoding="utf-8")
    return html, BeautifulSoup(html, "html.parser")


def _data(soup):
    return json.loads(soup.select_one("#jobs-data").string)


def test_low_fit_c_remains_visible_separately_from_hard_rejection(tmp_path):
    _, soup = _render(
        tmp_path,
        [
            _job(title="Adjacent opportunity", tier="C", filter_decision="review", fit_score=2),
            _job(title="Restricted vacancy", tier="C", filter_decision="reject", fit_score=0),
        ],
    )

    assert len(soup.select(".job-card")) == 2
    low_fit = soup.select_one('[data-group="low_fit"] .job-card')
    rejected = soup.select_one('[data-group="reject"] .job-card')
    assert low_fit.h3.text == "Adjacent opportunity"
    assert rejected.h3.text == "Restricted vacancy"
    assert "Низкое соответствие" in low_fit.select_one(".badge").text
    assert "Явное ограничение" in rejected.select_one(".badge").text
    assert _data(soup)[0]["evaluations"][0]["decision"] == "review"


def test_selected_profile_shows_its_verdict_and_score_not_global_best(tmp_path):
    job = _job(
        profile_evaluations=[
            _profile(),
            _profile(
                "product",
                profile_name="Product Management",
                decision="reject",
                tier="C",
                fit_score=0,
                fit_reason="Product requirements differ.",
                blocker_signals=["country:US only"],
            ),
        ]
    )
    _, soup = _render(tmp_path, [job], profile_id="product")

    product = soup.select_one('.evaluation[data-profile-id="product"]')
    analytics = soup.select_one('.evaluation[data-profile-id="analytics"]')
    assert not product.has_attr("hidden")
    assert analytics.has_attr("hidden")
    assert "0 баллов" in product.text
    assert "Явное ограничение" in product.text
    assert soup.select_one('[data-group="reject"] .job-card') is not None
    assert soup.select_one('#profile-filter option[value="product"]').has_attr("selected")
    scores = {item["profile_id"]: item["fit_score"] for item in _data(soup)[0]["evaluations"]}
    assert scores == {"analytics": 20.0, "product": 0.0}


def test_disabled_and_stale_evaluations_do_not_leak_into_current_review(tmp_path):
    job = _job(
        profile_evaluations=[
            _profile("disabled", profile_name="Do not show disabled", fit_score=99),
            _profile("analytics", fit_reason="Do not show stale", profile_version="old"),
            _profile("product", profile_name="Product Management", tier="B", fit_score=9),
        ]
    )
    html, soup = _render(
        tmp_path,
        [job],
        active_profile_ids=["analytics", "product"],
        active_profile_versions={"analytics": "version-1", "product": "version-1"},
    )

    assert "Do not show disabled" not in html
    assert "Do not show stale" not in html
    assert [item["profile_id"] for item in _data(soup)[0]["evaluations"]] == ["product"]
    assert "9 баллов" in soup.select_one(".evaluation").text


def test_job_without_current_evaluation_requests_reevaluation_instead_of_showing_old_best(tmp_path):
    _, soup = _render(
        tmp_path,
        [_job(profile_evaluations=[_profile(profile_version="old")])],
        active_profile_ids=["analytics"],
        active_profile_versions={"analytics": "new"},
    )

    assert soup.select_one(".evaluation") is None
    assert soup.select_one('[data-group="stale"] .job-card') is not None
    assert not soup.select_one(".stale-note").has_attr("hidden")
    assert _data(soup)[0]["evaluations"] == []


def test_same_version_profile_with_old_job_content_requires_reevaluation(tmp_path):
    job = _job(
        content_hash="content-v2",
        profile_evaluations=[_profile(fit_reason="Outdated content score.")],
    )
    html, soup = _render(
        tmp_path,
        [job],
        active_profile_ids=["analytics"],
        active_profile_versions={"analytics": "version-1"},
    )

    assert "Outdated content score." not in html
    assert _data(soup)[0]["evaluations"] == []
    assert soup.select_one('[data-group="stale"] .job-card') is not None


def test_best_profile_uses_tier_then_decision_before_score(tmp_path):
    job = _job(
        profile_evaluations=[
            _profile("review", tier="C", decision="review", fit_score=5),
            _profile("pass", tier="C", decision="pass", fit_score=1),
        ]
    )
    _, soup = _render(tmp_path, [job])

    assert not soup.select_one('.evaluation[data-profile-id="pass"]').has_attr("hidden")
    assert soup.select_one('.evaluation[data-profile-id="review"]').has_attr("hidden")


def test_summary_preserves_all_four_active_streams_even_without_jobs(tmp_path):
    names = {
        "product": "Product Management",
        "analytics": "Analytics Leadership",
        "business": "Business Analytics",
        "science": "Data Science Management",
    }
    _, soup = _render(
        tmp_path,
        [],
        active_profile_ids=list(names),
        active_profile_versions=dict.fromkeys(names, "v1"),
        active_profile_names=names,
    )

    summaries = soup.select(".stream-summary")
    assert [item.strong.text for item in summaries] == list(names.values())
    assert all("0 вакансий" in item.text for item in summaries)


def test_untrusted_text_and_json_cannot_create_html_or_close_script(tmp_path):
    attack = '</script><script>alert("unsafe")</script><img src=x onerror=alert(1)>'
    job = _job(
        title=attack,
        company="Example <svg onload=alert(1)>",
        description_clean=attack,
        original_url='https://example.com/job?q="&team=analytics',
        apply_url="javascript:alert(1)",
        profile_evaluations=[_profile(profile_name=attack, fit_reason=attack, risks=[attack])],
    )
    html, soup = _render(tmp_path, [job])

    assert len(soup.find_all("script")) == 2
    assert not soup.find_all(["img", "svg"])
    assert soup.h3.text == attack
    assert "<" not in soup.select_one("#jobs-data").string
    assert "\\u003c/script\\u003e" in html
    assert _data(soup)[0]["evaluations"][0]["profile_name"] == attack
    assert all(anchor["href"].startswith("https://") for anchor in soup.find_all("a"))
    assert all(not anchor.has_attr("onerror") for anchor in soup.find_all("a"))
    assert "&quot;&amp;team=analytics" in html
    assert all(not script.has_attr("src") for script in soup.find_all("script"))


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,unsafe",
        "//example.com/job",
        "ftp://example.com/job",
        "https://[broken",
        "https://example.com/\njob",
        "https://example.com\\unsafe",
        "https:///job",
    ],
)
def test_only_valid_http_links_are_rendered(tmp_path, url):
    _, soup = _render(tmp_path, [_job(original_url=url, apply_url=url)])
    assert soup.find_all("a") == []


def test_full_description_and_first_discovery_date_are_preserved(tmp_path):
    description = "Description start " + "a" * 1200 + " Critical hiring condition at the end."
    _, soup = _render(tmp_path, [_job(description_clean=description)])

    assert soup.select_one(".description-text").text == description
    assert "01.10.2026" in soup.select_one(".discovered").text
    assert "04.10.2026" not in soup.select_one(".discovered").text
    assert _data(soup)[0]["seen_at"] == datetime(2026, 10, 1, 12, tzinfo=UTC).timestamp()


def test_known_rule_codes_are_readable_in_the_visible_card(tmp_path):
    risks = [
        "geography:eligibility_unconfirmed",
        "language:working_language_unconfirmed",
        "incomplete_description",
        "title_blocker:data engineer",
        "review_flag:preferred in",
    ]
    _, soup = _render(
        tmp_path,
        [
            _job(
                profile_evaluations=[
                    _profile(
                        decision="review",
                        tier="B",
                        risks=risks,
                        fit_reason=f"Manual review: {', '.join(risks[:3])}.",
                    ),
                ]
            )
        ],
    )
    panel = soup.select_one(".evaluation")
    visible_notes = " ".join(item.text for item in panel.select(":scope > .evidence"))

    assert "Проверить, может ли компания нанимать из Бразилии" in visible_notes
    assert "Уточнить рабочий язык" in visible_notes
    assert "Описание неполное — проверьте страницу вакансии" in visible_notes
    assert "Проверить специализацию роли в названии: «data engineer»" in visible_notes
    assert "Уточнить условие: «preferred in»" in visible_notes
    assert all(code not in visible_notes for code in risks)
    assert "Вакансия сохранена для ручного отбора" in panel.select_one(".reason").text
    assert "Manual review:" not in panel.select_one(".reason").text
    assert _data(soup)[0]["evaluations"][0]["risks"] == risks


def test_hard_blocker_explanation_is_readable_and_arbitrary_ai_prose_is_preserved(tmp_path):
    blocker = "phrase_blocker:us work authorization required"
    prose = "Manual review: The role may fit because of your pricing experience."
    _, soup = _render(
        tmp_path,
        [
            _job(
                profile_evaluations=[
                    _profile(
                        decision="reject",
                        tier="C",
                        risks=[],
                        blocker_signals=[blocker],
                        fit_reason=f"Rejected due to: {blocker}",
                    )
                ]
            ),
            _job(profile_evaluations=[_profile(fit_reason=prose)]),
        ],
    )

    rejected = soup.select_one('[data-group="reject"] .evaluation')
    assert "Вакансия исключена по настройкам профиля" in rejected.select_one(".reason").text
    assert "Обязательное условие не соответствует профилю" in rejected.select_one(".evidence").text
    assert "phrase_blocker:" not in rejected.select_one(".evidence").text
    assert soup.select_one('[data-group="opportunities"] .reason').text == prose


def test_generated_reasons_remain_readable_after_signal_sorting_and_scoring(tmp_path):
    risks = [
        "geography:eligibility_unconfirmed",
        "incomplete_description",
        "language:working_language_unconfirmed",
    ]
    _, soup = _render(
        tmp_path,
        [
            _job(
                profile_evaluations=[
                    _profile(
                        decision="review",
                        tier="B",
                        risks=risks,
                        fit_reason=(
                            "Manual review: incomplete_description, "
                            "geography:eligibility_unconfirmed, "
                            "language:working_language_unconfirmed."
                        ),
                    )
                ]
            ),
            _job(
                profile_evaluations=[
                    _profile(
                        risks=[],
                        matched_signals=["title_signal:head of analytics", "title_weight:head"],
                        fit_reason=(
                            "Passed hard filters with signals: title_signal:head of analytics"
                        ),
                    )
                ]
            ),
        ],
    )

    reasons = [item.text for item in soup.select(".evaluation > .reason")]
    assert reasons == [
        "Вакансия сохранена для ручного отбора. Уточните вопросы ниже.",
        "Есть совпадения с профилем. Обязательные ограничения не обнаружены.",
    ]
