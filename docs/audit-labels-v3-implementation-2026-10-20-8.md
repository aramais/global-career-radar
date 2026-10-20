# Восьмой аудит labels-v3: P0–P1 после исправления A7-M1 — 20.10.2026

## 1. Итог

**A7-M1, включая прежнюю R-L6, закрыта в проверенных сценариях. A6-C1/A6-M1, F-C1/F-H1 и R-H1/R-M1–R-M4 не открылись повторно.** Найдены **0 критических, 0 высоких, 1 средняя и 8 низких находок**. Новая A8-M1: гонка основного pager и открытия старой карточки оставляет неверный offset, вследствие чего последовательная навигация пропускает страницу из 100 карточек. Новая низкая A8-L1: после успешного save и закрытия редактора может остаться устаревший loading-banner. Семь остальных низких находок сохраняются. Основной контракт P0 подтверждён; безоговорочная приёмка P1 требует исправления A8-M1. Разработку P2 можно продолжать с предпосылками §7; готовность к release-измерениям или платному пилоту не подтверждена. Потеря нового ввода, исправленная в A7-M1, не воспроизведена.

Код, design doc, критерии и рабочие данные не менялись. **8263 файла исходного снимка совпали по SHA-256 до/после проверок.** Единственный новый файл репозитория — этот отчёт.

## 2. Объём и метод

- Повторено исходное задание из приложенного `Pasted text.txt` с актуальным объёмом P0–P1. Ветка `feature/vacancy-tags`, HEAD `d63d5db9ebed8a91daaa28066a30846bbb2f29db`, база `8bb3a2a`. Проверена текущая рабочая копия, включая незакоммиченные изменения и untracked исходники/тесты, а не только HEAD.
- Прочитаны AGENTS.md, design doc с §21, optimization §3–§5, README/architecture, YAML/JSON Schema, labeling-модули, accuracy/frozen/cohorts/eval CLI, P1 store/frontend и тесты. Исходный diff `8bb3a2a..d63d5db` и текущие изменения сверены по областям. Три независимые ветви: метрики/рубрика/frozen; atomic/CLI/миграция; P1 store/JS. Новые UI-находки независимо воспроизведены root и P1-аудитором.
- TMP — **`/private/tmp/labels-v3-audit8-fIM3LL`**. Проверки Python работали в очищенном окружении с отключёнными dotenv, bytecode и pytest-cache. Audit hook запрещал **все** connect/DNS/bind, чтение настоящего `.env`, запись/мутации в репозитории и открытие рабочей SQLite без `mode=ro`. Eval дополнительно использует `query_only=ON` ([eval_runner.py:96](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/eval_runner.py:96)). Синтетические файлы/SQLite/journals создавались только в TMP.
- `.venv` доступен: `/Users/admin/.venvs/hr-automation`. Полный pytest: **3 failed, 3474 passed, 22 errors за 45,21 с**. Все 25 непрошедших случаев остановлены `PermissionError: Audit guard: all network and bind disabled` на bind; иных падений нет. Запрет не обходился, loopback не разрешался. **3499 passed/57,20 с** в design doc §21 — исторический результат предыдущей реализации с loopback, не результат этого аудита.
- Ruff и `git diff --check` — pass. Целевые 458/475/173 passed пересекаются с полным прогоном и не суммируются. Коллекция — 3499 случаев, включая 819 `test_labels_*` и 31 navigation case.
- Ключи, провайдеры, браузер и личная карьерная база не использовались. YAML сверялся с утверждённым блоком §5.2; критерии не оспаривались. Личные notes и тексты рабочих вакансий не цитируются.
- Pipeline, `run`, `reevaluate`, `annotate`, `crm serve`, `export-csv` не запускались. CLI-выходы направлялись в TMP. Каноническая когорта проверена прямым `run_relevance_accuracy`, без CLI/exposure. Все новые cohort/frozen/exposure-пробы — синтетические. Node harness исполняет настоящие функции UI, но не доказывает нативное поведение браузера или настоящий HTTP.

