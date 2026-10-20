# Одиннадцатый аудит labels-v3: P0–P3 после исправлений — 20.10.2026

## 1. Итог

Подтверждены **1 критическая, 3 высокие, 3 средние и 2 низкие находки**.
P0 сохраняет прежние метрики, схему и миграцию, но публичный Python API оценки
нарушает границу read-only; P1 имеет незакрытую ветку cleanup, P2 — ложные решения
найма, P3 — потерю принятых оценок при некоторых fallback. Поэтому полную приёмку
P0–P3 и переключение рабочих категорий подтверждать нельзя. P1/P2 уже реализованы:
вопрос теперь в устранении этих дефектов и выполнении условий пилота, а не в начале
фаз. P4 не входит в аудит. Код, критерии, рабочие данные и прежние отчёты не менялись;
создан только этот новый отчёт.

Это новый аудит текущего дерева, а не пересказ №10. Его исправленные конкретные
сценарии проходят регрессии; новые probes проверяют соседние API, scope и error-path.
Слова «все находки закрыты» в прежнем отчёте не означают полноту покрытия всех веток.

## 2. Объём и метод

- Ветка `feature/vacancy-tags`, HEAD `e61d483d4ac06157725cde8d81ea9a0f6bbada36`.
  P2–P3 и исправления №10 находятся в незакоммиченном дереве. База исходного
  проекта `8bb3a2a`, первоначальные P0-коммиты `624bd17`/`d63d5db`.
- Прочитаны AGENTS, исходное задание из приложения, design doc, обоснование §3–§5,
  README/architecture, весь labels-путь и его хуки в eval/frozen/CLI, pipeline,
  storage/purpose/CRM, тесты и отчёт №10. Полный исходный diff и текущие изменения
  сохранены для сопоставления; функциональная проверка относится к текущему дереву.
  Исходные критерии §13 не пересматривались; личная карьерная база не загружалась.
- До запусков сохранён SHA-256 **8293 файлов**: код, конфиги, данные и старые отчёты.
  `.git`, `.venv`, служебные кеши, `.obsidian`, node_modules и `.env*` исключены;
  symlink фиксируется по ссылке без чтения её target. После всех проверок, до
  создания отчёта: **8293→8293, changed=[], added=[]**. После публикации допускается
  только новый файл `docs/audit-labels-v3-implementation-2026-10-20-11.md`.
- Тесты, CLI и probes выполнялись с чистым env, запретом dotenv/bytecode/cache и
  строгим audit hook: запрещены все socket/connect/DNS/bind, чтение настоящего
  `.env*`, изменение файлов репозитория; рабочая SQLite — только `mode=ro`.
  Рабочие readers дополнительно используют `PRAGMA query_only=ON`. Все имитации
  I/O-сбоев, source/model answers и записи SQLite находятся в TMP.
- Не запускались рабочие run/annotate/reevaluate/CRM, платные API, браузер и команды
  с канонической когортой через CLI. Публичный pipeline.run в воспроизведении
  использует временную БД и подставной source adapter, без сети.
- Независимость ограничена: это новая проверка с отдельными probes и взаимным
  чтением выводов внутри той же команды моделей, а не внешний аудит третьей стороной.
  Fake clients проверяют реакцию кода на допустимые ответы, не частоту ошибок live LLM.
  Node harness не доказывает native browser DOM; HTTP в этой проверке заблокирован.
- Все логи, скрипты, исходные снимки и результаты:
  `/private/tmp/labels-v3-audit11-RpMr3J` (далее **TMP**).
  `.venv` доступна и ведёт в `/Users/admin/.venvs/hr-automation`.

## 3. Трассировка требований

