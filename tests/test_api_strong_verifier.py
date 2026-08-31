from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from scholartrace.contracts import Claim, Evidence
from scholartrace.model_provider import (
    ApiCallBudget,
    ApiCallCounter,
    OpenAICompatibleSemanticVerifier,
    ProviderBudgetError,
    ProviderInferenceError,
    ProviderSettings,
)

TEST_CREDENTIAL = "credential-sentinel"


def _claim() -> Claim:
    return Claim(
        claim_id="claim:test:01",
        text="A retrieval evaluator selects corrective actions.",
        claim_type="fact",
        evidence_ids=["evidence:test:01"],
        origin="author_stated",
    )


def _evidence() -> Evidence:
    quote = "A retrieval evaluator selects corrective actions."
    import hashlib

    return Evidence(
        evidence_id="evidence:test:01",
        canonical_paper_id="doi:10.1/test",
        quote=quote,
        evidence_level="abstract",
        content_sha256=hashlib.sha256(quote.encode()).hexdigest(),
    )


def _response(*, model: str = "gpt-5.6-terra") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "resp_verifier_test",
            "model": model,
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "status": "supported",
                                    "reason": "The exact Evidence directly states the Claim.",
                                    "recommended_action": "keep",
                                }
                            ),
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 300, "output_tokens": 80},
        },
    )


def _backend(
    client: httpx.AsyncClient,
    *,
    budget: ApiCallBudget | None = None,
) -> OpenAICompatibleSemanticVerifier:
    return OpenAICompatibleSemanticVerifier(
        client=client,
        settings=ProviderSettings(
            base_url="https://provider.test",
            api_key=TEST_CREDENTIAL,
            timeout_seconds=20,
        ),
        profile_id="api-strong",
        model="gpt-5.6-terra",
        protocol="responses",
        call_counter=ApiCallCounter(
            budget
            or ApiCallBudget(
                max_calls=1,
                max_input_token_upper_bound=10_000,
                max_output_tokens=500,
                max_cost_cny=1,
            )
        ),
    )


def test_responses_verifier_is_strict_bounded_and_records_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["idempotency"] = request.headers["idempotency-key"]
        seen["payload"] = json.loads(request.content)
        return _response()

    async def scenario() -> tuple[object, OpenAICompatibleSemanticVerifier]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = _backend(client)
            result = await backend.verify(claim=_claim(), evidence=[_evidence()])
            return result, backend

    result, backend = asyncio.run(scenario())
    assert result.status == "supported"
    assert len(backend.records) == 1
    assert backend.records[0].usage.input_tokens == 300
    assert backend.call_counter.actual_reference_cost_cny == backend.records[0].reference_cost_cny
    assert seen["url"] == "https://provider.test/v1/responses"
    assert seen["authorization"] == f"Bearer {TEST_CREDENTIAL}"
    assert str(seen["idempotency"]).startswith("m6-verifier-")
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["text"]["format"]["strict"] is True


def test_verifier_budget_rejects_before_network() -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        raise AssertionError("budget failure must happen before network access")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
            await _backend(
                client,
                budget=ApiCallBudget(
                    max_calls=1,
                    max_input_token_upper_bound=10,
                    max_output_tokens=1,
                    max_cost_cny=1,
                ),
            ).verify(claim=_claim(), evidence=[_evidence()])

    with pytest.raises(ProviderBudgetError, match="input token"):
        asyncio.run(scenario())


def test_verifier_rejects_model_drift_and_hides_provider_body() -> None:
    async def model_drift() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: _response(model="another-model"))
        ) as client:
            await _backend(client).verify(claim=_claim(), evidence=[_evidence()])

    with pytest.raises(ProviderInferenceError, match="model did not match"):
        asyncio.run(model_drift())

    secret = TEST_CREDENTIAL

    async def provider_failure() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    400,
                    content=f"{secret} private diagnostic".encode(),
                )
            )
        ) as client:
            await _backend(client).verify(claim=_claim(), evidence=[_evidence()])

    with pytest.raises(ProviderInferenceError) as caught:
        asyncio.run(provider_failure())
    assert secret not in str(caught.value)
    assert "diagnostic" not in str(caught.value)