## 3. Трассировка требований

Статусы относятся к P0–P1. Будущие P2–P4 не считаются отсутствующей реализацией текущей фазы. К1–К8 — проверки приложения §9.

| Требование | Статус | Доказательство / предел |
| --- | --- | --- |
| §0.1 Критерии, stable ID, статусы | Реализовано | К5/К7: YAML exact, rules/search_profiles/settings diff от базы пуст, исходные хеши сохранены |
| §0.2 Нет неразрешённых платных вызовов | Реализовано в проверенном объёме | API0 в К2–К4; сеть запрещена; реальный tagger/budget — P2 |
| §0.3 Env-ключи и privacy diagnostics | Не входит в P0–P1 в части tagger | Provider/error_diagnostic будущего tagger не проверен; blind diagnostic-key край — R-L5 |
| §0.4 Immutable выходы, сохранность gold/эталонов | Частично | A6-C1/F-C1 закрыты: [cohorts:61](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/cohorts.py:61), [eval_runner:440](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/eval_runner.py:440); lexical migration R-L4 и exposure preflight A7-L1 сохраняются |
| §0.5 Read-only eval/export | Реализовано для P0 | mode=ro/query_only, К2–К4; будущий label export — P2 |
| §0.6 Только аддитивные SQLite migrations | Не входит в P0–P1 | Storage diff пуст; startup CRM с create_schema не запускался и не выдаётся за чтение |
| §0.7 Сбой модели оставляет rules_only | Не входит в P0–P1 | Реальный tagger/fallback — P2 |
| §0.8 Недоверенный источник | Реализовано для P1 | Literal source/quote blocks и whitelist; prompt/S3 — P2 |
| §0.9 Прежнее поведение по умолчанию | Реализовано в проверенном объёме | Pipeline/prefilter/scoring/AI/settings неизменны, К7; новый immutable-report контракт явно описан §20 |
| §0.10 Стиль, атомарность, pytest | Частично | Ruff/diff pass, atomic tests pass; HTTP ограничен, native DOM заявление — F-L7 |
| §4.1 Enum, role syntax/taxonomy, null≠0 | Реализовано | [schema:169](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:169), :223; shared full-string ID syntax, membership отдельно; A6-M1 закрыта |
| §4.2 Шесть тегов, INDUSTRIES, §13 | Реализовано | 14 INDUSTRIES exact, schema snapshots valid, aggregate/UI tests, К5 |
| §4.2 Restrictive quote/basis, unknown/empty | Реализовано в P0 | Schema/runtime tests; human quote policy отделена от будущих provider section constraints |
| §4.3 Provenance, label_scope, partial rows | Реализовано | К2: 171 строка 1:1; null, absent tag, explicit unspecified/empty focus различаются |
| §4.3/4.5 Даты и chronology | Реализовано | [schema:348](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:348); offset/UTC/fractions/mixed precision, store rollover |
| §4.4 Closed prediction, finite JSON, enabled roles | Реализовано | 71 prediction case, CLI/frozen checks; bool/NaN/Infinity rejected, huge int сохраняется |
| §4.5 Снимки и Draft validation | Частично | Снимки совпадают, jsonschema runtime; portable integer2.0 против strict runtime — R-L3 |
| §4.5 Response order/unit enums/duty/title | Реализовано в P0 | [schema:291](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:291); S3/реальный provider — P2 |
| §5.2 YAML и независимые идентификаторы | Реализовано | К5: 2913 bytes exact; rubric version и два hashes независимо меняются |
| §5.2 Proposed не применяется | Частично | Nested proposed strong_companies исключён; root anchors/notes — R-L2; рабочий root confirmed |
| §5.3 Precedent grammar/domain/registry/holdout guards | Реализовано как loader | Настоящий synthetic registry: 35 invalid rejected, 14 valid accepted; [rubric:259](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/rubric.py:259); рабочих registry/precedents нет |
| §6.5 Base/cap/upgrade/penalties/clamp | Реализовано | [aggregate:37](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:37); controls argenx2/BVGroup1/PrizePicks0; Tiger — §5 |
| §6.5 normalize_company / p2_candidate | Частично | [aggregate:28](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:28), :80; p2 tests pass, punctuation — R-L1 |
| §6.7 QWK/MAE/±1/Spearman/missing/threshold | Реализовано | [metrics:226](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/metrics.py:226); К3/К6; 400 QWK и 400 Spearman по независимым references без расхождений |
| §6.7 Queue/P@k/NDCG/random/denominators | Реализовано | К3/К6: missing P@5 denominator5/P@10 denominator6, partial NDCG/coverage, точный порядок score±10**400 |
| §6.7 Partial tags/gate/domain/stability | Реализовано | Независимые human populations при relevance=null, required_domain quality/κ, tag-only repeat, missing≠empty focus |
| §6.7 Frozen versions/role recall/coverage/replay | Реализовано в P0 | К4/К6; R-H1/F-H1 закрыты, old runs valid; поддержанный v3 freeze/acceptance — P2 |
| §6.8 Tier→relevance baseline | Реализовано | [predictions:36](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/predictions.py:36); A3/B2/C1/other0 явно в JSON/MD, К3 |
| §6.9 1:1/assisted→curated/captured SHA | Реализовано | К2: 171 строка; manifest input/output SHA совпали, новый JSONL равен рабочему v3 |
| §6.9 Консервативный перенос tags | Реализовано | 146 случаев: неизвестные страна/город/альтернативы → dropped, не новый отрицательный факт |
| §6.9 Occupied/staging/rollback | Частично | Обычный occupied/own-inode rollback pass; dangling edge R-L4; предел crash-пары описан |
| §7 migrate-v3 / schema-aware validate | Реализовано | К2; unknown/mixed rejected; validate не заменяет existing reference |
| §7 accuracy rules/labels и saved compatibility | Реализовано | К3/К4/К6: inputs/read-only/all variants exclusive, прежние keys/shapes сохранены |
| §7 label/AI/budget/concurrency/export | Не входит в P0–P1 | Будущая P2 |
| §10 P1 v2/v3, шесть тегов/gate/provenance/time | Реализовано | 41 store+50 Node form+31 navigation case; default v3/explicit v2, partial/no-op, audit-only time |
| §10 Canonical source/metadata/quote binding | Реализовано | Metadata-only Node→store tests; подписи вне quote, source whitelist |
| §10 Blind read model | Частично | К4: machine fields отсутствуют; malformed unknown key достигает warnings — R-L5 |
| §10 Hotkeys/SUMMARY | Реализовано в проверенном объёме | Enter/i/0/3 не перехватываются в summary/span; native expansion не проверена |
| §10 Version/lock/backup/source binding/v2 | Реализовано в проверенном объёме | Non-HTTP store/concurrency/symlink/UTC tests pass; настоящий HTTP запрещён |
| §10/§21 Сохранность draft и нового выбора после await | Реализовано для A7-M1 | Fresh independent probes: dirty/notes/tags сохраняются, retry актуален, старый save-next не отменяет новый выбор |
| §10/§21 Queue/offset при гонке основного pager | Отклонение | A8-M1: committed offset меняется до принятия page; после отменённого refresh next пропускает страницу |
| §10/§21 Loading-state cleanup | Частично | Флаг goldLoading освобождён, но видимый устаревший banner может остаться — A8-L1 |
| §11 Тесты и сила доказательств | Частично | 3474 pass/25 blocked; actual pager pre-mutation и close-after-PATCH banner не покрыты, F-L7 |
| §12 Приёмка P0 | Частично | Основные результаты подтверждены, открытых high/medium P0 нет; полного green HTTP-run в этом аудите нет |
| §12 Приёмка P1 | Частично | A7-M1 закрыта; новая A8-M1 и HTTP/browser предел не позволяют безоговорочную приёмку |
| §12 P2–P4 / §14 риски | Не входит в P0–P1 | Предпосылки §7; отсутствие будущего stack не считается текущим дефектом |

