# Повторный аудит labels-v3 после исправлений P0–P1 — 20.10.2026

## 1. Итог

**R-H1 и R-M1–R-M4 четвёртого аудита закрыты в исходных сценариях. Сквозная приёмка P0–P1 остаётся с оговорками:** обнаружены **1 критическая, 1 высокая, 0 средних и 7 низких находок**. Обе новые существенные находки унаследованы от общего frozen-eval, существовали до последних исправлений и не являются их регрессиями. F-C1 — нарушение обещанной атомарности нового frozen-выхода; критический уровень следует буквальной шкале исходного задания (§0.4), **существующие данные не повреждены**. F-H1 — gate пропускает неполное покрытие, проверяя другую выборку. Шесть низких замечаний сохранены из предыдущего аудита; F-L7 уточняет доказательство DOM-тестов. Основная функциональность P0/P1 подтверждена, но считать все требования закрытыми нельзя. Разработку P2 можно продолжать; принимать результаты release-gates и начинать платный пилот до исправлений и предпосылок §7 преждевременно.

Код, design doc, критерии и рабочие данные **не менялись**. Хеши всех 7711 исходных файлов совпали до/после; единственный новый файл в репозитории — этот отчёт.

## 2. Объём и метод

- Проверена текущая рабочая копия /Users/admin/Documents/HR-Automation, включая незакоммиченные P0/P1 и последние исправления. Ветка feature/vacancy-tags, HEAD d63d5db9ebed8a91daaa28066a30846bbb2f29db; исходная база 8bb3a2a, P0-коммиты 624bd17/d63d5db. HEAD не описывает весь проверенный код.
- Исходный запрос из приложенного Pasted text.txt повторён с расширением на реализованную P1. Прочитаны AGENTS.md, design doc, optimization §3–§5, связанные README/architecture/index, YAML и обе JSON Schema; labeling-модули, accuracy/frozen/cohorts/CLI, Gold store, CRM-форма и тесты. Проверены исходный diff 8bb3a2a..d63d5db и текущий diff от 8bb3a2a. SHA-256 текущего tracked diff: 8bfe4b23eefe9834ae9f9609455b1fb06df3f040a86347d458fcadd2800ce954; новые untracked файлы отдельно входят в снимок.
- Три независимые ветви: P0/метрики/frozen, P1 store/schema/migration, P1 frontend. Обе существенные находки дополнительно подтверждены независимым проверяющим; root сверил сохранённые результаты default-policy пробы. Повторные прогоны тестов не суммируются.
- Промежуточные файлы только в **/private/tmp/labels-v3-p0-p1-reaudit-cgri4c_w** (далее TMP). Python: очищенное окружение, dotenv/bytecode/pytest cache отключены; audit hook запрещал запись в репозиторий, чтение настоящего .env, сеть, DNS и bind. Рабочая SQLite читалась только через mode=ro и query_only=ON; тесты создавали и изменяли синтетические SQLite в TMP. Ключи, личная карьерная база, реальные провайдеры и браузер не использовались. Рабочие notes не цитируются.
- Рабочая CRM, экспорт с миграцией, run/reevaluate/annotate основного pipeline не запускались. Синтетический CLI frozen run выполнялся только на TMP. Каноническая cohort проверена прямым run_relevance_accuracy, без CLI и записи exposure.
- **Полный pytest: 3 failed, 3224 passed, 22 errors за 42,25 с.** Все 25 непрошедших случаев остановлены запретом socket.bind; иных падений не найдено. Это ограниченный прогон, а не полностью зелёный результат. Ruff и git diff --check — pass. Исторические 3249 passed/54,32 с из §18 design doc относятся к исправлению с разрешённым loopback, не к этому аудиту.

## 3. Трассировка требований

Статусы относятся к реализованным P0–P1. «Не входит в P0–P1» — запланированная P2–P4, а не дефект. К1–К7 — проверки из приложения.

