# Четырнадцатый аудит labels-v3: полный P0–P3 — 20.10.2026

## 1. Итог

**P0–P3 реализованы.** Главные защитные контракты подтверждены независимыми
проверками на копиях рабочих данных:
- выключенный labels-режим даёт бит в бит прежний результат;
- обычные команды не делают платных вызовов даже при `ai_enabled: true`;
- неудачная разметка не понижает принятые оценки;
- shadow не трогает score, tier, decision и purpose.

Найдено **0 критических, 0 высоких, 2 средних и 1 низкая** новые находки. Все четыре
находки моего аудита №9 (A9-M1, A9-L1, A9-L2, A8-M1) закрыты; аудитор проверил это сам.

| ID | Уровень | Суть |
| --- | --- | --- |
| A14-M1 | средняя | `label --export --gold` отказывает на всех 171 строке рабочего v3-gold: снимок `source` никогда не воспроизводит `source_hash` |
| A14-M2 | средняя | Проверка доказательств S3 отбрасывает частые явные формулировки найма и офиса; обещание §23 о явном исключении Brazil выполняется частично |
| A14-L1 | низкая | `config/purpose_rubric.yaml` описывает только правило P2 v2, а режимы по умолчанию применяют v1 |

**Готовность.** Код готов к платному пилоту P2 по бандлу (`--bundle`). Перед пилотом
нужно решить A14-M2: иначе пилот недооценит вклад модели в gate, потому что верные
факты будут понижены до `unspecified`. Переключение `mode: labels` и
`tier_source: relevance` остаётся решением пользователя после гейтов.

**Независимость ограничена.** P0 написал этот же аудитор, а P1–P3 и исправления
аудитов №1–13 — другие исполнители.

## 2. Объём и метод

- Ветка `feature/vacancy-tags`, HEAD `e61d483` (P0–P1). P2–P3 и исправления аудитов
  №10–13 находятся в незакоммиченной рабочей копии: 40 изменённых и 23 новых файла
  исходников и тестов.
- Снимок дерева (SHA-256 diff и untracked-исходников) и SHA-256 рабочей SQLite, всех
  `data/local/gold/*.jsonl` и журнала экспозиций совпали до и после аудита. Каталога
  `data/local/labeling` нет: реальных вызовов, кеша и ledger не было.
- Все запуски CLI выполнялись через собственный guard (`sys.addaudithook`). Он
  запрещает `socket.connect/getaddrinfo/bind/sendto` для всего, кроме Unix-сокетов.
  Ключи API были сняты с окружения, каталоги без `.env`, базы — копии во временном
  каталоге.
- Прочитаны design doc §0–§12 и §22–§25, README, `prompt.py`, `gates.py`, `budget.py`,
  ключевые части `tagger.py`, `validate.py`, `proofs.py`, `runner.py`. Изучены diff
  `pipeline.py`, `profiles.py`, `purpose.py`, `tiering.py`, `ai.py` и
  `storage/database.py`.
- Отчёты №10–13 прочитаны только для сверки новизны, после собственных проверок.
- Не проверялись: платный путь с настоящим провайдером (пилот не разрешён), нативный
  браузер. Материалы — каталог `scratchpad/audit14` этой сессии.

## 3. Ключевые независимые проверки

