from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import requests

from job_intake.annotation.ai import AnnotationModelError
from job_intake.annotation.jev import JEV_CRITERIA, JevReviewClient, normalize_jev_review
from job_intake.config.settings import AnnotationConfig


def spec(**changes):
    return AnnotationConfig(
        review_provider="jev", review_model="jev-1.13.0", **changes
    ).resolved_stage("review")


def claim(number=1):
    return {
        "claim_id": f"claim-{number}", "kind": "working_language", "value": "English",
        "unit_id": "unit-1", "source_snippet": "Working language is English.",
        "requirement": "informational", "polarity": "affirmative", "is_inference": False,
    }


def answer(choice="SUPPORTED", probability=0.99, confidence=0.98):
    probabilities = dict.fromkeys(JEV_CRITERIA, (1 - probability) / 4)
    probabilities[choice] = probability
    return {"type": "choice", "choice": choice, "probabilities": probabilities,
            "confidence": confidence}


def response(body, status=200):
    return SimpleNamespace(status_code=status, text=json.dumps(body))


def audit(answers):
    return {"jev": {"model": "jev-1.13.0", "answers": answers,
                    "min_probability": 0.95, "min_confidence": 0.8}}


def test_jev_uses_official_typed_endpoint_and_question_contains_claim(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-private-key")
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        body = kwargs["json"]
        return response({"model": "jev-1.13.0", "answers": {
            key: answer() for key in body["questions"]
        }, "usage": {"input_tokens": 45, "output_tokens": 6}})

    monkeypatch.setattr(requests, "post", post)
    client = JevReviewClient(spec())
    payload, usage = client.review("source", {"units": [{"unit_id": "unit-1", "text":
                                  "Working language is English."}]}, [claim()])
    url, request = calls[0]
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert request["allow_redirects"] is False
    assert request["headers"]["Authorization"] == "Bearer test-private-key"
    question = request["json"]["questions"]["claim-1"]
    assert question["instructions"]["claim"]["value"] == "English"
    assert set(question["criteria"]) == set(JEV_CRITERIA)
    assert payload["reviews"][0]["review_status"] == "SUPPORTED"
    assert payload["reviews"][0]["review_confidence"] == 0.98
    assert "test-private-key" not in json.dumps(payload)
    assert usage == {"input_tokens": 45, "output_tokens": 6, "total_tokens": 51}


@pytest.mark.parametrize(("probability", "confidence"), [(0.94, 0.99), (0.99, 0.79)])
def test_low_confidence_cannot_become_supported_even_from_forged_cached_reviews(
    probability, confidence
):
    raw = audit({"claim-1": answer(probability=probability, confidence=confidence)})
    raw["reviews"] = [{"claim_id": "claim-1", "review_status": "SUPPORTED"}]
    payload = normalize_jev_review(raw, [claim()])
    assert payload["reviews"][0]["review_status"] == "NEEDS_VERIFICATION"


@pytest.mark.parametrize("mutation", ["nan", "boolean", "sum", "wrong_choice", "missing_option"])
def test_invalid_distributions_are_rejected(mutation):
    row = answer()
    if mutation == "nan":
        row["confidence"] = float("nan")
    elif mutation == "boolean":
        row["confidence"] = True
    elif mutation == "sum":
        row["probabilities"]["SUPPORTED"] = 0.5
    elif mutation == "wrong_choice":
        row["choice"] = "UNSUPPORTED"
    else:
        row["probabilities"].pop("CONFLICTING")
    with pytest.raises(ValueError):
        normalize_jev_review(audit({"claim-1": row}), [claim()])


@pytest.mark.parametrize("payload", [None, [], "bad"])
def test_invalid_review_envelope_fails_closed(payload):
    with pytest.raises(ValueError):
        normalize_jev_review(payload, [claim()])


def test_cached_gates_and_model_must_match_authoritative_settings():
    raw = audit({"claim-1": answer(probability=0.8, confidence=0.3)})
    raw["jev"].update(min_probability=0, min_confidence=0)
    with pytest.raises(ValueError):
        normalize_jev_review(raw, [claim()], min_probability=0.95, min_confidence=0.8,
                             model="jev-1.13.0")
    with pytest.raises(ValueError):
        normalize_jev_review(audit({"claim-1": answer()}), [claim()], model="jev-1.14.0")


def test_direct_client_rejects_wrong_endpoint_before_reading_key():
    with pytest.raises(AnnotationModelError, match="official endpoint"):
        JevReviewClient(replace(spec(), base_url="https://wrong.example/v1"))


def test_batches_review_each_claim_and_sum_usage(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    seen = []

    def post(_url, **kwargs):
        keys = list(kwargs["json"]["questions"])
        seen.extend(keys)
        return response({"model": "jev-1.13.0", "answers": {key: answer() for key in keys},
                         "usage": {"input_tokens": 10, "output_tokens": 2}})

    monkeypatch.setattr(requests, "post", post)
    client = JevReviewClient(spec(jev_batch_size=2))
    payload, usage = client.review("source", {"units": []}, [claim(i) for i in range(5)])
    assert seen == [f"claim-{i}" for i in range(5)]
    assert len(payload["reviews"]) == 5
    assert client.calls == 3
    assert usage["input_tokens"] == 30


@pytest.mark.parametrize("failure", ["missing", "extra", "model"])
def test_incomplete_or_mismatched_response_is_not_a_complete_review(monkeypatch, failure):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    body = {"model": "jev-1.13.0", "answers": {"claim-1": answer()}}
    if failure == "missing":
        body["answers"].clear()
    elif failure == "extra":
        body["answers"]["other-id"] = answer()
    else:
        body["model"] = "jev-1.14.0"
    monkeypatch.setattr(requests, "post", lambda *_args, **_kwargs: response(body))
    with pytest.raises(AnnotationModelError):
        JevReviewClient(spec()).review("source", {"units": []}, [claim()])


def test_retry_is_bounded_and_error_body_is_not_disclosed(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr("job_intake.annotation.jev.time.sleep", lambda _: None)
    monkeypatch.setattr(requests, "post", lambda *_args, **_kwargs: response(
        {"error": "private source and credential"}, status=529
    ))
    client = JevReviewClient(replace(spec(), max_retries=1))
    with pytest.raises(AnnotationModelError, match="remained unavailable") as error:
        client.review("source", {"units": []}, [claim()])
    assert client.calls == 2
    assert "private" not in str(error.value)


def test_context_and_claim_limits_fail_before_network(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")

    def forbidden(*_args, **_kwargs):
        pytest.fail("Exceeded request budget reached the network")

    monkeypatch.setattr(requests, "post", forbidden)
    client = JevReviewClient(spec())
    with pytest.raises(AnnotationModelError, match="context"):
        client.review("source", {"units": [{"text": "x" * 30001}]}, [claim()])
    with pytest.raises(AnnotationModelError, match="claim limit"):
        client.review("source", {"units": []}, [claim(i) for i in range(257)])
