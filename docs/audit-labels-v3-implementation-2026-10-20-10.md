# Десятый аудит labels-v3: P0–P3 и исправления — 20.10.2026

## 1. Итог

На входе аудита P0 сохраняет прежние численные результаты и эталоны, P1–P3 имеют
существенные разрывы: **1 критическая, 3 высокие, 7 средних и 11 низких находок**.
До исправлений автоматическую разметку с записью решений и платный массовый прогон
принимать нельзя. Это аудит незакоммиченной реализации P2–P3 поверх `e61d483`,
а не повторная оценка одних коммитов P0. Ниже сначала зафиксировано состояние
**до правок**; результаты исправлений добавлены отдельно в §10.

Критерии пользователя не оспаривались. Тестовые изменения статусов рубрики,
источники, ошибки провайдера и гонки создавались только в TMP. Рабочая рубрика
содержит confirmed-решения и proposed/empty strong_companies; пользовательские
комментарии из gold здесь не цитируются.

## 2. Объём и метод

- Ветка `feature/vacancy-tags`, HEAD `e61d483`; исходная база `8bb3a2a`, исторические
  коммиты P0 `624bd17`/`d63d5db`. Проверены текущие tracked и новые untracked исходники.
- Прочитаны AGENTS, исходное задание аудита из приложений, design doc, обоснование
  §3–§5, architecture/README, labeling, CLI, pipeline/purpose/profiles/tiering,
  storage/CRM, schema/frozen/cohort hooks и соответствующие тесты.
- Перед исполнением сохранены SHA-256 **8285 файлов**. После всех аудиторских запусков:
  **8285 файлов, изменённых 0, добавленных 0**. Копия 155 исходников/конфигов/документов
  до исправлений: `/private/tmp/labels-v3-audit10-cfh9abur/audited-source`.
- Аудиторские Python-процессы запускались с пустым окружением, `PYTHONDONTWRITEBYTECODE=1`,
  `PYTHON_DOTENV_DISABLED=1` и audit hook: запрещены все socket/DNS/bind, чтение `.env*`,
  изменения репозитория и открытие рабочей SQLite без `mode=ro`. Readers используют
  `PRAGMA query_only=ON`. Тестовые SQLite находятся вне репозитория.
- Ни live providers, ни браузер, ни рабочие `run`/`annotate`/`reevaluate`/CRM не запускались.
  Каноническая когорта анализировалась прямым Python-вызовом без записи exposure.
- Независимость ограничена: исполнители этой проверки принадлежат той же команде
  моделей, которая реализовала P2–P3. Findings основаны на новых независимых probes;
  это не внешний аудит третьей стороной. Реальное поведение моделей и browser DOM
  фейковые клиенты и Node-harness не доказывают.
- Воспроизведения, логи, снимки и сводки: `/private/tmp/labels-v3-audit10-cfh9abur`;
  дополнительные metrics/UI probes: `/private/tmp/labels-v3-a10-metrics-eI7n9S`.
  Номера строк в §3–§4 относятся к сохранённому состоянию до исправлений.

## 3. Трассировка требований

