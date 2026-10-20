# Седьмой аудит labels-v3: P0–P1 после последних исправлений — 20.10.2026

## 1. Итог

**A6-C1 и A6-M1 закрыты; прежние F-C1/F-H1 и R-H1/R-M1–R-M4 не открылись повторно в проверенных сценариях.** Найдены **0 критических, 0 высоких, 1 средняя и 7 низких находок**. Новая A7-M1: Gold UI теряет несохранённый ввод, сделанный во время ожидания навигации; прежняя низкая R-L6 включена в неё как дополнительное проявление. Из семи низких шесть сохраняются, одна новая — лишнее exposure-событие при заранее занятом dangling output frozen compare. Основной контракт P0 подтверждён; безоговорочная приёмка P1 требует A7-M1. Разработку P2 можно начинать с предпосылками §7, но готовность к release-измерениям и платному пилоту не подтверждена. Полный зелёный pytest в этом аудите не заявляется: 25 HTTP-сценариев ограничены прямым запретом сети/bind исходного задания.

Код, design doc, критерии и рабочие данные не менялись. **8261 исходный файл совпал по SHA-256 до/после проверок.** Единственный новый файл репозитория — этот отчёт.

## 2. Объём и метод

- Повторено исходное задание из приложенного `Pasted text.txt`, с актуальным объёмом P0–P1. Ветка `feature/vacancy-tags`, HEAD `d63d5db9ebed8a91daaa28066a30846bbb2f29db`; база `8bb3a2a`. Проверена рабочая копия, включая незакоммиченные изменения и untracked исходники/тесты, а не только HEAD.
- Прочитаны AGENTS.md, design doc, optimization §3–§5, README/architecture, YAML/JSON Schema, labeling-модули, accuracy/frozen/cohorts/eval CLI, P1 store/CRM и тесты. Исходный diff `8bb3a2a..d63d5db` и текущие изменения сопоставлены по областям. Три независимые ветви проверки: метрики/рубрика/frozen, атомарность/CLI/миграция, P1 store/frontend. Root отдельно воспроизвёл новую среднюю находку и проверил интеграцию.
- Временный каталог: **`/private/tmp/labels-v3-audit7-jpxJ5f`**, далее TMP. Python запускался в очищенном окружении с `PYTHONDONTWRITEBYTECODE=1`, `PYTHON_DOTENV_DISABLED=1`, без pytest-cache. Audit hook запрещал **все** socket connect/DNS/bind, чтение настоящего `.env` и запись/мутации в репозитории. Синтетические SQLite/gold/reports изменялись только в TMP. Рабочая SQLite открывалась через `mode=ro`, `query_only=ON` ([eval_runner.py:96](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/eval_runner.py:96)).
- `.venv` доступен: ссылка на `/Users/admin/.venvs/hr-automation`. Полный pytest: **3 failed, 3443 passed, 22 errors за 88,47 с**. Все 25 непрошедших случаев остановлены `PermissionError: Audit guard: all network and bind disabled` на bind; иных падений нет. Запрет не обходился, loopback не разрешался. **3468 passed/56,27 с** в design doc §20 — исторический результат предыдущей реализации с loopback, не результат этого аудита.
- Ruff и `git diff --check` — pass. Пересекающиеся целевые прогоны 458/313/142 passed не суммируются с полным pytest. Коллекция подтверждает 3468 случаев; подробности — §6.
- Ключи, провайдеры, браузер и личная карьерная база не использовались. YAML сверялся с утверждённым блоком §5.2; критерии не переоценивались. Личные notes и исходные тексты рабочих вакансий в отчёте не цитируются.
- Pipeline, `run`, `reevaluate`, `annotate`, `crm serve`, `export-csv` не запускались. CLI-выходы направлялись в TMP. Каноническая когорта проверена прямым `run_relevance_accuracy`, без CLI и без записи exposure. Все новые frozen/cohort/exposure пробы — синтетические.

## 3. Трассировка требований

Статусы относятся к P0–P1. Отсутствие будущих P2–P4 не считается дефектом текущей фазы. К1–К8 — проверки приложения §9.