| Требование | Статус | Доказательство / граница |
| --- | --- | --- |
| §0.1 Критерии/ID, proposed не применяется | Реализовано в проверенных ветках | §5.2 YAML2913bytes exact; root/nested proposed regression, current hashes прежние; config SHA неизменны |
| §0.2 Явные платные вызовы и бюджет | Реализовано с дефектом учёта | `labeling/cli.py`, BudgetGuard; модели фейковые, расход в памяти учитывается; terminal ledger loss — A11-M1 |
| §0.3 Ключи/ответы/промпты не в диагностике | Реализовано в проверенных ветках | Core/provider tests, безопасные errors/warnings; secrets не читались; cleanup diagnostic — A11-L1 |
| §0.4 Неизменяемые файлы/ownership cleanup | Частично | Exclusive new-output tests pass; manual Gold cleanup — A11-M3; primary error masking — A11-L1 |
| §0.5 Eval/export SQLite только чтение | Отклонение | CLI защищает DB/sidecars; public run_accuracy(output=...) создаёт journal — A11-C1 |
| §0.6 Аддитивные миграции | Реализовано | Temp schema/storage regression; рабочая схема не мигрировалась |
| §0.7 Ошибка/fallback не понижает принятую вакансию | Частично | Exact-source labels fallback tests pass; observation-only drift A→C — A11-H3, shadow labels loss — A11-M2 |
| §0.8 Источник недоверенный | Частично | POLICY явно запрещает инструкции источника; source-value proof всё ещё принимает ложные hiring/language факты — A11-H1/H2 |
| §0.9 Прежнее поведение по умолчанию | Реализовано для default legacy, частично для shadow | Rollout disabled/shadow/score; legacy regression pass; новые shadow machine fields теряются — A11-M2 |
| §0.10 Стиль/progress/errors/tests | Частично | Ruff/diff-check pass; ledger error-path и cleanup contracts требуют правок |
| §4 Enum/INDUSTRIES/nullable/provenance | Реализовано | Schema snapshots/runtime/date/partial tests; 14 industries, синтаксис stable ID |
| §4.2 Шесть тегов и источники доказательств | Частично | ID/секции/города проверяются, но scope и implied contradiction не защищены — A11-H1/H2 |
| §4.3 Gold v3/source binding | Реализовано в проверенных ветках | Migration171, human fields1:1, blind read model171/6tags; manual publication cleanup — A11-M3 |
| §4.4–4.5 Prediction/response schema | Реализовано с указанной границей | Strict finite/runtime int, structural schema/provider registration; portable integer2.0 явно документирован |
| §5.1 Config/flags/defaults | Реализовано | LabelingConfig tests, disabled/shadow/score; прежние значения config не изменены |
| §5.2 YAML/hashes/confirmed | Реализовано | Exact YAML, independent prompt/aggregation/rubric versions; proposed tests pass |
| §5.3 Прецеденты/holdout | Реализовано в проверенных guards | Registered loader/format/balance/exposure tests; пользовательские прецеденты и пилот не создавались |
| §6.1 Бесплатные маршруты | Реализовано в проверенных случаях | Closed/incomplete/too-long/title-out-of-family tests; hiring blockers не отменяют relevance |
| §6.2–6.4 Prompt/providers/S3 | Частично | Provider schemas и untrusted prompt есть; scope/contradiction gaps A11-H1/H2 |
| §6.5 Aggregation/gate/policies/P2 | Частично | Pure формулы/policies/P2 guards pass; ложный validated fact приводит к неверному gate |
| §6.6 Кеш/ретраи/бюджет/ledger | Частично | Current S3 revalidation/full/facts reuse pass; восстановившийся I/O теряет terminal attempt — A11-M1 |
| §6.7 Метрики/стабильность | Реализовано в проверенном объёме | 616 targeted tests, независимые 2000 QWK/Spearman и1000 partial-denominator sets без расхождений |
| §6.8 Rules baseline | Реализовано | Fresh N54/QWK0.373134/recall12/17/precision12/24/P@10=0.8, явный rules:tier-map-v1 |
| §6.9 Миграция/manifest | Реализовано в проверенных ветках | Новая v4 migration171, valid=True, bytes равны рабочему v3, SHA/1:1; lexical/ownership tests pass |
| §7 CLI и public API | Частично | Четыре обязательные CLI exit0; public output API не использует полный input guard — A11-C1 |
| §8 Labels/shadow/tiering/purpose/alerts | Частично | Confirmed gate→P1/queue и source caps pass; A11-H3/M2 |
| §9 Хранение/source revision/версии | Частично | Optimistic source и effective policy versions pass; retention при collection-only drift и shadow неполон |
| §10 Gold UI/CRM | Частично | Blind/partial/source/offset/loading regressions pass; Gold cleanup A11-M3, shadow CRM fields A11-M2; native HTTP не перепроверен |
| §11 Тесты | Частично | 3869 collected:3844passed/25blocked; новые probes выявили непокрытые ветки |
| §12 P0–P3/P4 | Частично / P4 вне объёма | Код фаз существует, но указанные дефекты мешают приёмке; paid pilot/holdout acceptance не выполнены |

