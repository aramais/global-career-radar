# Efficiency Audit — Job Intake Pipeline

_Audit date: 2026-07-04. Scope: storage/memory efficiency, token usage, external-model
robustness, decision accuracy, and runtime. Covers the deterministic + LLM-rerank path in
`src/job_intake/`._

## Pipeline in one paragraph

Sources → `RuleEngine` (hard filters) → `DeterministicScorer` (config-weighted pre-score) →
optional `OpenAIReranker` (`gpt-5-mini`, OpenAI Responses API) → `finalize_tier` → SQLite
persistence (`JobRepository`) → Telegram instant/digest alerts. LLM reranking is opt-in
(`llm.enabled`) and can never override a deterministic hard reject.

## Scorecard

| Parameter | Score | Core issue (before this audit) |
|---|---|---|
| LLM token efficiency | 🔴 2/5 | LLM re-ran for every PASS job on every pipeline run — no cache keyed on `content_hash`. Reasoning model with no `reasoning.effort` cap. |
| Cost control / observability | 🔴 2/5 | No `max_output_tokens`, no timeout, no token/cost logging. |
| Decision accuracy | 🔴 2/5 | Two conflicting tier systems (see finding 1). Substring signal matching without word boundaries. |
| External-model robustness | 🔴 1/5 | `json.loads` with no guard + `str.format` on the description → one bad response or a `{` in a JD crashes the whole run. |
| Memory efficiency | 🟠 3/5 | Stores `description_raw` **and** `description_clean` forever; no retention/pruning; growing JSON `audit_log`. |
| Runtime / latency | 🟠 3/5 | LLM calls are strictly sequential per job. HTTP layer is solid (retry+backoff). |
| Deterministic core | 🟢 4/5 | Clean, config-driven, hard-rejects protected. |

## Findings

**Accuracy**
1. **Two conflicting tier thresholds.** `DeterministicScorer.score` assigned tiers using config
   `threshold_a=14`/`threshold_b=8`, then `finalize_tier` overrode them with hardcoded
   `fit_score>=18 AND bridge_role` / `>=8`. The config thresholds were dead code and A-tier
   silently required an arbitrary 18. (Note: `bridge_role` **is** set deterministically in
   `filtering.py`, so A-tier was reachable without the LLM — but only past the hidden 18 cutoff.)
2. **Substring signal matching.** `contains_any` / scorer used bare `in`, so `ml` matched inside
   `html`, `pricing` inside `repricing`, `platform` (a −8 penalty signal) inside `cross-platform`.

**External model / tokens**
3. **No LLM cache.** `reranker.rerank` ran for every PASS job unconditionally, re-charging tokens
   for jobs unchanged since the previous run.
4. **Reasoning model uncapped.** `gpt-5-mini` is a reasoning model; without `reasoning.effort` it
   burns reasoning tokens on a classification task. No `max_output_tokens`.
5. **Fragile parsing.** `json.loads(response.output_text)` with no structured-output mode and no
   `try/except`; a single non-JSON or fenced reply crashed the run and dropped every later job.
6. **`str.format` on the description** — a literal `{`/`}` in a posting raised and crashed the run.
7. **Unbounded trust.** `semantic_score` was added to `fit_score` with no clamp despite the prompt
   advertising a −3..6 range.
8. **No timeout, no usage logging** on the OpenAI client.

**Memory**
9. Duplicate full-text storage (`description_raw` + `description_clean`), no retention/pruning of
   C-tier rows, growing JSON `audit_log`/`risks`/`source_metadata`. No `VACUUM`.
10. No alert de-duplication window.

## Prioritized backlog

### P0 — correctness / cost (DONE in this commit)
- **P0-1 — Unify tiering.** `finalize_tier(job, threshold_a, threshold_b)` is now the single source
  of truth, driven by config thresholds; scorer no longer assigns final tier. `tiering.py`,
  `pre_score.py`, `pipeline.py`. Covered by `tests/test_tiering.py`. _(finding 1)_
- **P0-2 — LLM robustness.** Brace-safe prompt render, `text.format=json_object`, markdown-fence
  stripping, full `try/except` deterministic fallback, client timeout. `scoring/llm.py`. Covered by
  `tests/test_llm.py`. _(findings 5, 6, 8)_
- **P0-3 — LLM cache by `content_hash`.** New nullable `semantic_score` column + idempotent SQLite
  migration; pipeline reuses the stored semantic contribution when a job's `content_hash` is
  unchanged instead of calling the API. `storage/models.py`, `storage/database.py`,
  `storage/repository.py`, `pipeline.py`. _(finding 3)_

### P1 — token / accuracy (DONE in this commit)
- **P1-1 — Reasoning & limits.** `reasoning_effort=minimal`, `max_output_tokens=400`,
  `request_timeout` in `LLMConfig` + `config/settings.yaml`. _(finding 4)_
- **P1-2 — Clamp `semantic_score`** to `[semantic_score_min, semantic_score_max]`. _(finding 7)_
- **P1-3 — Word-boundary matching.** `matches_phrase` replaces substring tests in `utils/text.py`
  and `pre_score.py`. _(finding 2)_
- **P1-4 — Token/cache observability.** LLM `usage` logging per call; `run()` returns `llm_calls`
  and `llm_cache_hits`. _(finding 8)_
- Also fixed a pre-existing broken test fixture (`test_rejects_non_target_ml_role_family` asserted a
  `title_blocker` its rules never produced).

### P3 — optimizations (not started)
- **P3-1** Batch reranking via the OpenAI Batch API for the nightly digest (−50% cost). _(finding 3/4)_
- **P3-2** Token-aware (not char-count) description trimming; strip boilerplate before send. _(4)_
- **P3-3** Memory retention: drop `description_raw` after cleaning or prune old C-tier rows;
  periodic `VACUUM`. _(finding 9)_
- **P3-4** Embedding-free bridge-role heuristic to gate more jobs away from the LLM. _(3)_
- **P3-5** Alert de-duplication window. _(finding 10)_

## Verification

```bash
.venv/bin/pip install -e .[dev] -q
.venv/bin/pytest -q          # 17 passing (rules + dedup + tiering + llm)
.venv/bin/ruff check src/ tests/
.venv/bin/python -c "from job_intake.pipeline import build_pipeline; build_pipeline('config/settings.yaml'); print('schema+init OK')"
```

The live LLM path is exercised via `tests/test_llm.py` with a fake client (braces, malformed JSON,
fenced JSON, score clamping); no real API call is made during tests.
