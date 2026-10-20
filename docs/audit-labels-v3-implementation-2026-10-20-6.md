# Шестой аудит labels-v3: P0–P1 после последних исправлений — 20.10.2026

## 1. Итог

**F-C1 и F-H1 пятого аудита закрыты в проверенных сценариях; прежние R-H1/R-M1–R-M4 также не открылись повторно.** Найдены **1 новая критическая, 0 высоких, 1 новая средняя и 7 сохранившихся низких находок**. A6-C1 — отдельный разрыв запрета перезаписи: новый accuracy rules может заменить уже существующий валидный frozen-эталон через --output. Критический уровень следует буквальной шкале исходного задания (§0.4); **рабочие данные не повреждены**, воспроизведение использовало только временную копию synthetic run. A6-M1 — отсутствие базовой проверки синтаксиса роли у loader прецедентов без role_ids. Основная функциональность P0/P1 подтверждена, но безоговорочная приёмка сквозного контракта преждевременна. Разработку P2 можно продолжать; release-измерения и платный пилот требуют исправлений и предпосылок §7.

Код, design doc, критерии и рабочие данные не менялись. **7714 исходных файлов совпали по SHA-256 до/после.** Единственный новый файл репозитория — этот отчёт.

## 2. Объём и метод

- Проверена текущая рабочая копия, включая незакоммиченные P0/P1, последние publisher/coverage исправления, новые тесты и untracked исходники. Ветка feature/vacancy-tags, HEAD d63d5db9ebed8a91daaa28066a30846bbb2f29db; исходная база 8bb3a2a, исходные P0-коммиты 624bd17/d63d5db. Проверка не ограничена HEAD.
- Повторён исходный запрос из приложенного Pasted text.txt, с расширением на реализованную P1. Прочитаны AGENTS.md, design doc, optimization §3–§5, соответствующие README/architecture/index, YAML/JSON Schema, labeling-модули, accuracy/frozen/cohorts/eval CLI, store/CRM/Discovery и тесты. Проверены исходный diff 8bb3a2a..d63d5db и текущие изменения от 8bb3a2a по областям. SHA-256 текущего tracked diff: 6feca5a032c63bb6857fac19079512b7fc40d24ee925d210f6fe73db80daccec; untracked файлы включены в файловый снимок отдельно.
- Три независимые ветви проверки: метрики/рубрика/frozen, atomic/schema/store/migration, frontend. Новые A6-C1/A6-M1 независимо воспроизведены root и другим проверяющим. Пересекающиеся pytest-прогоны не суммируются.
- Промежуточные файлы — **/private/tmp/labels-v3-audit6-awGR34** (далее TMP). Python запускался в очищенном окружении, с отключёнными dotenv/bytecode/cache и audit hook, запрещающим сеть/DNS/bind, чтение настоящего .env и запись в репозиторий. Рабочая SQLite — только mode=ro + query_only=ON; тесты могли изменять synthetic SQLite в TMP.
- Ключи, личная карьерная база, провайдеры и браузер не использовались. YAML сравнивался с утверждённым блоком design doc, а не с личной базой. Рабочие notes и исходные тексты вакансий в отчёт не цитируются.
- Рабочая CRM, pipeline run/reevaluate/annotate/export-csv не запускались. Canonical cohort проверена прямым run_relevance_accuracy, без CLI/exposure. Все CLI-выходы — TMP; synthetic frozen run/union-frame в тестах не относятся к рабочему pipeline.
- Полный pytest: **3 failed, 3325 passed, 22 errors за 44,23 с**. Все 25 непрошедших случаев остановлены запретом socket.bind; иных падений нет. Loopback не разрешался и ограничение не обходилось. Ruff и git diff --check — pass. Исторические 3350 passed/55,96 с из §19 — результат предыдущей реализации с разрешённым loopback, а не этого аудита.

## 3. Трассировка требований

Статусы относятся к P0–P1; будущие P2–P4 не считаются отсутствующей реализацией текущей фазы. К1–К7 — проверки приложения.

