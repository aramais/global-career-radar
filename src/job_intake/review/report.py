from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from job_intake.storage.models import JobORM


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _safe_url(value: object) -> str:
    url = _text(value).strip()
    if not url or any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
        return ""
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
            return url
    except ValueError:
        pass
    return ""


def _date(value: datetime | None) -> tuple[str, float]:
    if value is None:
        return "Дата неизвестна", 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d.%m.%Y"), value.timestamp()


def _score(value: object) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _evaluation(row: object, *, legacy: bool = False) -> dict:
    return {
        "profile_id": _text(getattr(row, "profile_id", "legacy")) or "legacy",
        "profile_name": _text(getattr(row, "profile_name", "Основной профиль")),
        "profile_version": _text(getattr(row, "profile_version", "")),
        "content_hash": _text(getattr(row, "content_hash", "")),
        "decision": _text(getattr(row, "filter_decision" if legacy else "decision", "review")),
        "tier": _text(getattr(row, "tier", "C")),
        "fit_score": _score(getattr(row, "fit_score", 0)),
        "matched_signals": list(getattr(row, "matched_signals", None) or []),
        "blocker_signals": list(
            getattr(row, "detected_blockers" if legacy else "blocker_signals", None) or []
        ),
        "fit_reason": _text(getattr(row, "fit_reason", "")),
        "risks": list(getattr(row, "risks", None) or []),
        "audit_log": list(getattr(row, "audit_log", None) or []),
    }


def _group(evaluation: dict | None) -> str:
    if evaluation is None:
        return "stale"
    if evaluation["decision"] == "reject":
        return "reject"
    return "low_fit" if evaluation["tier"] == "C" else "opportunities"


def _best(evaluations: list[dict]) -> dict | None:
    return min(
        evaluations,
        key=lambda item: (
            {"A": 0, "B": 1, "C": 2}.get(item["tier"], 3),
            {"pass": 0, "review": 1, "reject": 2}.get(item["decision"], 3),
            -item["fit_score"],
            item["profile_id"],
        ),
        default=None,
    )


def _list_markup(label: str, values: list) -> str:
    if not values:
        return ""
    return (
        f'<div class="evidence"><strong>{escape(label)}</strong><ul>'
        + "".join(f"<li>{escape(_text(value))}</li>" for value in values)
        + "</ul></div>"
    )


def _human_signal(value: object) -> str:
    text = _text(value)
    labels = {
        "geography:eligibility_unconfirmed": "Проверить, может ли компания нанимать из Бразилии",
        "geography:applicant_locations_unconfirmed": (
            "Уточнить список стран, из которых компания принимает кандидатов"
        ),
        "geography:residency_location_unconfirmed": "Уточнить требование к месту проживания",
        "geography:onsite_location_unknown": "Уточнить адрес офиса и место работы",
        "language:working_language_unconfirmed": "Уточнить рабочий язык",
        "incomplete_description": "Описание неполное — проверьте страницу вакансии",
        "status:closed": "Вакансия закрыта",
    }
    if text in labels:
        return labels[text]
    prefixes = {
        "title_blocker:": "Проверить специализацию роли в названии",
        "desc_blocker:": "Проверить специализацию роли в описании",
        "review_flag:": "Уточнить условие",
        "status_phrase:": "В описании указано закрытие вакансии",
        "company_blacklist:": "Компания исключена из поиска",
        "language:working_language:": "Рабочий язык не соответствует профилю",
        "timezone_blocker:": "Требуемый часовой пояс не соответствует профилю",
        "geo_blocker:applicant_locations:": "Ограничение страны найма",
        "geo_blocker:onsite:": "Офисная работа вне выбранной локации",
        "geo_blocker:": "Ограничение места проживания или найма",
        "phrase_blocker:": "Обязательное условие не соответствует профилю",
    }
    for prefix, label in prefixes.items():
        if text.startswith(prefix):
            return f"{label}: «{text[len(prefix) :].strip()}»"
    return text


def _joined_signals_match(detail: str, signals: list, maximum: int) -> bool:
    """Recognize formatter output even after signal sorting or scoring adds more signals."""
    if not detail or maximum <= 0:
        return False
    for value in signals:
        signal = _text(value)
        if signal and detail == signal:
            return True
        prefix = signal + ", "
        if (
            signal
            and detail.startswith(prefix)
            and _joined_signals_match(detail[len(prefix) :], signals, maximum - 1)
        ):
            return True
    return False