| Требование | Статус | Доказательство / предел |
| --- | --- | --- |
| §0.1 Критерии, stable ID, подтверждённые статусы | Реализовано | К5/К7: YAML exact, rules/search_profiles/settings diff от базы пуст, исходные хеши сохранены |
| §0.2 Нет неразрешённых платных вызовов | Реализовано | API0 в К2–К4; проверенный объём без провайдеров; реальный tagger/budget — P2 |
| §0.3 Env-ключи, privacy diagnostics | Не входит в P0–P1 в части tagger | Провайдеры не вызывались; будущий error_diagnostic не проверен; blind diagnostic-key край — R-L5 |
| §0.4 Immutable новые выходы, сохранность gold/эталонов | Частично | A6-C1/F-C1 закрыты: [cohorts:61](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/cohorts.py:61), [eval_runner:440](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/eval_runner.py:440); lexical migration край R-L4, лишний exposure A7-L1 |
| §0.5 Read-only eval/export | Реализовано для P0 | mode=ro/query_only, К2–К4; будущий label export — P2 |
| §0.6 Только аддитивные SQLite migration | Не входит в P0–P1 | Storage diff пуст; startup CRM с create_schema не запускался и не выдаётся за чтение |
| §0.7 Ошибка модели оставляет rules_only | Не входит в P0–P1 | Реальный tagger/fallback — P2 |
| §0.8 Недоверенный источник | Реализовано для P1 | Literal source/quote blocks и whitelist; prompt/S3 — P2 |
| §0.9 Прежнее поведение по умолчанию | Реализовано | Pipeline/prefilter/scoring/AI/settings неизменны, К7; отказ повторному report output явно описан в §20 |
| §0.10 Стиль, атомарность, pytest | Частично | Ruff/diff pass, atomic tests pass; полный HTTP ограничен, native DOM заявление слабее проверки — F-L7 |
| §4.1 Роли, nullable решения, human-only причины | Реализовано | [schema:169](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:169), :223; full shared ID syntax, enabled membership отдельно; A6-M1 закрыта |
| §4.2 Шесть тегов, INDUSTRIES, §13 | Реализовано | Schema/aggregate/UI tests, YAML exact; значения и статусы не менялись |
| §4.2 Restrictive quote/basis, unknown/empty | Реализовано в P0 | Runtime/schema проверки; human quote policy и provider section constraints разделены, §5/§8 |
| §4.3 Provenance, label_scope, partial rows | Реализовано | К2: 171 строка 1:1; store partial/no-op; null не превращается в 0 |
| §4.3/4.5 Строгие даты и chronology | Реализовано | [schema:348](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:348), offset/UTC/fractions/mixed precision; store rollover tests |
| §4.4 Closed prediction, finite JSON, taxonomy | Реализовано | 71 prediction-случай, CLI/frozen checker; bool/NaN/Infinity rejected, huge int сохраняется |
| §4.5 Снимки и Draft validation | Частично | Снимки совпадают, jsonschema runtime; portable integer 2.0 против strict int — R-L3 |
| §4.5 Response order/unit enums/duty/title | Реализовано в P0 | [schema:291](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:291); S3/реальный provider — P2 |
| §5.2 YAML и независимые идентификаторы | Реализовано | К5: 2913 bytes exact; version/prompt/aggregation и nested-proposed регрессии |
| §5.2 Proposed не применяется | Частично | Nested strong_companies корректно исключён; root anchors/notes — R-L2; рабочий root confirmed |
| §5.3 Precedent loader, role/domain/holdout guards | Реализовано как loader | Настоящий synthetic registry: 35 invalid rejected, 14 valid accepted; [rubric:259](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/rubric.py:259); рабочих registry/precedents нет |
| §6.5 Base/cap/upgrade/penalties/clamp | Реализовано | [aggregate:37](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:37); 44 случая и независимые controls; Tiger — §5 |
| §6.5 normalize_company / p2_candidate | Частично | [aggregate:28](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:28), :80; p2 pass, punctuation — R-L1 |
| §6.7 QWK/MAE/±1/Spearman/missing/threshold | Реализовано | [metrics:226](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/metrics.py:226); К3/К6, 200 независимых Fraction QWK без расхождений |
| §6.7 Queue/P@k/NDCG/random/denominators | Реализовано | К3; missing P@5=1/5, P@10 denominator6; score10**400 order сохраняется |
| §6.7 Partial tags/gate/domain/stability | Реализовано | Независимые human samples, required_domain quality/κ, tag-only repeat, explicit empty focus |
| §6.7 Frozen versions/role recall/coverage/replay | Реализовано в P0 | К4/К6; R-H1/F-H1 закрыты; поддержанный v3 build/save/load и acceptance — P2 |
| §6.8 Tier→relevance baseline | Реализовано | [predictions:36](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/predictions.py:36); A3/B2/C1/other0 явно в JSON/MD, К3 |
| §6.9 Перенос 1:1, assisted→curated, SHA | Реализовано | К2: 171 строка, manifest SHA input/output совпали; новый JSONL равен рабочему v3 |
| §6.9 Консервативный перенос tags | Реализовано | 146 случаев; неизвестные страна/город/альтернативы → dropped, не новый отрицательный факт |
| §6.9 Occupied/staging/rollback | Частично | Обычный occupied и own-inode rollback pass; dangling lexical edge R-L4; предел crash-пары описан |
| §7 migrate-v3 / schema-aware validate | Реализовано | К2; unknown/mixed rejected; validate не заменяет existing reference |
| §7 accuracy rules/labels, saved compatibility | Реализовано | К3/К4/К6: input protection, read-only, all variants exclusive; формы прежних saved ключей сохранены |
| §7 label/AI/budget/concurrency/export | Не входит в P0–P1 | P2; отсутствие команды не засчитывается как дефект |
| §10 P1 v2/v3 store, tags/gate/provenance/time | Реализовано | 41 store + 50 Node UI случаев; default v3, explicit v2, partial/unspecified/empty focus, journal-only time |
| §10 Canonical source/metadata/quote binding | Реализовано | [index:1808](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1808), metadata-only Node→store tests; подписи вне цитат |
| §10 Blind read model | Частично | К4: machine fields отсутствуют; malformed unknown key достигает warnings — R-L5 |
| §10 Hotkeys вне controls/SUMMARY | Реализовано в проверенном объёме | Handler не перехватывает Enter/i/0/3 в summary/span; native browser не проверен |
| §10 Version/lock/backup/source binding/v2 | Реализовано в проверенном объёме | Non-HTTP store/concurrency/symlink tests pass; настоящий HTTP в этом аудите запрещён |
| §10 Сохранность несохранённого ввода при навигации | Отклонение | A7-M1: dirty создаётся после начала GET и теряется без нового discard confirmation |
| §11 Покрытие и сила доказательств | Частично | 3443 pass/25 blocked; pending-GET edit и exposure dangling CLI не покрыты; F-L7 |
| §12 Приёмка P0 | Частично | Основные результаты подтверждены, high/medium P0 закрыты; полного green HTTP-run в этом аудите нет |
| §12 Приёмка P1 | Частично | Основная функция подтверждена; новая A7-M1 и HTTP/browser предел не позволяют безоговорочную приёмку |
| §12 P2–P4 / §14 риски | Не входит в P0–P1 | Реестр, v3 freeze/gates, scope/config/tagger/pilot остаются предпосылками §7 |