## 4. Находки

### A8-M1 — средняя: отменённый refresh оставляет offset будущей страницы и навигация пропускает 100 карточек

**Проверено независимо двумя аудиторами.** Основные pager handlers [index.html:2062](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:2062) и :2063 меняют `goldOffset` **до** ответа `refreshGold(false)`. Старые карточки остаются видимыми и кликабельными (:1749/:1757); `openGold` не запрещён при `goldLoading`. Открытие карточки корректно инвалидирует старый refresh, но предварительный offset не отменяется. [goldNeighborUID:1935](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1935) затем вычисляет соседнюю страницу из него, а не из принятого `goldPage.offset`.

**Свежая последовательность:** показаны 100 карточек page0, последняя — synthetic `one`. Нажать основной next: запрос page100 ожидается. Открыть ещё видимую `one`, затем завершить page100 GET. Refresh отвергнут как устаревший, но состояние: `goldOffset=100`, `goldPage.offset=0`, anchor99, editor.pageOffset100. Нажать следующую в редакторе: запрос **page200**, открыта `first-at-200`. Последовательность page-запросов **[100,200]**, page100 не показана. Root evidence: `root-pagination-probe.py`, `root-pagination-results.json`; независимое: `ui-independent-results.json/main_pagination_then_open_old_card_skips_page`.