def _human_fit_reason(evaluation: dict) -> str:
    """Translate recognized rule explanations while preserving arbitrary AI prose."""
    reason = evaluation["fit_reason"]
    risks = evaluation["risks"]
    blockers = evaluation["blocker_signals"]
    matches = evaluation["matched_signals"]
    review_prefix = "Manual review: "
    review_detail = reason[len(review_prefix) : -1]
    if (
        evaluation["decision"] == "review"
        and reason.startswith(review_prefix)
        and (
            reason.endswith(".")
            and (
                review_detail == "insufficient high-confidence profile signals"
                or _joined_signals_match(review_detail, risks, 3)
            )
        )
    ):
        return (
            "Вакансия сохранена для ручного отбора. Уточните вопросы ниже."
            if risks
            else "Недостаточно явных совпадений с профилем. Проверьте обязанности и условия."
        )
    if reason == (
        "Manual review: passed blockers but lacked enough high-confidence bridge-role signals."
    ):
        return "Недостаточно явных совпадений с профилем. Проверьте обязанности и условия."
    reject_prefix = "Rejected due to: "
    if (
        evaluation["decision"] == "reject"
        and reason.startswith(reject_prefix)
        and (_joined_signals_match(reason[len(reject_prefix) :], blockers, 4))
    ):
        return "Вакансия исключена по настройкам профиля. Причины указаны ниже."
    pass_prefix = "Passed hard filters with signals: "
    if evaluation["decision"] == "pass" and reason.startswith(pass_prefix):
        pass_detail, separator, risk_detail = reason[len(pass_prefix) :].partition("; risks: ")
        valid_matches = pass_detail == "deterministic match" or _joined_signals_match(
            pass_detail, matches, 4
        )
        valid_risks = not separator or _joined_signals_match(risk_detail, risks, 2)
        if valid_matches and valid_risks:
            return "Есть совпадения с профилем. Обязательные ограничения не обнаружены."
    return reason


def _evaluation_markup(evaluation: dict, selected: dict | None) -> str:
    group = _group(evaluation)
    labels = {
        "pass": "Подходит по правилам",
        "review": "Проверить вручную",
        "reject": "Явное ограничение",
    }
    verdict = (
        "Низкое соответствие"
        if group == "low_fit"
        else labels.get(evaluation["decision"], "Проверить вручную")
    )
    hidden = "" if evaluation is selected else " hidden"
    name = evaluation["profile_name"] or evaluation["profile_id"]
    return (
        f'<section class="evaluation" data-profile-id="{escape(evaluation["profile_id"])}"'
        f'{hidden}><div class="evaluation-heading">'
        f'<span class="badge {group}">{escape(verdict)} · {escape(evaluation["tier"])}</span>'
        f"<strong>{evaluation['fit_score']:g} баллов</strong>"
        f'<span class="profile-name">{escape(name)}</span></div>'
        f'<p class="reason">{escape(_human_fit_reason(evaluation))}</p>'
        + _list_markup("Риски и вопросы", [_human_signal(item) for item in evaluation["risks"]])
        + _list_markup(
            "Обязательные ограничения",
            [_human_signal(item) for item in evaluation["blocker_signals"]],
        )
        + '<details class="signals"><summary>Почему такая оценка</summary>'
        + _list_markup("Совпадения", evaluation["matched_signals"])
        + _list_markup("Проверки", evaluation["audit_log"])
        + f'<p class="version">Версия профиля: {escape(evaluation["profile_version"] or "—")}</p>'
        + "</details></section>"
    )


def _card_markup(job: JobORM, index: int, evaluations: list[dict], selected: dict | None) -> str:
    first_seen, _ = _date(getattr(job, "first_seen_at", None))
    metadata = [
        getattr(job, "location_text", None),
        getattr(job, "remote_text", None),
        getattr(job, "employment_type", None),
        getattr(job, "salary_text", None),
        getattr(job, "timezone_text", None),
    ]
    metadata_text = " · ".join(dict.fromkeys(_text(value) for value in metadata if value))
    source_url = _safe_url(getattr(job, "original_url", ""))
    apply_url = _safe_url(getattr(job, "apply_url", "")) or source_url
    links = ""
    for label, url in [("Отклик", apply_url), ("Источник", source_url)]:
        if url:
            links += (
                f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{label}</a>'
            )
    chips = "".join(
        f'<button class="profile-chip" type="button" '
        f'data-select-profile="{escape(evaluation["profile_id"])}">'
        f"{escape(evaluation['profile_name'] or evaluation['profile_id'])}"
        f" · {escape(evaluation['tier'])} · {evaluation['fit_score']:g}</button>"
        for evaluation in evaluations
    )
    description = _text(
        getattr(job, "description_clean", None) or getattr(job, "description_raw", "")
    )
    stale_hidden = " hidden" if selected is not None else ""
    return (
        f'<article class="job-card" data-job-index="{index}">'
        '<div class="card-heading"><div>'
        f'<h3>{escape(_text(job.title))}</h3><p class="company">{escape(_text(job.company))}</p>'
        f'</div><nav class="links" aria-label="Ссылки вакансии">{links}</nav></div>'
        f'<p class="metadata">{escape(metadata_text or "Условия требуют проверки")}</p>'
        f'<p class="discovered">Найдена {first_seen} · {escape(_text(job.source))}</p>'
        f'<div class="profile-chips">{chips}</div>'
        + "".join(_evaluation_markup(item, selected) for item in evaluations)
        + f'<p class="stale-note"{stale_hidden}>'
        "Нет актуальной оценки. Пересчитайте сохранённые вакансии для текущих профилей.</p>"
        + '<details class="description"><summary>Полное сохранённое описание</summary>'
        f'<div class="description-text">{escape(description or "Описание не сохранено.")}</div>'
        + "</details></article>"
    )