| Требование | Статус | Доказательство / предел |
| --- | --- | --- |
| §0.1 Критерии, stable ID и статусы не менять | Реализовано | Rules/search_profiles/settings diff от 8bb3a2a пуст; YAML равен §5.2 побайтно, К5/К7 |
| §0.2 Нет неразрешённых платных вызовов | Реализовано в объёме | CLI/read-only К2–К4: API0; tagger/бюджет — P2 |
| §0.3 Env-ключи, приватные diagnostics | Частично / не входит в P0–P1 | Провайдеров нет; error_diagnostic будущего tagger — P2. Защитный край malformed UI diagnostics — R-L5 |
| §0.4 Новые выходы атомарно, без перезаписи; ручной UI с backup/version | Отклонение | Миграция staging/exclusive и P1 replace/lock проходят; общий writer нового frozen-run не атомарен — F-C1; lexical symlink край — R-L4 |
| §0.5 Eval/export читают SQLite только read-only | Реализовано | [eval_runner.py:97](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/eval_runner.py:97), query_only:102; К2–К4 |
| §0.6 Только аддитивные миграции SQLite | Не входит в P0–P1 | Storage diff пуст. CRM startup остаётся операцией create_schema и не выдаётся за чистое чтение |
| §0.7 Сбой модели оставляет rules_only | Не входит в P0–P1 | Реального tagger/fallback ещё нет; fake/schema tests не доказывают adapter |
| §0.8 Недоверенный текст | Реализовано для P1 / не входит для prompt | UI выводит текст, canonical quote отделена от оформления; prompt/S3 — P2 |
| §0.9 Прежнее поведение по умолчанию | Реализовано в объёме | Pipeline/prefilter/scoring/storage/AI/settings diff пуст; legacy tests прошли кроме HTTP |
| §0.10 Стиль, атомарная запись и pytest | Частично | Ruff pass; pytest ограничен bind; общий writer F-C1 |
| §4.1 Enum, role taxonomy, null ≠ 0 | Реализовано | [schema.py:164](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:164), schema/CLI tests; enabled membership и общий ID-синтаксис сохранены |
| §4.2 Все шесть тегов, INDUSTRIES, решения §13 | Реализовано | Schema/aggregate/DOM tests, YAML exact К5; provider section enforcement — P2 |
| §4.2 Restrictive quotes, basis, unknown/empty | Реализовано с границей | [schema.py:389](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:389), closed/quote tests; human section policy требует уточнения, §8 |
| §4.3 Provenance, partial tags/gate, label_scope | Реализовано | 1:1 К2; independent store partial/no-op tests; aware save [gold_ui.py:499](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:499) |
| §4.3 Даты и хронология | Реализовано | [schema.py:348](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:348); 600 независимых сравнений с точной дробью, rollover, first date preserved, К6 |
| §4.4 Prediction schema, finite JSON, human-only reasons | Реализовано | 71 prediction cases, CLI/frozen checker, deep nonfinite и huge integers К6 |
| §4.5 Inline Schema и snapshots/runtime | Реализовано с границей | Schema snapshots/tests проходят; portable integer 2.0 против Python-int — R-L3 |
| §4.5 Duty/title/order/evidence response builder | Реализовано в P0 | [schema.py:291](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:291); реальные provider constraints и S3 — P2 |
| §5.2 YAML, независимые хеши | Реализовано | 2913 bytes exact; version/prompt/aggregation tests, К5 |
| §5.2 Proposed не применяется | Частично | Вложенные proposed исключены; root anchors исключением не защищены — R-L2; текущий root confirmed |
| §5.3 Прецеденты/domain/holdout guards | Реализован loader | [rubric.py:274](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/rubric.py:274), registered loader tests; registry/реальные precedents отсутствуют |
| §6.5 Base/cap/upgrade/penalties/clamp | Реализовано | [aggregate.py:37](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:37), 44 cases; argenx2/BVGroup1/PrizePicks0 на synthetic facts; Tiger — §5 |
| §6.5 normalize_company / p2_candidate | Частично | [aggregate.py:21](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:21), :80; p2 tests pass, punctuation — R-L1 |
| §6.7 QWK/MAE/±1/Spearman/missing/recall/precision | Реализовано | [metrics.py:226](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/metrics.py:226), собственные N и covered pairs явны; К3/К6 |
| §6.7 Очередь/P@k/NDCG/random/знаменатели | Реализовано для метрик | [metrics.py:94](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/metrics.py:94), missing остаётся в required N; ошибочен отдельный comparison gate F-H1 |
| §6.7 Partial tags/gate/domain/stability | Реализовано | Независимые tag/gate/ordinal samples, tag-only repeat, required_domain quality/κ; К6 |
| §6.7 Frozen hooks, version/replay/старые ключи | Частично | R-H1 закрыт versioned replay; три saved run valid и 16 saved keys сохранены; coverage gate F-H1 |
| §6.8 Tier-map baseline и явная таблица | Реализовано | A→3/B→2/C→1/other→0, rules:tier-map-v1 в JSON/Markdown, К3 |
| §6.9 1:1, assisted→curated, captured SHA | Реализовано | 171 row, manifest, single-read tests; новый TMP JSONL равен рабочему v3, К2 |
| §6.9 Консервативный tag map/unknown locations | Реализовано | 146 cases: полные aliases, неизвестность/альтернативы → dropped; реальных новых human tags N0 |
| §6.9 Occupied/staging/rollback | Реализовано с краем | Обычный occupied отказ и rollback tests pass; dangling symlink R-L4; crash-атомарность пары JSONL+manifest не обещана |
| §7 migrate-v3 / validate dispatch | Реализовано | К2; mixed/unknown schema rejected |
| §7 accuracy rules/labels и защита входов | Реализовано | К3; 42 CLI cases, enabled taxonomy/full checker до отчёта; cohort CLI имеет exposure side effect и не запускался |
| §7 label/export/--ai/--max-cost | Не входит в P0–P1 | P2/P3; отсутствие будущих команд не finding |
| §10 V2/v3/default/mixed rejection/canonical path | Реализовано | [gold_ui.py:189](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:189), :209; default/explicit/file/dir symlink — общий target/lock, stale409, К6 |
| §10 Шесть тегов/reasons/partial/empty/domain | Реализовано | 41 store и 50 DOM cases; отсутствующее, unspecified и пустой focus различены |
| §10 Полный источник/metadata/canonical quotes | Реализовано | [index.html:1668](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1668), :1808; metadata-only DOM→quote→real store tests pass; layout/native browser не проверен |
| §10 Blind read model | В основном реализовано | Whitelist source/labels и К4; malformed-key diagnostic R-L5 |
| §10 Hotkeys вне interactive controls | Реализовано | [index.html:1630](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1630) исключает SUMMARY/потомков; direct handler tests pass; точность заявления о native DOM — F-L7 |
| §10 Review_seconds только journal, bounds/no-op | Реализовано | [gold_ui.py:452](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:452); no-op JSONL bytes unchanged, dedup event, К6 |
| §10 Version/lock/backup/source binding/v2 | Реализовано в проверенном объёме | Concurrent different-row merge, stale409, source drift rejection, old observation proof, v2 date-only; HTTP заблокирован |
| §11 Pytest/Ruff и сила тестов | Частично | 3224 pass/25 blocked; 630 labels collected/614 pass; F-C1/F-H1 не покрыты, F-L7 |
| §12 Приёмка P0–P1 | Частично | Последние H/M исправлены; сквозные hard-output и gate gaps остаются |
| §12 P2–P4 / §14 риски | Не входит в P0–P1 / предпосылки | §7 отчёта; отсутствие tagger/pipeline не объявляется дефектом реализованной фазы |