В Node исполнялись настоящие JS-функции и дословное тело основного next handler; браузерные события/HTTP не запускались. Доступность старой карточки подтверждена source review. Пропуск страницы — проверенный результат; риск пропуска вакансий при последовательной ручной разметке — следствие. Сохранённые gold и текущий draft не повреждены, восстановление навигации возможно. Уровень **средний** обусловлен функциональным разрывом P1, не косметическим статусом и не ошибкой метрики. Это отдельная находка, не повторное открытие A7-M1.

**Рекомендация:** отделить requested offset от committed offset; менять `goldOffset` вместе с принятым page. Не делать поздний rollback поверх нового контекста. Добавить regression с вызовом основного pager → открытием старой карточки → editor next и проверкой page100. Нынешний [test:349](/Users/admin/Documents/HR-Automation/tests/test_labels_gold_navigation.py:349) вызывает refresh напрямую, без предварительного изменения offset обработчиком, и этого разрыва не видит.

### A8-L1 — низкая: после закрытия редактора остаётся видимый loading-banner завершённого refresh

**Проверено независимо двумя аудиторами** в Node harness с synthetic API. `saveGold(false)` получает успешный PATCH, рендерит новый clean editor, затем ожидает refresh. Закрытие разрешено; [closeModal:1188](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1188) сбрасывает editor и инвалидирует навигацию. Ответ refresh отвергается в [:1712](/Users/admin/Documents/HR-Automation/src/job_intake/crm/index.html:1712), до `goldMessage('')` в :1717; finally :1722 снимает только `goldLoading`.

Итог пробы: saved=true, editor=null, modal hidden, goldLoading=false, но видимый **«Загрузка выборки…»** остаётся после завершения запросов. Evidence: `root-ui-results.json/close_after_save_refresh` и `ui-independent-results.json/close_after_saved_patch_leaves_loading_banner`. Данные сохранены в модели сценария, потери draft нет; фактические рабочие файлы не менялись. Низкая — неверен статус загрузки, а не сохранение. Рекомендация: очищать завершённое loading-сообщение только его владельцем, сохраняя сообщения более позднего запроса; добавить close-after-PATCH/pending-refresh regression. Безусловное очищение global message в finally может снова стереть статус нового действия.

### Сохранившиеся низкие находки

Все семь строк подтверждены свежими synthetic probes и текущим кодом. Их наличие не переоткрывает исправленные high/medium.