| Требование | Статус до правок | Доказательство |
| --- | --- | --- |
| §0.1 Критерии/ID, proposed не применяется | Частично | YAML §5.2 exact; runtime proposed P2 всё же вычисляет bool — A10-H3 |
| §0.2 Явные платные вызовы и бюджет | Реализовано в проверенных ветках | `budget.py`, CLI `--ai --max-cost`, 94 core cases; реальных POST нет |
| §0.3 Секреты/промпты/ответы не в диагностике | Реализовано в проверенных provider/error ветках | P2 fake error/ledger tests; неизвестный gold tag key — R-L5 |
| §0.4 Неизменяемые эталоны/атомарные выходы | Частично | Exclusive publisher; R-L4 lexical symlink и A10-M5 staging cleanup |
| §0.5 Экспорт/оценка SQLite только чтение | Отклонение | P2 export принимает DB journal как output и ломает RO-open — A10-C1 |
| §0.6 Миграции аддитивные | Реализовано | `storage/database.py`, temp legacy-schema regressions; рабочая схема не мигрировалась |
| §0.7 Ошибка модели не удаляет/не понижает вакансию | Реализовано в проверенных same-source ветках | `test_labels_failure_preservation.py`; accepted score/purpose сохранены, наблюдение теряется — A10-M3 |
| §0.8 Источник недоверенный | Реализовано в prompt; S3 частично | `prompt.py` POLICY; value/proof contradiction — A10-H1 |
| §0.9 Прежнее поведение по умолчанию | Частично | Выключенный rollout и legacy/shadow suites pass; занятые default reports — A9-M1 |
| §0.10 Стиль/progress/ошибки/тесты | Частично | Ruff pass; ledger interrupt — A10-M1; противоположные writer API — A9-L2 |
| §4 Enum/INDUSTRIES, nullable/partial/provenance | Реализовано | 14 industries, schema snapshots exact, schema/date/partial tests |
| §4.2 Доказательность шести тегов | Частично | Секции/ID/context проверяются; Barueri принят как city São Paulo — A10-H1 |
| §4.3 Gold, v2/v3/schema-aware validation | Реализовано | Fresh migration/validation 171 строк, источник 1:1, blind UI |
| §4.4–4.5 Prediction/response schema, strict finite | Реализовано с оговоркой R-L3 | Registered provider schemas/fake clients; portable integer2.0 vs runtime |
| §5.1 LabelingConfig/rollout | Реализовано | `settings.py`, default disabled/shadow/score, config tests |
| §5.2 Literal YAML и independent hashes | Частично | 2913 bytes exact, текущие hashes прежние; root status/cache — A10-M2 |
| §5.3 Прецеденты/holdout guard | Реализовано в проверенных registry cases | `load_precedents`, прежние реальные registry tests; платный пилот не проведён |
| §6.1 Бесплатные маршруты | Реализовано в проверенных cases | `gates.py`, closed/incomplete/too-long/title tests |
| §6.2–6.4 Prompt/providers/S3 | Частично | Provider schema registration pass; A10-H1 |
| §6.5 Rules/final policies/role/gates/P2 | Частично | Числа pure aggregation pass; A10-H2/H3 |
| §6.6 Tagger/cache/escalation/ledger/budget | Частично | Fresh 94 pass; A10-M1/M2 |
| §6.7 Метрики и стабильность | Реализовано в проверенных cases | 500 Fraction-QWK + 500 независимых Spearman без расхождений; current/legacy suites |
| §6.8 Базовый уровень | Реализовано | N54, QWK0.373134, recall12/17, precision12/24, P@10=0.8 |
| §6.9 Migration/manifest | Частично | 171 bytes-identical, SHA/1:1; R-L4/A9-L1/A10-M5 |
| §7 P0 и P2/P3 CLI | Частично | Guarded real CLI без API; A10-C1/A9-M1 |
| §8 Pipeline/shadow/labels/tiering/purpose | Частично | 144 targeted pass; A10-H2, A10-M3/M4 |
| §9 Хранение/версии/optimistic source | Частично | Additive/scoped/failure tests; effective policy не входит в version — A10-M4 |
| §10 P1 Gold UI и P3 CRM | Частично | Blind store шесть тегов; A8-M1/A8-L1; подтверждённый gate не доходит до P1/alerts — A10-H2 |
| §11 Полнота тестов | Частично | 3739 collected, новые probes находят непокрытые интеграции; см. §6 |
| §12 Готовность P0–P3/P4 | Частично / P4 вне scope | Пилот/production rollout не принимается по одним fake tests; P4 не реализовывать |

## 4. Находки до исправлений

### A10-C1 — критическая: export создаёт SQLite journal вместо отчёта