## 4. Находки

### F-C1 — критический по заданной шкале: новый frozen-выход публикуется частично при ошибке записи

**Проверено.** [cohorts.py:59](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/cohorts.py:59) сериализует JSON, затем открывает **конечный** путь через mode x и пишет прямо в него (:63–65). Staging/атомарной публикации/очистки неполного файла нет. Поддержанный CLI frozen run вызывает этот helper в [evaluation_cli.py:141](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/evaluation_cli.py:141) (:149 write_new).

**Воспроизведение на TMP:** обычный CliRunner, валидный v2 bundle и predictions, запись свежего результата; искусственный OSError после реальной записи первых 20 байт. CLI завершился exit2 с явной ошибкой; final существует, содержит 20 байт и не является JSON. Обычный повтор той же команды также exit2: FileExistsError. Прямую helper-пробу независимо повторил второй проверяющий. Evidence: frozen-cli-atomic-probe.py, frozen-cli-interrupted-run.json и store-atomic-review-8kngjzgf/new-run.json в TMP.

**Последствие:** незавершённый новый результат занимает окончательный путь; повтор без нового пути/восстановления невозможен. Чтение отклоняет повреждённый JSON, silent success нет; существующие bundle/gold/run и рабочие данные сохранны. Это сбой хранения, смоделированный fault injection, а не наблюдавшаяся авария рабочего диска.

**Почему critical:** исходный аудит определяет этот уровень как нарушение жёсткого §0; §0.4 прямо требует атомарных новых выходов и ссылается на write_new. Исключение legacy writer в тексте отсутствует. Практический ущерб ограничен новым артефактом, но понизить уровень до medium без изменения заданной шкалы нельзя.

**Происхождение:** helper не изменён относительно 8bb3a2a; унаследованный дефект действующего общего writer, не регрессия последних исправлений.

**Рекомендация:** писать во временный файл рядом с целью, завершать запись до атомарной эксклюзивной публикации, отказывать при уже занятом final и очищать только собственный staging. Проверить write/close/publication failure, занятый путь и безопасный retry. Простой replace поверх чужого final нарушит вторую половину того же требования.

### F-H1 — высокий: frozen quality-gates принимают неполные предсказания из-за разных выборок

