# Методы и HTTP-интерфейс

Срез рабочей копии: **19.10.2026**. Это справочник реализованных операций для перехода
от задачи к коду. Имена Python-методов описывают внутренний интерфейс проекта;
стабильный внешний SDK здесь не объявлен.

Навигация: [документация](index.md) · [карта проекта](project-map.md) ·
[сущности](entities.md) · [порядок чтения](reading-guide.md) · [подробный контракт](architecture.md).

## С чего начать поиск метода

| Задача | Первая точка входа |
| --- | --- |
| Собрать вакансии из настроенных источников | `JobIntakePipeline.run` |
| Понять, почему выбрана профессия, балл или категория | `RoleClassifier.classify` → `RuleEngine.evaluate` → `DeterministicScorer.score` → `finalize_tier` |
| Переоценить сохранённые вакансии | `JobIntakePipeline.reevaluate_saved` |
| Найти новые источники через поисковый API | `build_plan` → `run_discovery` |
| Перенести подтверждённый результат discovery в HR | `promotion_drafts` → `promote_drafts` |
| Разобраться с карточкой отклика и историей этапов | `CRMService.mutate` → `CRMRepository` |
| Понять данные списка вакансий в UI | `list_job_page` → `serialize_job` |

В таблицах «память» означает изменение Python-объекта, «БД» — SQLite через SQLAlchemy,
«файлы» — чтение/запись локальных артефактов, «API» — сетевой запрос, который может
быть платным. Отсутствие API-вызова само по себе не означает режим только чтения.
Методы репозиториев обычно делают `flush`, а `commit` выполняет вызывающий сервис.

## Конфигурация и основной поток

Исходники: [settings.py](../src/job_intake/config/settings.py),
[pipeline.py](../src/job_intake/pipeline.py).