**Проверено.** `labeling/cli.py:136–149` защищает сам DB path, но не `-wal`, `-shm`,
`-journal`. Реальный `label --export TMP/jobs.db-journal` без AI возвращает 0,
пишет 1469 байт JSONL; следующий `sqlite3(...mode=ro)` + query_only + SELECT падает
с `OperationalError`. DB bytes до/после прежние. WAL/SHM имена также принимаются.
Это нарушение границы export-read-only (§0.5) и доступности БД при допустимой
ошибке выбора output; **рабочая база таким запуском не открывалась**.

Evidence: `root-sidecars.py`, `root-sidecars-results.json`. Исправить: включить
служебные SQLite paths и их aliases в protected inputs до чтения/модели/миграции.

### A10-H1 — высокая: S3 не связывает hard value с содержанием доказательства

**Проверено реальным теггером с fake client.** `validate.py:101–188` проверяет ID,
секцию, basis и общие контексты, но принимает `office_sao_paulo` с
`office_location=Barueri` по точной строке «This is a hybrid role in Barueri,
São Paulo, Brazil.»; итог `eligible`, warnings пусты, один tag call. Это противоречит
U5c — только город São Paulo. Отдельно geography restricted + office London
принимаются с единственным доказательством «English required.»; повтор второй
моделью даёт ineligible без географического доказательства.

Evidence: `core-city-probe/results.json`, `core-probe/results.json`; aggregate
принимает эти tags (`aggregate.py:106–128`). Исправить: консервативная value-specific
проверка географии/формата/языка, согласование города и value; неизвестное понижать,
не менять критерии. Простое совпадение двух моделей не заменяет источник.

### A10-H2 — высокая: подтверждённый gate не применяется в labels pipeline

**Проверено** real tagger + два fake stages + temp SQLite. Источник явно ограничивает
найм резидентами Namibia; теггер возвращает ineligible, relevance3, escalated true.
Pipeline сохраняет **PASS/eligible/A, purpose P1 suitable и 1 queued alert**.
Отправки сообщения не было. `pipeline.py:368–388` переносит только ordinal/status;
`:465` пересчитывает legacy eligibility, `purpose.py:76` читает прежнее значение.

Evidence: `pipeline-independent-result.json`. Исправить только labels-mode:
подтверждённый gate должен влиять на hiring decision/eligible/P1/alerts, сохраняя
relevance и её категорию независимо от найма; shadow не менять. Согласовать export.

### A10-H3 — высокая: proposed P2 применяется как подтверждённое правило

**Проверено.** Только в TMP p2.status переключён в proposed; loader даёт
`rubric.p2_rule=None`, но `tagger.py:499–501` без guard вычисляет
`p2_candidate=True` для analytics_reporting/relevance1. P3 может использовать это
как machine purpose. Нарушена runtime-граница применения proposed-раздела (§0.1).
Рабочий P2 confirmed; реальных пользовательских решений аудит не менял, поэтому
не заявляется наблюдаемая порча production criteria.

Evidence: `core-p2-proposed-probe/results.json`. Исправить: при отсутствии effective
confirmed P2 возвращать null; pure P0 function может сохранять прежний API.

### Средние находки