def render_html_report(
    jobs: list[JobORM],
    output_path: Path,
    *,
    profile_id: str | None = None,
    active_profile_ids: list[str] | None = None,
    active_profile_versions: dict[str, str] | None = None,
    active_profile_names: dict[str, str] | None = None,
) -> Path:
    """Render an offline review feed, preserving low-fit jobs and profile-specific verdicts."""
    active_ids = set(active_profile_ids) if active_profile_ids is not None else None
    names = dict(active_profile_names or {})
    data = []
    groups: dict[str, list[str]] = {"opportunities": [], "low_fit": [], "reject": [], "stale": []}
    for index, job in enumerate(jobs):
        stored = list(getattr(job, "profile_evaluations", None) or [])
        evaluations = []
        content_hash = _text(getattr(job, "content_hash", ""))
        for row in stored:
            item = _evaluation(row)
            item_id = item["profile_id"]
            if active_ids is not None and item_id not in active_ids:
                continue
            if active_profile_versions is not None and (
                item_id not in active_profile_versions
                or item["profile_version"] != active_profile_versions[item_id]
            ):
                continue
            if content_hash and item["content_hash"] != content_hash:
                continue
            if item_id in names:
                item["profile_name"] = names[item_id]
            evaluations.append(item)
            names.setdefault(item_id, item["profile_name"] or item_id)
        if not stored and active_ids is None and active_profile_versions is None:
            evaluations = [_evaluation(job, legacy=True)]
            names.setdefault("legacy", "Основной профиль")
        selected = (
            next((item for item in evaluations if item["profile_id"] == profile_id), None)
            if profile_id
            else _best(evaluations)
        )
        _, seen_at = _date(getattr(job, "first_seen_at", None))
        data.append(
            {
                "index": index,
                "seen_at": seen_at,
                "search_text": " ".join(
                    [
                        _text(job.company),
                        _text(job.title),
                        _text(
                            getattr(job, "description_clean", None)
                            or getattr(job, "description_raw", "")
                        ),
                    ]
                ),
                "evaluations": evaluations,
            }
        )
        groups[_group(selected)].append(_card_markup(job, index, evaluations, selected))

    profile_ids = list(active_profile_ids) if active_profile_ids is not None else sorted(names)
    if active_profile_versions is not None:
        profile_ids = [item_id for item_id in profile_ids if item_id in active_profile_versions]
    options = '<option value="">Все потоки · лучшая актуальная оценка</option>'
    summaries = []
    for item_id in profile_ids:
        name = names.get(item_id, item_id)
        selected_option = " selected" if item_id == profile_id else ""
        options += f'<option value="{escape(item_id)}"{selected_option}>{escape(name)}</option>'
        items = [
            evaluation
            for job in data
            for evaluation in job["evaluations"]
            if evaluation["profile_id"] == item_id
        ]
        high_fit = sum(
            item["tier"] in {"A", "B"} and item["decision"] != "reject" for item in items
        )
        review = sum(item["decision"] == "review" for item in items)
        summaries.append(
            f'<button class="stream-summary" type="button" '
            f'data-select-profile="{escape(item_id)}"><strong>{escape(name)}</strong>'
            f"<span>{len(items)} вакансий · {high_fit} A/B · {review} проверить</span></button>"
        )
    titles = {
        "opportunities": "Вакансии для отбора",
        "low_fit": "Низкое соответствие · C — доступны для просмотра",
        "reject": "Явные ограничения — проверьте причины",
        "stale": "Нужна переоценка",
    }
    sections = "".join(
        f'<section class="feed-group" data-group="{group}"><h2>{title}</h2>'
        f'<div class="job-list">{"".join(groups[group])}</div></section>'
        for group, title in titles.items()
    )
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
    for character, replacement in [
        ("&", "\\u0026"),
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ]:
        payload = payload.replace(character, replacement)
    html = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Личный поиск работы</title><style>" + _STYLES + "</style></head><body>"
        '<main><header><p class="eyebrow">Личный поиск работы</p>'
        "<h1>Возможности по всем потокам</h1>"
        '<p class="intro">Сравнивайте роли и условия. Низкая оценка сохраняет вакансию '
        "для ручного отбора; обязательные ограничения показаны отдельно.</p></header>"
        f'<div class="stream-summaries">{"".join(summaries)}</div>'
        '<form class="filters" onsubmit="return false">'
        f'<label>Поток<select id="profile-filter">{options}</select></label>'
        '<label>Результат<select id="decision-filter">'
        '<option value="all">Все результаты</option><option value="pass">Подходит</option>'
        '<option value="review">Проверить вручную</option>'
        '<option value="low_fit">Низкое соответствие · C</option>'
        '<option value="reject">Явное ограничение</option>'
        '<option value="stale">Нужна переоценка</option></select></label>'
        '<label>Порядок<select id="sort-order"><option value="fit">Соответствие</option>'
        '<option value="newest">Сначала новые</option></select></label>'
        '<label class="search-label">Поиск<input id="text-filter" type="search" '
        'placeholder="Компания, роль или слово в описании"></label></form>'
        f'<p id="result-count" aria-live="polite">Сохранено вакансий: {len(jobs)}</p>'
        f'{sections}<p id="empty-state" hidden>По этим условиям вакансий нет. '
        "Попробуйте другой поток или результат.</p>"
        "<noscript>Фильтры работают при включённом JavaScript. Все сохранённые "
        "вакансии доступны ниже.</noscript></main>"
        f'<script type="application/json" id="jobs-data">{payload}</script>'
        "<script>" + _SCRIPT + "</script></body></html>"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


