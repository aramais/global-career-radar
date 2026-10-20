# Повторный аудит labels-v3: P0–P1 — 20.10.2026

## 1. Итог

**P0 в основном соответствует уточнённому контракту; P1 реализована функционально, но её приёмка без оговорок преждевременна.** Найдены **0 критических, 1 высокая, 4 средние и 6 низких находок**. Высокая R-H1 — ранее существовавшая ошибка связанной frozen-метрики, а не регрессия, внесённая labels-v3. Средние касаются выбора файла через symlink, невидимых metadata-ограничений, горячих клавиш и сохранения допустимых дат. Прежняя средняя S-M1 закрыта; прежняя низкая S-L2 получила подтверждённое влияние на P1 и включена в R-M4 без двойного подсчёта. Рабочие данные и критерии не изменились. P2 можно разрабатывать как следующую фазу, но измерения и пилот требуют исправления находок и подготовки перечисленных ниже предпосылок; текущий baseline не доказывает готовность пилота.

## 2. Объём и метод

- Репозиторий: /Users/admin/Documents/HR-Automation. Ветка feature/vacancy-tags; HEAD d63d5db9ebed8a91daaa28066a30846bbb2f29db.
- Проверена **текущая рабочая копия**, включая незакоммиченные исправления P0 и P1, новые numeric.py, schema_validation.py, utils/identifiers.py и новые тесты. Аудит не ограничен содержимым HEAD. База сравнения — 8bb3a2a; исходные P0-коммиты — 624bd17 и d63d5db.
- SHA-256 tracked diff относительно 8bb3a2a до аудита: 77d73105372c69c34e7f04f9e4f3e0c6059d353fdb68e5d7a4288f3a160d07fc. Новые untracked исходники/тесты дополнительно включены в общий файловый снимок; один tracked diff их не описывает.
- Прочитаны исходное задание аудита, AGENTS.md, design doc целиком, optimization §3–§5, соответствующие разделы README/architecture/index, rubric и обе схемы. Проверены labeling-модули, accuracy/frozen/cohorts/CLI, Gold store, форма CRM, Discovery и связанные тесты; diff и неизменённые pipeline/prefilter/scoring/storage сопоставлены с базой.
- Три независимые ветви проверки: P0, P1 store, P1 frontend. Дополнительно выполнены интеграционные CLI/read-only проверки и воспроизводимые пробы. Пересекающиеся тесты не суммируются.
- Все промежуточные файлы — **/private/tmp/labels-v3-p0-p1-audit-5trjsw91** (далее TMP). До проверок сохранены SHA-256 7709 существующих файлов; после проверок расхождений нет. Единственная новая запись аудита в репозиторий — этот отчёт.
- Python запускался в очищенном окружении с отключённым dotenv/bytecode/cache; audit hook запрещал сетевые подключения, DNS, bind, чтение настоящего .env и изменения репозитория. Рабочая SQLite открывалась через mode=ro и query_only=ON. Ключи и личная карьерная база не читались; содержимое рабочих notes не цитировалось. YAML сверялся с design doc, а не повторно с личной базой.
- Рабочая CRM, экспорт с миграцией, run/reevaluate/annotate и CLI с канонической когортой **не запускались**. Когорта проверена прямым вызовом run_relevance_accuracy без записи exposure. Браузер и реальные провайдеры в этом аудите не использовались.

**Ограничение тестового результата:** полный pytest завершился **3 failed, 3139 passed, 19 errors, 41,18 с**. Все 22 непрошедших случая остановлены запретом bind локального HTTP; иных падений не обнаружено. Ruff и git diff --check — pass. Исторические 3161 passed/51,90 с из §17 design doc относятся к предыдущей реализации с разрешённым loopback и не выдаются за результат этого аудита.

## 3. Трассировка требований

Статусы относятся к P0–P1. «Не входит» означает запланированные P2–P4, а не дефект отсутствующей реализации. К1–К7 — проверки из приложения.