## 4. Находки по серьёзности

### A11-C1 — критическая: public accuracy API может создать SQLite journal

**Проверено.** `src/job_intake/annotation/accuracy.py:1097–1101` защищает сам DB,
но не его sidecar names при передаче optional `output` в публичный `run_accuracy`.
Временный вызов `run_accuracy(settings, gold=v3, variant='rules',
output=Path(str(db)+'-journal'))` публикует отчёт **17351 байт** и Markdown.
Следующий `sqlite3(...mode=ro)` + query_only + SELECT получает `OperationalError`.
DB bytes не меняются, модельных вызовов0. WAL/SHM имена также принимаются
(17343 байта); на этом fixture они не вызвали ошибку последующего чтения.

Последствие — нарушение §0.5 и доступности БД, несмотря на неизменность SHA самого
DB файла. Условие — вызывающий использует публичный Python API с таким output;
CLI уже проверяет sidecars, его регрессия A10-C1 не возобновилась.
Рабочая база этим воспроизведением не открывалась.

Рекомендация: единый guard всех SQLite input paths, включая -wal/-shm/-journal,
их aliases, конфиги/gold/predictions, в API до вычисления и публикации обоих выходов.
Добавить direct API regression наряду с CLI.

Evidence: `TMP/root-api-sidecars.py`, `root-api-sidecars/result.json`;
контракт/API дополнительно прочитан pipeline-агентом.

### A11-H1 — высокая: слова о команде/клиентах/часах становятся запретом страны

**Проверено real VacancyTagger с fake stages.** `labeling/proofs.py:173–181`
требует соседство hiring + limit + foreign name при отсутствии буквального Brazil,
но не доказывает область ограничения. Точные синтетические цитаты:

- “We hire worldwide with only two hours of overlap with the US team.”
- “We hire globally and work only with US clients.”
- “We hire candidates from North America and South America only.”

Модельный `restricted_elsewhere` с правильным ID проходит S3 без warning; два
согласных fake tag/escalate → `labeled`, `ineligible`, `hiring_region_restricted`.
В этих случаях исключение Brazil не следует из цитаты. Общий hiring bridge затем
может исключить вакансию из P1/уведомлений. Частота такого ответа live LLM не измерена.

Рекомендация: связывать ограничение с местом/правом найма кандидата; учитывать
всё множество стран/регионов, включая South America. Слова о клиентах, overlap
или команде не являются доказательством исключения. Неоднозначное → unspecified.

Evidence: `core/probe-proof-scope.py`, `core/proof-scope-results.json`.
Старые Barueri и English-as-geography случаи исправлены; здесь другие source relations.

### A11-H2 — высокая: implied English игнорирует явное Portuguese-only

**Проверено.** `labeling/proofs.py:290–296` разрешает implied English по соседству
international и you/our/will/the/and, не проверяя противоположное явное требование.
Источник отдельно подтверждает Brazil eligibility и remote, затем:
“We are an international company and this role requires Portuguese only.”
Fake model выбирает `working_language=english`, `basis=implied`, exact ID последней
строки. Реальный теггер возвращает **eligible, warnings=[], один tag call**.

Последствие — мягкое подтверждение U5a перевешивает явный языковой факт, нарушая
U5b и смысл working_language. International customers вместо самой компании также
принимаются аналогично; критерий пользователя менять не требуется.

Рекомендация: explicit/contradictory language facts проверять до implied inference;
не выводить company international из клиентов. При конфликте → unspecified/unknown,
а не eligible. Добавить end-to-end gate regression.

Evidence: `core/probe-positive-contradiction.py`, `core/positive-contradiction/results.json`,
дополнительный lang-customer-international в proof-scope-results.json.

### A11-H3 — высокая: collection-only drift при cache miss понижает A→C