| ID | Проверенное доказательство и последствие | Исправление |
| --- | --- | --- |
| A10-M1 | `ledger.py:42–55,104–108`, `tagger.py:335–360`: request_start→KeyboardInterrupt→close оставляет ledger0bytes/0rows при Budget.spent0.002677; `core-probe/results.json` | Зафиксировать незавершённую начатую попытку с unknown usage при interrupt/close, не дублировать terminal и не логировать exception body |
| A10-M2 (уточняет R-L2) | confirmed→proposed root меняет prompt bytes, но не prompt_rubric_hash/full cache key; offline full hit, stale=False. `rubric.py:195–207` против `prompt.py:61–72`; `core-proposed-probe/results.json`. Изменение anchors внутри proposed, наоборот, меняет hash без изменения prompt | Хешировать effective applied parts, согласовать root guard и применения; сохранить current confirmed семантику |
| A10-M3 | `pipeline.py:508–528` fallback early-return сохраняет accepted result, но игнорирует observed=True: after actual observation last_seen остаётся 2020-01-01; `pipeline-extra-result.json` | Сохранить наблюдение/lifecycle без перезаписи принятой оценки и ручных решений |
| A10-M4 | `profiles.py:196–219`: final_policy llm→rules, tier_source score→relevance дают равные версии и stale reasons=[]; меняется effective decision/tier policy | Включить effective policy в labels evaluation version только active labels-mode, не инвалидировать fact cache/shadow |
| A10-M5 | `gold_v3.py:270–272`: при injected os.link failure уникальный staging path заменён чужим файлом, finally удаляет замену; `root-extras-results.json` replacement_survives=False | Проверять dev/inode до удаления staging, сохранять исходную ошибку; внешняя подмена staging — явная предпосылка |
| A8-M1 | Fresh literal main pager handler (`crm/index.html:2087–2088`) меняет offset до принятия page. Открытие старой карточки оставляет page.offset0/global100/editor100; next queries [100,200], пропуск 100 карточек | Отдельный requested offset, commit только вместе с accepted page; тест main handler→old card→next |
| A9-M1 | Fresh temp CLI `eval` с занятым default latest.json отказывает exit2; accuracy/validate/rules-diff также сохраняют фиксированные default names. Эксклюзивность правильная, повседневный повтор требует ручного имени | Автоматически выбирать новый default filename при занятом имени; явный --output по-прежнему не заменять |

### Низкие находки

| ID | Доказательство / граница | Правка |
| --- | --- | --- |
| R-L1 | normalize_company: Acme (LLC)/Acme-LLC остаются acme llc, Acme (S.A.)→acme s a; strong list сейчас proposed/empty | Нормализовать пунктуацию до удаления самостоятельной юридической формы |
| R-L3 | Стандартный Draft2020-12 считает 2.0 integer, strict runtime не принимает; стандартная семантика, не повреждённая JSON Schema | Явно описать границу portable schema/runtime и закрепить тестом |
| R-L4 | gold_workbook ensure/writer и migrate-v3 resolve до occupied check: dangling final принимается, создаётся target | Проверять lexical final до resolve; immutable publisher на финальном имени |
| R-L5 | Неизвестное имя тега дословно попадает в blind warnings; значение скрыто/save запрещён | Статический безопасный diagnostic без unknown key |
| F-L7 | Design:1439 «Настоящий DOM», но tests/test_gold_web_ui.py:38 имеет no-op addEventListener | Назвать Node Element harness; не заявлять native browser coverage |
| A7-L1 | Frozen compare exists-only preflight: dangling JSON/MD → exit2, journal+1, независимость True→False, отчёта нет | Оба lexical имени и input aliases проверять до journal |
| A8-L1 | Close после PATCH при pending owned refresh оставляет видимый «Загрузка выборки…», хотя loading=False | Очистка только собственного законченного loading сообщения; не стирать новое сообщение |
| A9-L1 | brazil_explicit + country_required Kenya → open_to_brazil при dropped foreign restriction; рабочие gold не имеют human tags | При непереносимом компоненте не объявлять совмещённый hiring факт открытым |
| A9-L2 | gold_workbook.write_new_text(content,path) и cohorts.write_new_text(path,content) дублируются | Один publisher и единый порядок аргументов |
| A10-L1 | Design:973 по-прежнему говорит build_bundle v2-only, тогда как P2–P3 и §22 поддерживают v3 | Обновить устаревший абзац без изменения исторических отчётов |
| A10-L2 | `gates.py:24–40` превращает неизвестный legacy blocker (например company blacklist) в hiring_region_restricted; fresh `core-rule-reasons-results.json` | Отображать только распознанные причины, не выдумывать географию; исходный rule outcome сохранять |