| Символ | Вход → выход | Побочный эффект и роль |
| --- | --- | --- |
| [`load_app_config`](../src/job_intake/config/settings.py#L398) | Путь YAML → `AppConfig` | Читает конфигурацию, раскрывает переменные окружения и разрешает пути; базу не инициализирует. |
| [`build_pipeline`](../src/job_intake/pipeline.py#L666) | Путь конфигурации → `JobIntakePipeline` | Читает конфиг, настраивает журналирование, вызывает конструктор pipeline. |
| [`JobIntakePipeline.__init__`](../src/job_intake/pipeline.py#L47) | `AppConfig` → готовый объект | Загружает правила/профили, создаёт клиенты и вызывает `Database.create_schema`: возможны создание БД и миграции. |
| [`JobIntakePipeline.run`](../src/job_intake/pipeline.py#L122) | Настроенные включённые источники → словарь статистики | Читает источники, оценивает и сохраняет вакансии; обновляет состояние источников; при настройке использует AI и отправляет Telegram. |
| [`JobIntakePipeline.reevaluate_saved`](../src/job_intake/pipeline.py#L491) | `use_ai=False`, `limit` либо `job_uids`, `include_archived`, callback → статистика | Читает и **перезаписывает оценки** в БД; AI включается явно; уведомления отключены. По умолчанию архив исключён. |
| [`JobIntakePipeline.send_daily_digest`](../src/job_intake/pipeline.py#L601) | Окно `hours` → текст дайджеста | Читает актуальные оценки из БД; отправляет текст в Telegram, если дайджест включён. |
| [`JobIntakePipeline.export_csv`](../src/job_intake/pipeline.py#L611) | Выходной путь, профиль, режим shortlist → `Path` | Читает БД и записывает CSV; сам объект pipeline уже мог мигрировать БД при создании. |
| [`JobIntakePipeline.render_html`](../src/job_intake/pipeline.py#L629) | Выходной путь, лимит, профиль → `Path` | Читает сохранённые вакансии и пишет HTML-отчёт. |
| [`JobIntakePipeline.add_feedback`](../src/job_intake/pipeline.py#L660) | `job_uid`, метка, заметка → `None` | Записывает обратную связь и фиксирует транзакцию. |
| [`JobIntakePipeline.prune`](../src/job_intake/pipeline.py#L650) | Возраст, категории, `do_vacuum` → число удалённых | Удаляет старые вакансии выбранных категорий; опционально выполняет `VACUUM`. |

Внутри `run` связующие операции расположены в
[`_evaluate_record`](../src/job_intake/pipeline.py#L236),
[`_save`](../src/job_intake/pipeline.py#L415) и
[`_dispatch_alerts`](../src/job_intake/pipeline.py#L438).
Первая выбирает **одну роль**, выполняет фильтры, разметку и scoring; вторая сохраняет
вакансию, оценку и outbox до доставки; третья проверяет актуальность ожидающего
уведомления и записывает результат отправки. Это полезные места для отладки потока,
а не отдельные пользовательские команды.

## Источники: общий контракт адаптеров

| Символ | Вход → выход | Побочный эффект и роль |
| --- | --- | --- |
| [`build_adapter`](../src/job_intake/adapters/factory.py#L13) | `SourceDefinition` → `JobSourceAdapter` | Выбирает реализацию по `source.type`; неизвестный тип вызывает `ValueError`. |
| [`JobSourceAdapter.fetch_jobs`](../src/job_intake/adapters/base.py#L103) | `name`/`params` экземпляра → `list[JobRecord]` | Абстрактный контракт сбора. Конкретный адаптер читает файлы или сеть; частичные ошибки доступны в `errors`. |
| [`source_snapshot`](../src/job_intake/adapters/base.py#L14) | Область наблюдения и метод сбора → обёрнутый метод | Собирает `board_snapshots` в памяти: статус, полноту, увиденные ID/URL, лимиты и ошибки. Поисковая выдача не становится полным снимком доски. |
| [`JobSourceAdapter.source_health`](../src/job_intake/adapters/base.py#L86) | Накопленные snapshots → словарь состояния | Агрегирует состояние источника в памяти; сам не пишет его в БД. |

Поддерживаемые типы из фабрики:

| `source.type` | Реализация | Что читает `fetch_jobs` |
| --- | --- | --- |
| `dailyremote` | [`DailyRemoteAdapter`](../src/job_intake/adapters/dailyremote.py) | Сетевые страницы DailyRemote; поведение зависит от параметров, лимитов и авторизации. |
| `watchlist` | [`CompanyWatchlistAdapter`](../src/job_intake/adapters/company_watchlist.py) | Файл компаний и настроенные ATS/карьерные страницы. |
| `html` | [`HtmlPageAdapter`](../src/job_intake/adapters/html_page.py) | Настроенные HTML-страницы. |
| `greenhouse` | [`GreenhouseAdapter`](../src/job_intake/adapters/ats.py) | Публичный API доски Greenhouse. |
| `ashby` | [`AshbyAdapter`](../src/job_intake/adapters/ats.py) | Публичные объявления Ashby. |
| `lever` | [`LeverAdapter`](../src/job_intake/adapters/ats.py) | Публичный API Lever с учётом региона. |
| `email_files` | [`EmailFilesAdapter`](../src/job_intake/adapters/email_files.py) | Явно переданную папку `.eml`, без подключения к почтовому ящику. |
| `gmail_snapshot` | [`GmailSnapshotAdapter`](../src/job_intake/adapters/gmail.py) | Локальный снимок выбранных Gmail-сообщений; коннектор запускается отдельно. |

## Профили, профессия, ограничения и баллы

Порядок важен: профессия определяется до оценки соответствия. `other` сохраняет
неизвестную/неподдерживаемую роль без scoring по чужому профилю.

| Символ | Вход → выход | Побочный эффект и роль |
| --- | --- | --- |
| [`load_streams`](../src/job_intake/profiles.py#L63) | Общие правила и YAML профилей как словари → `list[SearchStream]` | В памяти объединяет defaults/переопределения, проверяет ID/пороги, рассчитывает версии. |
| [`with_other_stream`](../src/job_intake/profiles.py#L166) | Потоки и правила → потоки с `other` | Добавляет явную категорию неизвестной профессии. |
| [`profile_query_order`](../src/job_intake/profiles.py#L48) | Потоки → список уникальных запросов | В памяти чередует запросы профилей; используется для DailyRemote с `use_profile_queries`. |
| [`role_taxonomy`](../src/job_intake/profiles.py#L146) / [`taxonomy_version`](../src/job_intake/profiles.py#L158) | Потоки → дескрипторы ролей / хеш | Строит классификационную таксономию без весов fit и предпочтений кандидата. |
| [`stale_version_reasons`](../src/job_intake/profiles.py#L190) | Прежние/текущие компоненты, стадия → список причин | Объясняет устаревание; изменения только поисковых запросов не устаревают оценку. |
| [`RoleClassifier.classify`](../src/job_intake/classification.py#L104) | `JobRecord`, `use_ai=False`, прежняя классификация → `(SearchStream, dict)` | Локально выбирает роль по титулу/обязанностям, может повторно использовать проверенный результат; при явном AI и неоднозначности вызывает модель. |
| [`RuleEngine.evaluate`](../src/job_intake/filtering.py#L144) / [`apply`](../src/job_intake/filtering.py#L300) | `JobRecord` → `JobEvaluation` / `EvaluatedJob` | В памяти выводит `pass/review/reject`, основания и риски по правилам выбранной роли. |
| [`refresh_eligibility_status`](../src/job_intake/filtering.py#L61) | `JobEvaluation` → `None` | Синхронизирует статус доступности найма с текущими препятствиями и неопределённостями. |
| [`DeterministicScorer.score`](../src/job_intake/scoring/pre_score.py#L49) | Данные вакансии, evaluation, опциональный record → evaluation | Изменяет баллы/основания в памяти. Поддерживает `legacy` и `role_fit_v1`; итоговую категорию определяет следующий шаг. |
| [`finalize_tier`](../src/job_intake/scoring/tiering.py#L6) | `EvaluatedJob`, пороги A/B → тот же объект | Выводит категорию A/B/C после scoring/reranking, проверяет мандат и применяет верхний предел категории. |
| [`build_reranker`](../src/job_intake/scoring/llm.py#L605) | `LLMConfig` → `SemanticReranker` | Выбирает `NullReranker` либо `OpenAIReranker`; активная реализация читает файл промпта. |
| [`OpenAIReranker.rerank`](../src/job_intake/scoring/llm.py#L180) | `EvaluatedJob` → тот же объект | Опциональный OpenAI Responses API; изменяет semantic score. При недоступности/ошибке сохраняет детерминированный результат. |
| [`BatchReranker.rerank_many`](../src/job_intake/scoring/llm.py#L471) | Список оценок → список оценок | OpenAI Batch API, локальные файлы и долговечное состояние batch; запоминает незавершённые/ошибочные запросы. |

Категория A/B/C, условия найма и назначение P1/P2 — отдельные оси. Методы scoring
не заменяют человеческое решение о следующей работе или практике собеседований.

## Разметка источника и предварительный фильтр

| Символ | Вход → выход | Побочный эффект и роль |
| --- | --- | --- |
| [`VacancyPrefilter.route`](../src/job_intake/annotation/prefilter.py#L738) | `JobRecord` → словарь маршрутизации | Использует модельные стадии и файловый кеш, чтобы решить, нужен ли полный AI review. Suppressed-источник сохраняется для ручной проверки. |
| [`VacancyPrefilter.cached_route`](../src/job_intake/annotation/prefilter.py#L570) | `JobRecord` → текущий маршрут либо `None` | Повторно проверяет сохранённый маршрут без нового API-запроса. |
| [`VacancyAnnotator.annotate`](../src/job_intake/annotation/service.py#L650) | `JobRecord`, `use_ai=False` → словарь annotation | Изменяет `record.annotation`, читает/может записывать кеш и checkpoints; при включённом AI извлекает и независимо проверяет claims через настроенные API. |
| [`VacancyAnnotator.inspect_cached_review`](../src/job_intake/annotation/service.py#L628) | `JobRecord` → `{cache_path, annotation}` либо `None` | Читает и валидирует подходящий оплаченный review без API-вызовов и записей. Подходит для аудита перед восстановлением. |
| [`apply_annotation_constraints`](../src/job_intake/annotation/service.py#L1022) | Record, evaluation, `RuleEngine` → `None` | В памяти применяет подтверждённые statements к ограничениям/неопределённостям. Черновые claims не повышают оценку. |

Полный review и prefilter имеют разные задачи: маршрут не становится доказанным
условием найма. Ошибки и неполные фрагменты не должны превращаться в подтверждение
доступности вакансии; подробности — в [архитектуре](architecture.md).

## Хранение, идентичность и назначение

| Символ | Вход → выход | Побочный эффект и роль |
| --- | --- | --- |
| [`Database.__init__`](../src/job_intake/storage/database.py#L14) | URL БД → engine и session factory | Для файловой SQLite может создать родительскую папку. Это не специальный read-only loader. |
| [`Database.create_schema`](../src/job_intake/storage/database.py#L27) | Настроенный engine → `None` | Создаёт отсутствующие таблицы и выполняет добавочные миграции столбцов. |
| [`Database.session`](../src/job_intake/storage/database.py#L110) | Нет аргументов → SQLAlchemy `Session` | Открывает рабочую сессию; не фиксирует транзакцию автоматически. |
| [`JobDeduplicator.build_identity`](../src/job_intake/storage/dedup.py#L18) | `JobRecord` → `DedupIdentity` | Вычисляет идентичность вакансии и content hash в памяти. |
| [`reconcile_source`](../src/job_intake/storage/source_state.py#L78) | Входящий `JobRecord`, сохранённая вакансия либо `None` → входящий record | Изменяет record в памяти перед классификацией/scoring: сохраняет прежнее полное описание при неполном наблюдении, удерживает новые противоречия для review и привязывает ожидаемую ревизию источника. |
| [`JobRepository.find_by_record`](../src/job_intake/storage/repository.py#L392) | `JobRecord` → `JobORM` либо `None` | Ищет сохранённую вакансию по идентичности/алиасам. |
| [`JobRepository.upsert_evaluated_job`](../src/job_intake/storage/repository.py#L77) | Оценённая вакансия, `observed` → `UpsertResult` | Обновляет каноническую вакансию, классификацию, события и источниковую идентичность в транзакции. |
| [`JobRepository.upsert_evaluations`](../src/job_intake/storage/repository.py#L291) | **Ровно одна** оценка, cache keys, флаги → `UpsertResult` | Атомарно готовит запись вакансии и её единственной текущей оценки; сохраняет прогноз назначения и при необходимости pending outbox. |
| [`JobRepository.list_jobs`](../src/job_intake/storage/repository.py#L462) | Лимит, выбранные UID, состояние архива → список `JobORM` | Читает вакансии; используется переоценкой и отчётом. |
| [`JobRepository.set_archived`](../src/job_intake/storage/repository.py#L482) | UID, `archived`, `expected_version` → job либо `None` | Меняет архив/версию, пишет событие; при архивации отменяет pending alerts. Конфликт версии вызывает исключение. |
| [`JobRepository.export_shortlisted_csv`](../src/job_intake/storage/repository.py#L569) | Путь, профиль, версии, параметры отбора → `Path` | Читает актуальные оценки и пишет CSV. Узкий режим берёт A/B; `wide` сохраняет более широкий обзор. |
| [`record_source_snapshot`](../src/job_intake/storage/source_lifecycle.py#L37) | Session, снимок наблюдения, grace/минимум наблюдений → изменённые records | Пишет историю наблюдений и состояние исчезновения/возврата; закрытие требует достаточного полного наблюдения источника. |
| [`predict_purpose`](../src/job_intake/purpose.py#L25) / [`save_prediction`](../src/job_intake/purpose.py#L98) | Оценка / session, UID, оценка → прогноз / `None` | Первая вычисляет P1/P2; вторая записывает машинный прогноз в отдельную сущность БД. |
| [`update_human_purpose`](../src/job_intake/purpose.py#L153) | Session, UID, payload с версией и source hash → назначение | Пишет человеческие P1/P2 и журнал изменения; отказывает при изменении источника/версии. |
| [`snapshot_audit_state`](../src/job_intake/storage/snapshot.py#L147) | Путь конфига и новая выходная папка → manifest | Читает существующую БД в read-only, копирует БД/артефакты и проверяет восстановление. Пишет snapshot, не мигрирует исходную БД. |

`observed=False` отличает переоценку уже известных данных от нового наблюдения
источника. Переоценка не должна изображать, что вакансия заново встретилась на сайте.
Человеческий выбор назначения хранится отдельно от прогнозов; его актуальность
связана с исходным текстом и версией рубрики.

## Discovery: план, поиск, очередь проверки, перенос

| Символ | Вход → выход | Побочный эффект и роль |
| --- | --- | --- |
| [`build_plan`](../src/job_intake/discovery/planning.py#L225) | Gold/holdout/feedback, лимиты, `objective` → `discovery-plan-v1` | Читает человеческие метки; строит план в памяти без записей. Holdout не поставляет seeds и контрпримеры для ранжирования. |
| [`rank_candidate`](../src/job_intake/discovery/planning.py#L384) | `JobRecord`, план → priority, seed IDs, evidence | Детерминированно определяет приоритет ручной проверки по обязанностям; не является fit score основного pipeline. |
| [`PerplexitySearch.search`](../src/job_intake/discovery/providers.py#L184) | Запрос либо список запросов, `max_results`, options → список hits | Вызывает Perplexity Search API; структурированные результаты затем обрабатываются проектом. |
| [`PublicJobFetcher.fetch`](../src/job_intake/discovery/providers.py#L498) | Search hit → `JobRecord` | Загружает публичный первоисточник и извлекает текст вакансии с учётом сетевых ограничений/политик. |
| [`Budget.reserve`](../src/job_intake/discovery/service.py#L37) / [`settle`](../src/job_intake/discovery/service.py#L48) | Цена / запись расхода и фактическое usage → запись либо `None` / `None` | Потокобезопасно резервирует и уточняет расходы в памяти; ledger сохраняется runner. |
| [`run_discovery`](../src/job_intake/discovery/service.py#L81) | План, новая папка, search/fetcher, бюджет/лимиты → manifest | Сеть и опциональные AI API; пишет run JSON/JSONL, журнал использования примеров и SQLite registry кеша. Основную HR-БД этот runner не импортирует. |
| [`prepare_labeling`](../src/job_intake/discovery/service.py#L629) | Папка завершённого run, размер/случайная доля/seed → batch | Читает кандидатов; создаёт `batch.json` и `labels.jsonl`, связывая очередь с digest исходников. Существующие ответы не перезаписывает. |
| [`promotion_drafts`](../src/job_intake/discovery/promote.py#L17) | Run, P1/P2, выбранные UID, профили → список drafts | Читает и проверяет привязанные к источнику человеческие решения; P1 требует `eligible`. Сам в HR не записывает. |
| [`promote_drafts`](../src/job_intake/storage/promote.py#L107) | Конфиг, drafts, `dry_run=True` → отчёт | По умолчанию только preview с read-only SQLite. При `dry_run=False` инициализирует pipeline, импортирует/связывает источники в HR и фиксирует транзакции; платный AI и alerts отключены. |

Для `objective="p1"` runner требует evaluator роли и условий найма. Бюджет и
`target_candidates` ограничивают исполнение, но не означают, что найдено столько
подтверждённых подходящих вакансий. Возвращённый manifest содержит фактические
счётчики, расходы и причину остановки.

## Проверка качества и обслуживание

Эти методы разделяют целостность разметки, совпадение с человеческими решениями
и сравнение вариантов на фиксированных исходниках. Все перечисленные проверки
используют сохранённые данные без вызова AI; запись отчёта — отдельный эффект.
Обслуживание ниже может изменять рабочую БД и имеет иной контракт.

| Символ | Вход → выход | Побочный эффект и роль |
| --- | --- | --- |
| [`read_saved_records`](../src/job_intake/annotation/eval_runner.py#L87) | URL БД, лимит/UID, флаг архива → `(Path, [(uid, JobRecord)], findings)` | Читает существующую SQLite с `mode=ro` и `query_only`; проверяет схему, не создаёт и не мигрирует БД. |
| [`run_evaluation`](../src/job_intake/annotation/eval_runner.py#L397) | `AppConfig`, лимит/UID, опциональные log/gold → report | Читает БД и заданные файлы; проверяет сохранённые annotations и происхождение evidence. Возвращает отчёт в памяти; `integrity_passed` не является доказательством семантической точности. |
| [`run_accuracy`](../src/job_intake/annotation/accuracy.py#L1017) | Конфиг, gold, `variant="saved"`, опциональные cohort/prices/log/output → report | Read-only чтение вакансий/прогнозов и человеческих меток; считает accuracy, coverage, readiness. При `output` записывает JSON/Markdown отчёты; другие variants не реализованы. |
| [`build_bundle`](../src/job_intake/annotation/frozen_eval.py#L83) | Конфиг, gold, заранее объявленные версии, cohort/policy → frozen bundle | Read-only чтение БД, включая архив; фиксирует sources, labels и сохранённые predictions в памяти. Source drift отклоняется; snapshot fallback включается явно. |
| [`create_run`](../src/job_intake/annotation/frozen_eval.py#L326) | Bundle, predictions, variant/version/run ID → run | Создаёт run и метрики в памяти; проверяет уникальную привязку прогнозов к UID/hash. Без predictions допускается только baseline из bundle. Модель сам не запускает. |
| [`compare_runs`](../src/job_intake/annotation/frozen_eval.py#L455) | Bundle, baseline, candidate, опциональные repeat/exposure log → comparison | Проверяет run-контракты, пересчитывает метрики, читает журнал exposure; возвращает gates, stability и `pass/fail/insufficient_evidence`, без записи. |
| [`record_exposure`](../src/job_intake/annotation/cohorts.py#L309) | Путь журнала, cohort manifest, UID, purpose, actor/context → event | С блокировкой дописывает JSONL-журнал использования примеров; фиксирует и developer analysis. Проверяет принадлежность UID и канонический путь журнала. |
| [`independent_holdout`](../src/job_intake/annotation/cohorts.py#L294) | Cohort manifest, опциональный log → Boolean | Читает provenance/exposure; подтверждает независимость только для проверенной blind holdout-когорты без использования, кроме inference. |

`build_bundle` и `create_run` возвращают словари; сохранение bundle выполняет
[`save_bundle`](../src/job_intake/annotation/frozen_eval.py#L729), а результатов
сравнения — [`write_comparison_report`](../src/job_intake/annotation/frozen_eval.py#L772).
Эти функции пишут локальные файлы. Неполная выборка, пропущенные predictions или
независимый повтор, который не выполнен, не превращаются в подтверждённое улучшение.
Просмотр holdout для разработки учитывается как exposure при последующих сравнениях.

Обслуживание оценок выполняется через подготовленный кандидат и проверяемые хеши:

| Символ | Вход → выход | Побочный эффект и роль |
| --- | --- | --- |
| [`prepare_repair`](../src/job_intake/storage/repair.py#L219) | Путь конфига, новая output-папка → report | Читает рабочую БД и делает snapshot; в копии `candidate.db` инициализирует схему и переоценивает вакансии без API/alerts. Пишет backup, кандидат и отчёт переходов; рабочую БД не меняет. |
| [`apply_repair`](../src/job_intake/storage/repair.py#L383) | Папка подготовленного отчёта → результат применения | Проверяет артефакты, код/конфиг и отсутствие drift БД; делает свежий backup и применяет подготовленный patch к рабочей SQLite под writer lock. Пишет журнал применения, проверяет защищённые данные и внешние ключи. |
| [`rollback_repair`](../src/job_intake/storage/repair.py#L488) | Папка применённого отчёта → результат отката | Восстанавливает прежние оценки из backup только при неизменившемся состоянии после apply; сохраняет совместимые добавленные столбцы. Пишет БД и `rolled-back.json`; при новых действиях пользователя отказывает в откате. |

Порядок проверки подготовленного кандидата и условия отката — в
[операционном протоколе](p1-data-operations.md#контролируемое-восстановление-r10).

## CRM: сервисы и операции над карточками

| Символ | Вход → выход | Побочный эффект и роль |
| --- | --- | --- |
| [`CRMService.__init__`](../src/job_intake/crm/server.py#L242) | Конфиг, необязательный gold **либо** discovery run → сервис | Загружает профили, вызывает `create_schema`, создаёт CSRF-токен и выбирает хранилище ручной разметки. |
| [`CRMService.state`](../src/job_intake/crm/server.py#L269) | Query-фильтры → состояние UI | Читает БД: отклики, контакты, компании, страницу вакансий, метрики, действия и справочники. Возвращает CSRF-токен. |
| [`CRMService.mutate`](../src/job_intake/crm/server.py#L401) | HTTP method, path, JSON-object → результат | Проверяет поля/версии, вызывает репозитории, фиксирует изменения БД. `/api/gold/{uid}` делегируется отдельному label store. |
| [`CRMRepository.create_application`](../src/job_intake/crm/repository.py#L171) | Компания, должность и поля карточки → `ApplicationORM` | Создаёт карточку и начальную историю в БД; не отправляет отклик работодателю. |
| [`CRMRepository.create_from_job`](../src/job_intake/crm/repository.py#L453) | `job_uid`, профиль → `ApplicationORM` | Создаёт отклик из вакансии либо связывает/возвращает существующий по UID/URL. |
| [`CRMRepository.update_application`](../src/job_intake/crm/repository.py#L326) | ID, `expected_version`, поля → application | Обновляет карточку с проверкой версии и дубликата URL. |
| [`CRMRepository.mark_stage`](../src/job_intake/crm/repository.py#L353) | ID, этап, дата, заметка, версия → application | Пишет переход этапа; повтор того же этапа может уточнить последнее событие вместо выдуманного перехода. |
| [`CRMRepository.upsert_contact`](../src/job_intake/crm/repository.py#L488) / [`upsert_company`](../src/job_intake/crm/repository.py#L645) | Поля контакта/компании → ORM-объект | Создаёт либо находит сущность. Для существующей компании также заполняет пустые URL/заметки и объединяет происхождение источников. Явное редактирование выполняют `update_contact` / `update_company`. |
| [`CRMRepository.link_contact`](../src/job_intake/crm/repository.py#L595) / [`unlink_contact`](../src/job_intake/crm/repository.py#L619) | ID отклика/контакта, тип связи → link / application | Изменяет связь контакта с откликом в БД. |
| [`CRMRepository.due_actions`](../src/job_intake/crm/repository.py#L722) | Дата → список applications | Читает наступившие следующие действия незавершённых откликов. |
| [`CRMRepository.funnel_metrics`](../src/job_intake/crm/repository.py#L741) | Профиль, канал, границы периода → метрики | Читает историю этапов; разделяет известные и неизвестные даты, считает конверсии когорт. |
| [`list_job_page`](../src/job_intake/crm/jobs.py#L183) / [`job_detail`](../src/job_intake/crm/jobs.py#L300) | Session, версии, query / UID → page / detail либо `None` | Читает БД. Отбор/сортировка/пагинация выполняются до загрузки подробного описания. |
| [`current_evaluations`](../src/job_intake/crm/jobs.py#L67) / [`serialize_job`](../src/job_intake/crm/jobs.py#L103) | Job, версии/компоненты → оценки / JSON-ready dict | Отделяет актуальные оценки от устаревших и готовит данные UI с причинами устаревания. |
| [`CRMService.import_xlsx`](../src/job_intake/crm/server.py#L553) | XLSX bytes, имя, `dry_run` → summary | Временно пишет файл для разбора; при dry run не импортирует строки, иначе пишет в БД и делает commit. Исходный workbook не изменяет. |

## HTTP-маршруты CRM

Реализация — стандартные `ThreadingHTTPServer` и `BaseHTTPRequestHandler`:
[`CRMHTTPServer`](../src/job_intake/crm/server.py#L570),
[`CRMRequestHandler._dispatch`](../src/job_intake/crm/server.py#L609).
Маршруты заданы вручную; автоматической схемы OpenAPI код не создаёт.
[`serve_crm`](../src/job_intake/crm/server.py#L721) связывает сервис с `127.0.0.1`,
по умолчанию порт **8765**, и запускает обработку запросов.

Чтение: обработчики вызывают методы `CRMService` и возвращают JSON, если не указано иное.

| Метод и путь | Вход | Результат |
| --- | --- | --- |
| `GET /` | — | HTML локального интерфейса. |
| `GET /api/state` | Фильтры карточек/периода и вакансий; `include_jobs`, `include_entities` | Состояние UI, метрики, действия, options, `csrf_token`. |
| `GET /api/metrics` | `profile_id`, `channel`, `since`, `until` | `metrics`, `actions`, `as_of`, `period`. |
| `GET /api/jobs` | Фильтры списка, описанные ниже | `jobs`, `total`, `offset`, `limit`, `has_more`. |
| `GET /api/jobs/{uid}` | UID в path | Подробная вакансия с оценкой/источником/назначением. |
| `GET /api/applications/{id}` | Числовой ID | Полная карточка отклика с историей и контактами. |
| `GET /api/gold` | Фильтры выбранного label store | Список кейсов для ручной разметки. |
| `GET /api/gold/{uid}` | UID в path | Один кейс выбранного label store. |
| `GET /api/discovery/report` | — | Отчёт run; доступен только при запуске с `--discovery`. |
| `GET /api/export.csv` | `profile_id`, `channel` | CSV откликов; это отдельный экспорт от pipeline CSV вакансий. |

Запись: JSON-объект, кроме двоичного импорта XLSX. Успешный ответ — `200` с JSON.
Связь строк маршрутов с реализацией находится в
[`CRMService.mutate`](../src/job_intake/crm/server.py#L401).

| Метод и путь | Основные поля | Изменение |
| --- | --- | --- |
| `POST /api/applications` | Компания, должность и поля карточки; начальный этап `saved` | Создаёт отклик. Этапы после создания добавляются отдельной операцией. |
| `POST /api/applications/from-job` | `job_uid`, опциональный `profile_id` | Создаёт/связывает отклик; профиль должен совпасть с ролью вакансии. |
| `PATCH /api/applications/{id}` | `expected_version`, изменяемые поля | Обновляет карточку отклика. |
| `POST /api/applications/{id}/stage` | `expected_version`, `stage`, опциональные `occurred_at`, `note` | Записывает или уточняет этап в истории. |
| `POST /api/applications/{id}/contacts` | `expected_version`, `contact_id`, `relationship` | Связывает контакт с откликом. |
| `DELETE /api/applications/{id}/contacts` | Те же поля в JSON body | Удаляет связь контакта с откликом. |
| `POST /api/contacts` | `name`, `company`, `email`, `linkedin_url`, `notes` | Создаёт/находит контакт. |
| `PATCH /api/contacts/{id}` | `expected_version`, поля контакта | Обновляет контакт. |
| `POST /api/companies` | `name`, `url`, `notes` | Создаёт/находит компанию. |
| `PATCH /api/companies/{id}` | `expected_version`, поля компании | Обновляет компанию. |
| `PATCH /api/jobs/{uid}/archive` | `expected_version`, Boolean `archived` | Архивирует/восстанавливает вакансию. Здесь версия — `archive_version` карточки. |
| `PATCH /api/jobs/archive` | `archived`, `jobs: [{job_uid, expected_version}]` | Архивирует/восстанавливает группу атомарно. |
| `PATCH /api/jobs/{uid}/purpose` | `expected_version`, `source_hash`, `human_p1`/`human_p2`, `human_note` | Сохраняет человеческое назначение вакансии. |
| `PATCH /api/gold/{uid}` | Поля и версия выбранного label store | Сохраняет человеческую разметку в gold/discovery журнале. |
| `POST /api/import-xlsx` | XLSX bytes; `X-File-Name`; `?dry_run=true` для preview | Разбирает workbook либо импортирует CRM-строки. |

Параметры списка вакансий определяет
[`JOB_QUERY_FIELDS`](../src/job_intake/crm/jobs.py#L16): `profile_id`, `job_archive`,
`query`, `tier`, `score_min`, `score_max`, `sort`, `offset`, `limit`, `purpose`.
Архив: `active` (по умолчанию), `archived`, `all`; категория: A/B/C/`unrated`;
назначение: `all`/`p1`/`p2`/`review`. `limit` по умолчанию 100, максимум 500.
Сортировки: `tier-asc`, `tier-desc`, `score-asc`, `score-desc`, `review-evidence`.

Все запросы проверяют `Host` с адресом `127.0.0.1:{port}`. Для записи нужен
`X-CSRF-Token` из `/api/state`; сервер также проверяет Origin/Fetch-Site.
JSON body ограничен 256 КиБ, XLSX — 25 МиБ; повторяющиеся query-параметры отклоняются.
Основные ошибки: `400` — данные/поля, `403` — адрес/CSRF, `404` — маршрут/объект,
`409` — конфликт версии, `413` — размер, `415` — неверный тип JSON-запроса.

Есть два ограничения, важные при чтении интерфейса по коду. Отдельных
`GET /api/contacts` и `GET /api/companies` нет: списки входят в `/api/state`.
`export_applications` допускает query `since`, `until`, `job_archive`, но в текущей
реализации CSV отбирает отклики только по `profile_id` и `channel`.

При изменении метода обновляйте соответствующую строку и, если меняется поведение
потока, [архитектурный контракт](architecture.md).
