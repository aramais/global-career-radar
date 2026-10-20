# Двенадцатый аудит labels-v3: P0–P3 после исправлений — 20.10.2026

## 1. Итог

Исправлены критическая A11-C1 и высокие A11-H1–H3 из
[аудита №11](audit-labels-v3-implementation-2026-10-20-11.md). После исправлений
в проверенном объёме подтверждены **0 критических, 0 высоких, 3 средние и 2 низкие
находки**. P0 сохраняет схему, миграцию и baseline; проверенные ошибочные решения
найма и потеря принятой оценки при изменении collection observations устранены.
Полную приёмку P0–P3 и рабочий rollout пока подтверждать нельзя: остаются учёт
terminal ledger, regular shadow retention и ownership cleanup manual Gold.
P4 вне объёма. Критерии и рабочие данные не менялись.

Это проверка текущего дерева после исправлений, с отдельными adversarial probes,
а не только повтор старых тестов. Новые соседние случаи, обнаруженные при проверке
исправлений, также включены в регрессии. Отсутствие подтверждённых C/H в этой выборке
не доказывает полноту распознавания естественного языка или качество live модели.

## 2. Объём и метод

- Ветка `feature/vacancy-tags`, HEAD `e61d483d4ac06157725cde8d81ea9a0f6bbada36`.
  P2–P3 и последующие исправления находятся в незакоммиченном дереве; исходная база
  `8bb3a2a`, P0-коммиты `624bd17`/`d63d5db`. Ветка и HEAD не менялись.
- Основание — исходное задание аудита из приложения, AGENTS, design doc целиком,
  обоснование §3–§5, README/architecture, labels-модули и хуки accuracy/frozen/CLI,
  pipeline/storage/CRM, тесты и №11. Объём расширен до уже порученных P0–P3;
  прежний критерий «pipeline не менялся» относится только к первоначальной P0-базе.
  Карьерная база не загружалась, пользовательские решения §13 не пересматривались.
- Фаза исправления меняет только перечисленные ниже исходники/тесты и актуальный
  design/architecture. После фиксации Python/HTML-дерева фаза аудита читает код,
  выполняет проверки в TMP и создаёт этот новый отчёт. Старые аудиты неизменны.
- Все Python-проверки выполнялись с чистым env, запретом dotenv/bytecode/cache,
  чтения `.env*` и записи репозитория; SQLite рабочих данных — `mode=ro`, readers
  дополнительно задают `PRAGMA query_only=ON`. Синтетические ответы, БД, журналы,
  source adapters и I/O-сбои находятся только в TMP. Платных и внешних сетевых
  вызовов нет. Проверка исправлений разрешает только loopback для HTTP-тестов;
  отдельный аудит использует исходный полный запрет socket/DNS/bind.
- Обязательные CLI направлены в TMP. Каноническая когорта проверена через Python
  без exposure; рабочие run/annotate/reevaluate/CRM и миграции БД не запускались.
- Защищены SHA-256 **7443 файлов**: все config/data и аудиты №1–№11.
  До/после: `changed=[]`, `added_data_config=[]`. Это включает рабочую SQLite,
  gold JSONL, frozen/cohort/exposure journals. Полный исходный manifest содержит
  8294 файла; изменения сверены с разрешённым объёмом. Служебные кеши, `.git`,
  `.venv`, `.obsidian`, node_modules и `.env*` исключены; symlink фиксируются
  без чтения target.
- Материалы: `/private/tmp/labels-v3-a12-fixes-EfzeSr` (далее **TMP**).
  Независимая проверка выполнялась другим агентом той же команды, не внешней
  аудиторской организацией. Fake providers не измеряют частоту ошибок live LLM;
  Node harness и loopback HTTP не доказывают поведение native browser DOM.

## 3. Трассировка требований

