from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from sa04_synthetic import make_synthetic_inputs

from scholartrace.model_provider.plan_generator import (
    ApiCallBudget,
    ApiCallCounter,
    ProviderBudgetError,
    ProviderInferenceError,
)
from scholartrace.model_provider.settings import ProviderSettings
from scholartrace.verification_ablation import (
    AblationReportRequest,
    OpenAICompatibleAblationReportGenerator,
    SharedBudgetGuard,
    prepare_variant_input,
)


def _request() -> AblationReportRequest:
    frozen = next(iter(make_synthetic_inputs().values()))
    dispositions = tuple(
        {
            "claim_id": claim.claim_id,
            "status": "unverified",
            "included": True,
            "marker": "[UNVERIFIED]",
        }
        for claim in frozen.claims
    )
    from scholartrace.verification_ablation.models import AblationClaimDisposition

    prepared = prepare_variant_input(
        frozen=frozen,
        variant="V-off",
        dispositions=tuple(AblationClaimDisposition.model_validate(item) for item in dispositions),
        max_context_characters=40_000,
    )
    return AblationReportRequest(
        question_id=frozen.question_id,
        split=frozen.split,
        question=frozen.question,
        variant="V-off",
        frozen_input_sha256=frozen.input_sha256,
        evidence_identity_sha256=prepared.evidence_identity_sha256,
        configuration_sha256="config:test",
        report_length_limit_chars=5_000,
        prepared_context=prepared.prepared_context,
        allowed_evidence_ids=prepared.allowed_evidence_ids,
        dispositions=prepared.dispositions,
    )


def _generator(handler):
    counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=1,
            max_input_token_upper_bound=100_000,
            max_output_tokens=1_200,
            max_cost_cny=3,
        )
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://provider.test/v1",
        trust_env=False,
    )
    generator = OpenAICompatibleAblationReportGenerator(
        client=client,
        settings=ProviderSettings(
            base_url="https://provider.test/v1",
            api_key="fake",
            timeout_seconds=5,
        ),
        model_profile="api-strong",
        model="test-model",
        protocol="responses",
        call_counter=counter,
        timeout_seconds=5,
    )
    return client, generator, counter


def _response(request: AblationReportRequest, support_status: str = "unverified") -> httpx.Response:
    context = json.loads(request.prepared_context)
    claim = context["claims"][0]
    draft = {
        "title": "Fixture report",
        "answer_status": "answered",
        "findings": [
            {
                "claim_id": claim["claim_id"],
                "claim": claim["text"],
                "evidence_ids": claim["evidence_ids"],
                "support_status": support_status,
            }
        ],
        "limitations": ["Mock transport only."],
    }
    return httpx.Response(
        200,
        json={
            "model": "test-model",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(draft)}],
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        },
    )


def test_sa04_adapter_success_is_strict_and_metered() -> None:
    request = _request()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _response(request)

    client, generator, counter = _generator(handler)
    try:
        generated = asyncio.run(generator.generate(request))
    finally:
        asyncio.run(client.aclose())
    assert generated.status == "succeeded"
    assert generated.input_tokens == 100
    assert generated.output_tokens == 50
    assert generated.provider_api_calls == 1
    assert counter.attempted_calls == 1
    assert counter.actual_reference_cost_cny > 0


def test_sa04_adapter_rejects_status_drift_without_retry() -> None:
    request = _request()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return _response(request, support_status="verified_supported")

    client, generator, counter = _generator(handler)
    try:
        with pytest.raises(ProviderInferenceError, match="semantic status") as captured:
            asyncio.run(generator.generate(request))
    finally:
        asyncio.run(client.aclose())
    assert counter.attempted_calls == 1
    assert counter.actual_reference_cost_cny > 0
    assert captured.value.diagnostics == {"category": "status_drift"}
    assert captured.value.generated.input_tokens == 100
    assert captured.value.generated.output_tokens == 50
    assert captured.value.generated.reference_cost_cny > 0


def test_sa04_adapter_rejects_evidence_bound_to_another_claim() -> None:
    request = _request()
    context = json.loads(request.prepared_context)
    first, second = context["claims"][:2]
    first["evidence_ids"] = []
    first["counter_evidence_ids"] = []
    drifted = replace(
        request,
        prepared_context=json.dumps(context, ensure_ascii=False, sort_keys=True),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        draft = {
            "title": "Invalid binding",
            "answer_status": "answered",
            "findings": [
                {
                    "claim_id": first["claim_id"],
                    "claim": first["text"],
                    "evidence_ids": second["evidence_ids"],
                    "support_status": "unverified",
                }
            ],
            "limitations": ["Mock transport only."],
        }
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(draft)}
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        )

    client, generator, counter = _generator(handler)
    try:
        with pytest.raises(ProviderInferenceError, match="unrelated") as captured:
            asyncio.run(generator.generate(drifted))
    finally:
        asyncio.run(client.aclose())
    assert captured.value.diagnostics == {"category": "claim_evidence_binding"}
    assert counter.actual_reference_cost_cny > 0


def test_sa04_adapter_preserves_timeout_as_timeout() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("mock timeout")

    request = _request()
    client, generator, counter = _generator(handler)
    try:
        with pytest.raises(TimeoutError):
            asyncio.run(generator.generate(request))
    finally:
        asyncio.run(client.aclose())
    assert counter.attempted_calls == 1


def test_sa04_adapter_rejects_invalid_json_and_counts_attempt() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    request = _request()
    client, generator, counter = _generator(handler)
    try:
        with pytest.raises(ProviderInferenceError, match="not JSON") as captured:
            asyncio.run(generator.generate(request))
    finally:
        asyncio.run(client.aclose())
    assert counter.attempted_calls == 1
    assert captured.value.diagnostics["category"] == "text_json"
    assert "response_sha256" in captured.value.diagnostics


def test_shared_budget_guard_blocks_cross_condition_overrun() -> None:
    guard = SharedBudgetGuard(max_calls=2, max_reference_cost_cny=1.0)
    guard.observe_reserve(source="verifier", attempted_total=1, reserved_total=0.6)
    with pytest.raises(ProviderBudgetError, match="shared provider"):
        guard.observe_reserve(source="report", attempted_total=1, reserved_total=0.5)
