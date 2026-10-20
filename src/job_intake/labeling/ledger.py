"""Attempt-only JSONL accounting; no prompts, responses, sources or provider text."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path


class LedgerWriter:
    def __init__(self, path, *, run_id=None):
        self.path = Path(path)
        if os.path.lexists(self.path):
            raise FileExistsError("Ledger output already exists")
        self.run_id = run_id or uuid.uuid4().hex
        self._lock = threading.Lock()
        self._pending = {}
        self._committed = {}
        self._stream = None
        self._broken = False
        self._closed = False
        self._labels_observed = 0
        self._labels_escalated = 0

    def _open(self):
        if self._broken:
            raise OSError("Ledger append could not be rolled back; accounting is incomplete")
        if self._closed:
            raise ValueError("Ledger is closed")
        if self._stream is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # An unbuffered binary append has a byte offset we can safely restore
            # after short writes or flush/fsync errors. Existing ledgers stay JSONL.
            self._stream = self.path.open("x+b", buffering=0)

    def _write(self, row):
        if self._broken:
            raise OSError("Ledger rollback failed; accounting remains incomplete")
        payload = (json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        call_id = row["call_id"]
        if call_id in self._committed:
            if self._committed[call_id] != payload:
                raise ValueError("Ledger call_id already identifies a different attempt")
            return
        self._open()
        offset = self._stream.tell()
        try:
            if self._stream.write(payload) != len(payload):
                raise OSError("Ledger append was incomplete")
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._committed[call_id] = payload
        except BaseException as error:
            try:
                self._stream.truncate(offset)
                self._stream.seek(offset)
                self._stream.flush()
                os.fsync(self._stream.fileno())
                self._committed.pop(call_id, None)
            except BaseException:
                # Continuing could append after a partial line or duplicate a
                # bill. Retain pending ownership and reject all later POSTs.
                self._broken = True
                error.add_note("Ledger rollback failed; pending accounting remains incomplete")
            raise

    def _finish_pending(self, key):
        pending = self._pending[key]
        if "_terminal" not in pending:
            row = {name: value for name, value in pending.items() if name != "_started"}
            row.update(wall_seconds=max(0.0, time.monotonic() - pending["_started"]),
                       status="interrupted", usage={})
            pending["_terminal"] = row
        self._write(pending["_terminal"])
        # Ownership only moves after the full line has passed flush and fsync.
        del self._pending[key]

    def __call__(self, event):
        name = event.get("event")
        if name not in {"request_start", "request_complete", "request_failed"}:
            return
        key = (threading.get_ident(), event.get("stage"))
        with self._lock:
            if name == "request_start":
                self._open()  # Refuse occupied/unwritable output before the POST.
                call_id = event.get("call_id") or uuid.uuid4().hex
                if call_id in self._committed:
                    raise ValueError("Ledger call_id is already committed")
                for pending_key, pending in self._pending.items():
                    if pending["call_id"] == call_id:
                        if pending_key == key and "_terminal" not in pending:
                            return  # Idempotent delivery of the same start event.
                        raise ValueError("Ledger call_id already belongs to a pending attempt")
                for pending_key in list(self._pending):
                    if "_terminal" in self._pending[pending_key]:
                        self._finish_pending(pending_key)
                if key in self._pending:
                    self._finish_pending(key)
                row = {
                    "call_id": call_id,
                    "run_id": self.run_id,
                    "requested_at": datetime.now(UTC).isoformat(),
                    "attempt": event.get("attempt", 1),
                    "model": event["model"],
                    "stage": event.get("stage"),
                    "cache_reuse": False,
                    "original_call_id": None,
                    "_started": time.monotonic(),
                }
                self._pending[key] = row
                return
            pending = self._pending.get(key)
            if pending is None:
                return
            if event.get("call_id") not in {None, pending["call_id"]}:
                if event["call_id"] in self._committed:
                    return
                raise ValueError("Terminal call_id does not identify the pending attempt")
            if "_terminal" not in pending:
                row = {name: value for name, value in pending.items() if name != "_started"}
                row["wall_seconds"] = max(
                    0.0, event.get("elapsed", time.monotonic() - pending["_started"]),
                )
                row["usage"] = {
                    key: event[key]
                    for key in (
                        "input_tokens", "output_tokens", "thinking_tokens", "cached_input_tokens",
                    )
                    if type(event.get(key)) is int and event[key] >= 0
                }
                if event.get("usage_known") is False:
                    row["usage"] = {}
                row["status"] = "complete" if name == "request_complete" else "failed"
                pending["_terminal"] = row
            # A client can emit request_failed after the complete callback raises
            # an I/O error. Retry the original terminal, preserving known usage.
            self._finish_pending(key)

    def record_cache(self, original_call_id, *, stage=None, model=None):
        if not original_call_id:
            return
        with self._lock:
            self._write(
                {
                    "call_id": uuid.uuid4().hex,
                    "run_id": self.run_id,
                    "original_call_id": original_call_id,
                    "cache_reuse": True,
                    "stage": stage,
                    "model": model,
                }
            )

    def observe_label(self, escalated):
        """Aggregate completed outcomes across taggers sharing this run writer.

        This is in-memory progress accounting, never a new ledger or data write.
        """
        with self._lock:
            self._labels_observed += 1
            self._labels_escalated += bool(escalated)
            return {
                "labeled": self._labels_observed,
                "escalated": self._labels_escalated,
                "share": self._labels_escalated / self._labels_observed,
            }

    def close(self):
        with self._lock:
            if self._broken:
                raise OSError("Ledger rollback failed; accounting remains incomplete")
            # Leave the owned descriptor open on append failure so graceful close
            # can retry later without reopening an occupied ledger. Not SIGKILL
            # durability: an unresolved failure raises instead of claiming success.
            while self._pending:
                self._finish_pending(next(iter(self._pending)))
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            self._closed = True