**Проверено публичным pipeline.run на TMP с fake adapter.**
`pipeline.py:522–523` сохраняет accepted fallback только при полном raw source_hash
равенстве. При единственном изменении `source_metadata.fetched_at` модельный
`annotation_input_hash` остаётся точно прежним, raw source_hash меняется.
С пустым cache_dir обычный offline labels run сохраняет **A/3/labeled →
C/1/rules_only**, без `labels_v3_attempt`, модельных вызовов0, record_errors0;
source_revision корректно продвигается1→2. Текст/содержательные факты не менялись.

Последствие — активная принятая оценка теряется и вакансия понижается в очереди
из-за нового наблюдения. Наличие старых данных в annotation archive не исключено:
физическое уничтожение всех следов не заявляется. Это непокрытый край §0.7/§22,
отдельный от исправленного exact-raw-source fallback.

Рекомендация: доказывать collection-only совместимость meaningful source snapshot,
units и completeness, учитывая status/новые ограничения отдельно; сохранить
accepted assessment/labels/purpose и provenance, обновить observation и attempt.
Произвольный изменившийся источник по одному похожему фрагменту не приравнивать.

Evidence: `pipeline/observation-fallback.py`, `pipeline/observation-fallback.json`;
`annotation/input_contract.py` отделяет модельный вход от collection observations.

### Средние

| ID | Проверенная суть / последствие | Доказательство и рекомендация |
| --- | --- | --- |
| A11-M1 | Однократный terminal ledger I/O failure удаляет pending до успешной записи. Настоящий AnnotationModelClient.generate с fake transport: 1 вызов, spent0.00015, после доступного close ledger0rows/pending0; ledger_metrics complete=True/cost0/attempts0/uncertain_bills0 | `labeling/ledger.py:58–76`, `tagger.py:147–153`; `core/probe-native-ledger-error.py`, `native-ledger-error/results.json`. Сохранять terminal/pending до durable success, восстанавливать учёт либо fail closed; учитывать partial-write/duplicate-ID риск. Это не SIGKILL и не обход in-memory budget |
| A11-M2 | Обычный shadow reevaluate при exact-source offline cache miss заменяет accepted labels3/labeled →1/rules_only в jobs, profile evaluation и CRM list/detail; attempt отсутствует. Legacy tierA/PASS, P1/P2 и alerts прежние, persisted1/errors0 | `pipeline.py:360–375,513–539`; `pipeline/shadow-preservation-expanded.py/.json`. Сохранять accepted same-source v3 отдельно от законного legacy reevaluation, не делать labels-mode early-return на весь shadow assessment. Dedicated shadow CLI уже сохраняет accepted; defect именно regular pipeline |
| A11-M3 | GoldLabelStore.save при injected replace failure и подмене уникального staging удаляет чужой файл; gold/DB прежние, foreign_staging_survives=false | `annotation/gold_ui.py:440–453`; `metrics_ui/probe_cleanup.py`, `cleanup-results.json`. fstat dev/inode, cleanup только своей identity, unknown ownership сохранять. Предпосылка — сторонняя подмена staging; тот же класс гонки, что прежняя A10-M5 в другом writer |

### Низкие

| ID | Проверенная суть / граница | Рекомендация |
| --- | --- | --- |
| A11-L1 | Shared publisher: link EIO + staging.lstat PermissionError выбрасывает cleanup error вместо primary; staging остаётся, final отсутствует. `annotation/cohorts.py:88–95`, root-cleanup.py/result.json и независимый metrics_ui/probe_primary_error.py | Сохранять primary при неизвестной ownership, secondary добавлять note. Данные не повреждены; первичная ошибка остаётся в Python context, но вызывающий/CLI получает другой верхний exception |
| A11-L2 | Design header:5–6 говорит о незакрытых №8–9; §0.4:67 и §7 требуют ручного нового default output, хотя §23/code уже auto unique. Безусловное обещание enabled taxonomy:974/§22 шире фактического optional freeze-пути | Согласовать актуальную шапку/§0/§7 с §23. Уточнить: freeze использует enabled membership при доступных configs, исторический load/create_run не доказывает текущую membership. Это doc limitation, не объявленный runtime defect frozen replay; старые аудиты не переписывать |

## 5. Признанные отклонения исходного задания