| Требование | Статус | Доказательство / предел |
| --- | --- | --- |
| §0.1 Критерии, stable ID, утверждённые статусы | Реализовано | Rules/search_profiles/settings diff от 8bb3a2a пуст; YAML exact §5.2; К5/К7 |
| §0.2 Нет неразрешённых платных вызовов | Реализовано в объёме | API0 в К2–К4, сеть запрещена; будущий tagger/бюджет — P2 |
| §0.3 Env-ключи и privacy diagnostics | Частично / не входит в P0–P1 | Провайдеров нет; error_diagnostic tagger — P2; защитный malformed-key край R-L5 |
| §0.4 Новые immutable выходы без перезаписи, UI backup/version | Отклонение | F-C1 publisher закрыт; accuracy writer заменяет занятый эталон — A6-C1; migration lexical symlink край R-L4 |
| §0.5 SQLite eval/export только read-only | Реализовано | [eval_runner.py:97](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/eval_runner.py:97), query_only:102; К2–К4 |
| §0.6 Только аддитивные SQLite migration | Не входит в P0–P1 | Storage diff пуст; startup CRM с create_schema не выдаётся за чистое чтение |
| §0.7 Сбой модели оставляет rules_only | Не входит в P0–P1 | Реальный tagger/fallback — P2, fake tests не подтверждают adapter |
| §0.8 Источник недоверенный | Реализовано для P1 / не входит для prompt | Текстовый вывод и отдельные canonical quotes; prompt/S3 — P2 |
| §0.9 Прежнее поведение по умолчанию | Реализовано в объёме | Pipeline/prefilter/scoring/storage/AI/settings diff пуст, legacy tests pass кроме HTTP |
| §0.10 Стиль, атомарность, pytest | Частично | Ruff pass; основной publisher staged/exclusive, HTTP ограничен; no-overwrite отдельно A6-C1 |
| §4.1 Gold/prediction enum, role syntax, null ≠ 0 | Реализовано | [schema.py:169](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:169), :223; schema/CLI tests; precedent path отдельно A6-M1 |
| §4.2 Шесть тегов, INDUSTRIES, §13 | Реализовано | Schema/aggregate/UI tests, YAML exact; provider evidence section enforcement — P2 |
| §4.2 Restrictive quotes, basis, unknown/empty | Реализовано с границей | [schema.py:388](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:388); human section policy требует уточнения, §8 |
| §4.3 Provenance, partial tags/gate, label_scope | Реализовано | 1:1 К2, partial/no-op store tests; [gold_ui.py:499](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:499) |
| §4.3 Даты и chronology | Реализовано | [schema.py:348](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:348); 1300 независимых Fraction comparisons без расхождений; rollover/store tests |
| §4.4 Closed prediction schema, finite JSON, human-only reasons | Реализовано | 71 prediction cases, CLI/frozen validation, huge/nonfinite regression tests |
| §4.5 Snapshots, Draft validation/runtime | Реализовано с границей | Снимки/генераторы и tests совпадают; portable 2.0 против strict int — R-L3 |
| §4.5 Response duty/title/order/evidence | Реализовано в P0 | [schema.py:291](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:291); реальные provider constraints/S3 — P2 |
| §5.2 YAML, три независимых хеша | Реализовано | К5:2913 bytes exact; version/prompt/aggregation independence; precedents hash tests |
| §5.2 Proposed не применяется | Частично | Nested strong_companies исключён; root anchors/notes R-L2; текущий root confirmed |
| §5.3 Precedent loader/domain/holdout guards | Частично | Registered provenance/closed domain проходят; default role grammar — A6-M1; registry/прецедентов в работе нет |
| §6.5 Base/cap/upgrade/penalties/clamp | Реализовано | [aggregate.py:37](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:37),44 cases; synthetic controls argenx2/BVGroup1/PrizePicks0; Tiger — §5 |
| §6.5 normalize_company / p2_candidate | Частично | [aggregate.py:21](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:21), :80; p2 cases pass, punctuation R-L1 |
| §6.7 QWK/MAE/±1/Spearman/missing/recall/precision | Реализовано | [metrics.py:226](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/metrics.py:226), К3/К6; covered-pair N и missing явны |
| §6.7 Queue/P@k/NDCG/random/denominators | Реализовано | [metrics.py:94](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/metrics.py:94); пропуски входят в требуемый N, К3/К6 |
| §6.7 Partial tags/gate/domain/stability | Реализовано | Независимые human samples, tag-only repeat, required_domain quality/κ, explicit empty focus |
| §6.7 Frozen hooks/versions/coverage/replay | Реализовано в P0 | [frozen_eval.py:523](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:523), :591; F-H1/R-H1 закрыты, old runs valid, К4/К6 |
| §6.8 Baseline tier map | Реализовано | A→3/B→2/C→1/other→0 явно в JSON/Markdown, rules:tier-map-v1, К3 |
| §6.9 1:1/assisted→curated/captured SHA | Реализовано | 171 row, manifest, single-read tests; новый TMP JSONL равен рабочему v3, К2 |
| §6.9 Conservative tag map/unknown geography | Реализовано | 146 cases; неизвестность/альтернативы → dropped, реальные новые human tags N0 |
| §6.9 Occupied/staging/rollback | Реализовано с краем | Обычный occupied refusal и owned rollback pass; dangling R-L4; предел пары явно описан |
| §7 migrate-v3 / validate dispatch | Реализовано | К2; unknown/mixed schema reject |
| §7 accuracy rules/labels, защита входов/выходов | Частично | Метрики/checker/input protection/read-only проходят; unrelated occupied output — A6-C1 |
| §7 label/export/--ai/--max-cost | Не входит в P0–P1 | P2/P3, отсутствие будущих команд не finding |
| §10 v2/v3/default/fallback/pin/mixed/canonical path | Реализовано | [gold_ui.py:189](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:189), :209/:225; current store tests и threaded alias probe |
| §10 Six tags/reasons/partial/empty/domain | Реализовано |41 store +50 DOM cases; absent/unspecified/empty и порядок массивов различены |
| §10 Source/metadata/canonical quote binding | Реализовано | [index.html:1668](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1668), :1808; свежие12 literal blocks, metadata-only DOM→real store tests |
| §10 Blind read model | В основном реализовано | Whitelist source/human fields; К4; diagnostic key R-L5 |
| §10 Hotkeys вне interactive controls | Реализовано в проверенном объёме | [index.html:1630](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1630);8 SUMMARY/span probes pass; native browser не проверен |
| §10 Review_seconds в journal, bounds/no-op | Реализовано | [gold_ui.py:455](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:455); no-op bytes unchanged, dedup1, elapsed bounds |
| §10 Version/lock/backup/source binding/v2 | Реализовано в проверенном объёме | Threaded different-row merge, stale409, source drift guard, timezone/v2 tests; HTTP запрещён |
| §11 Тесты и сила доказательств | Частично |3325 pass/25 blocked;700 labels+31atomic collected; A6-C1/A6-M1 не покрыты, F-L7 |
| §12 Приёмка P0–P1 | Частично | Последние исправления подтверждены; новый hard-output и loader gap остаются |
| §12 P2–P4 / §14 риски | Не входит в P0–P1 / предпосылки | §7; отсутствие tagger/pipeline не выдаётся за дефект текущей фазы |

