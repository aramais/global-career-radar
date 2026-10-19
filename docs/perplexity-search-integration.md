# Инструкция по работе с Perplexity

Сохранено со слов пользователя 2026-10-18. При изменении интеграции сначала
проверяйте актуальную официальную документацию. Исходная инструкция ниже сохранена
как требование к проекту; сведения об API могут меняться.

## Применение в HR-Automation

Search API подходит для discovery: возвращает ссылки и выдержки, затем проект
сам получает полные тексты вакансий, удаляет повторы и формирует очередь разметки.
Релевантность подтверждает человек в CRM.

Реализация использует официальный Python SDK `perplexityai` (проверена версия
`0.44.0`), `Perplexity.search.create` через интерфейс `with_raw_response` и
`POST https://api.perplexity.ai/search`. Скрытые повторы SDK отключены
(`max_retries=0`): один вызов — одна видимая попытка для бюджета discovery.
После HTTP 429 последующие вызовы того же клиента соблюдают `Retry-After`;
слишком длительная пауза возвращается как ошибка без нового POST. HTTP 401
останавливает отправку следующих пакетов discovery. Ответы провайдера и секреты
не включаются в сообщения об ошибках.

Ключ читается из `PERPLEXITY_API_KEY`; команды проекта также поддерживают
существующий локальный `.env`, исключённый из Git. Если ключа нет, создайте его
в [API Console](https://console.perplexity.ai) и задайте в собственном терминале.
Не вставляйте ключ в чат. Значение ключа не следует включать в команды, которые
сохраняются в истории; для zsh можно использовать скрытый ввод:

```zsh
read -rs 'PERPLEXITY_API_KEY?Perplexity API key: '
export PERPLEXITY_API_KEY
```

Минимальная проверка без вывода содержимого результатов:

```bash
PYTHONPATH=src .venv/bin/job-intake discovery search \
  --query "Perplexity Search API" --max-results 1 \
  --max-tokens 64 --max-tokens-per-page 64 --smoke
```

Для получения результатов уберите `--smoke`. Повторите `--query` до пяти раз
для одного пакетного запроса; результаты объединяются с удалением повторов URL.
Фильтры: `--country BR`, повторяемые `--domain example.com` и `--language en`,
`--recency month`, даты `--after`, `--before`, `--updated-after`, `--updated-before`
в формате `MM/DD/YYYY`. `--max-results` принимает 1–20.

**Расхождение источников на 2026-10-18:** HTML-справочник Search API описывает
`max_tokens` и `max_tokens_per_page`, но не перечисляет `search_context_size`.
Официальный SDK `0.44.0` содержит `search_context_size` со значениями
`low` / `medium` / `high`. В проекте доступны `--context-size` и явные лимиты
токенов; их нельзя задавать одновременно. Успешный smoke-тест выполнен с явными
лимитами токенов; вариант с `--context-size` проверен локально без платного вызова.

Для полного процесса с бюджетом, CRM и отчётом см.
[поиск по положительным примерам](positive-example-discovery.md).
У `discovery run` есть `--target-candidates N`: проверка полных текстов после каждого
Search POST и остановка по числу новых кандидатов. Бюджет остаётся потолком расходов;
релевантность подтверждается последующей человеческой разметкой.

## Проверка реализации 2026-10-18

Выполнены команды из корня проекта:

```bash
.venv/bin/python -m pip install perplexityai
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
git diff --check
PYTHONPATH=src .venv/bin/job-intake discovery search --help
PYTHONPATH=src .venv/bin/job-intake discovery search \
  --query "Perplexity Search API" --max-results 1 \
  --max-tokens 64 --max-tokens-per-page 64 --smoke
```

Итог: 2243 теста прошли; Ruff и проверка diff прошли. Отдельного type-checker
в `pyproject.toml` нет. Реальный smoke-запрос вернул HTTP 200, один результат,
поля `title`, `url`, `snippet`, `date`, `last_updated`. Содержимое результатов
и ключ при проверке не выводились. Полный платный поиск вакансий не запускался.

Изменённые файлы интеграции:

- `pyproject.toml` — зависимость SDK;
- `src/job_intake/discovery/providers.py` — клиент, валидация, дедупликация, ошибки;
- `src/job_intake/discovery/cli.py` — отдельный поиск и флаги discovery;
- `src/job_intake/discovery/service.py` — пакеты, происхождение, учёт попыток;
- `tests/test_discovery_providers.py`, `tests/test_discovery_service.py`,
  `tests/test_perplexity_search.py` — проверки интеграции без внешней сети;
- `AGENTS.md`, `README.md`, `docs/positive-example-discovery.md` и этот документ —
  инструкция и маршруты к ней;
- `scripts/export_shortlist.py`, `scripts/run_pipeline.py` — удалены лишние пустые
  строки, мешавшие общему Ruff.

## Исходная инструкция пользователя

Integrate the Perplexity Search API into this project.

Work inside the project I have open: use its existing language tooling, package manager, framework, and error-handling conventions. Do not scaffold a new demo app unless the project is empty. Install only the dependencies the integration needs.

Context — the Perplexity API platform (one key for all APIs):

- Router API (`POST /router/v1/chat/completions`, `/router/v1/messages`): direct access to frontier models from Anthropic, OpenAI, Google, xAI, and Perplexity through OpenAI- or Anthropic-compatible schemas. No web grounding. Uses the OpenAI/Anthropic SDKs with a base URL — not the Perplexity SDK.
- Agent API (`POST /v1/agent`): web-grounded, multi-provider responses with tools (`web_search`, `fetch_url`, `finance_search`, code execution), presets, conversation state, and structured output. Uses the official Perplexity SDKs.
- Search API (`POST /search`): raw ranked web results as structured data for your own processing — no LLM answer.
- Embeddings API: standard and contextualized text embeddings for semantic search and RAG.
- Decisions API (`POST /v1/decisions`): probabilities instead of text for questions about content you supply: yes or no, one of your options, or a level on your rubric. For classification, routing, and scoring. No web grounding.

I chose the Search API. If while reading this project you conclude a different Perplexity API fits its needs better, say so and why before writing code.

What to build: give this project programmatic web search with the Perplexity Search API.

- Endpoint: `POST https://api.perplexity.ai/search`.
- Returns ranked `results[]` with required `title`, `url`, and `snippet`, plus optional `date` and `last_updated` — structured data, not an LLM answer. If this project actually needs a synthesized, cited answer, the Agent API is the right surface; say so instead of forcing Search.
- `query` accepts a single string or up to five queries in one request; array entries are processed independently.
- `max_results` is 1–20 (default 10); `search_context_size` controls snippet depth; `country` (ISO 3166-1 alpha-2) regionalizes results; domain, language, and date filters are documented in the API reference.
- Dedupe results by URL when merging multi-query responses.

Python: Install the official SDK: `pip install perplexityai`; client is `from perplexity import Perplexity`.

Ground rules:

- The published docs are the source of truth. Start from the index at https://docs.perplexity.ai/llms.txt and read the pages linked below before writing code. Copy endpoints, headers, and request shapes exactly — do not guess parameter names, nesting, or response fields.
- The API key is a secret. Resolve it from the `PERPLEXITY_API_KEY` environment variable. Never hardcode, print, log, or commit it; check presence only (e.g. `test -n "$PERPLEXITY_API_KEY"`). If it is missing, tell me to create one in the API Console (https://console.perplexity.ai) and export it in my own terminal — never ask me to paste it into this chat. If the key is ever exposed, tell me to rotate it in the console.

Verify, then report:

1. Smoke-test with a minimal real request; print only the HTTP status or the response shape, never the key. Expect 200 — a 401 means an authentication/key problem; a 429 means rate limiting or, for the Router API, temporary model overload, so honor `Retry-After` (https://docs.perplexity.ai/docs/admin/rate-limits-usage-tiers).
2. Run the project's own lint/type/test commands.
3. Report files changed, commands run, and one example invocation.

Key docs:

- https://docs.perplexity.ai/docs/search/quickstart
- https://docs.perplexity.ai/docs/search/best-practices
- https://docs.perplexity.ai/docs/search/filters/domain-filter
- https://docs.perplexity.ai/api-reference/search-post
- https://docs.perplexity.ai/docs/sdk/overview
- https://docs.perplexity.ai/docs/getting-started/pricing
