# Тринадцатый аудит labels-v3: P0–P3 после исправления трёх средних находок — 20.10.2026

## 1. Итог

Три средние находки [аудита №12](audit-labels-v3-implementation-2026-10-20-12.md)
исправлены и проверены заново: terminal ledger не теряет попытку, regular shadow
сохраняет принятую v3-разметку при совместимом fallback, manual Gold cleanup
сохраняет чужой staging. В проверенном объёме **0 критических, 0 высоких,
0 средних и 2 низкие находки**. Схема, миграция, baseline и прежние C/H-регрессии
сохранены. P0–P3 реализованы, но paid pilot/holdout acceptance и рабочий rollout
этот аудит не подтверждает. P4 вне объёма; критерии и рабочие данные не менялись.

Это повторный аудит текущего дерева после исправлений: отдельные native probes,
I/O/interruption/concurrency проверки, полный pytest и обязательные CLI. Конечный
набор сценариев не является доказательством полного понимания естественного
языка или качества live LLM.

## 2. Объём и метод

- Ветка `feature/vacancy-tags`, HEAD `e61d483d4ac06157725cde8d81ea9a0f6bbada36`;
  P2–P3 и исправления находятся в незакоммиченном дереве. Исходная база `8bb3a2a`,
  первоначальные P0 `624bd17`/`d63d5db`. Исходный diff сохранён в TMP.
- Основание: исходное задание аудита, AGENTS, design/optimization §3–§5,
  README/architecture, labels-модули, accuracy/frozen/CLI, pipeline/storage,
  Gold/CRM и tests. Объём исходного P0 расширен до порученных P0–P3.
  Пользовательские решения §13 не пересматривались; карьерная база не загружалась.
- Фаза исправления меняет только четыре production-файла, новые регрессии и
  актуальные design/architecture. Последующая проверка зафиксированного кода
  читает репозиторий и пишет результаты в TMP; итоговый отчёт — новый файл.
  Аудиты №1–№12 сохранены.
- Чистый env без реальных ключей, dotenv/bytecode/cache выключены. Python guard
  запрещает чтение `.env*`, запись репозитория и рабочую SQLite без `mode=ro`;
  readers дополнительно задают `PRAGMA query_only=ON`. Все source adapters,
  fake providers, SQLite, журналы и инъекции сбоев находятся в TMP.
  Внешней сети/платных вызовов нет. Полная проверка исправления разрешает только
  loopback HTTP; отдельный строгий аудит запрещает также DNS/socket/bind.
- Рабочие run/annotate/reevaluate/CRM/миграции не запускались. Каноническая когорта
  использовалась через Python read-only, без CLI exposure. Четыре выходных CLI
  направлены в TMP. Тестовые изменения профилей касаются только временных fixtures.
- До изменений снят manifest **8300 файлов**, включая link targets как строки,
  без чтения содержимого symlink target. Защищены **7446** config/data/старых
  audit entries, включая две существующие ссылки node_modules внутри data.
  Кеши, node_modules и `.git` contents не читаются; `.venv` фиксируется по ссылке.
  Итоговые hashes и допустимый diff приведены в §9.
- TMP: `/private/tmp/labels-v3-a13-fixes-BpODOM`. Взаимное независимое чтение
  выполнено агентами той же команды; это не внешний аудит третьей стороной.
  Fake providers не измеряют live frequency; HTTP/Node harness не проверяют
  native browser DOM и доступность внешних провайдеров.

## 3. Трассировка требований