## 4. Находки

### A6-C1 — критический по заданной шкале: accuracy rules перезаписывает существующий frozen-эталон

**Проверено обычным CLI, без monkeypatch.** [cli.py:761](/Users/admin/Documents/HR-Automation/src/job_intake/cli.py:761) выбирает путь и передаёт его в write_accuracy_report (:785). Защита проверяет только отдельный список входов и совпадение JSON/Markdown ([accuracy.py:1555](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/accuracy.py:1555)); занятость постороннего output не проверяется. [eval_runner.py:464](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/eval_runner.py:464) публикует через replace; Markdown также заменяется (:1576 accuracy.py). Атомарность здесь работает, отсутствует запрет перезаписи.

**Воспроизведение:** в TMP скопирован валидный synthetic frozen-evaluation-run-v1, до команды прошедший validate_run. Затем:

    .venv/bin/job-intake eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output "$TMP/root-frozen-reference.json"

Exit0, API0, SQLite read_only. Тот же путь содержит уже scope=vacancy_level_labels_v3, N54; SHA изменён, validate_run больше не принимает файл. Исходный synthetic run в предыдущем TMP и **все рабочие файлы сохранны**. Другая независимая probe на полностью synthetic config/DB/gold подтвердила замену frozen-run, а также existing JSON и Markdown. Evidence: root-accuracy-overwrite.py/json/log, audit6-atomic-schema-probes.py/results.json.

**Последствие:** совпадение --output с другим существующим эталоном уничтожает его прежние bytes без отказа/force. Это не утверждение о повреждённом рабочем эталоне: уничтожена только специально созданная временная копия.

**Scope/severity:** обычный reusable accuracy-report сам по себе допускает спор об intended overwrite. Проба использует именно **валидный frozen-reference**, поэтому относится к «Gold и эталонные файлы не перезаписывать» §0.4. В исходной шкале нарушение жёсткого §0 — critical. Сохранение legacy defaults §0.9 не даёт новым rules/labels неоговорённого разрешения заменять эталоны. Проверен rules; labels использует тот же writer по коду.

**Происхождение:** общий replace-writer унаследован от 8bb3a2a. Это новая находка в поддержанном P0 accuracy-path, не регрессия закрытого F-C1: write_new_text теперь ведёт себя правильно, accuracy его не использует.

**Рекомендация:** для новых P0-выходов rules/labels отказывать при занятом JSON/Markdown и использовать эксклюзивную публикацию без race-overwrite. Покрыть существующий frozen-run, unrelated gold/report, занятый второй путь и конкурентного publisher. Если saved/current reports должны оставаться заменяемыми, явно ограничить и описать этот legacy-контракт, сохранив защиту эталонов.