Low evidence: `results.json`/`probes.py` в metrics TMP, A7-L1 —
`atomic-exposure-eyfsgx5v/results.json`. Численные метрики, данные и критерии не
предлагается менять ни одной из этих правок.

## 5. Признанные отклонения

| Отклонение из исходного аудита | Вердикт |
| --- | --- |
| company= в rules_relevance | Обосновано approved-company правилом; аргумент отражён в §6.5 |
| required_domain/basis в response | Обосновано отделением отрасли работодателя от требуемого опыта; присутствует в schema |
| duty/title ID в response_schema | Обосновано section constraints; P2 передаёт явные ID |
| label_scope в v3 | Сохраняет provenance v2, перенос 1:1 подтверждён |
| Tiger Analytics=1 через employer industry=pharma | Нельзя подменять employer industry требуемым доменом. Synthetic company+required-domain тесты не доказывают точную историческую human1; метка сохранена |
| Domain_fit по requirement/industry | Устарело: есть required_domain; частичные human populations раздельны |
| MAE/QWK/Spearman только covered pairs | Обосновано при явных N/coverage; missing остаётся в recall/exact/±1/P@k |
| Frozen build/load только v2 | После P2–P3 уже неверно; v3 поддержан, A10-L1 исправляет doc |
| LabelResult/TagValue отложены | Теперь реализованы в P2, отсутствие больше не заявляется |
| Cohort assisted=curated | Provenance adapter v2→v3, fresh canonical cohort read-only pass |
| root/nested proposed/confirmed | Рабочий YAML exact; новые runtime hash/P2 дефекты — A10-M2/H3 |
| Immutable reports вместо replace default | Защита оправдана; необходимость нового автоматического default имени — A9-M1 |

## 6. Покрытие тестами

До исправлений **3739 случаев**. Полный строгий аудиторский прогон:
**3714 passed, 3 failed, 22 errors за 49.23 с**. Все 25 непрошедших HTTP-сценариев
остановлены именно `Audit guard: all network and bind disabled`; это ограничение
проверки, а не 25 дефектов продукта. Ruff проходит. Число 3739 passed/60.66 с в §22
— результат предшествовавшей реализации с разрешённым loopback, не этого аудита.

| Группа | Свежая проверка / предел |
| --- | --- |
| P0 schema/rubric/aggregate/metrics/migration + Gold navigation/web harness | 501 passed, дополнительно 1000 независимых численных cases |
| P2 core/evidence/providers/budget/ledger | 94 passed; новые hard fact, proposed и interrupt probes выявляют пробелы |
| Pipeline/storage/CRM/frozen/profiles/tiering/purpose | 144 passed; confirmed gate/P1/queued alert, lifecycle и config-policy версии не покрыты |
| CLI/atomic/integration | 4 обязательные CLI exit0, export-sidecar/default/staging probes; preflight до sidecars отсутствует |
| P1 actual async JS | Свежие main pager и close-after-save пробы; Node API fake, не native DOM |
| Native HTTP/live models | HTTP заблокирован правилами аудита; paid/live не проверялись |

Группы пересекаются и не суммируются. Зелёные существующие тесты не исключили
подтверждённые новые интеграционные дефекты.

## 7. Готовность к следующим фазам

P0 сохраняет baseline и immutable исторические run, но средние/низкие дефекты writers
нужно закрыть. P1 требует A8-M1. Для приёмки P2–P3 нужны C1/H1–H3 и средние находки,
затем повторная проверка на временных данных. Сам факт их исправления не доказывает
качество модели, holdout acceptance или готовность к переключению рабочих категорий.

Предпосылки платного пилота остаются: человеческая доразметка/покрытие, утверждённые
прецеденты и зарегистрированные допустимые когорты, согласованный бюджет/выбранный
вариант, независимый holdout и гейты design §12. Strong companies не утверждать
самостоятельно. P4 вне поручения. Рабочие данные не доразмечиваются тестовыми моделями.