## 4. Находки

### A7-M1 — средняя: навигация стирает ввод, сделанный во время pending GET

**Проверено независимо двумя аудиторами.** [openGold:1769](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1769) проверяет discard до запроса. После `await` (:1773) проверяются только request number и view; [:1775](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1775) заменяет форму без проверки актуального draft. Поля во время ожидания остаются доступными. [moveGold:1913](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1913) также проверяет discard до `await goldNeighborUID`, затем :1917 передаёт устаревшее `discardConfirmed=true`.

**Свежая root-проба:** clean v3-форма → начать переход → задержать ответ GET → изменить relevance с 3 на 0 → `dirtyBefore=true`, input enabled → завершить GET. Результат: другая карточка, relevance снова 3, `dirtyAfter=false`, confirm вызван **0 раз**, хотя ответ confirm настроен на отказ. Это воспроизводится и для detail GET, и для перехода через границу страницы. Control: если dirty существовал до перехода, отказ discard сохраняет 0 и предотвращает GET. Отдельно подтверждён случай: discard разрешён для старого draft, но новый ввод во время GET стирается без повторного вопроса. Evidence: `root-navigation-probe.py`, `root-navigation-results.json`; независимая `ui-probes.py/results` проверяет также notes.

Ранее R-L6 подтверждал только отмену более позднего clean-выбора старым save-next; теперь потеря **несохранённого человеческого ввода** доказана, поэтому прежняя low включена в A7-M1, не посчитана дважды. Сохранённые gold/SQLite не повреждаются. Это средняя функциональная находка P1, не доказательство порчи рабочих файлов и не ошибка метрик.