**Проверено.** [frozen_eval.py:573](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:573) проверяет task_prediction_coverage из общего evaluate_vacancies, но эти coverage используют human sample с одновременно известными role/relevance/gate ([accuracy.py:420](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/accuracy.py:420), :631). Independent actionable sample и graded ranking включают валидные partial rows без human role ([frozen_eval.py:263](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:263), :303). NDCG gate (:690–698) не использует собственные prediction_n/required_n/status метрики.

**Воспроизведение с точным DEFAULT_POLICY, без ослабления порогов:** 160 полноценных blind v2-строк со всеми 19 legacy tags + одна допустимая relevance3/eligible/role=null partial-строка. Для неё schema-checked v3 output содержит eligible, но role/relevance/decision/tier/score=null. Для остальных всё предсказано. Holdout объявлен независимым; validate_gold, load_bundle и три create→JSON→validate_run прошли; человеческие evidence minima проходят. Operational числа здесь искусственные — платных вызовов не было.

| Показатель | Проверенный результат |
| --- | --- |
| Decision /ranking coverage, используемые gate | 160/160 и 160/160 |
| Eligibility coverage | 161/161 |
| NDCG@5/@10 собственное покрытие | prediction_n160 /required_n161, status=partial |
| Actionable P1 recall | 160/161 |
| Gates ndcg@5, ndcg@10, actionable_p1_recall | pass |
| Overall /independent_holdout | pass /true |

Evidence: frozen-default-policy-probe.py и frozen-default-{bundle,baseline,candidate,repeat,report}.json. Дополнительная probe 30+1 с заранее заданным custom minimum воспроизводит дефект полностью нормализованными v3 predictions; default-policy проба использует поддержанные legacy outputs для 160 full rows, чтобы удовлетворить старым 19-tag gates. Это поддержанный v2-bundle path с проверкой v3 outputs, **не** отложенный P2 v3-freeze.

**Последствие:** арифметика NDCG и recall корректна, но решение «доказательств достаточно для pass» неверно. Baseline/candidate/repeat с одинаковым пропуском проходят. Ошибочный вердикт на рабочем holdout не утверждается.

**Происхождение:** несовпадение sample уже присутствовало в 8bb3a2a; новое role-recall исправление его не вводило.

**Рекомендация:** coverage каждого gate рассчитывать по той же human sample и требуемым output-полям, что и проверяемая метрика; учитывать explicit partial/missing status. Добавить независимые partial-role/relevance-only/tag-only/gate-only cases с одинаковыми пропусками baseline/candidate/repeat и ожиданием insufficient_evidence.

### Низкие находки

R-L1–R-L6 сохранены из четвёртого аудита и проверены текущим чтением соответствующих путей; прежние воспроизведения находятся в неизменённом историческом отчёте. Их старые пробы не выдаются за новые запуски этого аудита. F-L7 найдена сейчас.

| ID | Доказательство / статус | Последствие / рекомендация |
| --- | --- | --- |
| R-L1 | [aggregate.py:27](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:27): юрформа удаляется до пунктуации; Acme LLC и Acme (LLC)/Acme-LLC нормализуются различно | Нормализация слабее §6.5. Согласовать скобки/дефисы. Strong_companies сейчас пуст/proposed, рабочий эффект не доказан |
| R-L2 | [rubric.py:194](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/rubric.py:194): root proposed не исключает anchors/notes из prompt hash; nested proposed исключён | Неоговорённая граница общего proposed-правила. Уточнить root guard до P2, текущий confirmed YAML не менять |
| R-L3 | [schema_validation.py:53](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema_validation.py:53) и strict-int runtime: portable schema принимает 2.0, Python отвергает | Допустимая семантика JSON Schema, но слово «паритет» шире контракта. Документировать различие и explicit case |
| R-L4 | [gold_workbook.py:45](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_workbook.py:45) и [gold_v3.py:242](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/gold_v3.py:242): resolve/exists не считает dangling symlink занятым lexical path | Отдельный край occupied-path; существующий regular-файл не перезаписывается. Явно определить symlink policy / lexists до resolve |
| R-L5 | [schema.py:393](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:393) → [gold_ui.py:340](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:340): неизвестное имя tag попадает в warning | Искусственно вложенный в **имя ключа** machine decision может попасть в blind diagnostics. Обычная утечка штатного value не найдена. Санитизировать неизвестные имена |
| R-L6 | [index.html:1937](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1937): identity guard есть до refresh; после await refresh старый save-next открывает свой successor | Может отменить более поздний выбор чистой карточки. Потеря dirty draft не доказана: discard confirmation остаётся. Проверить identity после await |
| F-L7 | [vacancy-labeling-architecture.md:1357](/Users/admin/Documents/HR-Automation/docs/vacancy-labeling-architecture.md:1357) говорит «Настоящий DOM summary/span», но [test_gold_web_ui.py:30](/Users/admin/Documents/HR-Automation/tests/test_gold_web_ui.py:30) использует Node harness, addEventListener no-op (:38); [test_labels_gold_web_ui.py:380](/Users/admin/Documents/HR-Automation/tests/test_labels_gold_web_ui.py:380) вызывает handler прямо | Тест доказывает отсутствие перехвата handler, а не native раскрытие details. Уточнить формулировку; настоящий browser keyboard/selection отдельно. Ошибка исполнения hotkey не найдена |