_STYLES = """
:root{color-scheme:light;--ink:#172d35;--muted:#60717a;--line:#d7e1e2;--accent:#08776c}
*{box-sizing:border-box}body{margin:0;background:#f3f6f5;color:var(--ink);
font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1140px;margin:auto;padding:36px 24px 60px}h1{font-size:32px;margin:8px 0 12px}
h2{font-size:19px;margin:26px 0 12px}h3{font-size:20px;margin:0 0 6px;overflow-wrap:anywhere}
p{margin:8px 0}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.13em;
color:var(--accent);font-weight:750}.intro{max-width:800px;color:var(--muted);line-height:1.6}
.stream-summaries{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));
gap:10px;margin:24px 0}.stream-summary{text-align:left;background:#fff;border:1px solid var(--line);
border-radius:10px;padding:15px;cursor:pointer;color:var(--ink)}.stream-summary span{display:block;
font-size:12px;color:var(--muted);margin-top:8px}.stream-summary.active{border-color:var(--accent);
background:#eef9f6}.filters{display:flex;flex-wrap:wrap;gap:12px;padding:16px;background:#e7eeec;
border-radius:10px}label{display:flex;flex-direction:column;gap:6px;font-size:12px;font-weight:650}
select,input{max-width:100%;border:1px solid #bccdcb;border-radius:6px;background:#fff;padding:9px;
color:var(--ink);font:inherit;font-size:14px}.search-label{flex:1;min-width:210px}
#result-count{color:var(--muted);font-size:13px;margin:16px 0}.job-list{display:grid;gap:12px}
.job-card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px}
.card-heading{display:flex;justify-content:space-between;gap:15px}.company{font-weight:650;
color:var(--muted)}.links{display:flex;align-items:flex-start;flex-wrap:wrap;gap:8px;flex-shrink:0}
a{color:var(--accent)}.links a{font-size:13px;border:1px solid #b4d4cb;border-radius:6px;
padding:7px 10px;text-decoration:none}.metadata{font-size:14px;line-height:1.6}.discovered,.version{
font-size:12px;color:var(--muted)}.profile-chips{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0}
.profile-chip{background:#f4f7f6;border:1px solid var(--line);padding:5px 8px;border-radius:20px;
font-size:11px;color:var(--ink);cursor:pointer}.evaluation-heading{display:flex;align-items:center;
gap:10px;flex-wrap:wrap;font-size:13px}.badge{border-radius:5px;padding:5px 8px;background:#e4f3ed;
color:#176148}.badge.low_fit{background:#f2eee2;color:#746130}.badge.reject{background:#fae9e7;
color:#8c3633}.profile-name{color:var(--muted)}.reason{font-size:14px;line-height:1.6}
.evidence{font-size:13px;line-height:1.5;margin:12px 0}.evidence ul{margin:5px 0;padding-left:20px}
details{margin-top:12px;font-size:13px}summary{cursor:pointer;color:var(--accent);padding:4px 0}
.description{border-top:1px solid var(--line);padding-top:10px}
.description-text{white-space:pre-wrap;
line-height:1.7;margin-top:12px;overflow-wrap:anywhere;max-height:560px;overflow:auto}
.stale-note{color:#756325;font-size:14px;line-height:1.6}.signals{color:var(--muted)}
[hidden]{display:none!important}:focus-visible{outline:3px solid #56a899;outline-offset:3px}
@media(max-width:620px){main{padding:24px 14px}h1{font-size:27px}.card-heading{display:block}
.links{margin:14px 0}.job-card{padding:16px}.filters label{width:100%}}
"""