### A6-M1 — средний: default loader прецедентов принимает некорректный синтаксис role_label

**Проверено.** [rubric.py:258](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/rubric.py:258) проверяет только isinstance(role,str), пока role_ids=None — это default публичного load_precedents (:275/:294). Общий [identifiers.py:3](/Users/admin/Documents/HR-Automation/src/job_intake/utils/identifiers.py:3) не применяется.

Настоящий register_cohort + load_precedents,8 balanced примеров и реальный TMP registry, без mocks: приняты пустая строка, пробелы, Analytics, not a role, ID с завершающим newline и _bad. Те же значения отклоняются gold role JSON Schema и при передаче enabled role_ids. Положительные controls analytics-leadership/other принимаются. Root независимо повторил empty/whitespace/newline/non-ID и schema comparison. Evidence: audit6-metric-probes.py/json, root-precedent-check.py/json.

**Последствие, выведено:** P2 caller, использующий default loader, может получить пример с невозможным v3 role ID для prompt. Теггер/рабочие precedents ещё отсутствуют; неправильное рабочее решение или реальный prompt не наблюдались. Это разрыв структурной проверки уже реализованного P0 loader (§4.1/§5.3), а не требование реализовать будущий P2.

**Рекомендация:** проверять базовый общий ID-синтаксис независимо от наличия taxonomy; supplied role_ids дополнительно проверяет membership. Отсутствие списка не требует угадывать enabled membership. Добавить default-mode grammar/empty/newline tests и controls допустимых новых IDs/other.

### Сохранившиеся низкие находки

| ID | Доказательство / статус | Последствие / рекомендация |
| --- | --- | --- |
| R-L1 | [aggregate.py:27](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:27); свежая probe: Acme LLC→acme, Acme (LLC)/Acme-LLC→acme llc | Порядок очистки юрформ/пунктуации слабее §6.5. Согласовать скобки/дефисы; текущий strong_companies пуст/proposed, рабочий эффект не доказан |
| R-L2 | [rubric.py:194](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/rubric.py:194); свежая root-proposed probe меняет prompt hash через anchors, nested proposed исключён | Уточнить root guard до P2; текущий confirmed YAML не менять |
| R-L3 | [schema_validation.py:53](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema_validation.py:53) / strict-int runtime; portable integer допускает2.0 | Это семантика JSON Schema, не её дефект; уточнить границу «паритета» и explicit regression |
| R-L4 | [gold_workbook.py:45](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_workbook.py:45), [gold_v3.py:242](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/gold_v3.py:242); прежний lexical dangling output edge сохраняется по коду | Existing regular-файл не заменяется; описать поддержку ссылок/lexical occupied check. Не смешивать с исправленным frozen publisher |
| R-L5 | [schema.py:394](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:394) → [gold_ui.py:341](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:341): unknown tag name дословно в warnings | Искусственно встроенный в имя ключа machine decision достигает blind diagnostics; обычная утечка value не доказана. Санитизировать unknown names |
| R-L6 | [index.html:1937](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1937); свежая deferred-refresh Node probe: chosen three → final two, dirty=false | Старый save-next отменяет более поздний clean выбор. Потеря dirty draft не доказана, discard guard остаётся. Проверять identity после await |
| F-L7 | [vacancy-labeling-architecture.md:1400](/Users/admin/Documents/HR-Automation/docs/vacancy-labeling-architecture.md:1400): «Настоящий DOM summary/span»; [test_gold_web_ui.py:30](/Users/admin/Documents/HR-Automation/tests/test_gold_web_ui.py:30) / :38 — Node harness, [test_labels_gold_web_ui.py:380](/Users/admin/Documents/HR-Automation/tests/test_labels_gold_web_ui.py:380) — прямой handler | Тест доказывает отсутствие перехвата, не native details expansion. Уточнить формулировку; browser keyboard отдельно |

Для R-L1/R-L2 — свежие metric probes; R-L6 — свежие UI probes. R-L3/R-L4/R-L5/F-L7 подтверждены текущим чтением и связанными tests; старые воспроизведения не выдаются за новые запуски. Они не входили в поручение исправить критическую/высокую находки.

## 5. Признанные отклонения и закрытие прошлых находок