| Требование | Статус | Доказательство / предел |
| --- | --- | --- |
| §0.1 Не менять критерии, stable ID, подтверждённые статусы | Реализовано | Diff rules/search_profiles/settings с 8bb3a2a пуст; rubric побайтно равен §5.2; К5/К7 |
| §0.2 Без неразрешённых платных вызовов | Реализовано в объёме | К2–К4: API0; P1 использует локальный store; tagger/бюджет будущей P2 не проверены |
| §0.3 Ключи из env, приватность diagnostics | Частично / не входит | Вызовов провайдера нет; error_diagnostic для нового tagger — P2; низкая R-L5 касается UI diagnostics |
| §0.4 Новые gold/выходы без перезаписи; ручной UI с backup/version | Частично | [gold_v3.py:228](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/gold_v3.py:228), [gold_ui.py:470](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:470); R-M1/R-L4 |
| §0.5 SQLite eval/export только read-only | Реализовано | [eval_runner.py:97](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/eval_runner.py:97): mode=ro; :102 query_only; К2–К4 |
| §0.6 Только аддитивные миграции | Не входит | Storage diff пуст; P0–P1 новых миграций не добавляют. Startup CRM остаётся операцией с create_schema, а не чистым чтением |
| §0.7 Ошибка модели сохраняет результат правил | Не входит | Новый tagger/fallback — P2; фактический P0/P1 сбой модели не заявляется проверенным |
| §0.8 Источник недоверенный | Реализовано для UI / не входит для prompt | UI строит текстовые узлы; DOM tests проверяют literal source. Prompt POLICY/S3 — P2 |
| §0.9 Прежнее поведение по умолчанию | Реализовано в объёме | Pipeline/prefilter/scoring/AI/settings diff пуст; импорт общей константы ID сохраняет синтаксис loader; v2 tests прошли кроме HTTP |
| §0.10 Стиль, атомарные записи, pytest | Частично | Ruff pass, staging/replace/lock есть; HTTP ограничен условиями; найденные края атомарности/identity ниже |
| §4.1 Enum, role taxonomy, null ≠ 0 | Реализовано | [schema.py:164](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:164), :415; schema/CLI tests; S-M1 пробы К5 |
| §4.2 Все шесть тегов, INDUSTRIES и решения §13 | Реализовано | Enum/schema/UI roundtrip; rubric §5.2 exact; provider section enforcement — P2 |
| §4.2 Human restrictive quote, basis, unknown/empty | Реализовано с уточнением | [schema.py:355](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:355), tests; human section policy неоднозначна, см. §8 |
| §4.3 Provenance, partial tags/gate, label_scope | Частично | Миграция 1:1 и partial save; [gold_ui.py:496](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:496); R-M4 |
| §4.4 Prediction schema, finite JSON, human-only причины | Реализовано | [schema.py:218](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:218), prediction validation/CLI/frozen tests; huge int и deep nonfinite К5 |
| §4.5 Inline schemas, snapshots, runtime validation | Реализовано с границей | Оба snapshot равны генераторам; строгий Python-int против portable schema — R-L3 |
| §4.5 Response builder duty/title/evidence/order | Реализовано в P0 | [schema.py:286](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:286); явные duty/title и нормализация provider пустых деталей — P2 |
| §5.2 YAML и независимые хеши | Реализовано | К5: 2913 bytes exact; version/prompt/aggregation независимы |
| §5.2 Proposed не применяется | Частично | Вложенный strong_companies исключён; root proposed — R-L2 |
| §5.3 Прецеденты, домен, cohort/holdout guards | Реализован loader | [rubric.py:274](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/rubric.py:274); registered-loader tests; реальных registry/прецедентов нет |
| §6.5 Base, cap other, upgrade, penalties, clamp | Реализовано | [aggregate.py:37](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:37), 44 aggregate cases; argenx2/BVGroup1/PrizePicks0 на синтетических фактах |
| §6.5 normalize_company и p2_candidate | Частично | [aggregate.py:21](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:21), :80; p2 cases проходят; R-L1 |
| §6.7 QWK/MAE/±1/Spearman/missing/recall/precision | Реализовано в relevance_scale | [metrics.py:226](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/metrics.py:226); covered pairs и независимые N явны; отдельная legacy frozen-ось R-H1 |
| §6.7 Очередь, P@k, NDCG/random, знаменатели | Реализовано | [metrics.py:94](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/metrics.py:94), К3/К5; при N2 и одном прогнозе P@5=1/2 |
| §6.7 Partial tags/gate, required_domain, stability | Реализовано | К5: независимые tag N1/gate N3; tag-only paired2/agreement0,5/κ0; tests |
| §6.7 Frozen hooks, versions, старые ключи | Частично | К4: три validate_run pass, 16 ключей saved сохранены; R-H1; поддержанный v3 freeze явно P2 |
| §6.8 Tier-map baseline и явная таблица | Реализовано | A→3/B→2/C→1/other→0, labeler rules:tier-map-v1 в отчётах К3 |
| §6.9 1:1, assisted→curated, SHA captured bytes | Реализовано | К2: 171 row, 1:1, output bytes equal, manifest SHA; tests single captured read |
| §6.9 Консервативный tag map, unknown locations | Реализовано | 146 migration cases; неизвестность/альтернативы → dropped; реальные human tags N0 |
| §6.9 Occupied path, staging, rollback | Частично | Обычные occupied/staging/publication tests проходят; dangling entry R-L4, crash-пара явно не обещана |
| §7 migrate-v3 / validate dispatch | Реализовано | К2, CLI tests; mixed/unknown schema отклоняются |
| §7 accuracy rules/labels, защита входов | Реализовано | К3, 42 CLI cases; labels требует enabled taxonomy и полный checker до отчёта; cohort CLI имеет exposure side effect |
| §7 label/export/--ai/--max-cost | Не входит | P2/P3; не считать отсутствие команд дефектом P0/P1 |
| §10 v2/v3, default v3/fallback, mixed rejection | Частично | [gold_ui.py:189](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:189), :220; К4; symlink identity R-M1 |
| §10 Шесть тегов, reasons, partial/empty/domain | Реализовано | [index.html:1557](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1557); 35 store/34 DOM cases |
| §10 Полный источник и выделяемые canonical quotes | Частично | [index.html:1776](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1776); HTML-list binding исправлен; metadata не показана — R-M2 |
| §10 Blind read model | В основном реализовано | _safe_source/_labels whitelist и P1 read-only К4; malformed diagnostic край R-L5 |
| §10 Горячие клавиши вне интерактивных контролов | Отклонение | [index.html:1623](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1623); R-M3 |
| §10 review_seconds только в journal, no-op | Реализовано | [gold_ui.py:452](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:452), :488; bounds/finite/dedup; no-op JSONL bytes unchanged |
| §10 Version/lock/backup/source binding, v2 | Частично | Store/legacy tests проходят; drift блокирует save; R-M1/R-M4; текущие HTTP не выполнены |
| §11 Полный pytest/Ruff, содержательность тестов | Частично | 3139 pass, 22 blocked HTTP; 542 labels collected, 529 выполнены; пробелы §6 |
| §12 P0/P1 готовность | Частично | Реализованный объём подтверждён с находками; новые labels metrics исправлены, общая frozen-ось и P1 края остаются |
| §12 P2–P4, §14 риски | Не входит / предпосылки | §7 этого отчёта; отсутствие теггера, адаптеров и pipeline не объявлено дефектом P0/P1 |