## 5. Признанные отклонения и закрытие прошлых находок

| Отклонение исходного задания | Вердикт / нужная правка |
| --- | --- |
| company= у rules_relevance | Обосновано strong_company; §6.5 уже отражает сигнатуру |
| required_domain/basis в response | Обосновано, §4.5 обновлён; пустые provider details нормализует будущая S3 |
| duty_unit_ids/title_unit_ids у response_schema | Обосновано для section constraints P2; широкий fallback сам по себе не доказывает эти constraints |
| Сохранён label_scope | Обосновано provenance, перенос 1:1 проверен К2 |
| Tiger Analytics=1 при industry=pharma_biotech | Старая подстановка employer industry неверна. Design различает consultancy и required pharma: 2−2=0, с focus 3−2=1. Human Tiger1 сохранён; без подтверждённых тегов не обещать его воспроизведение |
| domain_fit только requirement/industry | Уже исправлено: required_domain входит в quality/κ при соответствующем human requirement |
| MAE/QWK/Spearman только covered pairs | Обосновано: missing не имеет ordinal значения; coverage/собственные N вынесены явно |
| build_bundle/load_bundle только v2 | Ограничение обозначено: build v2-validator, load digest/policy/source не равен полной v3 validation. Поддержанный v3 freeze/gates — предпосылка P2 |
| LabelResult/TagValue отложены до P2 | Обосновано и явно отражено §3/§12 |
| Assisted в cohort = curated | Rename человеческого метода, не assisted_llm; [cohorts.py:152](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/cohorts.py:152), К4 |
| Старые proposed §3/§12 против confirmed §5.2 | Старый конфликт исправлен; текущий YAML exact. Root caveat R-L2 остаётся отдельной границей |
| P1 canonical quotes + отдельный raw preview | Обосновано unique source binding. Metadata/source-block и SUMMARY исправлены; native browser ограничение F-L7 |

| Предыдущий ID | Текущий вердикт | Проверенное доказательство |
| --- | --- | --- |
| R-H1 | Закрыт | Human-known-role sample, frozen-task-metrics-v2; на 10 рабочих valid partial rows current N0/value=null, legacy10/10; marker/hash/replay, unknown/mixed version rejection, К6 |
| R-M1 | Закрыт | Default/explicit resolve до pin/lock; file/dir aliases, настоящий concurrent merge разных строк и stale409 одной; symlink цел, К6 |
| R-M2 | Закрыт | Отдельные literal source fields и каждый string/list element metadata; четыре metadata-only DOM→payload→store cases pass |
| R-M3 | Закрыт в проверенном объёме | SUMMARY/потомки исключены, Enter/i/0/3 не меняют draft и не вызывают save; native default только не подавляется handler |
| R-M4 | Закрыт | Strict offset, exact UTC/fractions, mixed date-only policy, aware v3 save; 600 oracle comparisons, rollover и v2 compatibility К6 |
| A-H1/A-M1–A-M5 и S-M1 | Повторного открытия не найдено | Conservative migration, v3 checking независимо от variant/version, tag-only stability, huge ints, registered domain validator, enabled membership и общий stream-ID syntax проходят |

Новые F-C1/F-H1 не являются повторным открытием R-H1/R-M1–R-M4. Низкие прошлые замечания не входили в поручение исправить H/M.

## 6. Покрытие тестами

Collect-only: **630 labels cases**; в полном прогоне **614 прошли, 16 HTTP остановлены bind guard**. Это часть 3224 passed, не дополнительное число. Последние исправления добавили 88 случаев: 22 frozen, 41 schema +6 store, 16 DOM и3 HTTP; count diff с audit4 это подтверждает.