| Проверка | Метод | Результат |
| --- | --- | --- |
| §0.9 выключенный labels = прежнее поведение | `reevaluate --include-archived`: код `8bb3a2a` (до labels-v3) и рабочая копия, каждый на своей копии БД, под guard | 532/532 вакансии: все общие колонки всех таблиц совпали, кроме `jobs.updated_at`. Три новые колонки добавлены и пусты; новых таблиц нет |
| Нет платных вызовов вне `label --ai --max-cost` | Копия: `enabled: true`, `mode: labels`, `ai_enabled: true`, `tier_source: relevance`; обычный `reevaluate` под guard | Ни одной сетевой попытки. Без бюджета `_stage` бросает `budget_required` (`tagger.py:272`) |
| §0.7 сбой/пустой кеш не понижает | Тот же прогон против legacy-результата | 0 переходов категории. 483 вакансии получили только `labels_v3_attempt=rules_only`; 49 бесплатно отсечены гейтом title (relevance 0, роль `other`, C без изменения решения; добавлен риск `labels_v3:hiring_unknown`) |
| Shadow не меняет legacy | Копия, `enabled: true`, `mode: shadow`; команда `label` | 58 активных вакансий: изменились только `annotation` (`labels_v3`) и `updated_at`; `job_purpose_assessments` не изменилась |
| Сквозной P2 офлайн | `eval frozen freeze` v3-gold → `label --export --bundle` → `eval accuracy --variant labels` | Бандл 171, экспорт 142 rules_only + 29 gated, БД неизменна. Без модели N=54, recall≥2 0/17: так и должно быть |
| `--ai` без `--max-cost` | CLI | Отказ `--ai requires --max-cost` |
| Тесты | `.venv/bin/pytest -q`, `ruff check src tests` | 4182 passed за 71 с (loopback разрешён); ruff без замечаний |
| Закрытие №9 | Прямые вызовы и код | A9-L1: `migrate_tags` с неизвестной страной → `{}`. A9-L2: обе `write_new_text(path, content)`. A8-M1: обработчики передают смещение (`index.html:2093–2094`). A9-M1: повторный `eval` дал `latest-<UTC>-<id>.json` |

## 4. Трассировка требований P0–P3

| Требование | Статус | Доказательство |
| --- | --- | --- |
| §0.1 Критерии/веса/stable ID | Реализовано | `git diff 8bb3a2a -- config/`: rules/search_profiles без изменений; settings — только блок `labeling` с `enabled: false`; purpose — A14-L1 |
| §0.2 Платные вызовы только по команде | Реализовано | Проверки §3; `label --ai` требует `--max-cost` |
| §0.4 Gold не перезаписывается | Реализовано | Хеши gold неизменны |
| §0.5 Eval/экспорт read-only | Реализовано | `label --export`: `database_access: read_only`, хеш копии БД неизменен |
| §0.6 Аддитивные миграции | Реализовано | `storage/database.py:60–62`, `:94–96`: три nullable-колонки; дифф-проверка — данные прежние |
| §0.7 Сбой не понижает | Реализовано | Проверка §3 |
| §0.8 Текст вакансии недоверенный | Реализовано | `prompt.py` POLICY и метка `SOURCE (untrusted data)` |
| §0.9 Прежнее поведение по умолчанию | Реализовано | Дифференциальная проверка §3 |
| §4/§5 Схема, рубрика, хеши | Реализовано | Тесты; рубрика — точный блок §5.2 |
| §6.1 Gates | Реализовано | `gates.py`: closed/needs_fetch/too_long/title; блокер правил не останавливает |
| §6.2 Промпт | Реализовано | Детерминированный JSON (`sort_keys`), без весов и `notes`; таксономия, подтверждённая рубрика |
| §6.3 Регистрация схем | Реализовано | `ai.py`: ветки labels в Gemini/Anthropic до fallback; OpenAI strict `json_schema` |
| §6.4 S3 | Частично | Защитная сторона верна; полнота — A14-M2 |
| §6.5–§6.6 Агрегация, теггер, кеш | Реализовано | Тесты; офлайн-экспорт |
| Бюджет и ledger | Реализовано | `budget.py`: резерв до каждой попытки, `price_missing` до вызова; ledger — тесты (аудиты №12–13) |
| §7 CLI `label` | Частично | `--bundle`, `--job-uid`/`--limit`, shadow-запись работают; `--gold` — A14-M1 |
| §8 Pipeline | Реализовано | Проверки §3 |
| §9 Хранение | Реализовано | Колонки, `labels_v3`, `labels_v3_attempt` |
| §10 CRM/Gold UI | Реализовано в проверенном объёме | Тесты; A8-M1 закрыта; нативный браузер не проверялся |
| §12 P3: `enabled: false` бит в бит | Реализовано | Дифференциальная проверка §3 |