## 4. Находки

### R-H1 — высокий: frozen role_recall считает отсутствие обеих ролей правильным ответом

**Проверено.** [frozen_eval.py:271–274](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:271) сравнивает pred(row).get("role_label") с row.get("role_label") для всех relevance≥2, включая human role_label=null. Отсутствующий прогноз превращается в пустой dict, получается None==None. Синтетический частичный пример relevance3 даёт role_recall=1/1 при predictions={}, а конкретная предсказанная роль — 0/1 при отсутствии human-эталона роли. Независимое подтверждение на **10 валидных частичных строках текущего gold**, без изменения файлов: role_recall=10/10=1,0; role_classification accuracy имеет N0; relevance recall=0/10. Evidence: К6, frozen-partial-confirmation.json.

**Последствие:** метрика профессии вознаграждает отсутствие предсказаний и штрафует содержательный прогноз там, где правильная роль не размечена. Она также используется comparison gate ([frozen_eval.py:617](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:617)); ошибочный текущий релизный verdict не доказан. Три прежних run валидны, поскольку replay повторяет прежнюю формулу.

**Происхождение:** блок уже существовал в 8bb3a2a (:261–264). Это новая находка в проверяемом связанном eval, **не новая регрессия P0/P1**; новый relevance_scale recall считает пропуски правильно.

**Рекомендация:** формировать отдельную выборку relevant + известная human role; отсутствие предсказания не считать совпадением. Зафиксировать empty/abstention/partial cases. Изменение формулы версионировать, сохранив воспроизведение исторических run без перезаписи.

### R-M1 — средний: default symlink разветвляет канонический gold и обходит ожидаемый конфликт версии

**Проверено на TMP.** Явный path разрешается через resolve в [gold_ui.py:191](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:191), default в :206 сохраняется без resolve. Lock :477 и replace :446 используют получившиеся разные имена.

Сценарий: default vacancy-labels-v3.jsonl — symlink на canonical.jsonl; второй store открыт явно через тот же symlink. Оба читают одну version. Default save меняет relevance1→2, заменяя сам symlink regular-файлом. Explicit store затем **успешно** сохраняет gate с прежней version в target. Итог: default relevance2/gate=null, target relevance1/gate=unknown; bytes расходятся. См. audit-store-symlink.py/results.json.

**Последствие:** один первоначальный dataset становится двумя несогласованными копиями, процессы не разделяют lock/контроль изменений. Условие — default filename является ссылкой; на обычном regular path дефект не воспроизведён, рабочие данные не затронуты.

**Рекомендация:** одинаково канонизировать default и explicit path до фиксации/lock; если ссылки не поддерживаются, явно отклонять их до редактирования. Добавить default+explicit same-target regression.

### R-M2 — средний: человек не видит ограничения, присутствующие только в canonical metadata

**Проверено.** [index.html:1780–1795](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1780) показывает title/location/remote/description, но не source_metadata. Backend сохраняет допустимые metadata ([gold_ui.py:165](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:165)); source_units создаёт requirement units из applicant_location_requirements и working_language ([text.py:319](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/text.py:319), :351).

Синтетические “Applicants must live in Europe.” и “German required.” доходят до source/units, но отсутствуют **во всей модальной карточке** и quoteBlocks. Evidence: audit-ui-probes.json, canonical_metadata и source_field_visibility. Здесь цитируются только искусственные строки пробы.