Находка новая для аудита, но не является регрессией последних accuracy/rubric-исправлений: frontend diff совпадает с состоянием до них (`before.patch` предыдущего исправления против начального снимка этого аудита).

**Рекомендация:** после await сверять актуальные editor identity и draft/version; при новых правках повторно запросить discard либо отменить устаревший переход. Альтернатива — явно блокировать редактирование на время навигации. Save-next не должен отменять более поздний выбор. Регрессии нужны для direct GET, page GET, ввода после первого подтверждения и control отказа. Существующие [tests:170](/Users/admin/Documents/HR-Automation/tests/test_gold_web_ui.py:170), :228, :237 проверяют другие последовательности. Проба исполняет настоящие JS-функции в Node DOM harness; нативный браузер/HTTP не запускались.

### A7-L1 — низкая: dangling output frozen compare создаёт exposure до отказа

**Проверено ordinary CLI на synthetic bundle/runs/journal, без mocks.** [evaluation_cli:186](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/evaluation_cli.py:186) использует `exists()` без `is_symlink()`, пропускает dangling JSON/MD, затем :189–197 пишет `developer_analysis`. Правильный lexical guard [frozen_eval:1016](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:1016) отказывает позднее.

Regular occupied control: exit2, events0, `independent_holdout=True→True`. Dangling JSON и MD: exit2, полного отчёта нет, events1, `independent_holdout=True→False`. Root повторил CLI-пробу; evidence `atomic-exposure-preflight.py`, `atomic-exposure-7cdss8x2/results.json`. Только синтетический журнал создан в TMP; рабочие exposure-файлы не открывались на запись.

**Последствие:** заранее обнаружимая ошибка пути консервативно лишает свежую synthetic-когорту independence. **Перезаписи, acceptance bypass и нарушения пофайловой атомарности нет**; A6-C1/F-C1 не переоткрываются. Это low предварительной проверки/удобства, а не требование нулевых side effects при любом IO failure. Рекомендация: проверить оба lexical final имени до journal общим guard; regression должен сохранить journal при уже занятом dangling пути. Rollback exposure после произвольного сбоя не предлагается.

### Сохранившиеся низкие находки

Все строки ниже подтверждены свежими synthetic probes и текущим кодом; последствия для реальных решений отдельно ограничены.

| ID / уровень | Доказательство | Последствие / рекомендация |
| --- | --- | --- |
| R-L1 / низкий | [aggregate:28](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:28): Acme LLC→acme, Acme (LLC)/Acme-LLC→acme llc, Acme (S.A.)→acme s a | Порядок удаления пунктуации/юрформ слабее §6.5. Согласовать нормализацию. Strong list сейчас proposed/empty; рабочего эффекта не доказано |
| R-L2 / низкий | [rubric:198](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/rubric.py:198): при root status=proposed изменение anchor меняет prompt hash и остаётся в Rubric.anchors | Уточнить root guard до P2 или документировать исключение. Nested proposed корректно исключён; рабочий root confirmed |
| R-L3 / низкий | [schema_validation:55](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema_validation.py:55): portable Draft принимает relevance2.0, strict runtime отвергает | Стандартная семантика JSON Schema, не дефект emitted schema. Уточнить заявленный «паритет» и закрепить явную regression, без изменения шкалы |
| R-L4 / низкий | [gold_workbook:45](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_workbook.py:45), [gold_v3:242](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/gold_v3.py:242); ordinary migrate-v3 принимает lexical dangling out, сохраняет symlink и создаёт target | Resolve выполняется до occupied check. Existing regular/input bytes не заменяются. Согласовать lexical occupancy с immutable helpers; не смешивать с исправленным frozen publisher |
| R-L5 / низкий | [schema:394](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:394) → [gold_ui:344](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:344): marker из unknown tag key появляется в warnings | Искусственно встроенная в имя ключа machine-метка достигает blind diagnostics; значение тега скрыто, save запрещён. Санитизировать unknown names |
| F-L7 / низкий | [design doc:1423](/Users/admin/Documents/HR-Automation/docs/vacancy-labeling-architecture.md:1423) говорит «Настоящий DOM»; [test_gold_web_ui:30](/Users/admin/Documents/HR-Automation/tests/test_gold_web_ui.py:30) создаёт небольшой Element harness, :38 addEventListener пуст | Тест доказывает отсутствие перехвата/изменения draft, не native details expansion. Уточнить формулировку; отдельная browser-проверка остаётся непроверенной |

