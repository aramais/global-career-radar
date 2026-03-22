# Architecture

## Core principles

- Deterministic hard filters run before any semantic model.
- LLM scoring is optional and can only rerank jobs that already passed.
- Precision is favored over recall; ambiguous roles should land in review, not auto-alert.
- No LinkedIn login, authenticated scraping, or auto-apply flow is included.

## Layers

1. Ingestion
   - `DailyRemoteAdapter` parses public DailyRemote listing pages.
   - `HtmlPageAdapter` handles public career pages with configurable selectors.
   - `CompanyWatchlistAdapter` wraps HTML parsing for curated target companies.
2. Normalization
   - All sources emit `JobRecord` objects with a common schema.
3. Deterministic filtering
   - `RuleEngine` enforces location, authorization, timezone, title-family, company, and status rules.
4. Scoring
   - `DeterministicScorer` computes a pre-score from config-driven weights.
   - `OpenAIReranker` optionally adds semantic score and explanation for passing jobs only.
5. Persistence
   - SQLAlchemy models store all jobs, events, feedback, and alert state in SQLite.
6. Alerting and review
   - Telegram instant alerts for A-tier.
   - Daily digest for A/B-tier.
   - CSV export and local HTML review page.

## Upgrade path

- Swap SQLite URL for PostgreSQL URL with minimal repository changes.
- Add ATS adapters by implementing `JobSourceAdapter`.
- Add richer UI without disturbing the pipeline contract.