_SCRIPT = """
(() => {
  const jobs = JSON.parse(document.getElementById('jobs-data').textContent);
  const profile = document.getElementById('profile-filter');
  const decision = document.getElementById('decision-filter');
  const sort = document.getElementById('sort-order');
  const search = document.getElementById('text-filter');
  const cards = new Map(Array.from(document.querySelectorAll('.job-card'))
    .map(card => [Number(card.dataset.jobIndex), card]));
  const sections = new Map(Array.from(document.querySelectorAll('.feed-group'))
    .map(section => [section.dataset.group, section]));
  const tierOrder = {A: 0, B: 1, C: 2};
  const decisionOrder = {pass: 0, review: 1, reject: 2};
  const compare = (a, b) => (tierOrder[a.tier] ?? 3) - (tierOrder[b.tier] ?? 3)
    || (decisionOrder[a.decision] ?? 3) - (decisionOrder[b.decision] ?? 3)
    || b.fit_score - a.fit_score
    || (a.profile_id < b.profile_id ? -1 : a.profile_id > b.profile_id ? 1 : 0);
  const selected = job => profile.value
    ? job.evaluations.find(item => item.profile_id === profile.value)
    : [...job.evaluations].sort(compare)[0];
  const group = item => !item ? 'stale' : item.decision === 'reject' ? 'reject'
    : item.tier === 'C' ? 'low_fit' : 'opportunities';
  function refresh() {
    const counts = {opportunities: 0, low_fit: 0, reject: 0, stale: 0};
    const entries = jobs.map(job => ({job, evaluation: selected(job)}));
    entries.sort((a, b) => sort.value === 'newest'
      ? b.job.seen_at - a.job.seen_at || a.job.index - b.job.index
      : (b.evaluation?.fit_score ?? -Infinity) - (a.evaluation?.fit_score ?? -Infinity)
        || b.job.seen_at - a.job.seen_at || a.job.index - b.job.index);
    const query = search.value.trim().toLocaleLowerCase();
    for (const {job, evaluation} of entries) {
      const card = cards.get(job.index);
      const key = group(evaluation);
      const matchesDecision = decision.value === 'all'
        || (['stale', 'low_fit', 'reject'].includes(decision.value)
          ? key === decision.value : evaluation?.decision === decision.value);
      const visible = matchesDecision
        && (!query || job.search_text.toLocaleLowerCase().includes(query));
      card.hidden = !visible;
      for (const panel of card.querySelectorAll('.evaluation')) {
        panel.hidden = panel.dataset.profileId !== evaluation?.profile_id;
      }
      card.querySelector('.stale-note').hidden = Boolean(evaluation);
      sections.get(key).querySelector('.job-list').append(card);
      if (visible) counts[key] += 1;
    }
    for (const [key, section] of sections) section.hidden = counts[key] === 0;
    const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
    document.getElementById('result-count').textContent =
      `Показано ${total} из ${jobs.length} · Для отбора ${counts.opportunities}`
      + ` · Низкое соответствие ${counts.low_fit} · Ограничения ${counts.reject}`;
    document.getElementById('empty-state').hidden = total > 0;
    for (const button of document.querySelectorAll('[data-select-profile]')) {
      button.classList.toggle('active', button.dataset.selectProfile === profile.value);
    }
  }
  for (const control of [profile, decision, sort, search]) {
    control.addEventListener('input', refresh);
  }
  for (const button of document.querySelectorAll('[data-select-profile]')) {
    button.addEventListener('click', () => {
      profile.value = button.dataset.selectProfile;
      refresh();
    });
  }
  refresh();
})();
"""