Метрики/рубрика: `metrics-independent/results.json`; migration/atomic: `atomic-independent-4i8ohr_g/results.json`; UI: `ui-probes-results.json`. Прежняя R-L6 не потеряна: объединена с A7-M1.

## 5. Признанные отклонения и закрытие прежних находок

| Исходно признанное отклонение | Вердикт по текущему состоянию |
| --- | --- |
| company= у rules_relevance | Обоснован approved strong-company правилом; §6.5 уже отражает аргумент |
| required_domain/basis в model response | Обоснованы семантикой факта; §4.5 обновлён. Нормализация provider-пустых деталей — будущий S3 |
| duty_unit_ids/title_unit_ids у response_schema | Обоснованы ограничением секций. Fallback без duty-ID не доказывает выполнение этого ограничения; P2 обязан передать ID |
| label_scope в gold v3 | Сохраняет provenance v2, явно разрешён §4.3; перенос 1:1 подтверждён |
| Tiger Analytics=1 при industry=pharma_biotech | Подмена отрасли работодателя отраслью practice больше не объявляется доказательством. §5.2 объясняет synthetic consultancy+required pharma: без focus0, с focus1; историческое human1 сохранено, подтверждённого полного tag-набора нет |
| domain_fit только requirement/industry | Устарело: required_domain добавлен в quality и κ, с собственной применимой выборкой |
| MAE/QWK/Spearman только по covered pairs | Обосновано при явном N/coverage; missing участвует в recall/exact/±1/P@k. §6.7 отражает это |
| build_bundle/load_bundle только v2 | Поддержанный build остаётся v2. Load проверяет digest/policy/source, не доказывает labels-schema; ручной v3 bundle в тесте не заменяет P2 schema-aware freeze |
| LabelResult/TagValue отложены | Явно P2 в §3/§12, не отсутствие обещанной части P0 |
| assisted в когорте = curated | Provenance-адаптация v2→v3 в select_cases; canonical cohort проверена без exposure |
| §3/§12 proposed против confirmed §5.2 | Исходное расхождение исправлено: confirmed решения, только strong_companies proposed/empty. YAML exact |
| Раньше accuracy saved заменял default report | Теперь все accuracy/common evaluation outputs exclusive. Изменение явно описано §0.4/§7/§20 и README; новые имена требуются, saved JSON/MD serialization сохранена |

**Свежо подтверждённое закрытие:** A6-C1 — saved/rules/labels и gold validate отказали при существующем валидном frozen-reference, exit2, bytes прежние, последующий validate_run pass. A6-M1 — реальный registry+loader отвергает grammar независимо от taxonomy; membership остаётся дополнительной. F-C1 — partial write/flush/fsync/close/link и конкурентные публикации не дают частичного final/перезаписи; при ошибке Markdown полный JSON может остаться, как документировано. R-H1 — mixed partial выборка даёт новый recall0/1, explicit legacy replay1/2. F-H1 — synthetic DEFAULT_POLICY160+1 получает insufficient_evidence при пропущенных полях; новые заполненные run дают pass, исходные не меняются. R-M1–R-M4 подтверждены store/Node tests; HTTP-подтверждение в этом аудите ограничено.

## 6. Покрытие тестами

Коллекция — **3468 случаев**, в том числе **788 tests/test_labels_***, ещё 31 atomic и 30 shared-output. Full run прошёл 3443; 25 сетевых/bind блокировок не выдаются за функционально пройденные тесты.