## 8. Правки design doc

- §0/§7: fixed default names + ручной новый путь → автоматический новый default,
  explicit output остаётся immutable.
- §4/§6.4: ID/секция/basis → дополнительные value-specific source checks, город
  São Paulo не выводится из названия штата/агломерации.
- §5–§6: hash effective confirmation payload; P2 only confirmed; pending ledger
  attempts имеют unknown usage; обозначить границу внезапного завершения процесса.
- §8–§9: confirmed labels hiring gate применяется в labels-mode; категории по
  relevance не зависят от найма; effective policy входит в evaluation version;
  same-source error fallback сохраняет наблюдение при observed=True.
- §6.7: v2-only bundle → актуальная schema-aware v2/v3 реализация.
- §18: «настоящий DOM» → actual handler на Node Element harness.
- §4.5: portable integer semantics → явная строгая runtime граница.

## 9. Команды и ключевые результаты

Все выходы — `/private/tmp/labels-v3-audit10-cfh9abur`. Основной guard/env описан §2.

```text
pytest -q -p no:cacheprovider --basetemp TMP/pytest-audit
ruff check src tests --no-cache
gold migrate-v3 --source data/local/gold/vacancy-tags-v2-labeled.jsonl --out TMP/migrated.jsonl
gold validate --gold TMP/migrated.jsonl --output TMP/validation.json
eval accuracy --variant rules --gold data/local/gold/vacancy-labels-v3.jsonl --output TMP/rules.json
eval accuracy --variant saved --gold data/local/gold/vacancy-tags-v2-labeled.jsonl --output TMP/saved.json
python TMP/integration.py
python TMP/root-sidecars.py
python TMP/root-extras.py
```

- Миграция: 171 строка; 55 с каким-либо human label, 44 полных решения, 10
  relevance-only; методы blind44/curated11/assisted_llm0. Решения/source/hash/text/date/
  label_scope/notes сохранены 1:1, assisted→curated; байты равны рабочему v3, tags0.
- Baseline N54, QWK0.373134, recall12/17, precision12/24, P@10=0.8; blind N44,
  recall7/7, precision7/19. Человеческие ответы из отчёта не цитируются.
- Три исторических frozen run проходят validate_run без перезаписи. Saved report:
  прежние 16 top-level ключей, вложенные evaluation/operational/stability/readiness/
  selection сохранены. P1:171 карточка, 44 complete,11 partial,116 unanswered,
 17 relevant,10 strong,6 tags; machine fields отсутствуют в blind detail.
- YAML2913bytes exact; rubric version labels-rubric-2026-10-20,
  prompt hash `8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8`,
  aggregation hash `9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f`.
- SHA snapshot 8285→8285 без изменений. `.venv` доступна, symlink ведёт в
  `/Users/admin/.venvs/hr-automation`. API/ключи/канонические exposure journals
  не использовались для записи. Исправления начинаются только после этого снимка.

## 10. Исправления и повторная проверка

Правки выполнены по прямому поручению пользователя после фиксации §1–§9 и
read-only снимка. Прежние аудиты №1–№9 не редактировались. Таблица ниже относится
к исправленному дереву; pre-fix severity и доказательства выше сохранены.