| Файл / cases | Что проверяет | Пробел / предел |
| --- | --- | --- |
| test_labels_schema /109 | Enum/closed schema/snapshots/null/0/IDs; 41 новых date/offset/fraction/mixed/boundary cases | Portable integer R-L3; human section policy уточнить |
| test_labels_prediction_validation /71 | Nested/finite/duplicates/roles/human-only reasons/huge JSON | Реальная S3/provider — P2 |
| test_labels_cli /42 | Migration/validate/accuracy, protected inputs, enabled taxonomy/IDs/huge scores | Fake CLI не проверяет платный путь |
| test_labels_gold_v3 /146 | Captured read/SHA/1:1, aliases/unknown/alternatives, dropped manifest, staging/publication/rollback | R-L4; общий cohorts.write_new этим модулем не проверяется |
| test_labels_metrics /26 | Ручные QWK/Spearman/NDCG/P@k/missing, независимые samples/domain/focus, versions | Metric arithmetic не доказывает coverage gate F-H1 |
| test_labels_frozen_compatibility /21 | V3 markers/custom variant, malformed/v1 guard, tag-only repeat/huge score | Не проверяет partial sample composition в gate и writer failure |
| test_labels_frozen_role_recall /22 | Known/unknown/other/abstention, independent legacy formula, hashes/version/JSON/replay/mixed rejection | Role-recall исправлен; иные task-coverage populations F-H1 не охвачены |
| test_labels_rubric /42 | YAML/hash independence/nested proposed/registered domain/holdout guards | Root R-L2; реальные registry/approved reasons отсутствуют |
| test_labels_aggregate /44 | Formula/base/cap/penalties/clamp/p2/control facts | Punctuation R-L1; historical Tiger без подтверждённых тегов не переоценивается |
| test_labels_gold_ui /41 | V2/v3, canonical path/concurrency/source/provenance/date/partial/blind/no-op/time | Malformed-key diagnostic R-L5; arbitrary TOCTOU/crash recovery не доказаны |
| test_labels_gold_web_ui /50 | Six tags, absence/unspecified/empty, order, metadata literal/quote→store, summary hotkeys/409/time | Node harness; native keyboard/layout не доказаны, F-L7; await navigation R-L6 |
| test_labels_gold_web_server /16 | В коде: реальный HTTP GET/PATCH, stale/partial/journal/v2/symlink/rollover/metadata | **В текущем аудите не выполнены**: bind forbidden |
| Prompt/AI schema/S3/gates/tagger/pipeline | Будущая таблица §11 | Отсутствие P2/P3 tests не считается пробелом P0/P1 |

Дополнительно целевой Node/store frontend набор дал67 passed (пересекается с полным прогоном). Независимые audit probes — только TMP, в репозиторий как regression tests не добавлялись. F-C1/F-H1 требуют отдельных регрессий; эту задачу аудит не реализует.

## 7. Готовность к P2 и риски §14

| Предпосылка / риск | Проверенное состояние / необходимое |
| --- | --- |
| Приёмка P0–P1 | Последние пять H/M закрыты. F-C1/F-H1 остаются в поддержанном общем eval; functional P1 подтверждена с HTTP/browser ограничениями |
| V3 freeze/build/save/load/gates | [frozen_eval.py:146](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:146) — v2 validator в builder; load:218 не полноценный v3 label-checker; full-tags gate:608 требует19. Нужен целый schema-aware контракт, а не только команда |
| Cohort registry/precedents | data/local/evaluation/cohorts.jsonl и data/local/labeling/precedents.jsonl отсутствуют; loader/guards есть. Нужны registered calibration/development/holdout и утверждённые reasons |
| Достаточность измерения | N54/17 relevant; full blind44/7. Нет требуемых150+ решений/30+ relevant и нового независимого holdout/repeat; human six-tag labels N0. Синтетический F-H1 probe не evidence качества |
| Актуальность gold | Default UI уже v3, fallback v2. Новая migration равна рабочему снимку; автоматической двусторонней синхронизации v2/v3 нет |
| Зависимости/компоненты | jsonschema присутствует и исполняется. annotation/scope.py перенос, gates/prompt/validate/tagger, LabelingConfig/LabelResult/TagValue и export orchestrator ещё не реализованы — P2 |
| Claims fallback/strict evidence/order | Response builder есть; нужны реальные provider stage/schema, explicit duty/title, untrusted policy и S3 tests. Настоящий adapter не проверялся |
| Privacy/holdout exposure | Closed precedent loader запрещает notes; полный prompt/diagnostics/display exposure — P2. Loader не доказывает человеческое происхождение reason |
| Cost/retry/latency | Ledger/BudgetGuard/price-missing/per-attempt usage/concurrency не реализованы. API0 baseline не подтверждает бюджет tagger |
| Default/pipeline complexity | Pipeline/scorer/prefilter/storage/AI adapters неизменны. Shadow/labels/tier-source parity и stream rebuild — P3 |
| UI layout | Дополнительные source facts увеличивают колонку. Без browser viewport нельзя подтвердить sticky/scroll удобство; это непроверенный риск, не finding |
| Платный пилот | Только отдельная команда после подготовки; выбор моделей/лимит $3 не пересматривались и не использовались |

