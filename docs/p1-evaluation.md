# Независимая оценка и измерения P1 аудита

Реализованы инженерные R04/R05 и измерительная часть R13. Команды ниже не обращаются
к платным API, не мигрируют SQLite и не меняют gold. Создание отчёта, sidecar,
sample и журнала — явные действия CLI. Существующие gold v2 и XLSX сохранены:
дополнительные поля происхождения **не добавляются** в закрытую строку gold.

## Когорты и защита holdout

`evaluation-cohort-v1` хранит `cohort_id`, `dataset_role`, `rubric_version`,
`created_at`, `members`. Каждый member привязан к `job_uid`, `source_hash` и
`duplicate_group`; могут сохраняться `original_labeled_at`, `label_method`,
`selection`, `url`. Роли: `development`, `calibration`, `curated`, `discovery`,
`seed_exposed`, `holdout`. Изменение исходного timestamp, метода или snapshot
требует нового набора. Слепая разметка сама по себе независимость не подтверждает.

```sh
PYTHONPATH=src .venv/bin/job-intake eval frozen cohort-create \
  --gold data/local/gold/new-blind.jsonl \
  --cohort-id independent-01 --dataset-role holdout \
  --rubric-version human-tags-v2 \
  --exposure-log data/local/evaluation/independent-01-exposure.jsonl \
  --provenance-review data/local/evaluation/provenance-review.json \
  --output data/local/evaluation/independent-01-cohort.json

PYTHONPATH=src .venv/bin/job-intake eval frozen cohort-register \
  --cohort data/local/evaluation/independent-01-cohort.json
```

`provenance-review.json` — подтверждённая человеком проверка прежнего использования,
например `{"reviewed_at":"2026-10-19T12:00:00Z","reviewed_by":"human",
"clean_history_confirmed":true,"prior_exposures":[]}`. Программа не проставляет
такое подтверждение сама. Для ранее использованных seeds/calibration оно неверно.
Без проверки истории и blind provenance gates остаются `insufficient_evidence`.

Глобальный append-only реестр по умолчанию:
`data/local/evaluation/cohorts.jsonl`. Он защищает holdout даже при выборе другого
sidecar для discovery. При регистрации проверяются пересечения UID, source hash,
URL и группы дублей с разработческими когортами. Одинаковые company/title/body
группируются независимо от технических метаданных; это защита от утечки, а не
перенос человеческих меток между региональными версиями. Реестр проверяет цепочку
хешей; повторная регистрация того же набора идемпотентна.

Planner/selector используют `validate_seed_usage` и глобальную
`validate_registered_seed_usage`. Holdout и его зеркала запрещены как позитивы,
негативы и prompt demos. Передача самого проверяемого исходника для inference
разрешена. `record_exposure` записывает тип использования и actor без изменения
sidecar. Анализ отчёта разработчиком — `developer_analysis`: после него набор
больше не считается независимым для следующего выпуска. `compare` и
`experiment-report` требуют journal и регистрируют это действие. `inference`
сам по себе независимость не снимает. Оригинальные даты меток сохраняются.

## Неизменяемый bundle и сравнение

Перед запуском объявляются версии обоих вариантов, например `versions.json`:
`{"baseline":"policy-before","candidate":"policy-after"}`. Они могут быть Git
commit/config digests; CLI не подменяет их текущим состоянием после измерения.

```sh
PYTHONPATH=src .venv/bin/job-intake eval frozen freeze \
  --gold data/local/gold/new-blind.jsonl \
  --cohort data/local/evaluation/independent-01-cohort.json \
  --versions data/local/evaluation/versions.json \
  --output data/local/evaluation/independent-01-bundle.json

PYTHONPATH=src .venv/bin/job-intake eval frozen run \
  --bundle data/local/evaluation/independent-01-bundle.json \
  --variant baseline --version policy-before --run-id baseline-01 \
  --output data/local/evaluation/baseline-01.json
```

Bundle фиксирует IDs, полный raw/clean/source facts, сохранённые annotations,
saved predictions, исходные labels, cohort, policy и версии вариантов. Bundle ID
защищает всё содержимое. Файлы создаются исключительно как новые; повторная запись
отклоняется. Новый gold даёт новый bundle. Архивные jobs включаются явно.
Смена текущей БД после freeze на старый bundle не влияет.

`freeze --source-snapshot` позволяет прочитать gold source snapshot при отсутствии
строки SQLite. Условия: явный флаг, валидный snapshot и точный исходный hash.
Отсутствующий saved prediction остаётся `null`; он не восстанавливается из метки.
Неверная схема/ошибка доступа к БД не скрывается fallback. Изменившийся источник
не получает старую человеческую метку автоматически.