**Выведенное последствие:** человек может размечать geography/language/gate по неполному источнику, тогда как следующий machine-path увидит эти ограничения. Ошибочная рабочая метка не утверждается.

**Рекомендация:** отображать релевантные whitelisted canonical metadata отдельными читаемыми/выделяемыми source-блоками, сохранив слепоту; проверить metadata-only constraints через DOM→quote→save. Это не предложение изменить критерии.

### R-M3 — средний: Enter на raw-preview summary сохраняет карточку вместо работы с контролом

**Проверено.** [index.html:1629](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1629) исключает INPUT/TEXTAREA/SELECT/BUTTON/A/contenteditable, но не SUMMARY, добавленный новым preview (:1802). DOM-проба создаёт настоящий summary формы: Enter получает handled=true/preventDefault=true и вызывает saveGold(true); клавиша i меняет gate на ineligible и делает форму dirty.

**Выведенное последствие:** клавиатурная работа с «Полное исходное оформление» может неожиданно сохранить/перелистнуть карточку вместо раскрытия preview. Перехват handler доказан; native браузер в этом аудите не запускался. Это прямой разрыв §10 «вне интерактивных контролов».

**Рекомендация:** исключать SUMMARY и интерактивных предков; покрыть summary/вложенный узел и отсутствие PATCH/next при штатном Enter. Текущий тест [test_labels_gold_web_ui.py:229](/Users/admin/Documents/HR-Automation/tests/test_labels_gold_web_ui.py:229) перечисляет шесть типов, summary среди них нет.

### R-M4 — средний: допустимая timestamp-разметка не сохраняется при смене календарной даты между timezone

**Проверено.** P1 записывает labeled_at как дату São Paulo ([gold_ui.py:496](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:496)); валидатор сравнивает только первые десять символов ([schema.py:491–493](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:491)).

Валидная строка first_labeled_at=2026-10-21T00:30:00+14:00 и labeled_at=…00:31… имеет can_save=True. Эти моменты — утро **20.10** в São Paulo. Изменённый save в 13:00 20.10 SP, то есть позже, отклонён: first_labeled_at cannot follow labeled_at. Контроль с обычными timestamps того же локального дня сохраняется. Evidence: audit-store-date.py/results.json с фиксированным временем.

**Последствие:** корректную строку P1 показывает доступной для сохранения, но запрещает правку. Это подтверждённое влияние прежней S-L2 на новую фазу; отдельно низкая S-L2 не считается. Дополнительно прежние пробы снова подтвердили принятие обратного порядка timestamps в одном дне и некорректного offset +00:60.

**Рекомендация:** определить семантику date/date-time, строго проверять offset, сравнивать aware timestamps в UTC и не терять timezone/time при ручном обновлении. Не менять человеческие решения ради обхода валидатора.

### Низкие находки

| ID | Проверенное доказательство | Последствие / рекомендация / происхождение |
| --- | --- | --- |
| R-L1 | [aggregate.py:27–29](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:27): Acme LLC→acme, но Acme (LLC)/Acme-LLC→acme llc, (S.A.) оставляет s a | Порядок удаления юрформ/пунктуации не соответствует §6.5. Согласовать нормализацию и тесты. Прежняя S-L1; текущий strong_companies пуст/proposed, оперативный эффект не доказан |
| R-L2 | [rubric.py:194–215](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/rubric.py:194): root proposed сохраняет anchors, их изменение меняет prompt hash; вложенный proposed корректно исключён | Общее правило §0/§5.2 имеет недокументированное root-исключение. Уточнить effective root guard до P2; текущий root confirmed не менять. Прежняя S-L3 |
| R-L3 | [schema_validation.py:53–58](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema_validation.py:53): Draft202012Validator принимает relevance=2.0, runtime отвергает | Это допустимая JSON Schema-семантика integer и более строгий runtime, а не ошибка стандарта. Явно документировать границу «паритета» и покрыть её. Прежняя S-L4 |
| R-L4 | [gold_workbook.py:45–50](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_workbook.py:45), [gold_v3.py:242](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/gold_v3.py:242): dangling output symlink принят, создан missing target и manifest | Не перезаписан существующий regular-файл; разрыв lexical occupied-path policy. Определить поддержку ссылок/проверять lexists до resolve. Прежняя S-L5, отдельна от P1 разветвления R-M1 |
| R-L5 | [schema.py:360–361](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:360) → [gold_ui.py:340–341](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:340): неизвестный tag key дословно попадает в warnings | Искусственно вложенный в имя ключа model decision достигает blind response при очищенном labels.tags. Обычная утечка значения через conventional key не найдена. Санитизировать неизвестные имена в diagnostics; это защитный край, не доказанная утечка штатных оценок |
| R-L6 | [index.html:1910–1918](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1910): после PATCH await refresh старый save-and-next открывает свой successor, даже если пользователь уже выбрал другую чистую карточку | DOM deferred-refresh probe заканчивается на старом successor two. Добавить editor/request identity check после await. Потеря dirty draft не доказана: discard confirmation сохраняется. Навигационная гонка существующего общего пути |