| Требование | Статус | Доказательство / граница |
| --- | --- | --- |
| §0.1 Критерии/ID/proposed | Реализовано в проверенном объёме | Config SHA прежние, exact YAML2913bytes, rubric hashes прежние; proposed guards проходят |
| §0.2 Явные AI/бюджет | Реализовано в проверенном объёме | Budget/provider/CLI tests; native billing и durable terminal recovery A12-M1 проверены без live API |
| §0.3 Секреты/диагностика | Реализовано в проверенных ветках | Чистый env и secret-read guard; diagnostic/provider tests |
| §0.4 Immutable outputs/ownership | Частично | Exclusive outputs и manual Gold ownership проходят; shared publisher primary error A13-L1 остаётся |
| §0.5 Eval/export RO SQLite | Реализовано в проверенном объёме | Public API/CLI sidecar refusal, native mode=ro SELECT, рабочие DB hashes прежние |
| §0.6 Аддитивная схема | Реализовано | Storage/migration tests на TMP; рабочая схема не мигрировалась |
| §0.7 Fallback retention | Реализовано в проверенных ветках | Labels-mode и regular shadow: same-role/source и collection-only, отдельный attempt, true fact/role changes не rebind |
| §0.8 Недоверенный источник | Реализовано в проверенных случаях | Prompt policy, S3/value/source proof;80native adversarial/control cases без ошибок, unrecognized → abstention |
| §0.9 Legacy default | Реализовано в проверенном объёме | Disabled/legacy suites; shadow сохраняет accepted v3, полный legacy upsert продолжает работать |
| §0.10 Tests/errors/style | Частично | Ruff/diff и полный pytest проходят; низкий primary-error contract A13-L1 |
| §4 Enum/14 industries/null/ID/provenance | Реализовано | Schema/runtime/date/partial suites, human migration1:1 |
| §4.2 Шесть тегов/evidence | Реализовано в проверенном объёме | Source ID/section/relation/город/язык/AND-OR/uncited contradictions suites |
| §4.3 Gold/source binding | Реализовано в проверенных ветках | Migration171, blind read model; v2/v3 manual save ownership, backup/audit и errors |
| §4.4–4.5 Prediction/response JSON Schema | Реализовано с явной границей | Runtime strict finite/int; portable JSON Schema integer2.0 по значению, runtime float/bool отказывает |
| §5.1 LabelingConfig/flags | Реализовано | Disabled/shadow/labels/default/provider tests; существующий config прежний |
| §5.2 YAML/hashes/proposed | Реализовано | Exact literal, независимые prompt/aggregation/rubric versions; strong companies proposed/empty |
| §5.3 Прецеденты/holdout | Реализовано в guards | Registered loader/exposure/balance tests; пользовательские human precedents и acceptance не создавались |
| §6.1 Бесплатные gates | Реализовано в проверенных случаях | Closed/incomplete/too-long/title; hiring отдельно от relevance |
| §6.2–6.5 Providers/S3/aggregation/gate/P2 | Реализовано в проверенном объёме | Pure formula/policy/confirmation/cache/source regressions; technical aggregation-v3 прежняя |
| §6.6 Кеш/ретраи/budget/ledger | Реализовано в проверенном объёме | Current S3 revalidation, full/facts reuse; terminal/partial append/retry/idempotence/interruption/close tests |
| §6.7 Метрики/stability | Реализовано в проверенном объёме | Полные suites и свежие2000 QWK/Spearman+1000 partial-denominator sets,0mismatch |
| §6.8 Rules baseline | Реализовано | N54/QWK.373134/recall12/17/precision12/24/P@10=.8; rules:tier-map-v1 A→3/B→2/C→1, роль other→0 |
| §6.9 Миграция/manifest | Реализовано в проверенных ветках | v4 migration171/valid, source/decisions/provenance1:1, output bytes равны рабочемуv3 |
| §7 CLI/public API | Реализовано в проверенном объёме | Четыре обязательные CLI exit0; public sidecars3refused, RO SELECT доступен |
| §8 Labels/shadow/purpose/alerts | Реализовано в проверенных ветках | Accepted retention отдельно от legacy upsert; temp score8→1/tierA→C действительно сохраняются, human answers прежние |
| §9 Хранение/pins/версии/CAS | Реализовано в проверенных ветках | Source/input/role guards, observation provenance, revision+updated_at CAS, old labels_version не повышается |
| §10 Gold UI/CRM | Реализовано с границей | Blind/partial/async/HTTP/ownership tests; CRM accepted values preserved, native DOM не проверен |
| §11 Тесты | Реализовано в проверенном объёме | Новые содержательные regressions + полный suite; ограничения coverage явно перечислены |
| §12 P0–P3/P4 | Частично / P4 вне объёма | Код фаз есть и проверен offline; live pilot/holdout/rollout conditions не выполнены |