## 8. Предлагаемые исправления design doc

Документ не редактировался. Предложения касаются технического контракта, не критериев.

| Раздел | Было → стало |
| --- | --- |
| §0.4/§0.10/§11 | «write_new уже атомарен» → реализовать/описать staged exclusive publication и failure/retry tests; существующее ложное утверждение снять |
| §6.7/§11 | Общие task coverage → каждая gate metric использует собственную human population, prediction requirements и partial/missing status |
| §18 | «Настоящий DOM summary/span» → «Node DOM-harness: handler не подавляет native default; native details отдельно не проверен» |
| §4.2/§4.5 | Общая таблица секций при явном machine enforcement P2 → определить требование для human quotes; текущее принятие иных source-секций не объявлять отсутствующим P1 S3 |
| §5.2/§0 | Proposed неэффективен → явно задать root anchors/notes guard; текущий YAML не менять |
| §4.5 | Полный паритет schema/runtime → уточнить strict Python-int против portable integer2.0 |
| §6.5/§6.9 | Нормализация юрформ/занятые пути → определить скобки/дефисы и lexical symlink entry |
| §10 | Blind diagnostics / save-next → безопасные malformed-key warnings и request/editor identity после await |
| §6.7/§12/§14 | V3 freeze как предпосылка → весь schema-aware lifecycle/acceptance вместо19 tags; registry/privacy/budget/holdout остаются незавершёнными |
| §18/шапка | Исторические H/M закрыты → сохранить это как историю; не приравнивать к отсутствию новых находок и не смешивать 3249-pass implementation с ограниченным audit-run |

## 9. Приложение: команды и ключевые выводы

### К1. Среда и проверки

Префикс всех Python/CLI:

    env -i HOME=/Users/admin PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR=/private/tmp LC_ALL=C PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=/private/tmp/labels-v3-p0-p1-reaudit-cgri4c_w

Команды (TMP — абсолютный каталог §2):

    .venv/bin/python -m pytest -q --tb=short -p no:cacheprovider --basetemp="$TMP/full-tests"
    .venv/bin/ruff check src tests --no-cache
    .venv/bin/python -m pytest --collect-only -q -p no:cacheprovider tests/test_labels_*.py
    git diff --check
    git diff --exit-code 8bb3a2a -- config/search_profiles.yaml config/rules.yaml config/settings.yaml src/job_intake/pipeline.py src/job_intake/annotation/prefilter.py src/job_intake/scoring src/job_intake/storage src/job_intake/annotation/ai.py

Pytest выполнен модульной точкой входа той же .venv. **3 failed,3224 passed,22 errors/42,25 с**, Ruff pass, collect630, git checks pass. Распределение25 bind-blocked:1 Discovery +2 archive failed;2 CRM +4 legacy Gold +16 labels Gold errors. Во всех причина PermissionError: Audit guard: network disabled на socket.bind. Разрешения на loopback не запрашивались, ограничение не обходилось. .venv symlink доступен. Логи pytest.log/labels-collected.txt — TMP.

### К2. Миграция и validation

    .venv/bin/job-intake gold migrate-v3 --source data/local/gold/vacancy-tags-v2-labeled.jsonl --out "$TMP/migrated.jsonl"
    .venv/bin/job-intake gold validate --gold "$TMP/migrated.jsonl" --output "$TMP/validation.json"

Exit0/valid=True; 171 строка, decision_labeled44, relevant7, labeled_rows55, relevance_only10; blind44/curated11/assisted_llm0; migrated_tags0/dropped_tags0. Проверены1:1 ID/решения/source/source_hash/text_version/labeled_at/label_scope/notes без вывода содержимого, assisted→curated, first_labeled_at=labeled_at, manifest SHA. Новый JSONL побайтно равен рабочему v3. Evidence: migrated.manifest.json, validation.json, integration-summary.json.

### К3. Baseline

    .venv/bin/job-intake eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output "$TMP/rules.json"

Exit0, database_access=read_only, API0. Явная таблица JSON/Markdown **A→3/B→2/C→1/other→0**. С сохранённым data/local/eval/accuracy-rules.md совпали N54/QWK≈0,373/recall12/17.

| Показатель | Результат |
| --- | --- |
| N/coverage | 54/54 из54 |
| QWK/MAE/Spearman | 0,373134/0,888889/0,389303 |
| Exact/±1 | 21/54 и42/54 |
| Recall≥2/precision≥2 | 12/17 и12/24 |
| P@5/P@10 | 4/5 и8/10 |
| NDCG@5/random | 0,874799/0,261829 |
| NDCG@10/random | 0,771175/0,261603 |

