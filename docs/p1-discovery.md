# Исправления discovery P1, 19 октября 2026

Инженерные R08/R09 и часть R11 реализованы без нового платного поиска и без
изменения имеющихся партий разметки. Машинный accept остаётся предложением для
человека; рост реальной precision пока не измерен.

## Контексты, источники и наблюдаемость

План чередует Brazil, LATAM и worldwide **до** ограничения числа запросов,
как для seed, так и для exploration. В строке запроса сохраняются context,
stream и версия критериев. Это поисковые направления, а не новые личные критерии.
`--feedback` повторяется; несовместимые ответы или теги одного исходника дают
ошибку, взаимно дополняющие ответы объединяются. Holdout исключён по ID/URL;
дополнительно проверяются переданный cohort и глобальный реестр защищённых когорт.
Готовый/старый план повторно проходит эти проверки при `run` до оплаты; добавленный
позже holdout и его mirrors нельзя провести через сохранённый plan. Переданный
sidecar с зарегистрированным cohort ID должен совпадать с его immutable manifest.
Planner автоматически пишет canonical `cohort.exposure_log` без отдельного флага;
runtime фиксирует query seeds и реальные selector/reranker examples в canonical
журналах всех совпадающих зарегистрированных когорт. Положительные demonstrations
и отрицательные примеры отмечаются отдельно. Отсутствующий canonical journal
останавливает использование зарегистрированных примеров до платного вызова.
У legacy/unregistered примеров дополнительно остаётся run-local
`example-exposure.jsonl`: stage, UID, source_hash, model и намерение отправки
prompt фиксируются до вызова selector/reranker. Это не создаёт когорту автоматически.

Perplexity Search продолжает использовать официальный SDK, `POST /search`,
`max_retries=0`. Envelope читается до поэлементной валидации: один испорченный
элемент не уничтожает соседние оплаченные результаты. Сведения о пропущенных
элементах записываются в manifest. 429 повторяет тот же запрос максимум дважды,
с соблюдением Retry-After, времени и общего бюджета. Пауза более 60 секунд
сохраняется для следующего запуска вместо ожидания или раннего повторного POST.
401 прекращает последующие запросы. Каждая попытка видна отдельно.

Загрузка различает HTTP, timeout/network, schema/parse, non-vacancy, missing_text,
access/domain/robots denial и непроверенный 404/410. Последний не становится
закрытием вакансии. Явное закрытие распознаётся на английском и португальском.
Название работодателя берётся из ATS company_name / board identity либо
JSON-LD/HTML hiringOrganization, с отдельным provenance наблюдения. Название
сайта `og:site_name` не является доказательством работодателя. Пустая компания
видна отдельно; она не подставляется из поискового snippet.

Настоящий PublicJobFetcher проверяет robots для каждого origin и явную доменную
политику. `--domain-policy policy.json` принимает `{ "example.com": "deny" }`;
allow не отменяет robots. Неизвестная/недоступная robots policy откладывает
загрузку. 401/403 и запрет политики не обходятся через ATS fallback. Контакт в
User-Agent не публикуется. Внедрённые offline sessions тестов могут задавать свою
политику явно; production default всегда проверяет robots.

Отчёт отдельно показывает observations, unique URLs, попытки загрузки вакансий,
HTTP запросы (включая robots), cache hits и типы ошибок. Эти знаменатели не смешаны.

## Память и расходы между запусками

По умолчанию реестр находится рядом с папками запусков:
`.discovery-registry.sqlite3`. Другой путь задаётся `--registry`.

| Этап | Ключ | Срок повторного использования |
|---|---|---|
| Search | Нормализованные запросы, все filters, max_results, endpoint/контракт | 24 часа; `--search-ttl-seconds` |
| Fetch | URL identity, версия извлечения, domain/robots policy | 6 часов; `--fetch-ttl-seconds` |
| Content gate | Точный snapshot, prompt/units, примеры, критерии, objective, model/effort/generation/schema implementation | 24 часа; uncertain decision 5 минут |
| Transient fetch / Search | Тип ошибки, происхождение | 5 минут / минимум 1 минута и Retry-After |
| Неопределённый платный вызов | Pending/uncertain, reservation и исходный run | 1 час |

`--repeat-queries` обновляет успешный Search до истечения TTL; он не отменяет
pending/uncertain reservation. Перед каждым платным POST резерв и pending ledger
сохраняются на диск. Crash не превращает неопределённое списание в нулевой расход.
Кеш ссылается на первоначальный run/usage; попадание в кеш не является новой оплатой.
Новая модель, prompt, snapshot, критерии или примеры не наследуют прежний reject.

Identity вакансии и cluster похожих текстов разделены. Региональные ограничения
входят в дедупликацию очереди: US-копия не скрывает Brazil-копию с теми же задачами.
У каждой собственный source_hash, ID и human label. Карточка возвращает список
regional_variants; cluster не переносит ответы между ними. `audit-rejected.jsonl`
содержит отдельную очередь машинных отрицательных решений, не вытесняя requested
accepted target. Неоплаченная/незавершённая проверка остаётся uncertain.

## Цель и перенос в CRM

`discovery plan/run --objective training|p1|p2` явно задаёт цель. Training собирает
примеры независимо от найма. P1 требует подтверждённых задач и hiring evidence по
настроенным правилам; unknown не считается допуском. P2 использует согласованную
пользователем рубрику: analytics/data с похожими задачами, даже если уровень или
найм не подходят для следующей работы. Уровень, C tier и hiring rejection не
определяют пригодность к практике автоматически. Предсказание отделено от human.

```bash
PYTHONPATH=src .venv/bin/job-intake discovery plan \
  --gold data/local/gold/vacancy-tags-v2-labeled.jsonl \
  --feedback data/local/discovery/runs/first-5/labels.jsonl \
  --feedback data/local/discovery/runs/next-5/labels.jsonl \
  --objective training --output data/local/discovery/plans/new.json

PYTHONPATH=src .venv/bin/job-intake discovery promote \
  --run data/local/discovery/runs/next-5 --objective p1
```

Promote по умолчанию read-only dry-run. Явный `--apply` выполняет идемпотентный
перенос подтверждённых источников через операционное хранилище. Требуются полное
human решение (профессия, relevance 2/3, hiring, method/date) и совпадающий snapshot;
для P1 — human eligible. Для P2 ineligible/unknown не препятствуют предложению.
Исходная метка остаётся в immutable journal; ссылка и цель сохраняются отдельно.
Human P1/P2 assessment, CRM application status и outbox не переносятся и не
придумываются. Уже известная вакансия не получает старое OPEN поверх свежего source.

## Проверка контракта Perplexity

Перед изменением повторно прочитана сохранённая
[инструкция](perplexity-search-integration.md), затем официальный
[индекс](https://docs.perplexity.ai/llms.txt),
[quickstart](https://docs.perplexity.ai/docs/search/quickstart),
[best practices](https://docs.perplexity.ai/docs/search/best-practices),
[domain filter](https://docs.perplexity.ai/docs/search/filters/domain-filter),
[Search reference](https://docs.perplexity.ai/api-reference/search-post),
[SDK overview](https://docs.perplexity.ai/docs/sdk/overview),
[pricing](https://docs.perplexity.ai/docs/getting-started/pricing) и
[rate limits](https://docs.perplexity.ai/docs/admin/rate-limits-usage-tiers).
Выбранный Search API по-прежнему соответствует структурированной обработке ссылок.
Реальный smoke в этом выпуске не запускался: изменений endpoint/auth/request shape
нет, пользовательский бюджет не расходуется. Внешние сервисы заменены fixtures.
