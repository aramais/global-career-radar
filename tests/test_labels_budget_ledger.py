from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
import test_labels_tagger as support
from test_labels_tagger import label, payload

from job_intake.annotation.ai import AnnotationModelError, _RetryableModelError
from job_intake.labeling.budget import BudgetExceeded, BudgetGuard
from job_intake.labeling.ledger import LedgerWriter
from job_intake.labeling.tagger import VacancyTagger

config = support.config
record = support.record
streams = support.streams


@pytest.mark.parametrize("maximum", [0, -1, float("inf"), float("nan"), True, "1"])
def test_budget_rejects_invalid_limits(maximum):
    with pytest.raises(ValueError):
        BudgetGuard(maximum, {})


def test_missing_price_prevents_first_post():
    guard = BudgetGuard(1, {"models": {}})
    with pytest.raises(BudgetExceeded, match="price_missing"):
        guard.reserve("x", SimpleNamespace(model="missing", max_output_tokens=20))
    assert guard.spent == 0


def test_pending_reservations_are_counted_across_parallel_jobs():
    guard = BudgetGuard(
        0.01, {"models": {"test": {"input_per_million": 0, "output_per_million": 1}}}
    )
    spec = SimpleNamespace(model="test", max_output_tokens=6000)

    def reserve(_):
        try:
            return guard.reserve("x", spec)
        except BudgetExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(reserve, range(8)))
    assert sum(row is not None for row in result) == 1
    guard.complete(next(row for row in result if row), None)
    assert guard.spent == pytest.approx(0.006)
    with pytest.raises(BudgetExceeded):
        guard.reserve("x", spec)


def test_unposted_and_actual_usage_finish_reservations_once():
    guard = BudgetGuard(1, {"models": {"test": {"input_per_million": 1, "output_per_million": 2}}})
    spec = SimpleNamespace(model="test", max_output_tokens=500)
    reservation = guard.reserve("x", spec)
    guard.complete(reservation, posted=False)
    assert guard.spent == 0
    reservation = guard.reserve("x", spec)
    guard.complete(reservation, {"input_tokens": 100, "output_tokens": 50})
    guard.complete(reservation, {"input_tokens": 100, "output_tokens": 50})
    assert guard.spent == pytest.approx(0.0002)


@pytest.mark.parametrize("kind", ["file", "dangling", "directory"])
def test_ledger_refuses_occupied_path_without_replacing_it(tmp_path, kind):
    path = tmp_path / "ledger.jsonl"
    if kind == "file":
        path.write_text("preserve")
    elif kind == "directory":
        path.mkdir()
    else:
        path.symlink_to(tmp_path / "absent")
    with pytest.raises(FileExistsError):
        LedgerWriter(path)
    assert path.is_symlink() if kind == "dangling" else path.exists()


def test_native_retry_is_reserved_and_recorded_as_two_attempts(
    config, streams, record, tmp_path, monkeypatch
):
    config = replace(config, max_retries=1, retry_backoff=0.001)
    monkeypatch.setenv(config.api_key_env, "fake-key")
    prices = {
        "models": {
            config.tag_model: {"input_per_million": 1, "output_per_million": 1},
            config.escalate_model: {"input_per_million": 1, "output_per_million": 1},
        }
    }
    guard = BudgetGuard(1, prices)
    path = tmp_path / "retry.jsonl"
    ledger = LedgerWriter(path)
    service = VacancyTagger(config, streams, budget=guard, ledger=ledger)
    calls = []

    def generate(*_):
        calls.append(1)
        if len(calls) == 1:
            raise _RetryableModelError("SECRET-PROVIDER-TEXT", code="connection_error")
        return payload(record), {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}

    monkeypatch.setattr(service.client.tag, "_gemini", generate)
    value = label(service, record, use_ai=True)
    ledger.close()
    assert value.status == "labeled" and len(calls) == 2 and service.stats["calls"] == 2
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["attempt"] for row in rows] == [1, 2]
    assert rows[0]["status"] == "failed" and rows[0]["usage"] == {}
    assert rows[1]["status"] == "complete" and rows[1]["usage"]["output_tokens"] == 50
    assert guard.spent > 0.00015 and "SECRET-PROVIDER-TEXT" not in path.read_text()


def test_budget_exhaustion_stops_retry_before_second_post(
    config, streams, record, tmp_path, monkeypatch
):
    config = replace(config, max_retries=1, retry_backoff=0.001)
    monkeypatch.setenv(config.api_key_env, "fake-key")
    price = {"input_per_million": 0, "output_per_million": 1}
    guard = BudgetGuard(
        config.max_output_tokens / 1_000_000 * 1.5, {"models": {config.tag_model: price}}
    )
    service = VacancyTagger(
        config, streams, budget=guard, ledger=LedgerWriter(tmp_path / "ledger.jsonl")
    )
    calls = []

    def fail(*_):
        calls.append(1)
        raise _RetryableModelError("secret", code="connection_error")

    monkeypatch.setattr(service.client.tag, "_gemini", fail)
    with pytest.raises(BudgetExceeded):
        label(service, record, use_ai=True)
    assert len(calls) == 1


def test_missing_api_key_has_no_attempt_and_no_charge(
    config, streams, record, tmp_path, monkeypatch
):
    monkeypatch.delenv(config.api_key_env, raising=False)
    prices = {"models": {config.tag_model: {"input_per_million": 1, "output_per_million": 1}}}
    guard = BudgetGuard(1, prices)
    path = tmp_path / "ledger.jsonl"
    service = VacancyTagger(config, streams, budget=guard, ledger=LedgerWriter(path))
    value = label(service, record, use_ai=True)
    assert value.status == "error" and value.errors[0]["code"] == "missing_api_key"
    assert not path.exists() and guard.spent == 0


def test_unknown_failed_client_remains_billing_exposure(config, streams, record, tmp_path):
    from test_labels_tagger import service

    path = tmp_path / "ledger.jsonl"
    ledger = LedgerWriter(path)
    tagger = service(config, streams, [AnnotationModelError("SECRET")], ledger=ledger)
    value = label(tagger, record, use_ai=True)
    ledger.close()
    assert value.status == "error" and tagger.budget.spent > 0
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["usage"] == {} and rows[0]["status"] == "failed"


def test_unrepresentable_usage_cannot_reopen_budget():
    guard = BudgetGuard(
        1, {"models": {"test": {"input_per_million": 1.0, "output_per_million": 2.0}}}
    )
    spec = SimpleNamespace(model="test", max_output_tokens=500)
    reservation = guard.reserve("x", spec)
    guard.complete(reservation, {"input_tokens": 10**400, "output_tokens": 1})
    assert guard.spent == 1
    with pytest.raises(BudgetExceeded):
        guard.reserve("x", spec)