| Требование | Статус | Проверенное доказательство / граница |
| --- | --- | --- |
| §0.1 Критерии/ID, proposed | Реализовано | Config SHA прежние; §5.2 YAML2913bytes exact, confirmed hashes прежние; proposed guards проходят |
| §0.2 Явные платные вызовы/бюджет | Частично | CLI/BudgetGuard и in-memory расход проверены; terminal ledger loss A12-M1 остаётся |
| §0.3 Секреты/диагностика | Реализовано в проверенных ветках | Чистый env/secret-read guard; provider/error tests; ключи и настоящие ответы не читались |
| §0.4 Неизменяемые файлы/cleanup | Частично | Exclusive outputs проходят; A12-M3/L1 в других cleanup-ветках остаются |
| §0.5 Eval SQLite только чтение | Реализовано в проверенном объёме | Public API+CLI защищают DB/-wal/-shm/-journal до чтения; direct API regression и native RO SELECT |
| §0.6 Аддитивные миграции | Реализовано | Schema/storage suites на temp DB; рабочая схема не мигрировалась |
| §0.7 Fallback сохраняет принятую оценку | Частично | Exact-source и collection-only labels retention проходят; regular shadow A12-M2 остаётся |
| §0.8 Недоверенный источник | Реализовано в проверенных случаях | Prompt policy, typed source proof, полный geography set/противоречия/языковые veto; неизвестное → abstention |
| §0.9 Legacy default | Реализовано для legacy, частично для shadow | Legacy suites проходят; rollout disabled; A12-M2 затрагивает v3-поля shadow |
| §0.10 Ошибки/tests/progress | Частично | Ruff/diff-check проходят; ledger/cleanup остаются открытыми |
| §4 Enum/14 industries/nullable/ID/provenance | Реализовано | Schema snapshots/runtime/partial/date tests, миграция171 |
| §4.2 Шесть тегов/evidence | Реализовано в проверенном объёме | Source ID/section/value/relation regression; клиент/команда/overlap не доказывают страну; язык проверяется по всему source |
| §4.3 Gold/source binding | Частично | Human поля1:1, blind read model и labels rebind проверены; manual cleanup A12-M3 |
| §4.4–4.5 JSON Schema/response | Реализовано с явной границей | Строгие finite/runtime int; portable JSON Schema допускает integer2.0 по значению, runtime отказывает float/bool |
| §5.1 LabelingConfig/flags | Реализовано | Disabled/shadow/score/default tests; существующие значения config сохранены |
| §5.2 YAML/хеши/proposed | Реализовано | Точный YAML, независимые rubric/prompt/aggregation hashes; proposed не применяется |
| §5.3 Прецеденты/holdout | Реализовано в guards | Registered loader/exposure/balance tests; human прецеденты/holdout acceptance не создавались |
| §6.1 Бесплатные маршруты | Реализовано в проверенных случаях | Closed/incomplete/too-long/title tests; hiring не отменяет relevance |
| §6.2–6.4 Providers/S3 | Реализовано в проверенном объёме | Provider schemas, untrusted prompt, original+adversarial proof probes; неизвестная грамматика консервативно abstains |
| §6.5 Aggregation/gate/P2 | Реализовано в проверенном объёме | Pure formula/policies/P2 tests; текущая техническая версия labels-aggregation-v3, критерии прежние |
| §6.6 Cache/ретраи/budget/ledger | Частично | Cache проходит текущую S3, full/facts reuse; terminal persistence A12-M1 |
| §6.7 Метрики/stability | Реализовано в проверенном объёме | Targeted suites + независимые2000 QWK/Spearman и1000 partial-denominator sets, расхождений0 |
| §6.8 Rules baseline | Реализовано | N54/QWK0.373134/recall12/17/precision12/24/P@10=.8; rules:tier-map-v1 A→3/B→2/C→1/D→0 |
| §6.9 Миграция/manifest | Реализовано в проверенных ветках | v4 migration171/valid=True, source/decisions/provenance1:1, bytes равны рабочемуv3, SHA корректны |
| §7 CLI/public accuracy API | Реализовано в проверенном объёме | Четыре CLI exit0, direct API preflight всех inputs/двух outputs до анализа |
| §8 Labels/shadow/purpose/alerts | Частично | Labels collection-only retention и optimistic guard проходят; regular shadow A12-M2 |
| §9 Source revision/хранение/версии | Реализовано в проверенных labels-ветках | Rebind atomic с provenance, model input/facts equality, optimistic revision; старые policy versions не повышаются |
| §10 Gold UI/CRM | Частично | Blind/partial/offset/loading/HTTP regression проходят; A12-M2/M3 остаются; native DOM вне проверки |
| §11 Регрессии | Частично | Новые C/H regressions и полный прогон; непокрытые production error paths перечислены отдельно |
| §12 P0–P3/P4 | Частично / P4 вне объёма | Код P0–P3 есть; остаются3M/2L и условия пилота/holdout |

## 4. Находки и состояние исправлений

### Закрытые критическая и высокие №11