| Исходно признанное отклонение | Вердикт |
| --- | --- |
| company= у rules_relevance | Обосновано strong_company, §6.5 отражает сигнатуру |
| required_domain/basis в response | Обосновано, §4.5 обновлён; provider-пустые details нормализует будущая S3 |
| duty_unit_ids/title_unit_ids | Обосновано section constraints P2; fallback сам по себе не доказывает их применение |
| label_scope сохранён | Обосновано provenance,1:1 К2 |
| Tiger1 через pharma industry | Старая подстановка employer industry неверна; consultancy и required pharma различены. Design показывает2−2=0, с focus3−2=1; human Tiger1 сохранён и без подтверждённых тегов не обещается его воспроизведение |
| domain_fit только requirement/industry | Уже исправлено: required_domain учитывается в quality/κ при соответствующем requirement |
| MAE/QWK/Spearman только covered pairs | Обосновано: у missing нет ordinal значения; coverage/собственный N явны |
| build/load только v2 | Уточнение обосновано: build v2-validator; load digest/policy/source не равен полной v3 label validation. Supported v3 freeze — P2 |
| LabelResult/TagValue до P2 | Обосновано, явно отложено §3/§12 |
| cohort assisted→curated | Rename человеческого метода, не assisted_llm; [cohorts.py:192](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/cohorts.py:192), К4 |
| Старые proposed §3/§12 против confirmed §5.2 | Конфликт исправлен; текущий YAML exact, root caveat R-L2 остаётся |
| P1 canonical quotes + raw preview | Обосновано unique binding; metadata и SUMMARY исправлены; native граница F-L7 |

| Прежняя находка | Текущий вердикт / доказательство |
| --- | --- |
| F-C1 | Закрыта: общий JSON/text publisher staged→flush→fsync→close→exclusive link;31 regressions, независимый reader видел только absent/full244000bytes. MD/JSONL используют helper. Per-file гарантия и orphan/postcommit limits явно описаны, К6 |
| F-H1 | Закрыта: прежние immutable4JSON + точный DEFAULT_POLICY161 теперь insufficient по actionable/NDCG5/NDCG10/repeat; own coverage160/161. Заполненный control проходит19 gates; inputs неизменны, К6 |
| R-H1 | Закрыта: current known-role1/2 против legacy2/3; marker/hash/unknown/mixed-version tests и saved replay pass |
| R-M1/R-M4 | Закрыты: default/file/dir aliases делят target/lock, real threads merge different rows и stale409; chronology1300 exact oracle comparisons без ошибок, timezone/v2 tests pass |
| R-M2/R-M3 | Закрыты в проверенном объёме:12 literal blocks, metadata-only quote→real store regressions;8 SUMMARY/span shortcut probes не перехвачены |
| A-H1/A-M1–A-M5 и S-M1 | Повторного открытия не найдено: conservative map, v3 marker checking, tag-only repeats, huge scores, registered domain validation, enabled membership и общий ID syntax проходят |

Новые A6-C1/A6-M1 не являются повторным открытием F-C1/F-H1. Ограничения: MD/JSON не единая транзакция, directory fsync/power-loss durability и произвольный filesystem TOCTOU не обещаны. UI recovery .bak/.audit пишутся до canonical replace; на ошибке подготовки канон остаётся прежним. Без отдельного нарушенного обещания/потери канона этот boundary не считается новым critical.

## 6. Покрытие тестами

Collect-only: **700 labels cases +31 atomic =731**. В полном прогоне прошли **684 labels и31 atomic**,16 labels HTTP blocked; это часть3325 passed. Дополнительные целевые327 passed пересекаются с полным прогоном и к итогу не прибавляются.

| Файл / cases | Покрытие | Пробел / предел |
| --- | --- | --- |
| test_labels_schema /109 | Enum/closed schema/snapshots/null0/IDs/dates/offsets/fractions/boundary | R-L3; human section policy |
| test_labels_prediction_validation /71 | Nested/finite/duplicates/roles/human-only reasons/huge JSON | Реальная S3/provider — P2 |
| test_labels_cli /42 | Migrate/validate/accuracy, input protection, taxonomy/IDs/huge scores | Нет occupied unrelated reference — A6-C1 |
| test_labels_gold_v3 /146 | Captured SHA/1:1/aliases/unknown/alternatives/dropped/staging/rollback | R-L4; crash-pair граница явна |
| test_labels_metrics /26 | Ручные QWK/Spearman/NDCG/P@k/missing/independent samples/domain/focus/versions | Описательные метрики не равны pilot readiness |
| test_labels_frozen_compatibility /21 | V3 variants/markers, v1 malformed guard, tag-only repeat/huge score | Supported v3 build/load — P2 |
| test_labels_frozen_role_recall /22 | Known/unknown/other/abstention, independent legacy replay/hash/version | Исходная R-H1 покрыта |
| test_labels_frozen_coverage /70 | DEFAULT160+1, baseline/candidate/repeat holes, independent task fields, full/NA controls, immutable replay | F-H1 закрыта; immutable publisher не защищает другой accuracy writer |
| test_cohorts_atomic /31 | Write/flush/fsync/close/link failures, retry, observed publication, occupied/race/concurrency, ownership, MD/union callers | Не покрывает write_accuracy_report; per-file/no power-loss гарантия |
| test_labels_rubric /42 | YAML/hash/nested proposed, real registered domain/holdout guards | Нет default-mode bad-role cases — A6-M1; остальные default-mode отказы не проверяют эту grammar |
| test_labels_aggregate /44 | Base/cap/penalties/clamp/p2/control facts | Company punctuation R-L1; historical Tiger не переоценивается |
| test_labels_gold_ui /41 | Schema/path/concurrency/source/date/provenance/partial/blind/no-op/time | Unknown-key R-L5; arbitrary crash/TOCTOU не доказаны |
| test_labels_gold_web_ui /50 | Six tags, absent/unspecified/empty/order, metadata quote→store, hotkeys/time/409 | Node harness, R-L6/F-L7; native viewport не проверен |
| test_labels_gold_web_server /16 | В коде real GET/PATCH/stale/partial/journal/v2/symlink/rollover/metadata | Здесь не выполнены: bind forbidden |
| Будущие prompt/S3/gates/tagger/pipeline | Таблица §11 | Отсутствие P2/P3 tests не finding текущей фазы |