Для R-L1–R-L4 evidence — audit-p0-probes.json; R-L5 — audit-store-probe-results.json; R-L6 — audit-ui-probes.json. Пробы выполнены на TMP, причины и реальные gold notes не опубликованы.

## 5. Признанные отклонения и прежние находки

| Исходно признанное отклонение | Вердикт |
| --- | --- |
| company= у rules_relevance | Обосновано strong_company; §6.5 уже приведён к фактической сигнатуре |
| required_domain и basis в response | Обосновано; §4.5 обновлён. Provider-пустые детали нормализует будущий S3 |
| duty_unit_ids/title_unit_ids | Обосновано; явные списки нужны P2, широкий совместимый fallback не доказывает соблюдение секций |
| label_scope сохранён | Обосновано provenance; К2 подтвердил перенос 1:1 |
| Tiger Analytics=1 через pharma industry | Прежняя подстановка неверна; работодатель consultancy ≠ требуемый pharma domain. Design теперь отдельно показывает 2−2=0 и с focus 3−2=1. Человеческое Tiger1 сохранено; не обещать воспроизведения без подтверждённых тегов |
| domain_fit только requirement/industry | Пробел закрыт: required_domain входит в quality и κ при нужном human requirement |
| MAE/QWK/Spearman только covered pairs | Обосновано: у missing нет ordinal значения; coverage и собственные N рядом |
| build/load только v2 | Уточнение design обосновано: build использует v2 validator, load проверяет digest/policy/source, а не полную v3-схему. Поддержанный v3 freeze и gates — P2 |
| LabelResult/TagValue до P2 | Обосновано и явно перенесено в §3/§12 |
| cohort assisted→curated | Обосновано rename метода, не assisted_llm; [cohorts.py:152](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/cohorts.py:152), прямой cohort check К4 |
| proposed в старых §3/§12 против confirmed §5.2 | Старый конфликт исправлен; root proposed R-L2 остаётся отдельной границей |
| P1 quote only canonical block + separate raw preview | Обосновано unique binding; HTML-list UI→store regression проходит. Дополнить metadata и SUMMARY, не возвращать UI-маркеры в доказательство |

Высокая и пять средних **повторного** аудита A-H1/A-M1–A-M5 в исходных сценариях остаются закрытыми: conservative office mapping, v3 checks независимо от variant/metrics version, tag-only stability, huge integers, registered domain validator, enabled membership. **S-M1 третьего аудита закрыта:** new_enabled_role, new--role, new-role-, r0 и 0 приняты loader, gold/prediction runtime/portable schema и response; newline, uppercase, leading underscore, пробел и неизвестное enabled membership отклонены. Низкие прежние находки не были включены в исправления §17; их сохранение не трактуется как невыполнение поручения исправить только H/M.

## 6. Покрытие тестами

Collect-only: **542 labels cases**. В этом аудите прошли **529**, а 13 новых HTTP cases остановлены запретом сети. Это часть полного результата 3139 passed; повторные целевые прогоны не прибавляются к числу уникальных случаев.

| Файл / cases | Что покрыто | Пробел / предел |
| --- | --- | --- |
| test_labels_schema /68 | Enum, structure, snapshots, restrictive/other/domain/provenance, null/0, stream-ID boundary | Date/offset R-M4; portable integer R-L3 |
| test_labels_prediction_validation /71 | Closed/nested/finite, duplicates, roles, human-only reasons, huge scores | Provider S3 и секции — будущая P2 |
| test_labels_cli /42 | Migrate/validate/accuracy, protected inputs, enabled/unknown/disabled/missing taxonomy, accepted ID forms, huge JSON roundtrip | Сеть/пилот не доказываются fake CLI |
| test_labels_gold_v3 /146 | One-read/SHA/1:1, aliases/unknown/alternatives, dropped manifest, staging/publication/owned rollback | Dangling lexical entry R-L4; crash-pair явно вне гарантии |
| test_labels_metrics /26 | Ручные QWK/Spearman/NDCG, missing/P@k, independent samples, required_domain/focus, v1/v2 | Legacy independent_axes.role_recall не покрыт этим модулем — R-H1 |
| test_labels_frozen_compatibility /21 | V3 markers/custom variants, malformed/v1 guard, tag-only repeats, huge score | Нужна независимая partial-role regression R-H1 и schema-aware v3 gates |
| test_labels_rubric /42 | YAML/hash independence, nested proposed, real registered domain loader/holdout guards | Root proposed R-L2; реальные registry/approved reasons отсутствуют |
| test_labels_aggregate /44 | Formula/base/cap/penalties/clamp, p2, control facts, company normalization | Юрформы со скобками/дефисом R-L1; исторический Tiger не переоценивается |
| test_labels_gold_ui /35 | V2/v3 store, schema/membership/source, first-label fields, partial tags/reasons, blind whitelist, no-op/dedup/duration bounds, path pin | Default symlink R-M1; timestamp rollover R-M4; diagnostic R-L5 |
| test_labels_gold_web_ui /34 | Six tags, absent/unspecified/empty focus, domain null, array order, canonical quote→real store, hotkeys, save-time separation и conflict draft | No-op reset/retention time при 409 проверены дополнительными audit probes; metadata R-M2, summary R-M3, navigation R-L6; DOM harness не заменяет native браузер |
| test_labels_gold_web_server /13 | В коде: реальный GET/PATCH, valid/invalid/stale/partial/journal/explicit v2 | В текущем аудите **не выполнены** из-за bind guard |
| prompt/ai_schema/validate/gates/tagger/pipeline | Будущая таблица §11 | Отсутствие P2/P3 tests не считается отсутствием P0/P1 |