| §11 / текущие файлы | Случаи | Что доказано / что слабее обещания |
| --- | ---: | --- |
| labels_schema / prediction_validation | 109 / 71 | Enum, snapshots, closed/finite/types/partial/date/source/role; переносимый integer не равен Python strict-int, R-L3 |
| labels_rubric / aggregate | 95 / 44 | Hash/proposed/domain/registry/grammar, формула; root-proposed и punctuation края остаются |
| labels_metrics | 26 | Численные примеры, missing, очередь, partial tags/gate, domain κ, versions; дополнительно 200 независимых Fraction QWK |
| frozen_compatibility / role_recall / coverage | 21 / 22 / 70 | Replay/markers, malformed v3/custom variants, own samples, DEFAULT_POLICY/missing/repeat; v3 production freeze не реализован |
| labels_gold_v3 / labels_cli | 146 / 42 | 1:1, geography, captured SHA, staging/rollback; read-only/inputs/taxonomy/finite checker; lexical migration symlink отдельно R-L4 |
| labels_accuracy_outputs / eval_report_outputs / cohorts_atomic | 35 / 30 / 31 | 118 новых случаев последней правки вместе с 53 rubric; all variants/direct writers, frozen-reference, aliases/dangling/races/exact bytes/failure. Frozen compare journal preflight A7-L1 ими не покрыт |
| labels_gold_ui / labels_gold_web_ui | 41 / 50 | v3 store/form/quotes/metadata/partial/blind/provenance/time, SUMMARY handler. Нет edit после начала pending GET; A7-M1 не ловится |
| labels_gold_web_server | 16 | Код тестов покрывает настоящий HTTP/store binding; все 16 ограничены bind в этом аудите |
| labels_prompt/ai_schema/validate/gates/tagger/pipeline | Отсутствуют | Будущие P2–P3 по §12, не пропуск тестов обещанной P0/P1 реализации |
| test_prefilter*.py | 160 | Legacy regression pass; сам scope.py ещё не перенесён |

Изменений тестов в ходе аудита нет. Node harness исполняет настоящие функции формы и async логику, но не DOM движок браузера, event dispatch и native `<details>` поведение. Проверки на синтетических данных не доказывают качество будущего реального model adapter.

## 7. Готовность к следующим фазам

- **P0:** schema/rubric/metrics/baseline/migration/CLI hooks подтверждены; открытых high/medium P0 не найдено. Границы lows и запрета HTTP приведены выше. Последние исправления сохраняют рабочие данные и legacy replay.
- **P1:** рабочий read model по умолчанию открывает v3; 171 строка, 44 complete, 11 partial, 116 unlabeled, шесть тегов; machine fields отсутствуют. Общие 17 relevance≥2 включают partial curated, тогда как validate complete relevant=7. A7-M1 требует исправления до безоговорочной приёмки защиты draft. Native browser и HTTP здесь не проверены.
- **До измерений P2:** нужен поддержанный v3 build/save/load и schema-aware acceptance. [frozen_eval:738](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:738) всё ещё считает full-tags как 19; нынешний v3 relevance block описательный, старые gates не становятся v3 quality-протоколом автоматически.
- **Данные и provenance:** рабочих `data/local/evaluation/cohorts.jsonl` и `data/local/labeling/precedents.jsonl` нет. Loader/registry API существуют и synthetic guards проверены; это не готовый рабочий registry, approved reasons или независимый holdout. Есть 44 complete blind/7 complete relevant и 10 partial curated positives; реальные v3-теги отсутствуют. Требования U8 о доразметке, 30+ relevant и repeat через ≥2 недели не объявляются выполненными.
- **Реализация P2:** отсутствуют `annotation/scope.py`, `LabelingConfig`, gates/prompt/S3/tagger/ledger/budget и CLI label export. `jsonschema` уже есть ([pyproject:20](/Users/admin/Documents/HR-Automation/pyproject.toml:20)); исходное предположение «нет зависимости» устарело. Сейчас разметка UI по умолчанию уже v3, прежнее «UI продолжает v2» также устарело; явный v2 поддержан.
- **Риски §14:** реальный provider schema fallback/order, source trust/S3, retry ledger/budget, исключение notes, disabled/shadow behavior и stream rebuild ещё требуют P2–P3 реализации/тестов. Сильные компании остаются proposed/empty. Платный пилот и переключение tier_source требуют отдельной команды пользователя; ничего из этого аудит не запускал.

## 8. Предлагаемые исправления design doc

Это предложения, документ не изменялся.

