# Выбор моделей для разметки вакансий

Проверено по первичным источникам **03.10.2026**. Задача разделена на извлечение
утверждений с цитатами и независимую проверку этих утверждений. Результат проверки
не заменяет детерминированные ограничения найма и правила категории A/B/C.
Точность перечисленных моделей на наших вакансиях пока не измерена: live benchmark
без API-ключей не запускался. Пресеты — кандидаты для сравнения, не рейтинг точности.

## Тарифы и возможная роль

USD за миллион токенов: обычный вход без кеша и обычный выход. Тарифы Batch, кеш,
длинный контекст, налоги и сторонние gateways сюда не включены. Thinking tokens
могут входить в оплачиваемый выход; разные модели токенизируют один текст по-разному.

| Модель | Вход / выход | Что проверять в нашей задаче | Первичный источник |
| --- | ---: | --- | --- |
| `gpt-6-luna` | $0.10 / $0.50 | Недорогое извлечение цитат и атомарных условий | [OpenAI Docs: pricing](https://developers.openai.com/api/docs/pricing) |
| `gpt-6.1-sol` | $2.00 / $10.00 | Альтернативный генеративный reviewer для сложных условий | [OpenAI Docs: pricing](https://developers.openai.com/api/docs/pricing) |
| `jev-1.13.0` | $0.042 / $0 | Выбор статуса проверки каждого готового утверждения | [TypeSafe models](https://docs.typesafe.ai/models) |
| `gemini-3.5-flash-lite` | $0.30 / $2.50 | Альтернативное извлечение | [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| `gemini-3.8-flash` | $0.75 / $3.75 | Генеративная проверка в пресете `balanced` | [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| `mistral-small-2603` | $0.15 / $0.60 | Альтернативное извлечение или проверка | [Mistral Small 4](https://docs.mistral.ai/models/mistral-small-4-0-26-03) |
| `claude-haiku-4-5` | $1.00 / $5.00 | Генеративное извлечение или проверка с JSON Schema | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| `claude-sonnet-5-5` | $2.00 / $10.00 | Альтернативная генеративная проверка | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| `qwen3.7-flash-2026-07-15` | $0.028 / $0.110 | Бюджетный кандидат; Global Frankfurt/US, вход ≤32K | [Alibaba Cloud pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing) |
| `qwen3.7-flash-2026-07-15` | $0.030 / $0.130 | International Singapore, вход ≤32K | [Alibaba Cloud pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing) |
| `glm-5.3-flash` | $0.15 / $0.50 | Альтернативное извлечение или проверка; учитывать thinking | [Z.AI pricing](https://docs.z.ai/guides/overview/pricing) |
| `deepseek-flash` | $0.30 / $1.20 peak; $0.15 / $0.60 off-peak | DeepSeek V4.1 Flash; фиксировать время и обслужившую версию | [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/) |

Цена Gemini 3.8 Flash действует до 31.12.2026 включительно; с 01.01.2027 указан
тариф $1.50/$7.50. OpenAI в таблице использует Standard short-context тариф;
длинный контекст стоит иначе. Для Qwen важны регион, scope и длина входа.
Источники тарифов: [Gemini](https://ai.google.dev/gemini-api/docs/pricing),
[OpenAI Docs](https://developers.openai.com/api/docs/pricing),
[Alibaba Cloud](https://www.alibabacloud.com/help/en/model-studio/model-pricing).

## Почему Jev используется только для проверки

Jev возвращает выбор из заданных вариантов и их вероятности. Он не создаёт
произвольный JSON с новыми цитатами, поэтому извлечение остаётся у генеративной
модели. Адаптер формирует отдельный вопрос для каждого `claim_id`, используя
исходные фрагменты и полный проверяемый черновик. Ответы возвращаются в том же
контракте `reviews[]`; исходные поля утверждения не переписываются.
[TypeSafe API](https://docs.typesafe.ai/api).

В пресете фиксируется `jev-1.13.0`: aliases `jev-latest`/`jev-preview` могут
измениться. Probability ≥0.95 и confidence ≥0.8 — начальные консервативные пороги,
которые ещё нужно проверить на нашем gold. Они не доказывают фактическую точность
или независимость подтверждения. Ниже порога поддержка ослабляется; совпадение
цитаты, контекст компании, отрицания и обязательность по-прежнему проверяет код.
[Версии TypeSafe](https://docs.typesafe.ai/models).

Разные provider/model/endpoint в конфигурации ещё не доказывают независимость:
одна фактическая модель через другой alias или gateway остаётся той же моделью.
Проверка конфигурации различает настроенные identities, но не разрешает все aliases
провайдеров. Для сравнения нужно фиксировать обслужившую версию и избегать такой пары.

Локальные ограничения адаптера: до 256 claims на chunk, до 30 000 UTF-8 bytes state
и до 60 000 bytes полного запроса; `jev_batch_size: 16` ограничивает вопросы в
одном запросе. Это предохранители приложения, не заявленные vendor limits. Превышение
или неполный ответ оставляет условия непроверенными; поддержка не придумывается.

Производитель описывает зависимость от порядка вариантов, ошибки на числах/датах
и ухудшение при большом нерелевантном контексте. Поэтому сравнение включает
перестановку вариантов, условия в конце текста, отрицания и company/role scope.
Числа, даты и итоговые баллы вычисляются кодом.
[Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13).

## Подключение и пресеты

Локальная разметка работает без ключей. Для вызовов API нужны одновременно
`--ai` и ключи выбранных провайдеров. `budget-jev` использует `OPENAI_API_KEY`
для извлечения и `TYPESAFE_API_KEY` для проверки. `balanced` использует
`GEMINI_API_KEY`. Значения задаются локально; YAML содержит только имена переменных.

```bash
.venv/bin/job-intake annotate --config config/settings.yaml --limit 10
.venv/bin/job-intake annotate --config config/settings.yaml --ai --preset budget-jev --limit 10
.venv/bin/job-intake annotate --config config/settings.yaml --ai --preset balanced --limit 10
```

Старые `provider`, `api_key_env`, `extract_model`, `review_model` продолжают работать.
Для смешанного подключения в `annotation` можно задать `extract_provider`,
`review_provider`, `extract_api_key_env`, `review_api_key_env`,
`extract_base_url`, `review_base_url`, `extract_reasoning_effort`,
`review_reasoning_effort`. Provider и reasoning без переопределения используют общую
настройку. При смене provider стадия получает стандартное имя ключа и endpoint
этого провайдера; явные stage-поля имеют приоритет. Пример `budget-jev`:

```yaml
annotation:
  enabled: true
  ai_enabled: false
  extract_provider: openai
  extract_model: gpt-6-luna
  extract_api_key_env: OPENAI_API_KEY
  extract_reasoning_effort: none
  review_provider: jev
  review_model: jev-1.13.0
  review_api_key_env: TYPESAFE_API_KEY
  jev_min_probability: 0.95
  jev_min_confidence: 0.8
  jev_batch_size: 16
```

Транспорт различает Gemini GenerateContent, OpenAI Responses, Anthropic Messages,
TypeSafe SystemOne и совместимый Chat Completions. Именованные compatible providers:
`mistral`, `deepseek`, `qwen`, `zai`, `openrouter`; собственный gateway —
`openai_compatible` с явным HTTPS `*_base_url`. URL не берётся из вакансии или ответа
модели. Стандартные настройки разделяют ключи провайдеров; явное переопределение
ключа и endpoint должно соответствовать выбранному сервису.

Anthropic использует JSON Schema через `output_config.format`; старые модели могут
не поддержать этот режим. Для Anthropic и compatible-провайдеров адаптер использует
reasoning/effort по умолчанию у провайдера; stage-параметр reasoning передаётся только
OpenAI reasoning-моделям и Gemini 3. Compatible transport запрашивает JSON object, затем
применяет локальный строгий валидатор; поддержка server-side JSON Schema конкретного
провайдера автоматически не используется. У Qwen есть ограничения по регионам,
включая отсутствие JSON Schema режима у Singapore models согласно документации.
[Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
[Qwen structured output](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output).

Стандартный endpoint `qwen` относится к Singapore. Для нового workspace или другого
региона задайте соответствующий `*_base_url`, например
`https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1` для
Singapore либо `https://dashscope-us.aliyuncs.com/compatible-mode/v1` для US. Имя
workspace нужно подставить; ключ должен соответствовать региону и workspace.
[Alibaba OpenAI compatibility](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope).

## Узкий offline benchmark reviewers

Модуль сравнивает **одинаковые вручную размеченные черновики** с ответами разных
reviewers. Он не обращается к API, не читает ключи, не меняет SQLite и не оценивает
полноту extraction либо категорию A/B/C. Несколько примеров ниже иллюстрируют формат;
они не являются репрезентативным измерением качества модели.

Gold — JSONL, одна вакансия/кейс на строку: `case_id`, исходные `units[]` и
фиксированные `drafts[]`. У каждого draft есть существующие поля утверждения и
ручной `expected_review_status`. Флаг `restriction: true` помечает подтверждённое
обязательное условие, полноту проверки которого нужно измерить. Ошибочные drafts
оставляются намеренно: reviewer должен заметить неверную полярность или scope.
В запрос reviewer передаются только исходные units и drafts **без**
`expected_review_status` и `restriction`: gold labels не должны попадать в модель.

`gold.jsonl` (каждый объект ниже — одна строка):

```jsonl
{"case_id":"english-negation","units":[{"unit_id":"u1","text":"English is not required."}],"drafts":[{"claim_id":"c1","kind":"working_language","value":"English","unit_id":"u1","source_snippet":"English is not required.","requirement":"required","polarity":"affirmative","is_inference":false,"expected_review_status":"UNSUPPORTED"}]}
{"case_id":"mandatory-residence","units":[{"unit_id":"u1","text":"Candidates must reside in Germany."}],"drafts":[{"claim_id":"c1","kind":"hiring_location","value":"Germany","unit_id":"u1","source_snippet":"Candidates must reside in Germany.","requirement":"required","polarity":"affirmative","is_inference":false,"expected_review_status":"SUPPORTED","restriction":true}]}
```

Prediction — `case_id` и `reviews[]` с исходными `claim_id` и `review_status`.
Например, следующие строки — **ручной пример правильных ответов**, не ответ Jev:

```jsonl
{"case_id":"english-negation","reviews":[{"claim_id":"c1","review_status":"UNSUPPORTED"}]}
{"case_id":"mandatory-residence","reviews":[{"claim_id":"c1","review_status":"SUPPORTED"}]}
```

```bash
.venv/bin/python -m job_intake.annotation.benchmark \
  --gold data/local/benchmark/gold.jsonl \
  --predictions data/local/benchmark/reviewer.jsonl \
  --output data/local/benchmark/metrics.json
```

В отчёте `review_coverage` — доля уникальных валидных exact-ID ответов,
`review_status_accuracy` — совпадение с ручным статусом, `supported_precision` —
доля правильных прямых подтверждений среди всех предложенных `SUPPORTED`,
`supported_quote_exact_rate` — совпадение исходной цитаты в своём unit,
`restriction_recall` — доля подтверждённых gold-ограничений, `false_promotion_rate` —
доля ошибочных подтверждений. Пустой знаменатель даёт `null`, не искусственные 100%.
Дубликаты, неизвестные IDs, переписанные drafts и пропущенные ответы не получают
кредит. Регистр и пунктуация цитат сохраняются; допустимы только whitespace/NFC.

Для пилота следует независимо разметить реальные обезличенные примеры и добавить
company/role, preferred/not_required, исключение Brazil, citizenship-not-required,
English advert, bilingual/exclusive language, HTML headings и поздний хвост текста.
Reviewer сравнивается на одном фиксированном наборе drafts, отдельно от extraction.
Начальный критерий безопасности — ноль false promotions при полной coverage;
результат на маленьком наборе не является гарантией точности в production.

В текущей разметке сохраняются provider/model/endpoint и usage; отсутствующий usage
помечается неизвестным. Статистика annotator содержит счётчики запросов, cache hits
и ошибок. Latency каждой стадии и число реально оплаченных retry пока не сохраняются.
Для живого пилота нужно отдельно записать wall time, retries, обслужившие версии,
вход/выход/thinking/cache tokens и тариф, отделяя оценку от счёта провайдера.
Цена за токен не равна цене за корректно размеченную вакансию. Тарифная таблица
помогает выбрать пилот; окончательный выбор требует собственных измерений.