## 5. Находки

### A14-M1 — средняя: `label --export --gold` не работает на реальном gold

**Доказательство (проверено).** `label --export … --gold data/local/gold/vacancy-labels-v3.jsonl`
завершается отказом «Gold snapshot does not match its source_hash» (`runner.py:57–60`).
Прямая сверка показала: у **всех 171** строк `source_hash(record_from_snapshot(row["source"]))`
не равен `row["source_hash"]`. Снимок `source` — это whitelist `gold._snapshot`, в который
входят только три поля `source_metadata`. А `source_hash` считается по полной записи.
Значит, для строк, созданных `gold sample` или `gold migrate-v3`, совпадения не бывает
никогда. Тесты используют синтетические строки, где снимок полон.

**Последствие.** Путь из §7 и README:672 на реальных данных не работает, а сообщение
наводит на мысль о порче данных. Обход через `eval frozen freeze` → `--bundle`
работает, и именно он стоит в примере README.

**Рекомендация.** Либо убрать `--gold` из `label --export` и ссылаться только на бандл,
либо при `--gold` брать источник из SQLite read-only с проверкой хеша, как делает
`build_bundle`. Сообщение об ошибке должно называть причину и предлагать `--bundle`.

### A14-M2 — средняя: S3 понижает частые явные формулировки найма и офиса

**Доказательство (проверено).** Собственные 19 + 15 + 8 проб. Модель утверждает факт и
цитирует нужный unit, затем результат проходит `validate_tags`. Ожидания заданы до
запуска.
- **Защитная сторона безошибочна.** Отброшены все ложные утверждения: офис HQ как
  ограничение найма, «preferred», часовой пояс, «may visit», Кампинас как São Paulo,
  язык «a plus», опыт кандидата как обязанность.
- **Явные факты понижены до `unspecified`.**
  - «This position is not open to candidates in Brazil», «Candidates in Brazil are not
    eligible for this role» и «Open to candidates worldwide except Brazil» дают
    `conditional_evidence` или `unsupported_evidence`. Это противоречит §23 («Явное
    исключение Brazil распознаётся как отрицательный факт»). Распознано только «We
    cannot hire candidates located in Brazil».
  - «We hire remote employees across LATAM, including Brazil», «Open to candidates
    worldwide», «This role is open to candidates based in the US or Canada» и «We can
    only hire in the US, UK and Germany» понижены. По §4 оптимального дизайна явные
    Brazil/LATAM/global — это `open_to_brazil`.
  - Структурные `location_text` «Brazil (Remote)», «LATAM» и «United States (Remote)»
    не принимаются как доказательство найма.
  - Офис: «…3 days per week in our São Paulo office» и «…in our London office» дают
    `location_mismatch`. `_OFFICE_PLACE` (`proofs.py:126`) захватывает хвост
    предложения («our sao paulo office»), и город не распознаётся. При этом «based in
    São Paulo.» и «from our São Paulo office» проходят.

**Последствие.** Ошибка безопасна: вместо ложного блока получается `unknown`. Но вклад
LLM в `gate_outcome` будет в основном `unknown`, blocker recall и `eligible` для P1 —
низкими, а пилот недооценит модель. Это не ошибка критериев: §13 не меняется.

**Рекомендация.** До пилота собрать корпус фраз о найме и офисе из 54 размеченных
вакансий gold и измерить долю сохранённых фактов S3. Расширить распознавание явных
исключений («not open to / not eligible / except Brazil») и включений
(«including Brazil», «worldwide/global»). Извлекать город по таблице, а не хвостом
предложения. Решить, доказывает ли `location_text` найм.

### A14-L1 — низкая: `purpose_rubric.yaml` описывает неактивное правило P2