| Находка | Исправление и регрессионное доказательство |
| --- | --- |
| A10-C1 | Общий sqlite_input_paths защищает DB, WAL, SHM, rollback journal до модели и чтения. Шесть real CLI случаев: три sidecar через alias родителя × AI/offline; exit2, модель не вызвана, DB bytes/mtime прежние. `test_labels_audit10_atomic.py` |
| A10-H1 | Новый conservative proofs.py связывает hard value с точной цитатой, обязанностью работать в офисе, однозначным городом и конкретным языком. Unknown/contradictory/scope-negative факты понижаются в unspecified; кеш повторно проходит S3. Real tagger + fake stages не подтверждают Barueri как São Paulo и English как географию. Корректные Namibia/U.S./явные Brazil exclusions сохраняются. `test_labels_audit10_core.py` |
| A10-H2 | Общий apply_labels_hiring применяется до tier/purpose/alerts в active labels-mode и экспорте. Temp SQLite: REJECT/ineligible/relevance3, P1 unsuitable, P2 suitable, queued alerts0. Score/relevance/shadow/source-conflicts и validated-cache export проверены отдельно. `test_labels_audit10_pipeline.py`, `test_labels_audit10_runner.py` |
| A10-H3 | Unconfirmed P2 → p2_candidate=None; корневой proposed выключает все effective секции/платные решения/decision-cache. `test_labels_audit10_core.py` |
| A10-M1 | BaseException завершает начатую попытку с unknown usage; ledger.close сохраняет pending attempts, terminal не дублируется. Ledger metrics видит uncertain bill. Контракт не распространяется на SIGKILL. `test_labels_audit10_core.py` |
| A10-M2 | Prompt/aggregation hashes строятся из effective applied частей; root status соответствует prompt/cache. Текущие confirmed hashes прежние, редактирование anchors внутри proposed не влияет на пустой effective prompt. `test_labels_audit10_core.py` |
| A10-M3 | Fallback сохраняет принятую оценку и ручные решения, но observed=True атомарно обновляет last_seen/алиасы/complete-snapshot lifecycle, включая архивную запись без unarchive. `test_labels_audit10_pipeline.py` |
| A10-M4 | Labels evaluation versions включают effective final_policy, tier_source, rubric_status; legacy/shadow и facts keys не меняются. labels-aggregation-v2 отражает исправление proof/gate/нормализации, исторические run не переписаны. `test_labels_audit10_pipeline.py` |
| A10-M5 | Cleanup staging проверяет dev/inode, неизвестная принадлежность оставляет orphan, исходная ошибка сохраняется. Injected link failure с подменой staging сохраняет чужой файл. `test_labels_audit10_atomic.py` |
| A8-M1 | Main next/prev/filter reset используют requested offset; accepted page единолично фиксирует global offset. Open-old-card race больше не пропускает страницу. `test_labels_gold_navigation.py` |
| A9-M1 | Неявные defaults eval/validate/accuracy/rules-diff выбирают новое свободное UTC+unique имя, проверяя пару JSON/MD; explicit output остаётся immutable. Повторные реальные CLI и прежние bytes/mtime проверены. `test_labels_audit10_atomic.py`, `test_labels_accuracy_outputs.py` |
| R-L1 | Пунктуация нормализуется до удаления юридической формы; пять Acme-вариантов дают acme. Техническая версия агрегации v2. `test_labels_aggregate.py` |
| R-L3 | Design §23 и отдельная регрессия фиксируют: portable JSON Schema принимает mathematical integer2.0, runtime требует Python int. Шкала и schema snapshots не меняются. `test_labels_audit10_atomic.py` |
| R-L4 | Gold/migration проверяют lexical final до разрешения symlink. Dangling output/manifest/writer отказывают, target не создаётся. `test_labels_audit10_atomic.py` |
| R-L5 | Blind diagnostic неизвестного тега статический, ключ и значение скрыты. `test_labels_gold_ui.py` |
| F-L7 | Design §18 уточнён: actual handler в Node Element harness, native browser behavior не доказан |
| A7-L1 | Общий comparison_destinations проверяет JSON/MD и protected aliases до compare/exposure; dangling outputs/journal alias не создают exposure и сохраняют independent_holdout. Применён также к соседнему experiment-report; 8 отказов до анализа и happy path с одним exposure. `test_labels_audit10_atomic.py`, `test_labels_experiment_preflight.py` |
| A8-L1 | Loading-message ownership привязано к generation; close/settle снимают своё сообщение, сохраняют более позднюю ошибку. `test_labels_gold_navigation.py` |
| A9-L1 | Неизвестное country_required блокирует перенос combined hiring fact даже при brazil_explicit; manifest сохраняет обе dropped причины, human decisions 1:1. Таблица переноса v4. `test_labels_audit10_atomic.py`, `test_labels_gold_v3.py` |
| A9-L2 | Gold wrapper использует общий cohorts publisher и единый path-first порядок аргументов; вызовы CLI/UI и race tests обновлены. `test_gold_workbook.py` |
| A10-L1 | Устаревший v2-only абзац design §6.7 заменён актуальным schema-aware v2/v3 контрактом |
| A10-L2 | Только распознанные operational blockers переводятся в закрытый enum; company/timezone/unknown не получают выдуманную hiring-географию, rule ineligible сохраняется. `test_labels_audit10_core.py` |

