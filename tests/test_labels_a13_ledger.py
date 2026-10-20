from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
import test_labels_tagger as support

from job_intake.annotation.experiments import ledger_metrics
from job_intake.labeling.budget import BudgetGuard
from job_intake.labeling.ledger import LedgerWriter
from job_intake.labeling.tagger import VacancyTagger

config = support.config
streams = support.streams
record = support.record


def start(ledger, call_id="first"):
    ledger({"event": "request_start", "stage": "tag", "model": "fake",
            "attempt": 1, "call_id": call_id})


def terminal(ledger, *, status="complete", call_id="first"):
    ledger({"event": "request_" + status, "stage": "tag", "call_id": call_id,
            "elapsed": 0.25, "input_tokens": 100, "output_tokens": 50,
            "usage_known": status == "complete"})


def rows(ledger):
    return [json.loads(line) for line in ledger.path.read_text().splitlines()]


def measure(items, model="fake"):
    return ledger_metrics(
        items, prices={"schema": "eval-prices-v1", "currency": "USD",
                       "as_of": "2026-10-20", "models": {model: {
                           "input_per_million": 1, "output_per_million": 1}}},
        maximum_price_age_days=0, measured_at=datetime(2026, 10, 20, tzinfo=UTC),
    )


class FaultyStream:
    def __init__(self, original, mode):
        self.original, self.mode, self.armed = original, mode, True

    def __getattr__(self, name):
        return getattr(self.original, name)

    def write(self, value):
        if self.armed and self.mode in {"before", "partial", "short"}:
            self.armed = False
            if self.mode != "before":
                written = self.original.write(value[:len(value) // 2])
                if self.mode == "short":
                    return written
            raise OSError("injected append failure")
        return self.original.write(value)

    def flush(self):
        self.original.flush()
        if self.armed and self.mode == "flush":
            self.armed = False
            raise OSError("injected flush failure")


def inject_once(ledger, monkeypatch, mode):
    if mode == "fsync":
        from job_intake.labeling import ledger as module

        original = module.os.fsync
        calls = []

        def fail(fd):
            if not calls:
                calls.append(fd)
                raise OSError("injected fsync failure")
            return original(fd)

        monkeypatch.setattr(module.os, "fsync", fail)
    elif mode == "after":
        original = ledger._write
        calls = []

        def fail(row):
            original(row)
            if not calls:
                calls.append(row["call_id"])
                raise OSError("injected post-commit failure")

        monkeypatch.setattr(ledger, "_write", fail)
    else:
        ledger._stream = FaultyStream(ledger._stream, mode)


@pytest.mark.parametrize("status", ["complete", "failed"])
@pytest.mark.parametrize("failure", ["before", "partial", "short", "flush", "fsync", "after"])
def test_terminal_failure_recovers_exactly_once_on_close(tmp_path, monkeypatch, status, failure):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    inject_once(ledger, monkeypatch, failure)
    with pytest.raises(OSError):
        terminal(ledger, status=status)
    ledger.close()
    ledger.close()
    recovered = rows(ledger)
    assert len(recovered) == 1 and recovered[0]["call_id"] == "first"
    assert recovered[0]["status"] == status
    assert recovered[0]["usage"] == (
        {"input_tokens": 100, "output_tokens": 50} if status == "complete" else {}
    )
    measured = measure(recovered)
    assert measured["attempts"] == 1
    assert measured["complete"] is (status == "complete")
    assert measured["uncertain_bills"] == (status == "failed")
    assert measured["cost_usd"] == pytest.approx(0.00015 if status == "complete" else 0)
    assert not ledger._pending


@pytest.mark.parametrize("failure", ["before", "partial", "flush", "fsync", "after"])
def test_failed_callback_after_complete_keeps_original_known_bill(
    tmp_path, monkeypatch, failure,
):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    inject_once(ledger, monkeypatch, failure)
    with pytest.raises(OSError):
        terminal(ledger)
    terminal(ledger, status="failed")
    ledger.close()
    recovered = rows(ledger)
    assert len(recovered) == 1 and recovered[0]["status"] == "complete"
    assert measure(recovered)["cost_usd"] == pytest.approx(0.00015)


@pytest.mark.parametrize("failure", ["before", "partial", "flush", "fsync", "after"])
def test_interrupted_close_can_be_retried_without_reopening_occupied_file(
    tmp_path, monkeypatch, failure,
):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    inject_once(ledger, monkeypatch, failure)
    with pytest.raises(OSError):
        ledger.close()
    ledger.close()
    recovered = rows(ledger)
    assert len(recovered) == 1 and recovered[0]["status"] == "interrupted"
    assert recovered[0]["usage"] == {}
    measured = measure(recovered)
    assert measured["attempts"] == measured["uncertain_bills"] == 1
    assert measured["complete"] is False


def test_new_request_cannot_erase_unfinished_attempt(tmp_path):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    start(ledger, "second")
    terminal(ledger, call_id="second")
    ledger.close()
    recovered = rows(ledger)
    assert [row["call_id"] for row in recovered] == ["first", "second"]
    assert [row["status"] for row in recovered] == ["interrupted", "complete"]
    assert measure(recovered)["uncertain_bills"] == 1


def test_new_start_recovers_previous_terminal_before_accepting_another_attempt(
    tmp_path, monkeypatch,
):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    inject_once(ledger, monkeypatch, "before")
    with pytest.raises(OSError):
        terminal(ledger)
    start(ledger, "second")
    assert [row["call_id"] for row in rows(ledger)] == ["first"]
    terminal(ledger, call_id="second")
    ledger.close()
    recovered = rows(ledger)
    assert [row["call_id"] for row in recovered] == ["first", "second"]
    assert all(row["status"] == "complete" for row in recovered)
    assert measure(recovered)["cost_usd"] == pytest.approx(0.0003)


def test_unresolved_terminal_write_prevents_the_next_post(tmp_path, monkeypatch):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    original = ledger._write

    def fail(row):
        raise OSError("injected persistent write error")

    monkeypatch.setattr(ledger, "_write", fail)
    with pytest.raises(OSError):
        terminal(ledger)
    with pytest.raises(OSError):
        start(ledger, "second")
    assert {row["call_id"] for row in ledger._pending.values()} == {"first"}
    monkeypatch.setattr(ledger, "_write", original)
    ledger.close()
    assert [row["call_id"] for row in rows(ledger)] == ["first"]


def test_start_cannot_reuse_call_id_of_uncommitted_terminal(tmp_path, monkeypatch):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    inject_once(ledger, monkeypatch, "before")
    with pytest.raises(OSError):
        terminal(ledger)
    with pytest.raises(ValueError, match="call_id"):
        start(ledger)
    ledger.close()
    assert len(rows(ledger)) == 1


def test_terminal_io_error_is_recovered_before_a_different_worker_can_post(tmp_path, monkeypatch):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    original = ledger._write

    def fail(row):
        raise OSError("injected persistent write error")

    monkeypatch.setattr(ledger, "_write", fail)
    with pytest.raises(OSError):
        terminal(ledger)
    with ThreadPoolExecutor(max_workers=1) as pool, pytest.raises(OSError):
        pool.submit(start, ledger, "second").result()
    assert {row["call_id"] for row in ledger._pending.values()} == {"first"}
    monkeypatch.setattr(ledger, "_write", original)
    ledger.close()
    assert [row["call_id"] for row in rows(ledger)] == ["first"]


def test_duplicate_committed_call_id_refused_before_new_attempt(tmp_path):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    terminal(ledger)
    terminal(ledger)
    with pytest.raises(ValueError, match="call_id"):
        start(ledger)
    ledger.close()
    assert len(rows(ledger)) == 1


def test_terminal_for_other_call_cannot_consume_pending_attempt(tmp_path):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    with pytest.raises(ValueError, match="call_id"):
        terminal(ledger, call_id="different")
    ledger.close()
    recovered = rows(ledger)
    assert len(recovered) == 1 and recovered[0]["call_id"] == "first"
    assert recovered[0]["status"] == "interrupted"


def test_failed_rollback_is_fail_closed_and_keeps_pending(tmp_path):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    original = ledger._stream

    class RollbackFailure(FaultyStream):
        def truncate(self, *args):
            raise OSError("injected rollback failure")

    ledger._stream = RollbackFailure(original, "partial")
    with pytest.raises(OSError):
        terminal(ledger)
    with pytest.raises(OSError):
        start(ledger, "second")
    with pytest.raises(OSError):
        ledger.close()
    assert len(ledger._pending) == 1
    original.close()


@pytest.mark.parametrize("moment", ["before", "after"])
def test_interrupting_commit_bookkeeping_rolls_back_line_and_memo(tmp_path, moment):
    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)

    class InterruptedCommit(dict):
        armed = True

        def __setitem__(self, key, value):
            if self.armed:
                self.armed = False
                if moment == "after":
                    super().__setitem__(key, value)
                raise KeyboardInterrupt
            return super().__setitem__(key, value)

    ledger._committed = InterruptedCommit()
    with pytest.raises(KeyboardInterrupt):
        terminal(ledger)
    assert ledger.path.read_bytes() == b"" and not ledger._committed
    ledger.close()
    recovered = rows(ledger)
    assert len(recovered) == 1 and recovered[0]["status"] == "complete"
    assert measure(recovered)["cost_usd"] == pytest.approx(0.00015)


def test_broken_rollback_cannot_use_committed_memo_to_consume_pending(tmp_path, monkeypatch):
    from job_intake.labeling import ledger as module

    ledger = LedgerWriter(tmp_path / "ledger.jsonl")
    start(ledger)
    original_fsync = module.os.fsync
    calls = []

    def rollback_fsync_error(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("injected rollback fsync error")
        return original_fsync(fd)

    class InterruptedCommit(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            raise KeyboardInterrupt

    ledger._committed = InterruptedCommit()
    monkeypatch.setattr(module.os, "fsync", rollback_fsync_error)
    with pytest.raises(KeyboardInterrupt):
        terminal(ledger)
    assert ledger.path.read_bytes() == b"" and ledger._committed
    with pytest.raises(OSError):
        terminal(ledger)
    with pytest.raises(OSError):
        start(ledger, "second")
    with pytest.raises(OSError):
        ledger.close()
    assert len(ledger._pending) == 1
    ledger._stream.close()


@pytest.mark.parametrize("failure", ["before", "partial", "fsync", "after"])
def test_native_client_terminal_io_failure_preserves_actual_bill(
    config, streams, record, tmp_path, monkeypatch, failure,
):
    monkeypatch.setenv(config.api_key_env, "fake-test-key")
    prices = {"models": {config.tag_model: {"input_per_million": 1, "output_per_million": 1}}}
    budget = BudgetGuard(1, prices)
    ledger = LedgerWriter(tmp_path / "native.jsonl")
    tagger = VacancyTagger(config, streams, ledger=ledger, budget=budget)
    original = ledger._open
    armed = []

    def open_and_inject():
        original()
        if not armed:
            armed.append(True)
            inject_once(ledger, monkeypatch, failure)

    monkeypatch.setattr(ledger, "_open", open_and_inject)
    calls = []

    def provider(*args):
        calls.append(True)
        return support.payload(record), {"input_tokens": 100, "output_tokens": 50,
                                         "total_tokens": 150}

    monkeypatch.setattr(tagger.client.tag, "_gemini", provider)
    result = support.label(tagger, record, use_ai=True)
    ledger.close()
    recovered = rows(ledger)
    assert len(calls) == 1 and result.status == "error"
    assert len(recovered) == 1 and recovered[0]["status"] == "complete"
    assert budget.spent == pytest.approx(0.00015)
    measured = measure(recovered, config.tag_model)
    assert measured["complete"] is True and measured["attempts"] == 1
    assert measured["cost_usd"] == pytest.approx(budget.spent)