**Доказательство.** Файл заменил текст `p2` v1 формулой labels-v3 и сменил `version` на
v2 (§22 даёт таблицу «было/стало»). Но в режимах по умолчанию (legacy и shadow)
`purpose.py` применяет `LEGACY_PURPOSE_RUBRIC_VERSION` v1. Дифференциальная проверка
подтвердила, что purpose не изменился. YAML кодом не исполняется и входит только в хеш
provenance ремонтов (`storage/repair.py:148`).

**Последствие.** Конфиг больше не документирует действующее правило P2. По AGENTS
config — место критериев, так что читатель увидит не то правило, которое работает.

**Рекомендация.** Хранить в YAML оба правила с режимом применения (v1 — legacy/shadow,
v2 — labels) или вернуть v1 до переключения режима.

## 6. Покрытие тестами

4182 теста, около 1060 из них по labels, проходят. Пробелы:
- нет теста `label --export --gold` на строке, созданной `gold sample`/`migrate-v3` (A14-M1);
- нет корпусного теста полноты S3 на реальных формулировках, есть лишь отдельные
  позитивные контроли (A14-M2);
- дифференциальная проверка «enabled: false = код до labels-v3» существует только в
  виде сравнения сериализации в тестах, а не прогона двух версий кода на одной базе.
  Этот аудит выполнил такой прогон.

## 7. Готовность

- **P0–P1:** приняты; A8-M1 закрыта.
- **P2:** механика готова — бюджет, ledger, кеш, экспорт по бандлу. Перед пилотом
  нужны A14-M2 (полнота S3) и решение по A14-M1. Пилот — только по команде
  пользователя с `--max-cost`, общий лимит $3.
- **P3:** безопасна при текущих настройках. Shadow можно включать, он не трогает legacy.
  Режим `labels` с `tier_source: relevance` даёт осмысленный результат только после
  пилота: без модели relevance почти везде 1, и `rules_only` тогда не управляет
  категорией.
- **Перед первым запуском `run`/`crm serve` текущим кодом** рабочая SQLite получит три
  nullable-колонки в двух таблицах. Миграция аддитивная, данные не меняются (проверено
  на копии), но откатить её можно только из резервной копии.

## 8. Предлагаемые правки документации

| Где | Было | Стало |
| --- | --- | --- |
| design doc §7, README:672 | `--gold` как источник экспорта | Указать, что строки `gold sample`/`migrate-v3` не содержат полного снимка; основной путь — `--bundle` (или исправить код по A14-M1) |
| design doc §23 | «Явное исключение Brazil распознаётся» | Перечислить поддержанные формулировки и границы, пока S3 не расширен (A14-M2) |
| `config/purpose_rubric.yaml` | Только p2 v2 | Оба правила с режимом применения (A14-L1) |

## 9. Приложение: команды

```text
guard.py: sys.addaudithook запрещает socket.connect/getaddrinfo/bind/sendto (кроме AF_UNIX)
reevaluate --include-archived  [8bb3a2a vs рабочая копия, копии БД]  → 532/532, diff только jobs.updated_at
reevaluate  [enabled, labels, ai_enabled, tier_source=relevance]     → 0 tier transitions, 483 attempts, 49 gated
label  [enabled, shadow]                                              → 58: только annotation и updated_at
label --export --gold vacancy-labels-v3.jsonl                         → «Gold snapshot does not match its source_hash»
eval frozen freeze → label --export --bundle                          → 171: 142 rules_only, 29 gated; read_only
eval accuracy --variant labels                                        → N=54, QWK 0.053, recall≥2 0/17 (офлайн)
label --export … --ai (без --max-cost)                                → «--ai requires --max-cost»
validate_tags: 42 состязательные пробы                                → 0 ложных фактов принято; истинный факт понижен
                                                                        в 21 пробе (из них 4 — строки под нераспознанным
                                                                        заголовком «Location» унаследовали секцию company,
                                                                        возможный артефакт шаблона пробы)
pytest -q / ruff                                                      → 4182 passed / All checks passed
shasum (SQLite, gold, exposures), снимок дерева                       → совпали
```