Свежие audit probes находятся только в TMP и **не добавлены в репозиторий** как regression tests.

## 7. Готовность к следующим фазам и риски §14

| Предпосылка / риск | Проверенное состояние |
| --- | --- |
| P0/P1 приёмка | Последние исправления подтверждены; устранить A6-C1/A6-M1 перед безоговорочной приёмкой. HTTP/native ограничения не сняты |
| Supported v3 freeze/gates | [frozen_eval.py:146](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:146) — v2 validator; load:218 не полный v3 checker; full-tag gate:738 требует19. Нужен весь schema-aware build/save/load/acceptance lifecycle |
| Registry/precedents | data/local/evaluation/cohorts.jsonl и data/local/labeling/precedents.jsonl отсутствуют; P0 loader есть. Нужны registered calibration/development/holdout, утверждённые reasons и A6-M1 |
| Достаточность gold | N54/17 relevant, полных blind44/7, human six-tag labels N0. Не достигнуты150+ решений/30+ relevant и независимый holdout/repeat; synthetic gates не evidence качества |
| Актуальность v3 | UI default уже v3, текущая новая migration равна снимку. Двусторонней синхронизации v2/v3 нет |
| Зависимости/компоненты | jsonschema установлен и используется. Scope transfer, gates/prompt/validate/tagger, LabelingConfig/LabelResult/TagValue/export orchestrator ещё P2 |
| Claims fallback/field order/evidence | Builder есть; реальные provider stage/strict schema, explicit duty/title, untrusted policy и S3 tests ещё нужны |
| Privacy/holdout | Notes запрещены closed loader; prompt/diagnostics/display exposure — P2. Сам loader не доказывает человеческое происхождение reason |
| Cost/retry/latency | Ledger/BudgetGuard/price-missing/per-attempt usage/concurrency не реализованы. API0 baseline не подтверждает будущий бюджет |
| Default/pipeline complexity | Pipeline/scorer/prefilter/storage/AI adapters неизменны. Shadow/labels/tier-source parity/stream rebuild — P3 |
| UI layout | Большая source колонка — непроверенный viewport/sticky/scroll риск, не finding без browser |
| Платный пилот | Только по отдельной команде после подготовки; модели/лимит $3 не менялись и не использовались |

## 8. Предлагаемые исправления design doc

Документ не редактировался; технические предложения не меняют критерии.

| Раздел | Было → стало |
| --- | --- |
| §0.4/§7/§11 | Immutable новые выходы → явно распространить occupied/refusal/exclusive guard на rules/labels accuracy JSON и Markdown; отдельно описать legacy reusable reports, если это намеренный режим |
| §5.3/§11 | Loader проверяет labels → общий ID-синтаксис обязателен всегда, enabled membership дополнительно при переданной taxonomy; default-mode regression |
| §18 | «Настоящий DOM summary/span» → Node DOM-harness: handler не подавляет default; native keyboard проверяется отдельно |
| §4.2/§4.5 | Общая таблица секций → явно определить human quote section policy, не объявляя будущую machine S3 частью P1 |
| §5.2/§0 | Proposed неэффективен → определить root anchors/notes guard; текущий YAML не менять |
| §4.5 | «Паритет» → strict Python-int против portable JSON integer2.0 |
| §6.5/§6.9 | Юрформы/занятые пути → определить скобки/дефисы и lexical symlink policy |
| §10 | Blind diagnostics / async save-next → безопасные unknown-key warnings и editor identity после await |
| §12/§14/§19 | Закрыты прежние H/M/C → сохранить историю, дополнить новые A6-C1/A6-M1 и не смешивать3350-pass implementation с3325-pass restricted audit |
| §6.7/§12/§14 | V3 freeze → сохранить schema-aware lifecycle, registry/privacy/budget/holdout как незавершённые предпосылки |