| Раздел | Было → предлагается |
| --- | --- |
| §10/§11 | Discard до навигации и tests до GET → явно требовать сохранности draft/нового выбора после каждого await, добавить pending detail/page/refresh tests A7-M1 |
| §18, строка R-M3 | «Настоящий DOM» → «Node Element harness; handler не предотвращает default; native details не проверено этим тестом» |
| §4.5/§11 | «Паритет» без границы числового представления → portable JSON Schema integer допускает2.0, runtime требует int; указать и тестировать различие |
| §5.2 | Любой proposed не применяется, но root anchors доступны → явно определить root status=proposed поведение до P2, не менять текущий confirmed YAML |
| §6.5 | Без пунктуации и юрформ → зафиксировать эквивалентность скобок/дефисов на примерах R-L1 |
| §0.4/§6.9 | Occupied refusal с неявной lexical семантикой → согласовать migration symlink policy с immutable publisher, R-L4 |
| Frozen compare preflight | Комментарий о проверке до consuming holdout → оба lexical имени должны быть проверены до journal; не обещать rollback при любом IO failure, A7-L1 |
| §12/§20 | История закрытия A6 остаётся верной → сохранить её, отдельно отразить новую A7-M1 и фактическую границу P1 приёмки |

## 9. Приложение: команды и ключевые выводы

Все пути выходов ниже находятся в TMP; `PYTHONPATH=TMP` загружает запретительный hook. Полный очищенный префикс:

```sh
env -i HOME=/Users/admin PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR=/private/tmp LC_ALL=C \
  PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
  PYTHONPATH=/private/tmp/labels-v3-audit7-jpxJ5f
```

| Проверка | Команда / evidence | Ключевой вывод |
| --- | --- | --- |
| К1 | `.venv/bin/python -m pytest -q --tb=short -p no:cacheprovider --basetemp=TMP/full-tests`; `pytest-full.log` | 3443 pass, 3 failed+22 errors только bind, 88,47с; Ruff/diff pass |
| К2 | `gold migrate-v3 --source data/local/gold/vacancy-tags-v2-labeled.jsonl --out TMP/migrated.jsonl`; `gold validate --gold TMP/migrated.jsonl --output TMP/validation.json` | Exit0; 171 строка, 1:1 decisions/source/hashes/dates/label_scope/notes; assisted→curated; first=labeled; manifest SHA совпали; JSONL равен рабочему v3, valid=True |
| К3 | `eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output TMP/rules.json`; прямой run_relevance_accuracy с canonical cohort | API0/read_only; N54, QWK0,373134, recall12/17, precision12/24, P@10=0,8; Markdown reference0,373. Blind44: recall7/7, precision7/19, QWK0,433663 |
| К4 | `eval accuracy --variant saved --gold data/local/gold/vacancy-tags-v2-labeled.jsonl --output TMP/saved.json`; `integration.py` | Прежние 16 top-level keys, evaluation/operational/stability/readiness/selection и nested evaluation shape сохранены. Три saved frozen-run validate_run pass; P1 readonly model подтверждён |
| К5 | `integration.py`, `metrics-independent-probes.py` | YAML §5.2 exact2913 bytes; rubric version labels-rubric-2026-10-20; prompt hash8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8; aggregation hash9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f |
| К6 | `atomic-independent.py`, `metrics-independent-probes.py`, `frozen-coverage-independent.py`, `root-navigation-probe.py`, `atomic-exposure-preflight.py` | Закрытие A6/F/R и новые A7 findings доказаны на TMP; source code не подменялся. Частичные IO/race тесты используют намеренные fault injections |
| К7 | `git diff --exit-code 8bb3a2a -- config/search_profiles.yaml config/rules.yaml config/settings.yaml src/job_intake/pipeline.py src/job_intake/annotation/prefilter.py src/job_intake/scoring src/job_intake/storage src/job_intake/annotation/ai.py`; `snapshot.py` | Exit0; 8261 исходный файл неизменён. Включены рабочая SQLite, gold, backup/eval/exposure, code/config/tests/docs и шесть прошлых аудитов; секреты/.git/.venv/cache/.obsidian исключены |
| К8 | `collect.py`, `collection-counts.json`; targeted logs | 3468 collected; 788 labels +31 atomic +30 shared-output; ветви 458/313/142 pass пересекаются и не складываются |

Рабочая SQLite SHA-256: `35fb18d4a28e7222cd6bc9de178df8fc992f4a4a02b89897f62b7432098ac075`;
v3 gold: `936f6259a2343d7ff882f447539a9b137b67eea4eb2580edd8c91bbb3163309a`.
Начальный/конечный снимки — `before-sha256.json`, `after-sha256.json`, `snapshot-comparison.json`;
исходный/current diff и журналы проверок сохранены в TMP. Единственный разрешённый новый файл репозитория — этот отчёт; исходные файлы не перезаписывались.
