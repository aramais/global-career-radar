"""Optional progress events and a flushed CLI status stream, separate from AI config."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

ProgressCallback = Callable[[dict[str, Any]], None]


def emit_progress(progress: ProgressCallback | None, event: str, **fields: Any) -> None:
    if progress is not None:
        progress({"event": event, **fields})


def _label(value: Any) -> str:
    # Source titles and configured model names must not emit terminal control sequences.
    return " ".join("".join(
        char for char in str(value) if char.isprintable()
    ).split())[:160]


def _error_details(event: dict) -> str:
    fields = []
    if event.get("code"):
        fields.append("code=" + _label(event["code"]))
    if type(event.get("http_status")) is int:
        fields.append("http=" + str(event["http_status"]))
    if event.get("finish_reason"):
        fields.append("finish=" + _label(event["finish_reason"]))
    if event.get("batch_id"):
        fields.append("batch=" + _label(event["batch_id"]))
    return "; " + ", ".join(fields) if fields else ""


class AnnotationProgressReporter:
    """Render only known metadata; never print prompts, response bodies or exceptions."""

    def __init__(self, write: Callable[[str], None], *, heartbeat_seconds: float = 10) -> None:
        self.write = write
        self.heartbeat_seconds = heartbeat_seconds
        self.started = time.monotonic()
        self.last_output = self.started
        self.job = ""
        self.chunk = ""
        self.activity = "initializing"
        self.reported_input_tokens = 0
        self.reported_output_tokens = 0
        self.completed_requests = 0
        self.unknown_usage_requests = 0
        self.failed_requests = 0
        self.failed_input_tokens = 0
        self.failed_output_tokens = 0
        self.failed_unknown_usage = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> AnnotationProgressReporter:
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _output(self, message: str) -> None:
        elapsed = time.monotonic() - self.started
        self.write(f"[{elapsed:.1f}s] {message}")
        self.last_output = time.monotonic()

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            with self._lock:
                if time.monotonic() - self.last_output >= self.heartbeat_seconds:
                    context = " ".join(part for part in (self.job, self.chunk) if part)
                    self._output(f"Still running: {context} {self.activity}".strip())

    def __call__(self, event: dict[str, Any]) -> None:
        with self._lock:
            name = event.get("event")
            if name == "initializing":
                self._output(f"Starting annotation ({_label(event['mode'])}); loading saved jobs.")
            elif name == "models":
                self._output(
                    f"Extract: {_label(event['extract_provider'])}/"
                    f"{_label(event['extract_model'])}; "
                    f"review: {_label(event['review_provider'])}/{_label(event['review_model'])}."
                )
            elif name == "prefilter_models":
                self._output(
                    f"Prefilter screen: {_label(event['screen_provider'])}/"
                    f"{_label(event['screen_model'])}; "
                    f"verify exclusions: {_label(event['verify_provider'])}/"
                    f"{_label(event['verify_model'])}."
                )
            elif name == "run_start":
                self._output("Reading saved vacancies.")
            elif name == "jobs_loaded":
                self._output(f"Loaded {event['total']} vacancies.")
            elif name == "job_start":
                self.job = f"Job {event['index']}/{event['total']}"
                self.chunk = ""
                self.activity = "preparing source text"
                self._output(
                    f"{self.job}: {_label(event['company'])} — {_label(event['title'])}"
                )
            elif name == "chunks_loaded":
                self._output(f"{self.job}: {event['total']} source chunks.")
            elif name in {"local_skip", "prefilter_local_skip"}:
                self.activity = "saving local exclusion"
                reason = _label(event.get("reason_code", event.get("reason", "confirmed blocker")))
                self._output(
                    f"{self.job}: skipped AI by local rules "
                    f"({reason}, "
                    f"blockers={event.get('blockers', 1)})."
                )
            elif name == "prefilter_start":
                self.chunk = ""
                self.activity = "checking preliminary filter cache"
                self._output(f"{self.job}: preliminary filtering started.")
            elif name == "prefilter_stage":
                self.activity = (
                    f"prefilter {_label(event['stage'])}: {_label(event['provider'])}/"
                    f"{_label(event['model'])}"
                )
                self._output(f"{self.job}: {self.activity} started.")
            elif name == "prefilter_cache_hit":
                self._output(
                    f"{self.job}: prefilter cache hit "
                    f"({_label(event.get('stage', 'decision'))})."
                )
            elif name == "prefilter_complete":
                route = _label(event.get("routing", "full_review"))
                decision = _label(event.get("decision", "uncertain"))
                reason = _label(event.get("reason_code", "uncertain"))
                self.activity = "saving preliminary decision" if route == "suppressed" else (
                    "preparing full annotation"
                )
                self._output(
                    f"{self.job}: prefilter route={route}, "
                    f"decision={decision}, reason={reason}."
                )
            elif name == "prefilter_error":
                self.activity = "continuing with full annotation"
                self._output(
                    f"{self.job}: prefilter failed ({_label(event['error_type'])})"
                    f"{_error_details(event)}; "
                    "continuing with full annotation."
                )
            elif name == "chunk_start":
                self.chunk = f"chunk {event['index']}/{event['total']}"
                self.activity = "checking cache"
                self._output(f"{self.job} {self.chunk}: checking cache.")
            elif name == "stage_start":
                self.activity = (
                    f"{_label(event['stage'])}: {_label(event['provider'])}/"
                    f"{_label(event['model'])}"
                )
                self._output(f"{self.job} {self.chunk}: {self.activity} started.")
            elif name == "request_start":
                self._output(
                    f"{self.job} {self.chunk}: {_label(event.get('stage', 'model'))} "
                    f"request attempt {event['attempt']}/{event['attempts']} "
                    f"(timeout {event['timeout']}s)."
                )
            elif name == "request_retry":
                self.activity = f"retrying {_label(event.get('stage', 'model'))}"
                self._output(
                    f"{self.job} {self.chunk}: retry {event['attempt']}/{event['attempts']} "
                    f"in {event['backoff']}s."
                )
            elif name == "request_complete":
                # Count only successful new requests, never cache events. Unknown
                # usage remains explicit instead of becoming an apparent zero bill.
                token_counts = [event.get(name) for name in ("input_tokens", "output_tokens")]
                valid_counts = [
                    value if type(value) is int and value >= 0 else 0 for value in token_counts
                ]
                usage_known = event.get("usage_known") is True and all(
                    type(value) is int and value >= 0 for value in token_counts
                )
                self.reported_input_tokens += valid_counts[0]
                self.reported_output_tokens += valid_counts[1]
                self.completed_requests += 1
                self.unknown_usage_requests += int(not usage_known)
                uncertainty = "" if usage_known else "; usage incomplete"
                self._output(
                    f"{self.job} {self.chunk}: {_label(event.get('stage', 'model'))} "
                    f"request completed in {event['elapsed']:.1f}s; "
                    f"tokens input={valid_counts[0]}, output={valid_counts[1]}{uncertainty}."
                )
            elif name == "request_failed":
                token_counts = [event.get(key) for key in ("input_tokens", "output_tokens")]
                valid = [
                    value if type(value) is int and value >= 0 else 0 for value in token_counts
                ]
                known = event.get("usage_known") is True and all(
                    type(value) is int and value >= 0 for value in token_counts
                )
                self.failed_requests += 1
                self.failed_input_tokens += valid[0]
                self.failed_output_tokens += valid[1]
                self.failed_unknown_usage += int(not known)
                usage = (
                    f"tokens input={valid[0]}, output={valid[1]}"
                    if known else "usage unavailable"
                )
                self._output(
                    f"{self.job} {self.chunk}: {_label(event.get('stage', 'model'))} "
                    f"request failed in {event['elapsed']:.1f}s{_error_details(event)}; {usage}."
                )
            elif name in {"review_batch", "review_batch_start"}:
                self._output(
                    f"{self.job} {self.chunk}: review batch "
                    f"{event['index']}/{event['total']}"
                    + (f", {event['claims']} claims." if "claims" in event else ".")
                )
            elif name == "stage_complete":
                self.activity = "checking reviewed evidence"
                self._output(
                    f"{self.job} {self.chunk}: {_label(event['stage'])} "
                    f"completed in {event['elapsed']:.1f}s."
                )
            elif name == "cache_hit":
                self._output(
                    f"{self.job} {self.chunk}: cache hit ({_label(event['stage'])})."
                )
            elif name == "chunk_error":
                self.activity = "preserving partial result"
                self._output(
                    f"{self.job} {self.chunk}: {_label(event['error_type'])}"
                    f"{_error_details(event)}; "
                    "partial evidence retained."
                )
            elif name == "chunk_complete":
                self.activity = "loading next chunk"
                self._output(
                    f"{self.job} {self.chunk}: completed, {event['claims']} claims reviewed."
                )
            elif name == "annotation_complete":
                self.activity = "scoring profiles and saving"
                self._output(
                    f"{self.job}: annotation {_label(event['status'])}, "
                    f"{event['claims']} claims."
                )
            elif name == "job_complete":
                error_counts = (
                    f"annotation errors={event['annotation_errors']}, "
                    f"prefilter errors={event['prefilter_errors']}"
                    if "annotation_errors" in event else f"annotation errors={event['errors']}"
                )
                self._output(
                    f"{self.job}: saved {event['evaluations']} profile evaluations "
                    f"in {event['elapsed']:.1f}s; {error_counts}."
                )
                self.activity = "loading next vacancy"
            elif name == "job_error":
                self._output(f"{self.job}: failed ({_label(event['error_type'])}).")
            elif name == "run_complete":
                self.activity = "complete"
                self._output(
                    f"Finished: {event['persisted']}/{event['total']} vacancies saved "
                    f"in {event['elapsed']:.1f}s."
                )
                if "prefilter_suppressed" in event:
                    self._output(
                        f"Prefilter totals: suppressed={event['prefilter_suppressed']}, "
                        f"full review={event.get('prefilter_full_review', 0)}, "
                        f"local skipped={event.get('prefilter_local_skipped', 0)}, "
                        f"cache hits={event.get('prefilter_cache_hits', 0)}, "
                        f"errors={event.get('prefilter_errors', 0)}."
                    )
                uncertainty = (
                    f" Usage incomplete for {self.unknown_usage_requests} completed requests."
                    if self.unknown_usage_requests else ""
                )
                self._output(
                    "Reported tokens from new completed requests: "
                    f"input={self.reported_input_tokens}, output={self.reported_output_tokens}, "
                    f"requests={self.completed_requests}.{uncertainty}"
                )
                if self.failed_requests:
                    self._output(
                        "Reported tokens from failed attempts: "
                        f"input={self.failed_input_tokens}, output={self.failed_output_tokens}, "
                        f"attempts={self.failed_requests}, "
                        f"usage unavailable={self.failed_unknown_usage}."
                    )