Повторные четыре обязательные CLI завершились с exit0 под исходным строгим
аудиторским guard, с выходами только в `TMP/post-integration`. Миграция 171 строки
сохранила все исходные решения/source/notes/provenance 1:1 и побайтно равна рабочему
v3; manifest tag_map_version теперь v4. Baseline по-прежнему N54, QWK0.373134,
recall12/17, precision12/24, P@10=0.8. Канонический cohort выбран без exposure:
163 строки, blind N44, recall7/7, precision7/19, QWK0.433663. Три исторических
frozen run валидны; saved report имеет прежние 16 top-level ключей и структуру.
P1 read model и точные 2913 bytes YAML/hashes также прежние.

SHA-256 **7441 защищённого файла** (все config, рабочие data и аудиты №1–№9)
совпали с начальным manifest. Новых non-cache файлов в data/config нет. Один
исключённый из обоих manifests `data/local/eval/__pycache__/*.pyc` имеет birthtime
и mtime 10.10.2026 и не является выходом этой проверки. Рабочая SQLite не
мигрировалась, .env/ключи и live API не читались/не вызывались. Исторические
config/purpose_rubric.yaml и settings.yaml уже входили в исходную незакоммиченную
P2–P3 реализацию: в этой задаче ни один config не менялся.

Итоговый полный прогон исправленного дерева: **3869 passed за 64.38 с**, то есть
**130 новых регрессионных случаев** сверх 3739 на входе. Проверены loopback HTTP,
реальные временные SQLite/CLI и Node handlers; live модели и native browser DOM
не проверялись. Полный набор запускался с чистым env и отдельным fix-guard:
разрешены только localhost/127.0.0.1/::1, запрещены внешняя сеть, .env и запись
Python в репозиторий; рабочая SQLite допускается только mode=ro. Это разрешение
loopback относится к приёмке исправлений и не меняет строгие правила аудита §2.
Все 242 source/test Python+HTML файла совпали с manifest перед финальным прогоном.

Промежуточный полный прогон дал 3847 passed и 5 отказов только на ожидаемом тексте
immutable-output ошибки. Совместимый diagnostic восстановлен; адресный повтор
49 passed, затем выполнен указанный полный зелёный прогон. `.venv/bin/ruff check
src tests --no-cache` и `git diff --check` — pass.

Финальные evidence: `pytest-final.log`, `source-at-final.json`,
`final-verification.json`, `protected-before.json`, `post-integration/` в TMP §2;
UI independent before/after остаются в дополнительном metrics TMP. Сфокусированный
независимый review исправленных gate/pipeline и writers/CLI новых подтверждённых
дефектов в этих ветках не выявил. Все 22 подтверждённые находки §4 закрыты кодом,
регрессиями либо явно требуемым уточнением документации. Из них критических,
высоких и средних незакрытых — **0**.

Это приёмка исправлений реализации P0–P3 в проверенном объёме, а не принятие
качества live-разметки или включение production rollout. Остаются условия платного
пилота/holdout §7; P4, рабочая разметка и изменения пользовательских критериев
не выполнялись. Новые commit/push этой задачей не создавались.