Перенос времени сбора/ошибок загрузки в `collection_observations` не требует
переписывать человеческие pins. Совместимость доказывается точным старым public hash
по сохранённому `source_superseded` / `annotation_superseded` snapshot или точным
обратимым переносом observations, затем равенством **всех** source facts, identity,
status и units. Значимые metadata (включая географию/язык) остаются в сравнении;
даже разница пробелов текста не нормализуется. Недоказуемый случай остаётся drift.
Provenance возвращается отдельно в eval/bundle/UI, gold/XLSX исходные hash, snapshot
и timestamp не меняют. В bundle current source hash и legacy label pin могут
различаться только при наличии проверяемого доказательства совместимости. Слепой
UI не выводит произвольные metadata исторического снимка.

Для нового варианта передаются записанные predictions JSONL:
`job_uid`, `source_hash` обязательны; отдельные `role_label`, `gate_outcome`,
`decision`, `tier`, `fit_score`, `route`, `tags` отражают соответствующие задачи.
Missing outputs остаются пропусками. `run` без `--predictions` читает только
замороженные saved predictions и не запускает новую модель.

```sh
PYTHONPATH=src .venv/bin/job-intake eval frozen run \
  --bundle data/local/evaluation/independent-01-bundle.json \
  --predictions data/local/evaluation/candidate-predictions.jsonl \
  --variant candidate --version policy-after --run-id candidate-01 \
  --ledger data/local/evaluation/candidate-attempts.jsonl \
  --prices data/local/evaluation/prices.json --wall-seconds 17.3 \
  --output data/local/evaluation/candidate-01.json

PYTHONPATH=src .venv/bin/job-intake eval frozen compare \
  --bundle data/local/evaluation/independent-01-bundle.json \
  --baseline data/local/evaluation/baseline-01.json \
  --candidate data/local/evaluation/candidate-01.json \
  --repeat data/local/evaluation/candidate-02.json \
  --cohort data/local/evaluation/independent-01-cohort.json \
  --exposure-log data/local/evaluation/independent-01-exposure.jsonl \
  --output data/local/evaluation/comparison-01.json
```

Gate policy `audit-quality-gates-v2` замораживается до измерения. Экспериментальные
защитные defaults из плана аудита: минимум 160 decision labels, 40 relevant,
60 full-tag cases, 30 blind decisions; для сравниваемой задачи требуется полное
покрытие. Допустимая регрессия accuracy/recall и рост false block — 0,
cost/time ratio не выше 1.0. Это не согласованные пользователем новые продуктовые
допуски и не доказательство достаточной статистической мощности. Другие допуски
передаются JSON через `freeze --policy`, включая ту же версию policy. Старые
неизменяемые bundles v1 остаются читаемыми. Выпуск v2 создаёт новый bundle,
не переписывая старую политику или уже записанные результаты. Это защитные
допуски измерительного протокола, не новые продуктовые пороги score.

Каждый gate имеет `pass|fail|insufficient_evidence|not_applicable`, N, numerator /
denominator и minimum N. Нет полных predictions, независимой когорты, актуальной
цены или повторного запуска — соответствующий gate не пройден. Пустая метрика
имеет `null`; отсутствие labels задачи — `not_applicable`. Результат не разрешает
автоматически включить вариант. Малый N сопровождается Wilson CI, без заявления
доказанной экономии или отсутствия регрессии всего рынка.

Отдельно измеряются профессия, роль, найм и доступный результат:

- role recall не снижается из-за правильного hiring REJECT недоступной вакансии;
- actionable P1 precision/recall требуют человеческой релевантности, human-eligible
  и соответствующего машинного допуска; `unknown` остаётся отдельной стратой;
- blocker recall — по human-ineligible, false block — по human-eligible;
- coverage для decision/profession/eligibility/route/ranking/каждого тега выводится раздельно;
- routing имеет собственные N/predicted/missing/status и IDs пропусков: PASS, tier
  и профессия не подставляют отсутствующий результат маршрутизации;
- `other` не требует fit/rank; NDCG@5/10 имеет воспроизводимую random baseline
  (256 перестановок, seed 1729), а неполные ranks явно видны.

Назначение карьерного потока P1/P2 хранится отдельно; эти метрики не преобразуют
автоматически A/B в назначение человека.

Обычные `eval accuracy --cohort PATH [--source-snapshot]` и
`scripts/compare_role_classifier.py --cohort PATH` поддерживают sidecar. Независимые
release gates используют frozen runner; обычное чтение saved baseline не требует
завершённого платного эксперимента.