## 9. Приложение: команды и результаты

### К1. Среда, полный pytest и неизменённые части

Префикс всех Python/CLI:

    env -i HOME=/Users/admin PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR=/private/tmp LC_ALL=C PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=/private/tmp/labels-v3-audit6-awGR34

Запущено (TMP — абсолютный каталог §2):

    .venv/bin/python -m pytest -q --tb=short -p no:cacheprovider --basetemp="$TMP/full-tests"
    .venv/bin/ruff check src tests --no-cache
    .venv/bin/python -m pytest --collect-only -q -p no:cacheprovider tests/test_labels_*.py tests/test_cohorts_atomic.py
    git diff --check
    git diff --exit-code 8bb3a2a -- config/search_profiles.yaml config/rules.yaml config/settings.yaml src/job_intake/pipeline.py src/job_intake/annotation/prefilter.py src/job_intake/scoring src/job_intake/storage src/job_intake/annotation/ai.py

**3 failed,3325 passed,22 errors/44,23 с**, Ruff pass, collect731, оба git checks pass. .venv symlink доступен. Все25 blocked имеют PermissionError: Audit guard: network disabled на socket.bind:1 Discovery+2 archive failures,2 CRM+4 legacy Gold+16 labels Gold errors. Это ограничение разрешённого запуска, не25 доказанных дефектов. Логи pytest.log/collected.txt — TMP.

### К2. Миграция/validation

    .venv/bin/job-intake gold migrate-v3 --source data/local/gold/vacancy-tags-v2-labeled.jsonl --out "$TMP/migrated.jsonl"
    .venv/bin/job-intake gold validate --gold "$TMP/migrated.jsonl" --output "$TMP/validation.json"

Exit0, valid=True;171 rows, decision_labeled44, relevant7, labeled_rows55, relevance_only10, blind44/curated11/assisted_llm0, migrated/dropped_tags0. Проверены1:1 ID/три решения/source/hash/text_version/date/label_scope/notes без вывода содержимого; assisted→curated, first=labeled_at, manifest SHA. Новый JSONL побайтно равен рабочему v3. Evidence integration-summary.json/migrated.manifest.json/validation.json.

### К3. Baseline

    .venv/bin/job-intake eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output "$TMP/rules.json"

Exit0, database_access=read_only, API0; явная таблица **A→3/B→2/C→1/other→0** в JSON/Markdown. С сохранённым accuracy-rules.md совпали N54, QWK≈0,373, recall12/17.

| Показатель | Результат |
| --- | --- |
| N/coverage |54/54 из54 |
| QWK/MAE/Spearman |0,373134/0,888889/0,389303 |
| Exact/±1 |21/54 и42/54 |
| Recall≥2/precision≥2 |12/17 и12/24 |
| P@5/P@10 |4/5 и8/10 |
| NDCG@5/random |0,874799/0,261829 |
| NDCG@10/random |0,771175/0,261603 |

### К4. Legacy/cohort/frozen/P1 read model

    .venv/bin/job-intake eval accuracy --variant saved --gold data/local/gold/vacancy-tags-v2-labeled.jsonl --output "$TMP/saved.json"
    .venv/bin/python "$TMP/integration.py"

Exit0. Saved сохранил все16 top keys и наборы evaluation/operational/stability/readiness/selection, рекурсивную структуру evaluation против after-apply-accuracy.json. Равенство значений при ином числе gold не утверждается.

Прямой cohort run_relevance_accuracy выбрал163 rows: blind44/curated3/unlabeled116. Blind **N44, recall7/7, precision7/19, QWK0,433663**. Cohort/exposure не менялись.

load_bundle+validate_run pass для трёх пар под data/local/eval/p1-2026-10-19:

| Run | Bundle |
| --- | --- |
| baseline-run.json | bundle.json |
| candidate-run-v2.json | release-bundle-v2.json |
| release-baseline-run-v2.json | release-bundle-v2.json |

Read-only store: defaultv3,6tag options,171 total/44complete/11partial/116unlabeled/17relevant/10strong; detail can_save=True, основные machine fields отсутствуют. Рабочий PATCH не выполнялся. Evidence integration-summary.json/blind-summary.json.

### К5. YAML и хеши