Дополнительные audit probes проверили same-target symlink, timezone rollover с настоящим store.save, canonical metadata→DOM omission, SUMMARY event dispatch, no-op/conflict/retry, concurrent edits разных строк, malformed diagnostics, partial-role recall и прежние lows. Они находятся только в TMP и **не добавлены в репозиторий как регрессионные тесты**.

## 7. Готовность к следующим фазам

| Предпосылка / риск §14 | Проверенное состояние / требуемая работа |
| --- | --- |
| P0 schema/метрики/миграция | Основной контракт и S-M1 подтверждены. Связанная legacy frozen-метрика R-H1 требует отдельного исправления с replay compatibility |
| P1 ручная разметка | Default v3 доступен, v2 совместим, шесть тегов и журнал есть; закрыть R-M1–R-M4 перед безоговорочной приёмкой |
| V3 freeze и gates | [frozen_eval.py:144](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:144) вызывает v2 validator; load :216–236 не валидирует v3 labels; full-tags gate :577 всё ещё требует len(tags)==19. Адаптировать весь build/save/load/compare-контракт, не только freeze-команду |
| Cohort registry / precedents | data/local/evaluation/cohorts.jsonl и data/local/labeling/precedents.jsonl отсутствуют; loader/guards есть. Нужны зарегистрированные calibration/development/holdout и утверждённые reasons |
| Актуальность gold | UI теперь выбирает v3; К2 дал byte equality новой миграции и рабочего снимка. Последующие правки v2 и v3 не синхронизируются автоматически — это разные каноны выбранного workflow |
| Достаточность измерения | Relevance N54/17 relevant, полных blind44/7. Не достигнуты 150+ решений/30+ relevant и необходимые independent holdout/repeat. Новых человеческих tag labels N0 |
| Зависимости / компоненты | jsonschema установлен и проверен; scope.py, gates.py/prompt.py/validate.py/tagger.py, LabelingConfig, LabelResult/TagValue и export orchestrator отсутствуют — запланированная P2 |
| Claims fallback / field order / evidence | Response builder есть; реальная регистрация labels у провайдеров, strict schema, явные duty/title, untrusted policy и S3 tests ещё нужны. Ошибка реального adapter не утверждается проверенной |
| Privacy / holdout exposure | Closed loader запрещает notes; полный prompt/error_diagnostic/display-time exposure ещё P2. Происхождение человеческого reason один loader доказать не может |
| Стоимость / ретраи / latency | Tagger ledger/BudgetGuard/price-missing/per-attempt usage/concurrency ещё не реализованы. API0 baseline не подтверждает бюджет будущего пилота |
| Default/pipeline complexity | Pipeline, scorer, prefilter, storage и AI adapters не менялись. Shadow/labels/tier-source parity и перестройка stream — P3 |
| Платный пилот | Только по отдельной команде пользователя после подготовки; модели/общий лимит $3 не пересматривались и не использовались |

## 8. Предлагаемые исправления design doc

Документ и критерии **не изменялись**. Предлагается уточнить технический контракт:

| Раздел | Было → стало |
| --- | --- |
| §6.7/§11 | Общие frozen hooks → отдельные human-known-role выборки/пропуски и versioned replay для independent_axes.role_recall |
| §10/§11 | Default path фиксируется → default/explicit имеют одинаковую canonical identity, lock и symlink policy; regression same-target |
| §10 | Исходный текст для шести тегов → перечислить canonical metadata constraints в видимом source/quote UI |
| §10/§11 | Hotkeys вне интерактивных контролов → явно включить SUMMARY/вложенные узлы и проверить отсутствие save при native раскрытии |
| §4.3/§4.5/§10 | Даты/chronology → строгий date-time/offset, UTC order, mixed-date policy и точность save timestamp |
| §4.2/§4.5 | Таблица секций общая; enforcement явно только для машины в P2 → определить, требуется ли human section enforcement. Проба принимает function_core из requirements и preferred_focus из company; по текущему §4.5 это **не объявляется** отсутствующим P1 S3 |
| §5.2/§0 | Любой proposed неэффективен → уточнить root anchors/notes и future prompt guard; текущий YAML не менять |
| §4.5 | Паритет schema/runtime → описать строгое Python-int расширение: portable 2.0 и runtime int различаются |
| §6.5/§6.9 | Нормализация юрформ / occupied paths → определить скобки/дефисы и lexical symlink entry |
| §10/§17 | Слепота и malformed extras → уточнить безопасные diagnostics и identity старого save-after-await |
| §6.7/§12/§14 | V3 freeze как предпосылка → дополнить schema-aware acceptance gates вместо 19 tags; сохранить registry/privacy/budget/holdout как незавершённые |
| §17 | 3161 passed относится к реализации → отделять исторический зелёный прогон от сетево ограниченного аудита и новых находок |