| ID / уровень | Доказательство | Последствие / рекомендация |
| --- | --- | --- |
| R-L1 / низкий | [aggregate:28](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/aggregate.py:28): Acme LLC→acme, Acme (LLC)/Acme-LLC→acme llc; Acme (S.A.)→acme s a | Порядок удаления пунктуации/юрформ слабее §6.5. Согласовать нормализацию; влияние на будущий strong match выведено, текущий proposed/empty список не активирует его |
| R-L2 / низкий | [rubric:198](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/rubric.py:198): root proposed оставляет anchors/notes и меняет prompt hash при изменении anchor | Уточнить root guard или явное исключение до P2. Nested proposed strong исключён; рабочий root confirmed |
| R-L3 / низкий | [schema_validation:55](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema_validation.py:55): portable Draft принимает relevance2.0, strict runtime отклоняет | Стандартная семантика JSON Schema, не invalid emitted schema. Уточнить границу «паритета» и добавить явную regression без изменения шкалы |
| R-L4 / низкий | [gold_workbook:45](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_workbook.py:45), [gold_v3:242](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/gold_v3.py:242); ordinary migrate-v3 принимает lexical dangling output, сохраняет ссылку и создаёт target | Resolve до occupancy. Existing regular/input bytes не заменяются. Согласовать lexical occupied policy с immutable publisher |
| R-L5 / низкий | [schema:394](/Users/admin/Documents/HR-Automation/src/job_intake/labeling/schema.py:394) → [gold_ui:344](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/gold_ui.py:344): unknown tag key дословно попадает в warnings | Synthetic machine-marker из имени ключа достигает blind diagnostics; value скрыт, save запрещён. Санитизировать unknown names |
| F-L7 / низкий | [design:1434](/Users/admin/Documents/HR-Automation/docs/vacancy-labeling-architecture.md:1434) говорит «Настоящий DOM»; [harness:38](/Users/admin/Documents/HR-Automation/tests/test_gold_web_ui.py:38) имеет no-op addEventListener | Доказаны actual handler/no preventDefault, не native details expansion. Уточнить историческую формулировку; отдельный browser-тест не заявлять выполненным |
| A7-L1 / низкий | [evaluation_cli:186](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/evaluation_cli.py:186) проверяет только exists, :189–197 пишет journal до lexical refusal [frozen_eval:1016](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:1016) | Regular occupied: exit2/events0/independence True→True; dangling JSON/MD: exit2/events1/True→False, отчёта нет. Проверять оба имени до journal; перезаписи/acceptance bypass нет, rollback произвольного IO failure не требуется |

Evidence: `metrics-independent/results.json`, `atomic-independent-vp8l_b76/results.json`, `atomic-exposure-o9zd2cd0/results.json`, `ui-independent-results.json`. Все exposure-пробы использовали только synthetic journals в TMP.

## 5. Признанные отклонения и закрытие прежних находок

| Исходное отклонение | Вердикт по текущему состоянию |
| --- | --- |
| company= у rules_relevance | Обоснован approved strong-company правилом; §6.5 отражает аргумент |
| required_domain/basis в model response | Обоснованы семантикой факта; §4.5 обновлён. Удаление provider-пустых деталей — будущий S3 |
| duty_unit_ids/title_unit_ids у response_schema | Обоснованы section constraints; fallback без duty-ID их не доказывает, P2 обязан передать ID |
| label_scope сохранён в v3 | Provenance v2, явно разрешён §4.3; перенос 1:1 подтверждён |
| Tiger Analytics=1 через industry=pharma_biotech | Подмена employer industry больше не объявляется доказательством. Synthetic consultancy+required pharma без focus даёт0, с focus1; историческое human1 сохранено, полного подтверждённого tag-набора нет |
| domain_fit только requirement/industry | Устарело: required_domain включён в качество и κ по применимой human population |
| MAE/QWK/Spearman только по covered pairs | Обосновано при явном N/coverage; missing остаётся в recall/exact/±1/P@k, §6.7 |
| build_bundle/load_bundle только v2 | Поддержанный build остаётся v2. Load проверяет digest/policy/source, не доказывает labels-schema; ручной v3 bundle в тесте не заменяет P2 freeze |
| LabelResult/TagValue отложены | Явно P2 в §3/§12, не отсутствие обещанной части P0 |
| assisted в когорте = curated | Provenance-адаптация v2→v3 в select_cases; canonical cohort проверена без exposure |
| §3/§12 proposed против §5.2 confirmed | Исправлено: confirmed решения, только strong_companies proposed/empty; YAML exact |
| Раньше saved report заменял default output | Новый exclusive контракт явно описан §0.4/§7/§20 и README; требуются новые пути, прежняя serialization сохранена |