| ID | Проверено после исправления | Реализация / регрессии |
| --- | --- | --- |
| A11-C1 | Public run_accuracy отказывает DB sidecars и aliases до анализа; ни JSON/Markdown, ни journal не создаются; native RO SELECT работает, вызовов модели0 | `annotation/accuracy.py:1101`; `tests/test_labels_accuracy_api_inputs.py`,34cases; `TMP/review/api-sidecars/result.json` |
| A11-H1 | Worldwide+US overlap, US clients, authorization команды и North+South America не доказывают исключение Brazil. Полный список/authorization относится к кандидату; противоречие uncited structured applicant requirements отменяет hard fact | `labeling/proofs.py:308,325`, `validate.py:164`; `tests/test_labels_audit12_proofs.py`; `TMP/review/verified-matrix/results.json`, `verified-structured/results.json` |
| A11-H2 | Implied English не перевешивает Portuguese-only/другой required язык, English denied, domestic-only company или uncited рабочий язык. International customers не равны international company. Optional language, SQL/Python и клиентская география имеют positive controls | `labeling/proofs.py:398,416,490,528,560`; те же proof regressions; `TMP/review/verified-{extra,lists,dual,need}/results.json` |
| A11-H3 | Fetched_at и прочие observation-only изменения, включая неполную повторную listing-карточку с сохранённым полным canonical source, сохраняют A/3/labeled. Обновляются pins/revision/observations, failed attempt и provenance; purpose/версии/archive остаются. CAS сохраняет более новую concurrent оценку | `pipeline.py:539`, `storage/repository.py:347,420`, `source_state.py:89`; `tests/test_labels_a12_retention.py`,26cases; `TMP/review/final-retention-listing/results.json` |

Отказ неизвестной грамматики — явно консервативное ограничение parser, а не
доказательство hard exclusion. Тесты проверяют конкретные конструкции, отношения
и противоположные controls; все возможные перефразировки естественного языка
не проверены. Кеш проходит текущую S3, техническая aggregation-v3 инвалидирует
прежние принятые решения; model fact keys и пользовательские rubric hashes прежние.

### Средние — подтверждены заново, остаются открытыми

| ID (прежний) | Проверенная суть и последствие | Доказательство / рекомендация |
| --- | --- | --- |
| A12-M1 (A11-M1) | Terminal write при восстановимом I/O теряет pending ownership до durable persist. После1 fake transport call и spent0.00015 доступный close оставляет ledger0rows; metrics complete=True/cost0/attempts0. In-memory budget не обходится, но отчёт теряет расход | `labeling/ledger.py:58`, `tagger.py:147`; `TMP/review/final-remaining-ledger/results.json`. Сохранять terminal/pending до durable success, восстанавливать учёт или fail closed; учитывать partial write/duplicates |
| A12-M2 (A11-M2) | Regular shadow pipeline при same-source offline cache miss заменяет accepted labels3/labeled→1/rules_only в job/profile/CRM, attempt отсутствует. Legacy A/PASS, purpose/alerts прежние; dedicated shadow CLI уже сохраняет accepted | `pipeline.py:360,512`; `TMP/review/final-remaining-shadow/results.json`. Сохранять accepted v3 отдельно от законной legacy reevaluation, не early-return весь shadow assessment |
| A12-M3 (A11-M3) | GoldLabelStore при replace failure и чужой подмене staging удаляет foreign file. Gold/DB прежние; foreign_staging_survives=False | `annotation/gold_ui.py:451`; `TMP/review/final-remaining-io/cleanup-results.json`. Cleanup только inode/dev собственной staging identity; unknown ownership сохранять |

### Низкие — подтверждены заново, остаются открытыми

| ID (прежний) | Проверенная суть | Рекомендация |
| --- | --- | --- |
| A12-L1 (A11-L1) | Shared publisher link EIO + staging.lstat PermissionError выбрасывает cleanup error вместо primary. Final отсутствует, staging остаётся, primary доступен только в exception context. `annotation/cohorts.py:88`, `TMP/review/final-remaining-io/primary-error-results.json` | Сохранять primary exception, secondary добавить note; неизвестную ownership не удалять |
| A12-L2 (A11-L2) | Design header говорит об открытых №8–9, §0.4/§7 требуют ручной output при повторном default, хотя §23/code выбирают auto unique. Обещание enabled taxonomy шире optional configs на freeze; historical replay не доказывает текущую membership | Согласовать шапку/§0/§7 с §23; явно описать границу freeze configs и structural historical replay. Это документационный разрыв |

Все пять находок воспроизведены на временных файлах/БД; они не являются
предположениями о рабочем запуске. Средние и низкие не исправлялись в этой задаче.