## 9. Приложение: команды и ключевые выводы

### К1. Среда, тесты и неизменённые части

Префикс Python/CLI:

    env -i HOME=/Users/admin PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR=/private/tmp LC_ALL=C PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=/private/tmp/labels-v3-p0-p1-audit-5trjsw91

Запущено:

    .venv/bin/python -m pytest -q --tb=short -p no:cacheprovider --basetemp="$TMP/pytest-tmp"
    .venv/bin/ruff check src tests --no-cache
    .venv/bin/python -m pytest --collect-only -q -p no:cacheprovider tests/test_labels_*.py
    git diff --check
    git diff --exit-code 8bb3a2a -- config/search_profiles.yaml config/rules.yaml config/settings.yaml src/job_intake/pipeline.py src/job_intake/annotation/prefilter.py src/job_intake/scoring src/job_intake/storage src/job_intake/annotation/ai.py

Результаты: **3 failed,3139 passed,19 errors/41,18 с**, Ruff pass, collect542, оба git diff checks pass. Все неуспешные pytest-случаи содержат PermissionError: Audit guard: network disabled на socket.bind:

- один Discovery HTTP;
- два job_archive HTTP;
- два crm_server HTTP;
- четыре legacy gold_web_server HTTP;
- тринадцать labels_gold_web_server HTTP.

Это 22 запретных HTTP-сценария, а не 22 подтверждённых дефекта кода. Логи: pytest.log, labels-collected.txt; после ограниченного прогона разрешения на сеть не запрашивались и обходов guard не выполнялось. .venv доступна через существующий symlink.

### К2. Миграция и validation

    .venv/bin/job-intake gold migrate-v3 --source data/local/gold/vacancy-tags-v2-labeled.jsonl --out "$TMP/migrated.jsonl"
    .venv/bin/job-intake gold validate --gold "$TMP/migrated.jsonl" --output "$TMP/validation.json"

Обе команды exit0. 171 строка, decision_labeled44, relevant7, labeled_rows55, relevance_only10; blind44/curated11/assisted_llm0; migrated_tags0/dropped_tags0. Valid=True. Проверены 1:1 ID/решения/source/hash/text_version/даты/label_scope/notes без вывода содержания; assisted→curated и first_labeled_at=labeled_at. Новая миграция побайтно совпала с рабочим v3. Tag map v2-to-v3-tags-v3. Проверки manifest и 1:1 — integration-summary.json.

### К3. Baseline

    .venv/bin/job-intake eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output "$TMP/rules.json"

Exit0, database_access=read_only, api_calls0. Явная таблица в JSON/Markdown: **A→3/B→2/C→1/other→0**. Сверка с сохранённым accuracy-rules.md: N54, QWK0,373134, recall≥2=12/17 совпали.

| Показатель | Результат |
| --- | --- |
| N /coverage | 54 /54 из54 |
| QWK /MAE /Spearman | 0,373134 /0,888889 /0,389303 |
| Exact /±1 | 21/54 /42/54 |
| Recall≥2 /precision≥2 | 12/17 /12/24 |
| P@5 /P@10 | 4/5 /8/10 |
| NDCG@5 /random | 0,874799 /0,261829 |
| NDCG@10 /random | 0,771175 /0,261603 |

### К4. Legacy, cohort, frozen и P1 read model

    .venv/bin/job-intake eval accuracy --variant saved --gold data/local/gold/vacancy-tags-v2-labeled.jsonl --output "$TMP/saved.json"
    .venv/bin/python "$TMP/integration.py"

Exit0. У saved совпали со старым after-apply-accuracy.json **все 16 верхних ключей**, наборы evaluation/operational/stability/readiness/selection и рекурсивная структура evaluation. Равенство значений при изменившемся числе gold не заявляется.

Прямой run_relevance_accuracy с канонической cohort выбрал163 строки: blind44/curated3/unlabeled116. Для blind: **N44, recall7/7, precision7/19, QWK0,433663**. Ни cohort, ни exposure log не менялись.

load_bundle + validate_run — pass для всех сохранённых пар:

| Run | Bundle |
| --- | --- |
| baseline-run.json | bundle.json |
| candidate-run-v2.json | release-bundle-v2.json |
| release-baseline-run-v2.json | release-bundle-v2.json |