**Свежо подтверждённое закрытие:**

- **A7-M1/R-L6:** при вводе после начала detail/page GET сохраняются editor, notes/relevance/tags и dirty; stale transition отменяется, retry использует актуальный discard. Новый выбор, reset pending initial open, recursive refresh, second save409, competing moves и clock-only изменения проверены независимо. Новые A8 находки описаны отдельно, loss draft не воспроизведён.
- **A6-C1/F-C1:** ordinary CLI saved/rules/labels и gold validate отказали при выводе поверх valid frozen-reference, exit2, bytes неизменны, validate_run снова pass. Occupied second output, aliases, partial IO, retries, races, exact JSON/MD/object pass. Пара остаётся пофайловой: MD failure может оставить полный JSON, как документировано; power-loss durability/общая транзакция не обещаются.
- **A6-M1:** настоящий registry+loader — 35 invalid rejected, 14 valid accepted, supplied membership дополнительно; общий fullmatch работает без taxonomy.
- **R-H1/F-H1:** mixed known/null human-role sample даёт current recall0/1, explicit legacy1/2. Новый synthetic DEFAULT_POLICY160+1 при missing actionable/NDCG/repeat даёт insufficient_evidence; заполненные новые run — pass, исходные неизменны. R-M1–R-M4 не открылись по store/Node проверкам; HTTP-подтверждение здесь ограничено.

## 6. Покрытие тестами

Коллекция — **3499 случаев**, включая **819 tests/test_labels_***, ещё 31 atomic и 30 shared-output. Full run: 3474 pass и 25 bind-блокировок. Тесты при аудите не менялись.

| §11 / текущие файлы | Случаи | Доказательство / пробел |
| --- | ---: | --- |
| labels_schema / prediction_validation | 109 / 71 | Enum, snapshots, closed/finite/partial/date/source/role; integer2.0 граница — R-L3 |
| labels_rubric / aggregate | 95 / 44 | Hash/proposed/registry/domain/grammar, формула; root-proposed и punctuation остаются |
| labels_metrics | 26 | Missing/queue/partial/domain/versions; дополнительно 400 independent QWK и 400 Spearman |
| frozen_compatibility / role_recall / coverage | 21 / 22 / 70 | Replay/markers, malformed v3/custom variants, own populations, DEFAULT_POLICY/missing/repeat; не production v3 freeze |
| labels_gold_v3 / labels_cli | 146 / 42 | 1:1/geography/captured SHA/staging/rollback; read-only/input/taxonomy/finite checks; R-L4 отдельно |
| labels_accuracy_outputs / eval_report_outputs / cohorts_atomic | 35 / 30 / 31 | All variants/direct writers, immutable reference, aliases/dangling/races/exact bytes/failure; A7-L1 не покрыт |
| labels_gold_ui / labels_gold_web_ui / labels_gold_navigation | 41 / 50 / 31 | Полный draft, актуальный retry, stale responses/errors, save/409, staged page/detail; основной pager pre-mutation A8-M1 и close-after-PATCH banner A8-L1 отсутствуют |
| labels_gold_web_server | 16 | Код покрывает настоящий GET/PATCH; все 16 ограничены bind в этом аудите |
| labels_prompt/ai_schema/validate/gates/tagger/pipeline | Отсутствуют | Будущие P2–P3 по §12, не дефект обещанной P0/P1 |
| test_prefilter*.py | 160 | Legacy regression pass; scope.py ещё не перенесён |