## 4. Находки и проверка закрытия

### A12-M1 — закрыта: terminal ledger ownership

**Проверено.** `labeling/ledger.py:40,71,82,172` сохраняет pending и первый terminal
до durable append. Request_failed после ошибки complete callback повторяет тот же
terminal с известным usage. Unbuffered JSONL использует byte offset: partial/short
write, flush/fsync и graceful interruption откатываются перед повтором; committed
call_id идемпотентен. Memo входит в ту же rollback boundary. Неустранимый rollback
блокирует новые starts, terminal redelivery и успешный close, сохраняя pending.
Новый start сначала восстанавливает старые terminal pending, в том числе другого
потока, не прерывая ещё выполняющиеся запросы.

Native AnnotationModelClient с fake transport:1POST/1JSONLrow,100input+50output
tokens, in-memory spent=.00015 и ledger metrics cost=.00015/attempts1 совпадают.
Независимый trace нашёл промежуточное окно после fsync до memo; оно исправлено
до финального прогона. Повтор после interruption даёт1unique call_id/pending0.
Composite interruption+rollback-fsync failure даёт broken=True/pending1/bytes0,
terminal и close отказывают вместо ложного успешного нулевого счёта.
**37 новых cases**, связанный набор158passed/2.29с. SIGKILL/power-loss durability
не обещается. Evidence: `TMP/review/ledger-native-final/results.json`,
`ledger-postsync-final/results.json`, `ledger-broken-final/results.json`,
`peer-findings.json`; `tests/test_labels_a13_ledger.py`.

### A12-M2 — закрыта: regular shadow retention

**Проверено.** `retain_labels_on_fallback` включён только для regular shadow upsert.
Accepted snapshot/колонки job/profile сохраняются отдельно от failed attempt при
той же роли и совместимом source/input. Collection-only rebind использует полный
meaningful snapshot, trusted observation binding и provenance. Новая роль или
изменённые факты не наследуют прежнюю оценку; старые labels_version не обновляются
неудачной попыткой. CAS проверяет source_revision и updated_at.

Native прежний repro теперь сохраняет3/labeled в job/profile/CRM, отдельно пишет
rules_only attempt, legacyA/PASS/purpose/alerts прежние. Дополнительный native probe
доказывает выполнение legacy upsert: временный score8→1/tierA→C и новый profile
version сохраняются одновременно с accepted3/labeled и прежней labels_version;
human P1/P2 не меняются. Это не early return всей shadow оценки.
**16 новых cases**, связанный набор170passed/5.94с.
Evidence: `TMP/review/shadow-native/results.json`, `shadow-legacy-native/results.json`;
`tests/test_labels_a13_shadow.py`; `pipeline.py:552`,
`storage/repository.py:194,202,323`.

### A12-M3 — закрыта: manual Gold staging ownership

**Проверено.** Writer фиксирует dev/inode через fstat открытого staging, применяет
fchmod к descriptor, проверяет identity до replace и удаляет только совпавший
inode. Неизвестная identity или foreign replacement сохраняются. Secondary cleanup
OSError добавляется note к primary, не заменяя её. Backup/write-ahead audit и
выборочная human edit сохраняют прежний контракт.

Независимый native прежний repro: foreign_staging_survives=True, Gold/DB прежние,
primary publication error сохранена. **22 новых cases** на настоящем v2/v3 save,
write/flush/fsync/replace/lstat/unlink/file/symlink/missing и успешный save;
связанный набор167passed/3.53с, независимый22passed/0.34с.
Evidence: `TMP/review/final-remaining-io/cleanup-results.json`, `gold-pytest.log`;
`annotation/gold_ui.py:447,455,469`, `tests/test_labels_a13_gold_ownership.py`.