## 5. Признанные отклонения исходного задания

| Отклонение | Вердикт |
| --- | --- |
| company= у rules_relevance | Обосновано confirmed strong-company списком; сейчас proposed/empty, не применяется |
| required_domain/basis | Обосновано разделением работодателя/экспертизы и explicit/implied; отражено в design |
| duty_unit_ids/title_unit_ids | Обосновано source scope; синтаксической схемы недостаточно, отдельно проверяется S3/value proof |
| label_scope вv3 | Обосновано сохранением v2 provenance; migration1:1 подтверждена |
| Tiger Analytics=1 через pharma | Employer industry=consulting, preferred_focus отдельно; синтетическая формула1 требует pharma domain. Human оценка не изменялась и неполными тегами не доказывается |
| Domain metrics requirement/industry | Устаревшее описание: required_domain имеет отдельную human population, regression/random checks проходят |
| MAE/QWK/Spearman только covered pairs | Обосновано явными N/coverage; missing остаётся в exact/±1/recall/P@k |
| Frozen build/load толькоv2 | Устарело: schema-aware v2/v3 реализованы; enabled membership условна — A12-L2 |
| LabelResult/TagValue отложены | Устарело: реализованы P2 |
| Assisted=curated в когорте | Обосновано v2→v3 provenance adapter; fresh read-only cohort pass |
| Proposed против confirmed | Текущий exact YAML и root/nested guards проходят; strong_companies намеренно proposed |

Pipeline/prefilter/scoring отличаются от P0-базы по поручению реализовать P2–P3.
Это ожидаемое расширение, проверенное legacy suites, а не самостоятельный дефект.
Новые технические proof/retention guards применяют прежние критерии пользователя.

## 6. Покрытие тестами

Добавлено **238 регрессионных случаев** (API34 + proof178 + retention26).
Полный итоговый прогон исправления с loopback: **4107 passed за61.19с**.
Строгий read-only аудит: **4082 passed,3failed,22errors за53.46с**;
все25 относятся к HTTP и остановлены `Audit guard: all network and bind disabled`.
Это ограничение исходного запрета bind, а не25 дефектов продукта. На разрешённом
loopback они проходят в полном наборе. Ruff и `git diff --check` — pass.
Snapshot **245 Python/HTML файлов** совпадает до/после, включая независимый review.

Первый полный запуск был остановлен после новой доказанной ошибки H2 до freeze.
Ошибочное повторное использование того же basetemp затем дало2440passed/1667errors:
guard отверг относительный dir_fd cleanup старого TMP как возможную mutation
репозитория. Это ошибка обёртки проверки; итоговый прогон выполнен в новом пустом
`full-fix-final`, защита не ослаблялась. Промежуточные результаты не выдаются за
итоговую приёмку; логи сохранены в TMP.

| Проверка | Свежий результат |
| --- | --- |
| Public accuracy API/CLI/outputs | 111passed/2.34с, включая34 новых direct API cases; исходный API defect давал31failed/3passed до исправления |
| P0/P1 metrics/gold/frozen/navigation/atomic | Независимый strict прогон616passed/6.12с |
| Proof/gate/cache/aggregation/provider | 492passed;178новыхcases; независимые80nativeсценариев без ошибок |
| Collection-only retention/storage/pipeline | 142passed/4.22с;26новыхcases, включая revision и same-source assessment CAS |
| Независимые численные проверки | Seed120201026:2000 QWK/Spearman +1000 partial/missing denominator sets, расхождений0 |
| Обязательные CLI/исторические данные | Четыре CLI exit0; migration171/valid, baseline и3frozen replay прежние; saved16top-level+вложенные shapes прежние |
| Отдельные adversarial probes | Original C/H + структурированные противоречия/полные списки/typed language/repeated listing/concurrent save; все оставшиеся M/L воспроизведены |

Группы пересекаются и не суммируются. Наличие полного зелёного набора не закрывает
пять перечисленных непокрытых error paths. Тесты не меняют рабочую разметку и не
доказывают принятие live pilot или holdout.

## 7. Готовность к следующим фазам

- P0: схема, миграция, метрики и public read-only output boundary подтверждены в
  проверенном объёме; правила пользователя сохранены.
- P1: source-only/partial/async/HTTP регрессии проходят; manual Gold cleanup
  A12-M3 требует исправления, native DOM отдельно не проверялся.
- P2: проверенные false hiring/language decisions закрыты; terminal ledger A12-M1
  остаётся препятствием для надёжного платного пилота/анализа стоимости.