Целевые ветви: 458 metrics, 475 atomic/CLI/frozen, 173 non-HTTP store/Node UI passed. Это пересекающиеся подмножества. Новый navigation suite проверяет настоящие функции, но не связывает main pager event с предварительным изменением глобального offset. Node Element harness не заменяет browser DOM/events и не подтверждает native `<details>`. Fake API/clients не доказывают будущий model adapter.

## 7. Готовность к следующим фазам

- **P0:** schema/rubric/metrics/baseline/migration/CLI hooks подтверждены; открытых high/medium P0 нет. Lows и запрет HTTP не скрыты; исторический green run не подменяет текущую проверку.
- **P1:** A7-M1 закрыта. Read-only модель открывает v3: 171 строка, 44 complete, 11 partial, 116 unlabeled, шесть тегов, без machine fields. Общие17 relevance≥2 включают partial curated; validate complete relevant=7. Для безоговорочной приёмки последовательной навигации нужна A8-M1; A8-L1 — отдельный low статуса. Native browser/HTTP здесь не проверены.
- **До измерений P2:** нужен поддержанный schema-aware v3 build/save/load и acceptance. [frozen_eval:738](/Users/admin/Documents/HR-Automation/src/job_intake/annotation/frozen_eval.py:738) по-прежнему считает full-tags как19; описательный relevance block не превращает старые gates в v3 quality-протокол.
- **Данные/provenance:** рабочих `data/local/evaluation/cohorts.jsonl` и `data/local/labeling/precedents.jsonl` нет. API/loader guards проверены, но это не рабочий registry, approved reasons или новый независимый holdout. Реальных human v3-тегов нет; U8 о доразметке до150+ решений/30+ релевантных и repeat через≥2 недели не объявляется выполненным.
- **Stack P2:** отсутствуют scope.py, LabelingConfig, gates/prompt/S3/tagger/ledger/budget и CLI label export. `jsonschema` уже объявлен ([pyproject:20](/Users/admin/Documents/HR-Automation/pyproject.toml:20)); прежнее «нет зависимости» устарело. UI уже выбирает v3 по умолчанию, явный v2 поддержан — прежнее «разметка продолжается только в v2» тоже устарело.
- **Риски §14:** реальный provider schema/order, source trust/S3, retry ledger/budget, отсутствие notes в prompt, disabled/shadow behavior и stream rebuild требуют P2–P3 реализации/тестов. Strong list остаётся proposed/empty; платный пилот и переключение tier_source требуют команды пользователя и этим аудитом не запускались.

## 8. Предлагаемые исправления design doc

Документ не изменялся; ниже только предложения.

| Раздел | Было → предлагается |
| --- | --- |
| §10/§21 | Guard после await без правила main pager offset → requested offset отдельно, committed offset меняется только вместе с принятым page; regression A8-M1 через handler |
| §21/§11 | Loading освобождается владельцем sequence → отдельно гарантировать отсутствие завершённого own loading-banner после close, не стирая новый статус; A8-L1 regression |
| §18 R-M3 | «Настоящий DOM» → «Node Element harness; handler не предотвращает default; native expansion этим тестом не проверено» |
| §4.5/§11 | «Паритет» без числовой границы → portable integer допускает2.0, strict runtime требует int; явно описать и тестировать различие |
| §5.2 | Любой proposed не применяется, но root anchors доступны → определить root proposed контракт до P2, не менять текущий confirmed YAML |
| §6.5 | Без пунктуации и юрформ → зафиксировать эквивалентность скобок/дефисов на R-L1 примерах |
| §0.4/§6.9 | Occupied с неявной lexical семантикой → согласовать migration symlink policy с immutable publisher |
| Frozen compare preflight | Проверка до consuming holdout пропускает dangling → оба lexical имени до journal; не обещать rollback любого IO failure |
| §12/§21 | История закрытия A7 остаётся верной → сохранить её и отдельно отразить новую A8-M1 и фактическую границу P1 приёмки |

