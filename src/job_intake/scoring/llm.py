from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from job_intake.annotation.text import source_hash, source_identity
from job_intake.config.settings import LLMConfig
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation
from job_intake.utils.text import strip_boilerplate

LOGGER = logging.getLogger(__name__)


class _BatchStateError(RuntimeError):
    """A controlled lifecycle error that contains no provider response text."""


class _BatchPendingError(TimeoutError):
    """A controlled wait timeout; its durable remote task can still finish."""


def _safe_failure(exc: Exception) -> str:
    if isinstance(exc, _BatchStateError | _BatchPendingError):
        return str(exc)
    return type(exc).__name__


def _strip_json_fence(text: str) -> str:
    """Remove an optional ```json ... ``` markdown fence around a JSON payload."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped
        if stripped.endswith("```"):
            stripped = stripped[: -3]
    return stripped.strip()


def should_skip_llm(
    evaluation: JobEvaluation,
    threshold_a: float,
    threshold_b: float,
    margin: float,
) -> bool:
    """Skip the LLM when the deterministic outcome is already unambiguous.

    Avoids paying for reranking on jobs the semantic pass cannot realistically move:
    a clear A (well above ``threshold_a`` and a bridge role) or a clear non-bridge
    reject/low-fit (well below ``threshold_b`` and no bridge signal).
    """
    score = evaluation.deterministic_score
    clear_a = score >= threshold_a + margin and evaluation.bridge_role
    clear_low = score < threshold_b - margin and not evaluation.bridge_role
    return clear_a or clear_low


def _apply_semantic_payload(
    evaluation: JobEvaluation,
    payload: dict[str, Any],
    config: LLMConfig,
) -> float:
    """Apply a parsed rerank payload to an evaluation (shared by sync and batch).

    Clamps ``semantic_score`` to the advertised range so a misbehaving model cannot
    skew tiers, then folds it into the fit score and merges bridge/reason/risks.
    """
    if not isinstance(payload, dict):
        raise ValueError("Semantic payload must be a JSON object.")
    raw_score = payload.get("semantic_score", 0.0)
    if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
        raise ValueError("semantic_score must be a finite number.")
    semantic_score = float(raw_score)
    if not math.isfinite(semantic_score):
        raise ValueError("semantic_score must be a finite number.")
    bridge_role = payload.get("bridge_role", False)
    if not isinstance(bridge_role, bool):
        raise ValueError("bridge_role must be a JSON boolean.")
    explanation = payload.get("fit_reason", "")
    if not isinstance(explanation, str):
        raise ValueError("fit_reason must be a string.")
    risks = payload.get("risks", [])
    if not isinstance(risks, list) or any(not isinstance(item, str) for item in risks):
        raise ValueError("risks must be an array of strings.")
    # Validate the entire payload before changing any deterministic field.
    semantic_score = max(
        min(semantic_score, config.semantic_score_max),
        config.semantic_score_min,
    )
    explanation = explanation.strip()

    evaluation.semantic_score = semantic_score
    if evaluation.raw_fit_score is not None:
        evaluation.raw_fit_score += semantic_score
    evaluation.fit_score += semantic_score
    evaluation.bridge_role = evaluation.bridge_role or bridge_role
    if explanation:
        evaluation.fit_reason = explanation
    evaluation.risks = sorted(set(evaluation.risks + risks))
    evaluation.audit_log.append(f"Semantic rerank added {semantic_score:.2f} points.")
    return semantic_score


def _extract_output_text(body: dict[str, Any]) -> str:
    """Pull the model text out of a raw Responses API body (batch output line).

    Prefers the SDK convenience ``output_text`` when present, otherwise walks the
    ``output[].content[]`` array for the first ``output_text`` chunk.
    """
    if not isinstance(body, dict):
        return ""
    if body.get("output_text"):
        return str(body["output_text"])
    for item in body.get("output", []) or []:
        for chunk in item.get("content", []) or []:
            if chunk.get("type") in {"output_text", "text"} and chunk.get("text"):
                return str(chunk["text"])
    return ""


def render_prompt(template: str, config: LLMConfig, job: EvaluatedJob) -> str:
    """Render the rerank prompt via literal replacement.

    ``str.format`` would raise on job descriptions containing literal ``{``/``}``
    (common in engineering postings), so substitute known placeholders directly.
    Optionally strips boilerplate first to save tokens.
    """
    description = job.record.description_clean or job.record.description_raw
    if config.strip_boilerplate:
        description = strip_boilerplate(description)
    description = description[: config.max_description_chars]
    LOGGER.debug("llm_prompt_description_est_tokens=%s", len(description) // 4)
    replacements = {
        "{title}": job.record.title,
        "{company}": job.record.company,
        "{location}": job.record.location_text or "",
        "{remote_text}": job.record.remote_text or "",
        "{employment_type}": job.record.employment_type or "",
        "{timezone_text}": job.record.timezone_text or "",
        "{description}": description,
        "{profile_id}": getattr(job, "profile_id", "default"),
        "{profile_name}": getattr(job, "profile_name", "") or "Default profile",
        "{profile_version}": getattr(job, "profile_version", ""),
        "{profile_context}": getattr(job, "profile_context", "") or (
            "Assess fit to the configured role-family signals. Do not infer a candidate profile."
        ),
    }
    prompt = template
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


class SemanticReranker:
    def rerank(self, job: EvaluatedJob) -> EvaluatedJob:
        raise NotImplementedError


class NullReranker(SemanticReranker):
    def rerank(self, job: EvaluatedJob) -> EvaluatedJob:
        job.evaluation.audit_log.append("Semantic rerank skipped.")
        return job


class OpenAIReranker(SemanticReranker):
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.prompt_template = Path(config.prompt_path).read_text(encoding="utf-8")

    def _render_prompt(self, job: EvaluatedJob) -> str:
        return render_prompt(self.prompt_template, self.config, job)

    def rerank(self, job: EvaluatedJob) -> EvaluatedJob:
        if job.evaluation.decision == FilterDecision.REJECT:
            job.evaluation.audit_log.append("Semantic rerank not allowed for hard-rejected job.")
            return job

        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            job.evaluation.audit_log.append("Semantic rerank skipped because API key is missing.")
            return job

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=self.config.request_timeout)
            prompt = self._render_prompt(job)
            response = client.responses.create(
                model=self.config.model,
                input=prompt,
                reasoning={"effort": self.config.reasoning_effort},
                max_output_tokens=self.config.max_output_tokens,
                text={"format": {"type": "json_object"}},
            )
            usage = getattr(response, "usage", None)
            if usage is not None:
                LOGGER.info(
                    "llm_rerank_usage input_tokens=%s output_tokens=%s",
                    getattr(usage, "input_tokens", None),
                    getattr(usage, "output_tokens", None),
                )
            payload = json.loads(_strip_json_fence(response.output_text))
            _apply_semantic_payload(job.evaluation, payload, self.config)
        except Exception as exc:  # noqa: BLE001 - any failure must fall back deterministically
            failure = _safe_failure(exc)
            LOGGER.warning("llm_rerank_failed error=%s", failure)
            job.evaluation.audit_log.append(
                f"Semantic rerank failed, kept deterministic result: {failure}"
            )
            return job

        return job


class BatchReranker:
    """Rerank a batch of non-rejected jobs in one OpenAI Batch API job (≈50% cheaper).

    Opt-in and asynchronous by nature: it submits all requests together, polls until
    the batch completes, then applies the parsed scores. Suited to the nightly digest
    path rather than instant alerts — it blocks up to ``batch_max_wait`` seconds. On
    any error or timeout the jobs keep their deterministic result (``semantic_score``
    stays ``None``). Durable request state lets a later run resume the same remote
    batch, including after a timeout or process restart.
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.prompt_template = Path(config.prompt_path).read_text(encoding="utf-8")
        self.last_stats = {"submitted": 0, "resumed": 0, "completed": 0, "pending": 0, "errors": 0}

    def _build_request_line(self, custom_id: str, job: EvaluatedJob) -> dict[str, Any]:
        return {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": {
                "model": self.config.model,
                "input": render_prompt(self.prompt_template, self.config, job),
                "reasoning": {"effort": self.config.reasoning_effort},
                "max_output_tokens": self.config.max_output_tokens,
                "text": {"format": {"type": "json_object"}},
            },
        }

    def _request_key(self, job: EvaluatedJob, request: dict[str, Any]) -> str:
        config = asdict(self.config)
        for name in ("batch_state_dir", "batch_max_wait", "batch_poll_interval", "request_timeout"):
            config.pop(name, None)
        payload = {
            "version": 1, "request": {k: v for k, v in request.items() if k != "custom_id"},
            "source_id": source_identity(job.record), "source_hash": source_hash(job.record),
            "profile_id": job.profile_id, "profile_version": job.profile_version,
            "profile_context": job.profile_context, "config": config,
        }
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, allow_nan=False,
        ).encode()).hexdigest()

    @staticmethod
    @contextmanager
    def _state_database(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        database_path = path.parent / "batch-state.sqlite3"
        database = sqlite3.connect(database_path, timeout=30)
        try:
            database_path.chmod(0o600)
            database.execute("PRAGMA foreign_keys = ON")
            database.execute("PRAGMA synchronous = FULL")
            database.executescript("""
                CREATE TABLE IF NOT EXISTS batch_tasks (
                    key TEXT PRIMARY KEY, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_requests (
                    request_key TEXT PRIMARY KEY,
                    batch_key TEXT NOT NULL REFERENCES batch_tasks(key)
                );
            """)
            yield database
        finally:
            database.close()

    @classmethod
    def _write_state(cls, path: Path, state: dict[str, Any]) -> None:
        serialized = json.dumps(state, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with cls._state_database(path) as database, database:
            database.execute(
                "INSERT INTO batch_tasks(key, state) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET state=excluded.state", (state["key"], serialized),
            )

    @classmethod
    def _read_state(cls, path: Path, key: str) -> dict | None:
        with cls._state_database(path) as database:
            row = database.execute("SELECT state FROM batch_tasks WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row is not None else None

    def _claim_requests(self, requests: dict[str, dict]) -> list[dict]:
        """Assign requests to batches in one durable transaction before any API mutation."""
        path = Path(self.config.batch_state_dir) / "allocation"
        with self._state_database(path) as database, database:
            database.execute("BEGIN IMMEDIATE")
            groups = {}
            unclaimed = {}
            for request_key, request in requests.items():
                row = database.execute(
                    "SELECT batch_key FROM batch_requests WHERE request_key=?", (request_key,),
                ).fetchone()
                if row is None:
                    unclaimed[request_key] = request
                elif row[0] not in groups:
                    saved = database.execute(
                        "SELECT state FROM batch_tasks WHERE key=?", (row[0],),
                    ).fetchone()
                    if saved is None:
                        raise _BatchStateError("Saved request has no batch state")
                    state = json.loads(saved[0])
                    saved_keys = {entry[0] for entry in database.execute(
                        "SELECT request_key FROM batch_requests WHERE batch_key=?", (row[0],),
                    )}
                    saved_mapping = state.get("mapping", {})
                    if (
                        state.get("version") != 1 or state.get("key") != row[0]
                        or not isinstance(saved_mapping, dict)
                        or set(saved_mapping.values()) != saved_keys
                        or len(saved_mapping) != len(saved_keys)
                    ):
                        raise _BatchStateError("Invalid saved batch request mapping")
                    for line in state["requests"]:
                        saved_key = saved_mapping.get(line["custom_id"])
                        if saved_key in requests and (
                            {k: v for k, v in line.items() if k != "custom_id"}
                            != {k: v for k, v in requests[saved_key].items() if k != "custom_id"}
                        ):
                            raise _BatchStateError(
                                "Saved batch request does not match the current input"
                            )
                    groups[row[0]] = state
            if unclaimed:
                key = hashlib.sha256(json.dumps(sorted(unclaimed)).encode()).hexdigest()
                mapping = {str(pos): request_key for pos, request_key in enumerate(unclaimed)}
                lines = [
                    {**unclaimed[request_key], "custom_id": cid}
                    for cid, request_key in mapping.items()
                ]
                state = {"version": 1, "key": key, "phase": "prepared",
                         "mapping": mapping, "requests": lines}
                database.execute("INSERT INTO batch_tasks(key, state) VALUES (?, ?)", (
                    key, json.dumps(state, ensure_ascii=False, sort_keys=True, allow_nan=False),
                ))
                database.executemany(
                    "INSERT INTO batch_requests(request_key, batch_key) VALUES (?, ?)",
                    [(request_key, key) for request_key in unclaimed],
                )
                groups[key] = state
            return list(groups.values())

    @staticmethod
    @contextmanager
    def _state_lock(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".lock").open("a+b") as stream:
            if os.name == "posix":
                os.fchmod(stream.fileno(), 0o600)
            if os.name == "posix":
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            else:
                import msvcrt

                stream.write(b"\0")
                stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if os.name == "posix":
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                else:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    @staticmethod
    def _remote_matches(page, predicate) -> list[Any]:
        iterator = page.auto_paging_iter() if hasattr(page, "auto_paging_iter") else iter(page)
        found = []
        for index, item in enumerate(iterator):
            if index >= 1000:
                raise _BatchStateError("Remote batch recovery lookup exceeded its limit")
            if predicate(item):
                found.append(item)
        return found

    def _prepare_batch(self, client, path: Path, key: str, requests: list[dict], mapping: dict):
        """Serialize mutations; unresolved remote outcomes never trigger a new submission."""
        with self._state_lock(path):
            state = self._read_state(path, key)
            if state is not None:
                saved_mapping = state.get("mapping") if isinstance(state, dict) else None
                if (
                    not isinstance(state, dict) or state.get("version") != 1
                    or state.get("key") != key or not isinstance(saved_mapping, dict)
                    or set(saved_mapping.values()) != set(mapping.values())
                    or len(saved_mapping) != len(mapping)
                    or not all(isinstance(cid, str) for cid in saved_mapping)
                ):
                    raise _BatchStateError(
                        "Invalid saved batch state; refusing duplicate submission"
                    )
            else:
                state = {"version": 1, "key": key, "phase": "prepared",
                         "mapping": mapping, "requests": requests}
                self._write_state(path, state)
            filename = f"job-intake-{key}.jsonl"
            resumed = state["phase"] != "prepared"
            if state["phase"] == "completed":
                self.last_stats["resumed"] += 1
                return state
            if state["phase"] == "upload_pending":
                matches = self._remote_matches(
                    client.files.list(purpose="batch"),
                    lambda item: getattr(item, "filename", None) == filename,
                )
                if len(matches) != 1:
                    raise _BatchStateError("Unresolved batch upload; refusing duplicate submission")
                state.update(upload_id=matches[0].id, phase="uploaded")
                self._write_state(path, state)
            if state["phase"] == "prepared":
                state["phase"] = "upload_pending"
                self._write_state(path, state)
                jsonl = "\n".join(json.dumps(line) for line in state["requests"])
                upload = client.files.create(file=(filename, jsonl.encode()), purpose="batch")
                state.update(upload_id=upload.id, phase="uploaded")
                self._write_state(path, state)
            if state["phase"] == "batch_pending":
                matches = self._remote_matches(client.batches.list(), lambda item: (
                    getattr(item, "metadata", None) or {}
                ).get("job_intake_key") == key and getattr(item, "input_file_id", None)
                    == state["upload_id"])
                if len(matches) != 1:
                    raise _BatchStateError(
                        "Unresolved batch creation; refusing duplicate submission"
                    )
                state.update(batch_id=matches[0].id, phase="submitted")
                self._write_state(path, state)
            if state["phase"] == "uploaded":
                state["phase"] = "batch_pending"
                self._write_state(path, state)
                batch = client.batches.create(
                    input_file_id=state["upload_id"], endpoint="/v1/responses",
                    completion_window=self.config.batch_completion_window,
                    metadata={"job_intake_key": key},
                )
                state.update(batch_id=batch.id, phase="submitted")
                self._write_state(path, state)
                self.last_stats["submitted"] += 1
            if state["phase"] != "submitted" or not isinstance(state.get("batch_id"), str):
                raise _BatchStateError("Invalid saved batch lifecycle state")
            if resumed:
                self.last_stats["resumed"] += 1
            return state

    def rerank_many(self, jobs: list[EvaluatedJob]) -> list[EvaluatedJob]:
        self.last_stats = dict.fromkeys(self.last_stats, 0)
        passing = [job for job in jobs if job.evaluation.decision != FilterDecision.REJECT
                   and job.evaluation.semantic_score is None]
        if not passing:
            return jobs

        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            self.last_stats["errors"] += 1
            for job in passing:
                job.evaluation.audit_log.append("Batch rerank skipped because API key is missing.")
            return jobs

        try:
            from openai import OpenAI

            # A mutation with an unknown outcome is reconciled from durable
            # state, rather than automatically retried by the SDK.
            client = OpenAI(api_key=api_key, timeout=self.config.request_timeout, max_retries=0)
            by_request: dict[str, list[EvaluatedJob]] = {}
            requests = {}
            for job in passing:
                cid = str(len(requests))
                request = self._build_request_line(cid, job)
                request_key = self._request_key(job, request)
                requests.setdefault(request_key, request)
                by_request.setdefault(request_key, []).append(job)
            states = self._claim_requests(requests)
        except Exception as exc:  # noqa: BLE001 - preserve data when allocation fails
            self.last_stats["errors"] += 1
            failure = _safe_failure(exc)
            LOGGER.warning("batch_rerank_failed error=%s", failure)
            for job in passing:
                job.evaluation.audit_log.append(
                    f"Batch rerank failed, kept deterministic result: {failure}"
                )
            return jobs

        applied = 0
        for allocated in states:
            affected = [job for request_key in allocated["mapping"].values()
                        for job in by_request.get(request_key, [])]
            try:
                key = allocated["key"]
                path = Path(self.config.batch_state_dir) / (key + ".task")
                state = self._prepare_batch(
                    client, path, key, allocated["requests"], allocated["mapping"],
                )
                if state["phase"] != "completed":
                    batch = self._poll(client, state["batch_id"])
                    if getattr(batch, "status", None) != "completed":
                        raise _BatchStateError("Remote batch did not complete successfully")
                    results = self._parse_output(client, batch.output_file_id)
                    # Validate independently, so malformed rows cannot discard good scores.
                    for cid, payload in results.items():
                        try:
                            _apply_semantic_payload(
                                JobEvaluation(FilterDecision.PASS), payload, self.config,
                            )
                        except (TypeError, ValueError):
                            results[cid] = None
                    state.update(
                        phase="completed", output_file_id=batch.output_file_id, results=results,
                    )
                    with self._state_lock(path):
                        self._write_state(path, state)
                self.last_stats["completed"] += 1
                for cid, request_key in state["mapping"].items():
                    targets = by_request.get(request_key, [])
                    if not targets:
                        continue
                    payload = state["results"].get(cid)
                    try:
                        # Validate once before touching any targets for this request.
                        _apply_semantic_payload(
                            JobEvaluation(FilterDecision.PASS), payload, self.config,
                        )
                        for job in targets:
                            _apply_semantic_payload(job.evaluation, payload, self.config)
                            applied += 1
                    except (TypeError, ValueError) as exc:
                        self.last_stats["errors"] += 1
                        for job in targets:
                            job.evaluation.audit_log.append(
                                f"Batch rerank failed, kept deterministic result: {exc}"
                            )
            except Exception as exc:  # noqa: BLE001 - another durable batch may still complete
                self.last_stats["pending" if isinstance(exc, TimeoutError) else "errors"] += 1
                failure = _safe_failure(exc)
                LOGGER.warning("batch_rerank_failed error=%s", failure)
                for job in affected:
                    job.evaluation.audit_log.append(
                        f"Batch rerank failed, kept deterministic result: {failure}"
                    )
        if hasattr(client, "close"):
            client.close()
        LOGGER.info("batch_rerank_applied count=%s", applied)
        return jobs

    def _poll(self, client, batch_id: str):
        deadline = time.monotonic() + self.config.batch_max_wait
        batch = client.batches.retrieve(batch_id)
        while getattr(batch, "status", None) in {"validating", "in_progress", "finalizing"}:
            if time.monotonic() >= deadline:
                raise _BatchPendingError("batch polling exceeded batch_max_wait; remote task saved")
            time.sleep(min(self.config.batch_poll_interval, max(0, deadline - time.monotonic())))
            batch = client.batches.retrieve(batch_id)
        return batch

    def _parse_output(self, client, output_file_id: str) -> dict[str, Any]:
        content = client.files.content(output_file_id)
        text = getattr(content, "text", None) or content.read().decode("utf-8")
        results: dict[str, Any] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                custom_id = record.get("custom_id")
                body = (record.get("response") or {}).get("body", {})
                output_text = _extract_output_text(body)
                parsed = json.loads(_strip_json_fence(output_text)) if output_text else None
            except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                LOGGER.warning("batch_rerank_output_invalid error=%s", exc)
                # Keep partial successes when a single output line is malformed.
                continue
            if not isinstance(custom_id, str):
                continue
            results[custom_id] = parsed
        return results


def build_reranker(config: LLMConfig) -> SemanticReranker:
    if not config.enabled:
        return NullReranker()
    return OpenAIReranker(config)