- P3: labels-mode collection-only retention исправлен; regular shadow A12-M2
  остаётся препятствием для полной приёмки сохранности v3-результатов.
- Pilot/rollout не выполнялись. Нужны подтверждённые прецеденты, допустимые
  зарегистрированные когорты и human coverage,3повтора/выбор варианта,
  независимый holdout и гейты §12. Модели/бюджет — по существующим U6/U7;
  strong companies не утверждаются аудитором. P4 не реализовывалась.

## 8. Design doc: внесённые и оставшиеся уточнения

| Раздел | Было → стало / требуется |
| --- | --- |
| §0.5, §24; architecture | API guard сам DB → public API+CLI preflight DB/sidecars/declared inputs/двух outputs до анализа и повторно перед публикацией |
| §6.5, §24 | labels-aggregation-v2 → v3; критерии/fact keys/rubric hashes прежние |
| §24 proof | Географическое соседство → candidate-scoped полный набор стран/регионов, all-source contradictions; explicit language выше implied, company international отдельно от customers |
| §24 retention | Только raw-source equality → full meaningful snapshot/model input, whitelist observations, trusted reconciler markers с hash binding, provenance/CAS без upgrade принятой policy |
| Header, §0.4/§7, §6.7/§22 | Требуется ещё исправить A12-L2: актуальный статус, auto unique implicit output, условность enabled taxonomy |
| §6.6/§0.4/§10 | После runtime исправлений M1/M3/L1 уточнить durable terminal ownership и inode/primary-error cleanup contract |
| §8–§9/§22 | После исправления M2 описать независимость accepted v3 retention от legacy shadow reevaluation |

Раздел §24 фиксирует только закрытые C/H и фактическую проверку. Исторические
аудиты/числа не переписываются. Изменение численных критериев/весов/YAML не требуется.

## 9. Приложение: команды и ключевые выводы

```text
env -i ... PYTHONPATH=TMP/fix-guard .venv/bin/pytest -q -p no:cacheprovider --basetemp=TMP/full-fix-final
env -i ... PYTHONPATH=TMP .venv/bin/pytest -q -p no:cacheprovider --basetemp=TMP/full-audit
.venv/bin/ruff check src tests --no-cache
git diff --check
gold migrate-v3 --source data/local/gold/vacancy-tags-v2-labeled.jsonl --out TMP/integration/migrated.jsonl
gold validate --gold TMP/integration/migrated.jsonl --output TMP/integration/validation.json
eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output TMP/integration/rules.json
eval accuracy --variant saved --gold data/local/gold/vacancy-tags-v2-labeled.jsonl --output TMP/integration/saved.json
python TMP/integration-check.py
python TMP/source-snapshot.py before|after
python TMP/verify-protected.py
python TMP/changed-files.py
```

Полные окружения/команды и результаты сохранены в TMP; `integration/commands.json`
фиксирует exit0 всех четырёх CLI. Миграция171:55 с любой human label,44complete,
10relevance-only; blind44/curated11/assisted_llm0, human tags0. Source/hash/text/date/
decisions/label_scope/notes1:1 без цитирования notes; first_labeled_at сохранён,
v4 manifest. Выход JSONL побайтно совпал с рабочимv3.

Rules baseline: N54,QWK0.373134,recall12/17,precision12/24,P@10=.8.
Read-only canonical cohort через Python:163selected, blindN44/recall7/7/
precision7/19/QWK0.433663; exposure не записывался. Три старых frozen run валидны.
P1 read model:171total/44complete/11partial/116unanswered,17relevant/10strong,
6tags; machine поля отсутствуют в blind detail.

YAML2913bytes exact; version `labels-rubric-2026-10-20`;
prompt hash `8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8`,
aggregation rubric hash `9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f`.
Техническая aggregation version — отдельная версия кода, не изменение этих hashes.

`protected-before.json`/`protected-after.json`:7443файла,changed=[],added_data_config=[].
`source-before-full.json`/`source-after-full.json`:245файлов,changed=[],added=[].
`review/reviewed-source-sha256.json` фиксирует независимо проверенные source/test.
Полный manifest8294→8298:11разрешённых существующих файлов изменены
(8production source,1тест версии,2актуальных документа), добавлены3регрессионных
тестовых файла и этот новый отчёт. Остальные файлы прежние. Ветка/HEAD, исходные
пользовательские изменения, все config/data и прежние аудиты сохранены; коммитов,
push, переключения ветки, чтения ключей и live API не было.