## 9. Приложение: команды и ключевые выводы

Префикс проверок (TMP содержит запретительный `sitecustomize.py`):

```sh
env -i HOME=/Users/admin PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR=/private/tmp LC_ALL=C \
  PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
  PYTHONPATH=/private/tmp/labels-v3-audit8-fIM3LL
```

| Проверка | Команда / evidence | Ключевой вывод |
| --- | --- | --- |
| К1 | `.venv/bin/python -m pytest -q --tb=short -p no:cacheprovider --basetemp=TMP/full-tests`; `pytest-full.log` | 3474 pass, 3 failed+22 errors только bind, 45,21с; Ruff/diff pass |
| К2 | `gold migrate-v3 --source data/local/gold/vacancy-tags-v2-labeled.jsonl --out TMP/migrated.jsonl`; `gold validate --gold TMP/migrated.jsonl --output TMP/validation.json` | Exit0,171 строка, decisions/source/hashes/dates/label_scope/notes1:1, assisted→curated, first=labeled; manifest SHA совпали; JSONL равен рабочему v3, valid=True |
| К3 | `eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output TMP/rules.json`; прямой canonical cohort `run_relevance_accuracy` | API0/read_only; N54,QWK0,373134,recall12/17,precision12/24,P@10=0,8; рабочий Markdown0,373. Blind44:recall7/7,precision7/19,QWK0,433663 |
| К4 | `eval accuracy --variant saved --gold data/local/gold/vacancy-tags-v2-labeled.jsonl --output TMP/saved.json`; `integration.py` | Прежние16 top-level keys и nested evaluation shape сохранены; evaluation/operational/stability/readiness/selection keys прежние. Три saved frozen-run validate_run pass; P1 readonly model подтверждён |
| К5 | `integration.py`, `metrics-extra-results.json` | YAML exact2913 bytes,14 INDUSTRIES, schema snapshots=функции/Draft valid; version labels-rubric-2026-10-20; prompt hash8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8; aggregation hash9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f |
| К6 | `root-ui-probes.py`, `root-pagination-probe.py`, `ui-independent.py`, `atomic-independent.py`, `atomic-extra.py`, `atomic-exposure-preflight.py`, metrics probes | Fresh closure A7/A6/F/R и новые A8 findings; ordinary CLI без mocks для reference/exposure, намеренные IO/race faults отдельно; только TMP |
| К7 | `git diff --exit-code 8bb3a2a -- config/search_profiles.yaml config/rules.yaml config/settings.yaml src/job_intake/pipeline.py src/job_intake/annotation/prefilter.py src/job_intake/scoring src/job_intake/storage src/job_intake/annotation/ai.py`; `snapshot.py` | Exit0;8263 исходных файла неизменны. Snapshot включает рабочую SQLite/gold, backup/eval/exposure, code/config/tests/docs и7 прошлых аудитов; исключает секреты/.git/.venv/cache/.obsidian |
| К8 | `collect.py`, `collection-counts.json`, targeted logs | 3499 collected,819 labels+31 atomic+30 shared-output; branch458/475/173 не суммируются |

Рабочая SQLite SHA-256: `35fb18d4a28e7222cd6bc9de178df8fc992f4a4a02b89897f62b7432098ac075`;
v3 gold: `936f6259a2343d7ff882f447539a9b137b67eea4eb2580edd8c91bbb3163309a`.
Начальный/конечный снимки: `before-sha256.json`, `after-sha256.json`, `snapshot-comparison.json`.
Исходный/current diff, CLI-отчёты, synthetic evidence и логи сохранены в TMP.
Ни существующие файлы, ни исторические аудиты не перезаписывались; единственное новое добавление в репозиторий — этот отчёт.