### Открытые низкие

| ID (прежний) | Проверено / последствие | Рекомендация |
| --- | --- | --- |
| A13-L1 (A12-L1) | Shared immutable publisher: link EIO + staging.lstat PermissionError возвращает secondary exception вместо primary; final отсутствует, orphan staging остаётся. `annotation/cohorts.py:88`; `TMP/review/final-remaining-io/primary-error-results.json` | Сохранять primary, secondary добавить note; неизвестную ownership не удалять. Исправление manual Gold не затрагивает этот другой writer |
| A13-L2 (A12-L2) | Design:6 всё ещё говорит об открытых №8–9; :67/:1070 требуют ручной output при повторе default, хотя код/§23 используют auto unique. Обещание enabled taxonomy:1687 шире optional-config freeze; historical replay не доказывает current membership | Согласовать шапку/старые инструкции; явно ограничить membership проверку доступными freeze configs |

Новых подтверждённых C/H/M в проверенном объёме нет. Две низкие не исправлялись
в рамках поручения закрыть три средние.

## 5. Признанные отклонения исходного аудита

| Отклонение | Вердикт |
| --- | --- |
| company= у rules_relevance | Обосновано confirmed strong companies, сейчас proposed/empty и не применяется |
| required_domain/basis, duty/title unit IDs | Обосновано разделением отрасли/экспертизы и source evidence; одной structural schema недостаточно, S3 проверяется отдельно |
| label_scope вv3, assisted→curated | Обосновано сохранением provenance; fresh migration1:1 |
| Tiger Analytics=1 через pharma | Industry работодателя consulting, preferred_focus отдельно; синтетическая formula1 требует pharma domain, human оценка не изменялась |
| Domain metrics requirement/industry | Старое описание: required_domain имеет отдельную human population |
| MAE/QWK/Spearman только covered pairs | Обосновано при явных N/coverage; missing остаётся в exact/±1/recall/P@k, свежие denominator checks проходят |
| Frozen толькоv2 / LabelResult отложен | Устарело после P1–P2: v2/v3 frozen, LabelResult/TagValue реализованы; membership boundary A13-L2 остаётся |
| Proposed против confirmed | Exact YAML/guards проходят, strong companies намеренно proposed |
| Pipeline/prefilter/scoring неизменны | Относилось к исходному P0; P2–P3 поручены позже. Legacy suites проверены, критерии этим исправлением не менялись |

Уточнение к трассировке №12: `D→0` было ошибкой того отчёта. Фактический
`baseline_tier_map` всегда A→3/B→2/C→1, **роль other→0**, missing/stale abstains;
свежий rules JSON это подтверждает. Код adapter в этом исправлении не менялся.

## 6. Покрытие тестами

Добавлены **75 регрессий**: ledger37/shadow16/Gold22. Независимый повтор нового
набора:75passed/1.64с. Полный итоговый прогон с loopback: **4182 passed за66.10с**
(`pytest-fix.log`), exit0. Ruff и `git diff --check` проходят.
Отдельный строгий аудит: **4157passed,3failed,22errors за54.24с**
(`pytest-audit.log`), все25 остановлены `Audit guard: all network and bind disabled`
в HTTP/bind проверках; это ограничение исходного режима, а не25 дефектов продукта.
Все они проходят в полном loopback наборе. Python/HTML snapshot248 совпадает
до/после итоговых прогонов; peer-reviewed source hashes также прежние.

Проверки не суммируются по группам. Дополнительно:80native source-proof/control
cases без ошибок;3public API sidecars отказаны, DB hash/RO SELECT прежние;
seed130201026:2000 независимых QWK/Spearman и1000 missing/partial denominator
наборов без расхождений. Миграция/baseline/frozen/saved/UI выполнены заново (§9).
Наличие зелёного suite не доказывает native browser DOM, качество live модели,
SIGKILL durability или полноту всех возможных source формулировок.