## Слепые audit/top-k выборки и спорные ответы

`eval frozen sample --frame frame.jsonl --quotas quotas.json --seed 1729
--mode audit --output sample.json` сохраняет весь frame (`job_uid`, `source_hash`,
`stratum`), seed, исходный hash, population N, selected N и вероятность включения
**каждого** элемента. Quotas — объект `{"accepted":10,"rejected":10,"uncertain":5}`.
Для top-k нужны `--mode top_k` и frozen `rank`; исключённые элементы имеют p=0.

`population_estimates(sample, labels)` считает design-weighted precision/recall
через Horvitz–Thompson totals. Recall всего frame недоступен, если хотя бы у одной
страты p=0, не размечена часть sample или потерян snapshot. По accepted-only можно
измерить precision, но нельзя заявлять recall selector. Market recall из такого
frame не следует. Бюджет/ошибка fetch сами по себе отрицательной меткой не являются.

`eval frozen adjudicate --gold gold.jsonl --disputes disputes.jsonl --output queue.json`
создаёт очередь с исходным ответом, его hash, причиной и отдельным предложением.
`resolve_adjudication` возвращает новую версию очереди с actor/timestamp/history;
проверяет snapshot и closed gold schema. Исходный gold не переписывается, предложение
модели не становится ответом человека.

## Предобъявленные эксперименты и стоимость

`eval frozen experiment-declare --bundle bundle.json --specification experiment.json
--output declaration.json` объявляет experiment_id, один factor, два варианта,
budget_usd, цели, minimum_repeats (не меньше 2 на вариант), максимальный возраст
тарифа. Пример факторного набора:

```json
{
  "experiment_id": "review-thinking-01",
  "factor": "review_thinking",
  "variants": {
    "baseline": {"review_thinking": "low", "chunk_chars": 3000},
    "candidate": {"review_thinking": "none", "chunk_chars": 3000}
  },
  "budget_usd": 0.50,
  "goals": {"scope_accuracy": "no regression", "cost_ratio": "<=1.0"}
}
```

Одновременная смена compact payload/chunk size/thinking отклоняется. Допустимы
также отдельные prefilter/reason-priming/independent-verify и search policy факторы.
Сам runner ничего не покупает: он проверяет записи реальных запусков, полученные
существующими provider/service средствами проекта. Задним числом объявить эксперимент
поверх записанных runs нельзя. Для свежего платного эксперимента нужны реальные
attempt timestamps после declaration, полная телеметрия и предоставленный бюджет;
в этом выпуске такие эксперименты не выполнялись.

Attempt ledger: уникальный `call_id`, `model`, `attempt` (с 1), `requested_at` с timezone,
`wall_seconds`, `usage` с `input_tokens`, `output_tokens`, необязательными
`thinking_tokens`, `cached_input_tokens`. Output уже включает thinking, input —
cached input: повторного сложения нет. Cache reuse — отдельная строка с
`cache_reuse:true`, `original_call_id`; повторной оплаты нет. Неизвестная usage,
timeout без usage или отсутствующий тариф оставляют `uncertain_bills`.

Prices JSON использует `eval-prices-v1`, USD, `as_of`, models и
input/output/cached-input rates per million. Будущий или просроченный тариф не
подтверждает экономию. Стоимость — оценка по сохранённому тарифу, не счёт провайдера.
Все attempts и retries включаются. `attempt_seconds` — сумма времени вызовов;
настоящий wall time передаётся отдельно, поэтому параллельность не создаёт фиктивное
ускорение. Отсутствующий wall time не проходит time gate.

`eval frozen experiment-report --bundle bundle.json --declaration declaration.json
--runs baseline-01.json --runs baseline-02.json --runs candidate-01.json
--runs candidate-02.json --exposure-log exposures.jsonl --output result.json`
сравнивает все записанные пары и независимые repeats. Цена/время на подтверждённый
результат считаются по реально сохранённым предсказаниям с human-positive; отвергнутый
позитив или пропуск не увеличивает знаменатель успеха. Для доступного P1 дополнительно
требуется подтверждённая доступность. При нулевом успехе — `null`, а не $0/result.
Дублированные run IDs, другой bundle, изменённая declaration и незаявленные версии
отклоняются. Измерения разных вариантов не складывают экономию без общего опыта.

Фактические ограничения: старые calibration и `next-5` не получили выдуманных
holdout labels. Независимая человеческая выборка, завершение текущей разметки и
настоящие платные/повторные прогоны остаются необходимыми данными для quality gates.