### К4. Legacy, cohort, saved frozen и P1 read model

    .venv/bin/job-intake eval accuracy --variant saved --gold data/local/gold/vacancy-tags-v2-labeled.jsonl --output "$TMP/saved.json"
    .venv/bin/python "$TMP/integration.py"

Exit0. Saved имеет прежние16 top keys, наборы evaluation/operational/stability/readiness/selection и рекурсивную структуру evaluation против after-apply-accuracy.json. Равенство значений при ином числе gold не утверждается.

Прямой run_relevance_accuracy выбрал163 строки: blind44/curated3/unlabeled116. Blind: **N44, recall7/7, precision7/19, QWK0,433663**. Cohort/exposure не менялись.

load_bundle+validate_run pass для всех сохранённых пар под data/local/eval/p1-2026-10-19:

| Run | Bundle |
| --- | --- |
| baseline-run.json | bundle.json |
| candidate-run-v2.json | release-bundle-v2.json |
| release-baseline-run-v2.json | release-bundle-v2.json |

Read-only GoldLabelStore: default v3,6tag options, stats total171/complete44/partial11/unlabeled116/relevant17/strong10; detail can_save=True, основные machine fields отсутствуют. Рабочий PATCH не выполнялся. Evidence integration-summary.json/blind-summary.json.

### К5. YAML и хеши

§5.2 побайтно равен config/labeling_rubric.yaml, **2913 bytes**, SHA-256 9b7f767c2817df6a2ed6e5b1738d7586c21ae9ba5cc1ca707d36fc806e10f4d2. Version labels-rubric-2026-10-20; prompt_rubric_hash 8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8; aggregation_hash 9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f.

Независимость version/prompt/aggregation, nested proposed и precedent hash покрыта текущими rubric tests. Исключение root proposed — R-L2. Канон личной базы не перечитывался: проверено применение утверждённых значений, а не переоценка критериев.

### К6. Дополнительные пробы

Запущены с тем же префиксом, выходы только TMP:

    .venv/bin/python "$TMP/frozen-probes.py"
    .venv/bin/python "$TMP/frozen-composition-probe.py"
    .venv/bin/python "$TMP/frozen-default-policy-probe.py"
    .venv/bin/python "$TMP/frozen-cli-atomic-probe.py"
    .venv/bin/python "$TMP/store-independent-probes.py"
    .venv/bin/python "$TMP/partial-role-check.py"

- Frozen: corrected role1/2 против legacy2/3 на mixed synthetic; unknown marker/null/object/int и mixed versions rejected; independent ordinal N3/tag N1/gate N4, P@5 required3; ±10**400 сохранены через baseline/JSON/validate и точную очередь; run inputs не меняются.
- Рабочие10 valid partial rows без human role: **current role recall0/0=value null**, explicit legacy10/10. Только агрегаты partial-role-check.json, без source/notes.
- Store:600 Fraction-based timestamp comparisons (300 случайных offset +300 same-UTC-second, fractions до99 digits),0 mismatches. +14→SP rollover сохранён, original first timestamp точен; новый last2026-10-20T13:00:00.987654−03:00.
- Store: file/dir/default/explicit aliases дают один target, links сохранены; concurrent different-row merge, same-row stale409, shared journal. Partial personal_constraint и no-op retry сохраняют JSONL bytes,1review event. Synthetic v2 migration/source/SHA/occupied refusal, v2 date-only save, Discovery v3 save/retry8сек, source observation compatibility при прежнем pin; изменение working_language rejected.
- F-C1/F-H1: артефакты и результаты §4. Повторный анализ F-H1 независимо load→validate→compare подтвердил partial ranking с pass; F-C1 независимо подтверждён реальным prefix write+EIO.

Arbitrary filesystem TOCTOU, power-loss recovery, настоящий provider/цены/бюджет, native keyboard/selection/layout в этом аудите не проверялись.

### К7. Контроль неизменности

**7711/7711 существующих файлов совпали:** 216 source/test,38 существовавших docs,7419 data/local,8 YAML и остальные tracked файлы. Before/after SHA-снимки, git-status и verification-summary.json — TMP. Исторические четыре аудита не перезаписывались. Ветка/HEAD сохранены. После публикации этого отчёта исходные файлы проверены повторно; единственное добавление к исходному git status — новый отчёт.

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

Gold/* в таблице — под /Users/admin/Documents/HR-Automation/data/local/. Критерии, код, design doc, базы/gold, существующие отчёты, backup и exposure journals сохранены.