GoldLabelStore только на чтение выбрал default v3, отдал6 tag options, stats total171/complete44/partial11/unlabeled116; проверенная detail can_save=True, основные machine fields отсутствуют. Полей working gold через PATCH не меняли. Evidence: integration-summary.json, blind-summary.json.

### К5. YAML, S-M1 и P0 synthetic

YAML §5.2 побайтно равен config/labeling_rubric.yaml: **2913 bytes**, SHA-256 9b7f767c2817df6a2ed6e5b1738d7586c21ae9ba5cc1ca707d36fc806e10f4d2.

- version labels-rubric-2026-10-20;
- prompt_rubric_hash 8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8;
- aggregation_hash 9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f.

Synthetic probes подтвердили S-M1 по пяти допустимым ID во всех схемах/loader, malformed/membership rejection, snapshot equality, deep NaN/±Infinity rejection, точную очередь ±10**400, partial/missing denominators и tag-only stability. Version-only/aggregation-only/nested proposed меняют только ожидаемые хеши; root caveat отдельно R-L2. Выходы: audit-p0-probes.py/json, audit-p0-readiness.json.

### К6. Дополнительные воспроизводимые проверки

Все команды ниже запускаются тем же префиксом, все результаты — TMP:

    .venv/bin/python "$TMP/audit-p0-frozen-partial.py"
    .venv/bin/python "$TMP/audit-store-probe.py"
    .venv/bin/python "$TMP/audit-store-date.py"
    .venv/bin/python "$TMP/audit-store-symlink.py"
    .venv/bin/python "$TMP/audit-ui-probes.py"

Артефакты: соответствующие *results.json, audit-p0-frozen-partial.json, audit-ui-probes.json. Дополнительная read-only проверка 10 валидных рабочих partial rows записала только агрегаты в frozen-partial-confirmation.json: validation_errors=[], role_recall10/10, role_accuracy N0, relevance_recall0/10. Никаких рабочих source/notes в этот файл не выгружено.

Проверенные пределы: обычные concurrent different-row saves сохраняют обе правки; no-op review retry создаёт один audit, не меняя JSONL; 409 сохраняет UI draft/time, не открывает successor. Arbitrary filesystem TOCTOU, crash recovery, реальные provider prompts/diagnostics/цены/бюджет и native-browser keyboard/selection в этом аудите не проверялись.

### К7. Контроль неизменности

**7709/7709 существующих файлов совпали** с исходным снимком: в том числе215 source/test файлов,37 уже существовавших docs,7419 файлов data/local,8 YAML,рабочая SQLite и10 gold JSONL. Снимки before-sha256.json/after-sha256.json и verification-summary.json — TMP. README/pyproject и прочие tracked файлы также вошли в снимок. После публикации отчёта исходные хеши проверены повторно; добавление этого нового отчёта не считается изменением исходного файла.

| Обязательный файл | SHA-256 до = после |
| --- | --- |
| data/local/job_intake.db | 35fb18d4a28e7222cd6bc9de178df8fc992f4a4a02b89897f62b7432098ac075 |
| gold/vacancy-labels-v3.jsonl | 936f6259a2343d7ff882f447539a9b137b67eea4eb2580edd8c91bbb3163309a |
| gold/vacancy-tags-v1.jsonl | 2165918bfe1185efb4aced9c1eac73d793177df9acd22390ef904df67ff3a533 |
| gold/vacancy-tags-v2-labeled-xlsx-2026-10-17.jsonl | 710eebb8ef419557cb22eaea3721786a504aad633c3decb3de67e12665f87256 |
| gold/vacancy-tags-v2-labeled.before-adapty-2026-10-17.jsonl | 6fda04402b67e13bdbca50dad6c592d2efd500dab00292309863cea79244fccf |
| gold/vacancy-tags-v2-labeled.before-user-positives-2026-10-17.jsonl | 2be3a198bca7df14faf1690b2bff22243cfed285be5b5f454860bb9b89e29152 |
| gold/vacancy-tags-v2-labeled.jsonl | 6cd7a884e2e7c13e7fd189a8dfe336f0857db608c0537d19d2d15674889a4d3d |
| gold/vacancy-tags-v2-user-links-2026-10-19.jsonl | f9ec399df7bb5c638b90190a8eb9dd017e6ecc5d7ba44ad5eead71804684fce7 |
| gold/vacancy-tags-v2-user-positive-adapty-2026-10-17.jsonl | 3ca9bfa970091f312219c66fa8357f07171dd92e8a5470563c0109d067c80757 |
| gold/vacancy-tags-v2-user-positives-2026-10-17.jsonl | bcd7dce016c096be837c19eb49d7dcfbee9e642b8796b16465010a565aeae836 |
| gold/vacancy-tags-v2.jsonl | 01566b36b6d2401b913d5c28940e758075ae059ac0533b13da8568aa3880456d |

Пути gold/* в последней таблице находятся под /Users/admin/Documents/HR-Automation/data/local/. Исторические три аудита не перезаписывались. Ветка, HEAD, код, design doc, конфиги, базы, gold, старые отчёты и exposure journals сохранены.