| Отклонение | Вердикт |
| --- | --- |
| company= у rules_relevance | Обосновано для подтверждённого strong-company списка; сейчас список proposed/empty, не применяется |
| required_domain и basis | Обосновано: отделяет работодателя от нужной экспертизы и explicit от implied; теперь отражено в design |
| duty_unit_ids/title_unit_ids response_schema | Обосновано для source-scope; синтаксическая схема сама не заменяет S3 |
| label_scope в v3 | Обосновано сохранением v2 provenance; migration1:1 подтверждена |
| Tiger Analytics=1 через pharma industry | Уточнено корректно: industry работодателя consulting, требуемый pharma domain отдельно; синтетическая формула1 требует соответствующего preferred_focus. Историческая human оценка не изменялась и не доказывается неполными тегами |
| Domain metrics по requirement/industry | Устаревшее описание: добавлен required_domain со своей human population; tests/random checks pass |
| MAE/QWK/Spearman только покрытые пары | Обосновано при явных N/coverage; missing остаётся в exact/±1/recall/P@k |
| Frozen build/load только v2 | Устарело: schema-aware v2/v3 реализованы; текущая enabled taxonomy не безусловна — A11-L2 |
| LabelResult/TagValue отложены | Устарело: реализованы P2; отсутствие больше не является предпосылкой |
| Cohort assisted=curated | Обосновано adapter provenance v2→v3; fresh cohort read-only pass |
| root/nested proposed против confirmed | Текущий YAML literal, root/nested runtime guards pass; strong_companies proposed сохраняется |

Отдельно: pipeline/prefilter/scoring изменены относительно исходной P0-базы,
что ожидается после поручения P2–P3. Перенос scope и флаги проверяются legacy suites;
их отсутствие изменений теперь нельзя использовать как критерий всей реализации.
Поисковые критерии/ID не менялись этим аудитом, исходное дерево сохранено.

## 6. Покрытие тестами

Свежий полный строгий прогон: **3844 passed, 3 failed, 22 errors за48.17с**,
3869 случаев всего. Все25 непрошедших относятся к HTTP/bind и остановлены именно
`PermissionError: Audit guard: all network and bind disabled`. Это ограничение
исходного задания, а не25 установленных дефектов продукта. HTTP не разрешался
дополнительно. Число3869passed/64.38с в design §23 — прежняя приёмка с loopback,
его нельзя выдавать за текущий полный зелёный аудит.

| Группа | Свежий результат / непокрытый край |
| --- | --- |
| Core/schema/rubric/aggregate/evidence/provider/budget/ledger | 434passed/1.21с; не покрыты H1/H2/M1 |
| Pipeline/runner/storage/CRM/CLI/profiles/tiering | 158passed/3.93с; не покрыты H3/M2; direct serializers проверены без HTTP |
| P0/P1 metrics/gold/frozen/navigation/atomic | 616passed/6.12с; не покрыты M3/L1; native DOM не проверен |
| Независимые численные проверки | seed110201026:2000 наборов QWK/Spearman,1000 partial/missing denominator наборов; расхождений0 |
| Обязательные CLI/migration/baseline/replay | Четыре CLI exit0;171rows1:1, исторические frozen и saved shape прежние |
| Новые adversarial probes | API sidecars, source relations/contradictions, transient ledger I/O, observation drift, regular shadow, foreign staging и primary cleanup error |

Группы пересекаются и не суммируются. Регрессии №10 проверяют свои конкретные
сценарии, но не доказывают полноту всех публичных API и областей цитат.
Новых тестов в репозиторий не добавлялось: воспроизведения находятся только в TMP.

## 7. Готовность к следующим фазам

- P0: baseline/схема/миграция сохранены; для полной приёмки закрыть API read-only A11-C1.
- P1: source-only форма, частичные теги и async regressions работают в проверенном
  harness; исправить A11-M3, затем перепроверить native HTTP/DOM отдельно.
- P2: исправить A11-H1/H2/M1 до платного пилота/доверия к hiring decisions.
- P3: исправить A11-H3/M2 до сохранения массовых результатов и переключения tier.
- Пилот по-прежнему требует подтверждённых прецедентов, допустимых зарегистрированных
  когорт, покрытия человеческих меток,3повторов/выбора варианта, независимого holdout
  и гейтов §12. Бюджет и модели — по существующим U6/U7; новые предпочтения не вводятся.
  Strong-company список не утверждается аудитором. P4 не начинать в рамках проверки.

