# Маршруты работы

Срез: **19 октября 2026**. Здесь — выбор сценария и команды; устройство системы —
в [карте проекта](project-map.md), термины — в [сущностях](entities.md), точки входа
кода — в [методах](methods.md). [Вернуться к навигации](index.md).

Все команды выполняются из корня репозитория. Нужны Python 3.11+ и подготовленная
`.venv`: [установка](../README.md#запуск-из-папки-проекта). Префикс `PYTHONPATH=src`
позволяет использовать текущий checkout, в том числе когда пакет ещё не установлен
в editable-режиме. Относительный путь к SQLite зависит от текущей папки;
`JOB_INTAKE_DATABASE_URL` из окружения/`.env` может переопределять конфигурацию.

## Выбрать маршрут

| Задача | Маршрут | Основной результат |
|---|---|---|
| Понять команды и настройки поиска | [Знакомство](#знакомство-без-записи) | Справка, действующие profile ID и запросы |
| Прочитать имеющиеся данные | [Чтение](#прочитать-данные-без-миграции) | Готовый отчёт либо SQLite read-only |
| Получить свежие вакансии | [Сбор](#собрать-и-проверить-вакансии) | Исходники и оценки в рабочей SQLite |
| Обновить оценки сохранённых вакансий | [Переоценка](#переоценить-или-разметить-сохранённое) | Новые машинные результаты |
| Выбирать вакансии и вести отклики | [CRM](#просмотреть-выгрузки-и-вести-crm) | Файлы просмотра и история откликов |
| Проверить качество системы | [Gold и evaluation](#проверить-разметку-и-качество) | Человеческие метки и отчёты |
| Искать по подтверждённым примерам | [Discovery](#discovery-от-примеров-к-кандидатам) | Отдельная партия поиска и разметки |
| Проверить источники или восстановить оценки | [Обслуживание](#источники-и-восстановление) | Диагностика либо проверяемый repair |

## Знакомство без записи

**Эффект: чтение кода/конфигурации; без SQLite, сети и Telegram.**

```bash
PYTHONPATH=src .venv/bin/job-intake --help
PYTHONPATH=src .venv/bin/job-intake profiles --config config/settings.yaml
PYTHONPATH=src .venv/bin/job-intake discovery --help
PYTHONPATH=src .venv/bin/job-intake eval frozen --help
```

Для флагов конкретного действия добавьте `--help`, например `crm serve --help`.
Вывод `profiles` показывает исполняемые настройки поиска. Он не заменяет принятые
карьерные решения: их маршрут задан в [AGENTS.md](../AGENTS.md#карьерный-контекст).
Назначения P1/P2 отличаются от профессии, балла и категории A/B/C.

## Прочитать данные без миграции

Для обычного чтения сначала используйте готовые CSV/HTML и отчёты по маршруту
[моста с карьерной базой](../../aramais-vault/codex/projects/career-orientation/codex/hr-automation.md).
HTML — снимок на момент создания, а не живое представление SQLite.

Если нужен SQL, сначала подтвердите путь к рабочей базе через конфигурацию и
окружение, затем открывайте именно её с `mode=ro` и `PRAGMA query_only=ON`.
Реализованный безопасный читатель — `annotation.eval_runner.read_saved_records`.
Для проверки сохранённых данных есть [read-only `eval`](#проверить-разметку-и-качество).

**Создание выгрузки и запуск CRM не являются чистым чтением базы.**
`build_pipeline()` и `CRMService` вызывают `Database.create_schema()`: отсутствующие
таблицы создаются, к существующей SQLite могут применяться лёгкие миграции.
То же относится к `crm export-csv`. Для чтения личных данных не запускайте эти
действия только ради просмотра. [Подробности хранения](architecture.md#хранение-и-устойчивость).

## Собрать и проверить вакансии

**Эффект: сеть к включённым источникам; запись SQLite и возможная миграция;
AI API и Telegram зависят от настроек.**

```bash
PYTHONPATH=src .venv/bin/job-intake run --config config/settings.yaml
```

`run` получает вакансии из источников, сохраняет исходники, определяет профессию,
считает оценку и при включённых настройках обрабатывает уведомления. Это отдельное
действие от `discovery run`. Ошибка одного источника не откатывает успешные записи;
ненулевой код завершения требует чтения перечисленных ошибок. Счётчик `persisted`
не означает, что все источники отработали успешно. Встроенного расписания нет.

Для выбранных писем используйте `import-email` с каталогом `.eml` или
`import-gmail` с подготовленным JSON-снимком; формат и эффекты описаны в
[README](../README.md#вакансии-из-gmail). Сам CLI `import-gmail` не читает почту.

## Переоценить или разметить сохранённое

| Команда | Когда нужна | Эффект |
|---|---|---|
| `reevaluate` | Пересчитать текущие правила на сохранённых исходниках | Запись SQLite, возможная миграция; без fetch, API и Telegram |
| `annotate` | Построить локальную разметку исходного текста и пересчитать оценку | Запись SQLite, возможная миграция; без fetch, API и Telegram |
| `annotate --ai` | Извлечь признаки и независимо проверить их моделями | Запись SQLite, возможная миграция, платные модельные API; без fetch источников и Telegram |

```bash
# Вся активная база: offline-переоценка.
PYTHONPATH=src .venv/bin/job-intake reevaluate --config config/settings.yaml

# Локальная разметка ограниченной выборки сохранённых вакансий.
PYTHONPATH=src .venv/bin/job-intake annotate --config config/settings.yaml --limit 10 --verbose

# Платный модельный этап; ключи и модели задаются настройками.
PYTHONPATH=src .venv/bin/job-intake annotate --config config/settings.yaml --ai --limit 10 --verbose
```

`reevaluate` может использовать доказанно совместимые сохранённые AI-результаты,
но не заказывает новые. `annotate` принудительно включает разметку, а без `--ai`
использует локальное извлечение. Оба маршрута пересчитывают итоговые оценки;
человеческую разметку они не подменяют. Для точного набора у `annotate` можно
повторять `--job-uid JOB_UID`; вместе с `--limit` этот параметр не применяется.
Архив пропускается по умолчанию; у `reevaluate` есть явный `--include-archived`.

Изменение критериев — отдельная работа по [правилам проекта](../AGENTS.md).
Здесь критерии не изменяются. Объяснение каскада, кешей и моделей —
[README](../README.md#разметка-текстов-с-опорой-на-источник) и
[архитектура](architecture.md#предварительный-каскад-перед-ai-разметкой).

## Просмотреть выгрузки и вести CRM

**Создание файлов: запись CSV/HTML, возможная миграция SQLite; без сетевых вызовов
и Telegram.** CSV по умолчанию широкий, `--shortlist` оставляет A/B;
`--include-rejected` включает явно отклонённые вакансии. Архив не выгружается.

```bash
PYTHONPATH=src .venv/bin/job-intake export-csv --config config/settings.yaml --output data/local/jobs.csv
PYTHONPATH=src .venv/bin/job-intake render-html --config config/settings.yaml --output data/local/review.html --limit 500
```

Откройте полученный HTML в браузере. Фильтры работают в пределах выгруженных
записей, для них нужен JavaScript. После изменения базы пересоздайте отчёт.

**CRM: запуск локального HTTP-сервера, возможная миграция SQLite; действия
интерфейса записывают отклики, архив и человеческие решения.**

```bash
PYTHONPATH=src .venv/bin/job-intake crm serve --config config/settings.yaml
```

Адрес — [http://127.0.0.1:8765](http://127.0.0.1:8765), остановка — `Ctrl+C`.
Во вкладке вакансий выберите вариант и создайте отклик; далее ведите этап,
контакты, следующее действие и срок. Архив вакансии и этап «Архив» отклика независимы.
Статусы и следующие действия имеют единственный канон в HR SQLite.
[Сценарии CRM](../README.md#crm-откликов-и-рефералов),
[назначения P1/P2](p1-data-operations.md#два-назначения-в-crm).

Историю JobCRM сначала проверьте без записи, затем импортируйте явно:

```bash
# Чтение XLSX без записи в SQLite; исходный файл сохраняется.
PYTHONPATH=src .venv/bin/job-intake crm import-xlsx /path/to/JobCRM.xlsx --dry-run

# Запись SQLite и возможная миграция; без сети и Telegram.
PYTHONPATH=src .venv/bin/job-intake crm import-xlsx /path/to/JobCRM.xlsx --config config/settings.yaml

# Запись CSV и возможная миграция SQLite; без сети и Telegram.
PYTHONPATH=src .venv/bin/job-intake crm export-csv --config config/settings.yaml --output data/local/crm.csv
```

## Проверить разметку и качество

**`eval`: SQLite read-only, запись JSON-отчёта; без миграций, пересчёта, API
и Telegram.** Он проверяет целостность сохранённых доказательств, а не устанавливает
правильность смысла без человеческих ответов.

```bash
PYTHONPATH=src .venv/bin/job-intake eval --all --output data/local/eval/integrity.json
```

Для измерения качества нужна человеческая выборка:

```bash
# SQLite read-only; новые JSONL и manifest, без API. Имя выборки должно быть новым.
PYTHONPATH=src .venv/bin/job-intake gold sample --out data/local/gold/new-sample.jsonl

# Возможная миграция SQLite при старте; ответы интерфейса записываются в JSONL с резервной копией.
PYTHONPATH=src .venv/bin/job-intake crm serve --gold data/local/gold/new-sample.jsonl

# SQLite read-only; запись validation JSON, без API и Telegram.
PYTHONPATH=src .venv/bin/job-intake gold validate --gold data/local/gold/new-sample.jsonl --output data/local/eval/new-validation.json

# SQLite read-only; запись JSON/Markdown, без API и Telegram.
PYTHONPATH=src .venv/bin/job-intake eval accuracy --gold data/local/gold/new-sample.jsonl --variant saved --output data/local/eval/new-accuracy.json
```

В CRM заполняйте профессию, релевантность, найм и комментарий по исходнику.
Частичные ответы разрешены интерфейсом, но не заменяют полную измерительную выборку.
Валидация проверяет схему и связь с источником; accuracy сравнивает сохранённые
предсказания с человеческими метками. Без независимой выборки нельзя заявлять
обобщённое качество. Если передана когорта, анализ может дополнительно писать
журнал использования набора.

[Протокол gold](vacancy-gold-labeling.md) объясняет рубрику и provenance.
[Frozen evaluation](p1-evaluation.md) нужен для неизменяемых bundle, holdout,
сравнения вариантов и предобъявленных экспериментов. `eval frozen run` обрабатывает
записанные predictions или frozen baseline; сам по себе новый модельный вызов
не выполняет.

## Discovery: от примеров к кандидатам

Это отдельный контур с папками запусков. `training` собирает примеры;
`p1`/`p2` задают назначение поиска. Машинное решение не становится ответом человека.
Ниже пример нового запуска; имена плана/папки должны быть свободны, а бюджет
платного этапа выбирается перед запуском (`0.10` USD здесь — пример значения).

```bash
# SQLite read-only; новый план и, при наличии когорт, журнал exposures; API = 0.
PYTHONPATH=src .venv/bin/job-intake discovery plan \
  --gold data/local/gold/vacancy-tags-v2-labeled.jsonl \
  --objective training --output data/local/discovery/plans/new.json

# Сеть и платный Perplexity Search; файлы запуска и отдельный SQLite-реестр discovery.
# Рабочая база вакансий читается read-only; нужен PERPLEXITY_API_KEY; Telegram не используется.
PYTHONPATH=src .venv/bin/job-intake discovery run \
  --plan data/local/discovery/plans/new.json \
  --output data/local/discovery/runs/new \
  --budget-usd 0.10 --target-candidates 5

# Запись слепой партии/сопроводительных файлов, без API и рабочей SQLite.
PYTHONPATH=src .venv/bin/job-intake discovery prepare-labeling \
  --run data/local/discovery/runs/new --size 5 --random-size 0

# Локальная CRM: возможная миграция рабочей SQLite при старте; ответы пишутся в файлы запуска.
PYTHONPATH=src .venv/bin/job-intake crm serve --discovery data/local/discovery/runs/new

# Чтение файлов запуска и меток; stdout, без API/SQLite-записи.
PYTHONPATH=src .venv/bin/job-intake discovery report --run data/local/discovery/runs/new

# Dry-run переноса подтверждённых вакансий: без записи SQLite, API и Telegram.
PYTHONPATH=src .venv/bin/job-intake discovery promote --run data/local/discovery/runs/new --objective p1

# Применение проверенного переноса: запись рабочей SQLite и возможная миграция; без API и Telegram.
PYTHONPATH=src .venv/bin/job-intake discovery promote --run data/local/discovery/runs/new --objective p1 --apply
```

`--target-candidates` ограничивает число кандидатов для разметки; это не обещание
стольких подходящих вакансий. Для `promote` нужны полные человеческие решения и
совпадающий снимок источника; для P1 — подтверждённый найм. Перенос сохраняет связь
с разметкой, но не создаёт отклик или человеческое P1/P2-решение автоматически.
Не используйте holdout как поисковые примеры.

Параметры отбора, модели, стоимость и файлы —
[основной протокол](positive-example-discovery.md), реестр/TTL и перенос —
[P1 discovery](p1-discovery.md), ключ —
[инструкция Perplexity](perplexity-search-integration.md).

## Источники и восстановление

Это обслуживание, а не обязательные шаги каждого сбора.

| Команда | Эффект |
|---|---|
| `sources check` | Чтение сохранённого health из SQLite read-only; без сети |
| `sources check --live` | Ограниченная сеть к источникам; без импорта и записи SQLite |
| `sources refresh-details --dry-run` | Read-only выбор кандидатов; без сети и миграции |
| `sources refresh-details` | Загрузка деталей, запись/возможная миграция SQLite, offline-пересчёт; без AI и Telegram |
| `sources recheck-urls` | Сеть к сохранённым URL, SQLite read-only; без автоматического закрытия вакансий |
| `repair prepare` | Чтение рабочей базы, резервная копия и миграция/пересчёт отдельной candidate SQLite; файлы отчёта, без сети |
| `repair apply` / `repair rollback` | Контролируемое изменение оценок рабочей SQLite; без AI и Telegram |

Доступные ограничения показывают `sources --help` и `repair --help`.
Полные команды, проверка конкретного diff и условия drift/rollback —
[операционный протокол](p1-data-operations.md#контролируемое-восстановление-r10).
Один 404/410 не доказывает, что вакансия закрыта. Для `repair` сначала изучите
`report.md` и подготовленные переходы; `prepare` не меняет рабочую базу.

Команды `digest`, `feedback`, `prune` также доступны в CLI: первая может отправлять
Telegram, вторая записывает feedback, третья удаляет подходящие записи.
Их параметры проверяйте через `--help`; они не нужны для знакомства или чтения.