§5.2 = config/labeling_rubric.yaml побайтно, **2913 bytes**, SHA-256 9b7f767c2817df6a2ed6e5b1738d7586c21ae9ba5cc1ca707d36fc806e10f4d2. Version labels-rubric-2026-10-20; prompt hash8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8; aggregation hash9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f. Свежие version/nested proposed/hash probes pass; root caveat — R-L2. Precedents hash и остальные rubric cases прошли в полном pytest.

### К6. Независимые дополнительные пробы

Выполнены тем же префиксом, все артефакты — TMP:

    .venv/bin/python "$TMP/audit6-metric-probes.py"
    .venv/bin/python "$TMP/audit6-atomic-schema-probes.py"
    .venv/bin/python "$TMP/audit6-ui-probes.py"
    .venv/bin/python "$TMP/root-precedent-check.py"
    .venv/bin/python "$TMP/root-accuracy-overwrite.py" before
    .venv/bin/job-intake eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output "$TMP/root-frozen-reference.json"
    .venv/bin/python "$TMP/root-accuracy-overwrite.py" after

- F-H1: exact old DEFAULT_POLICY161 false-pass устранён; заполненный control19pass; исходные4JSON bytes сохранены. R-H1: current1/2 vslegacy2/3. Partial ordinal/tag/gate N независимы; очередь ±10**400 точна.
- F-C1: независимый reader во время медленной реальной записи244000bytes наблюдал только absent/full, ни одного partial.31 atomic regressions pass, включая caller failures/occupied/concurrency/identity.
- Chronology:1000 random +300 equal-UTC-second Fraction oracle comparisons,80-digit fractions,0 mismatches. Threaded aliases/different rows merge, stale409, no-op dedup1/DBunchanged проверены sub-audit.
- UI:12 canonical literal blocks без UI labels/machine values;8 direct SUMMARY/span key cases не перехвачены; old navigationrace R-L6 воспроизведена.
- A6-C1/A6-M1: точные triggers и результаты §4; scope/inference отделены.

Native browser/HTTP, реальные provider prompts/цены/бюджет, power-loss recovery и произвольные TOCTOU в этом аудите не проверялись. Audit probes не меняют код и не заменяют будущие repository regressions.

### К7. Сохранность

**7714/7714 исходных файлов совпали:**218 source/test,39 существовавших docs,7419 data/local,8 YAML и остальные tracked файлы. Нет новых файлов в data/local/config. Before/after SHA, status и verification-summary.json — TMP. После сохранения отчёта исходные хеши проверены повторно; добавлен только этот новый отчёт. Ветка/HEAD сохранены, исторические пять аудитов не перезаписывались.

| Обязательный файл | SHA-256 до = после |
| --- | --- |
| data/local/job_intake.db |35fb18d4a28e7222cd6bc9de178df8fc992f4a4a02b89897f62b7432098ac075 |
| gold/vacancy-labels-v3.jsonl |936f6259a2343d7ff882f447539a9b137b67eea4eb2580edd8c91bbb3163309a |
| gold/vacancy-tags-v1.jsonl |2165918bfe1185efb4aced9c1eac73d793177df9acd22390ef904df67ff3a533 |
| gold/vacancy-tags-v2-labeled-xlsx-2026-10-17.jsonl |710eebb8ef419557cb22eaea3721786a504aad633c3decb3de67e12665f87256 |
| gold/vacancy-tags-v2-labeled.before-adapty-2026-10-17.jsonl |6fda04402b67e13bdbca50dad6c592d2efd500dab00292309863cea79244fccf |
| gold/vacancy-tags-v2-labeled.before-user-positives-2026-10-17.jsonl |2be3a198bca7df14faf1690b2bff22243cfed285be5b5f454860bb9b89e29152 |
| gold/vacancy-tags-v2-labeled.jsonl |6cd7a884e2e7c13e7fd189a8dfe336f0857db608c0537d19d2d15674889a4d3d |
| gold/vacancy-tags-v2-user-links-2026-10-19.jsonl |f9ec399df7bb5c638b90190a8eb9dd017e6ecc5d7ba44ad5eead71804684fce7 |
| gold/vacancy-tags-v2-user-positive-adapty-2026-10-17.jsonl |3ca9bfa970091f312219c66fa8357f07171dd92e8a5470563c0109d067c80757 |
| gold/vacancy-tags-v2-user-positives-2026-10-17.jsonl |bcd7dce016c096be837c19eb49d7dcfbee9e642b8796b16465010a565aeae836 |
| gold/vacancy-tags-v2.jsonl |01566b36b6d2401b913d5c28940e758075ae059ac0533b13da8568aa3880456d |

Gold/* в последней таблице — под /Users/admin/Documents/HR-Automation/data/local/. Рабочие базы/gold/backups/отчёты/exposure journals, YAML, код и design doc сохранены.