Ни fake tests, ни зелёный baseline не доказывают качество live модели или готовность
production rollout. Рабочая разметка и переключение режимов не выполнялись.

## 8. Предлагаемые исправления design doc

| Раздел | Было → нужно уточнить |
| --- | --- |
| Header, §0.4, §7 | Открытые №8–9/ручной новый default → актуальная история и auto unique implicit defaults, immutable explicit output |
| §0.5, §7 | Защита только CLI → единая read-only граница public API+CLI, включая SQLite sidecar identities до анализа |
| §6.4, §23 | Соседство слов как value proof → relation/scope-aware проверка, все страны inclusive списка, explicit language facts выше implied |
| §0.7, §8–§9, §22 | Сохранение по raw-source equality → доказуемая совместимость фактов отдельно от collection observations; shadow retention отдельно от legacy reevaluation |
| §6.6 Ledger | Одна строка на попытку/close → terminal I/O failure не теряет ownership, учёт восстанавливается или run fail-closed; partial-write/duplicates оговорены |
| §0.4, §10 | Inode cleanup в части writers → такой же ownership и primary-error контракт для manual Gold publication |
| §6.7, §22 | Безусловная enabled taxonomy → точная граница configs на freeze, structural validation и независимый historical replay |

Это рекомендации; design doc в этом аудите не редактировался. Численные критерии,
штрафы, YAML и пользовательские решения менять не предлагается.

## 9. Приложение: команды, результаты и сохранность

Тесты/CLI/probes: чистый env, PYTHONDONTWRITEBYTECODE=1,
PYTHON_DOTENV_DISABLED=1, PYTHONPATH=TMP со strict sitecustomize.py.

```text
.venv/bin/pytest -q -p no:cacheprovider --basetemp TMP/full
.venv/bin/ruff check src tests --no-cache
git diff --check
gold migrate-v3 --source data/local/gold/vacancy-tags-v2-labeled.jsonl --out TMP/integration/migrated.jsonl
gold validate --gold TMP/integration/migrated.jsonl --output TMP/integration/validation.json
eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output TMP/integration/rules.json
eval accuracy --variant saved --gold data/local/gold/vacancy-tags-v2-labeled.jsonl --output TMP/integration/saved.json
python TMP/integration-check.py
python TMP/root-api-sidecars.py
python TMP/root-cleanup.py
python TMP/snapshot.py before|after
```

Ruff и git diff --check — pass. `integration/commands.json` фиксирует exit0 всех
четырёх CLI. TMP-обёртка после них ошиблась в имени проверочного скрипта;
`integration-check.py` затем исполнен отдельно успешно — это ошибка обёртки аудита,
не отказ продукта и не повтор публикации в занятые outputs.

- Миграция171:55 с какой-либо human label,44complete,10relevance-only;
  blind44/curated11/assisted_llm0, human tags0. source/hash/text/date/decisions/
  label_scope/notes1:1, assisted→curated, first_labeled_at сохранён, v4 manifest.
  JSONL побайтно равен рабочему v3; входной и выходной SHA корректны.
- Rules baseline: N54, QWK0.373134, recall12/17, precision12/24, P@10=0.8.
  Канонический cohort через Python без exposure:163selected, blindN44,
  recall7/7, precision7/19, QWK0.433663.
- Три сохранённых frozen run проходят validate_run. Saved accuracy сохраняет16
  top-level ключей и вложенные evaluation/operational/stability/readiness/selection.
- P1 read model:171total,44complete,11partial,116unanswered,17relevant,10strong,
  6tags; machine fields отсутствуют в blind detail.
- YAML2913bytes exact; version labels-rubric-2026-10-20;
  prompt hash `8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8`,
  aggregation hash `9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f`.
- Снимки `before-sha256.json`/`after-sha256.json` включают рабочую
  `data/local/job_intake.db` и все gold JSONL; их SHA совпадают. До публикации
  отчёта все8293 файла прежние; итоговая сверка после неё допускает только этот
  новый отчёт (8293→8294, changed=[]).
  Ветка/HEAD, исходные изменения пользователя, рабочие данные/конфиги, прежние
  аудиты, frozen/cohort/exposure journals не менялись. Ключи/.env/live API не
  использовались; коммитов/переключения веток нет.