## 7. Готовность к следующим фазам

P0: schema/миграция/метрики/public read-only граница подтверждены. P1: manual Gold
ownership исправлена; остаётся низкий shared cleanup diagnostic и native DOM
вне проверки. P2: terminal учёт восстановим и fail closed при неустранимом append;
проверенные source/hiring cases прежние. P3: labels-mode и regular shadow retention
проверены отдельно от legacy routing, с source/role/CAS границами.

Live pilot/rollout не запускались. Для приёмки качества остаются confirmed
прецеденты, допустимые зарегистрированные когорты/human coverage,3повтора/выбор
варианта, независимый holdout и гейты §12. Бюджет/модели — по прежним U6/U7;
strong companies не утверждаются аудитором. P4 не реализовывалась.

## 8. Design doc

В §6.6/новом §25 и architecture обновлены terminal pending/durable append,
rollback/idempotence/close/fail-closed контракт; regular shadow retention отдельно
от legacy upsert; manual Gold descriptor identity и primary cleanup errors.
Формат ledger, модельные fact keys, aggregation-v3 и пользовательские rubric
hashes не меняются. Низкие header/default/taxonomy противоречия A13-L2 остаются
рекомендацией; historical audits/числа не переписываются.

## 9. Приложение: команды, цифры и сохранность

```text
env -i ... PYTHONPATH=TMP/fix-guard .venv/bin/pytest -q -p no:cacheprovider --basetemp=TMP/full-fix
env -i ... PYTHONPATH=TMP .venv/bin/pytest -q -p no:cacheprovider --basetemp=TMP/full-audit
.venv/bin/ruff check src tests --no-cache
git diff --check
gold migrate-v3 --source data/local/gold/vacancy-tags-v2-labeled.jsonl --out TMP/integration/migrated.jsonl
gold validate --gold TMP/integration/migrated.jsonl --output TMP/integration/validation.json
eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output TMP/integration/rules.json
eval accuracy --variant saved --gold data/local/gold/vacancy-tags-v2-labeled.jsonl --output TMP/integration/saved.json
python TMP/integration-check.py
python TMP/review/run-stable-probes.py
python TMP/review/probe-metrics.py
python TMP/source-snapshot.py before|after
python TMP/snapshot.py after
```

Все четыре CLI exit0 (`integration/commands.json`). Migration171,55 с любой human
label,44complete/10relevance-only, blind44/curated11/assisted_llm0, human tags0.
Source/hash/text/date/decisions/label_scope/notes1:1 без цитирования notes,
first_labeled_at сохранён, v4 manifest/hash корректны; JSONL побайтно равен рабочемуv3.
Rules: N54/QWK.373134/recall12/17/precision12/24/P@10=.8. Read-only cohort163selected,
blindN44/recall7/7/precision7/19/QWK.433663, exposure не изменялся.
Три прежних frozen run валидны; saved16top-level и nested shapes прежние.
Gold read model171/44complete/11partial/116unanswered,17relevant/10strong/6tags,
machine fields отсутствуют в blind detail.

YAML2913bytes exact, `labels-rubric-2026-10-20`;
prompt hash `8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8`,
aggregation rubric hash `9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f`.

`before-sha256.json`/`after-summary.json`:8300→8304entries; изменены только
4production-файла (ledger, pipeline, repository, gold_ui) и2актуальных документа,
добавлены3регрессионных файла и этот новый отчёт. Остальные entries прежние.
`protected-before.json`:7446entries,protected_changed=[],added_data_config=[].
`source-before-full.json`/`source-after-full.json`:248files,changed=[],added=[].
`review/peer-findings.json` содержит независимые source hashes и evidence.
БД, gold, config, frozen/cohort/exposure journals и аудиты №1–№12 прежние;
ветка/HEAD и исходные изменения пользователя сохранены. Ключи не читались,
коммитов/push/переключения ветки и live API не было.
