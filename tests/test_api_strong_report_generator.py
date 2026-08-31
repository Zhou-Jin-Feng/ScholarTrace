from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from scholartrace.model_provider import (
    ApiCallBudget,
    ApiCallCounter,
    OpenAICompatibleReportGenerator,
    ProviderBudgetError,
    ProviderInferenceError,
    ProviderSettings,
)
from scholartrace.scholargraph.experiment import ReportGenerationRequest

EVIDENCE_ID = "evidence:documind:test-01"
TEST_CREDENTIAL = "credential-sentinel"


def _draft(*, evidence_id: str = EVIDENCE_ID) -> dict[str, object]:
    return {
        "title": "Adaptive retrieval evidence synthesis",
        "answer_status": "answered",
        "findings": [
            {
                "claim": "Adaptive systems use retrieval-quality signals to select actions.",
                "evidence_ids": [evidence_id],
                "support_status": "supported",
            }
        ],
        "limitations": ["The frozen packet contains one Evidence item."],
    }


def _request(*, report_length_limit: int = 4000) -> ReportGenerationRequest:
    return ReportGenerationRequest(
        question_id="sg-eligible-02",
        subset="scholargraph_eligible_eval",
        question="How do adaptive RAG methods decide when to retrieve?",
        variant="B4",
        paper_pool_sha256="a" * 64,
        report_length_limit=report_length_limit,
        evidence_context=json.dumps(
            {
                "evidence": [
                    {
                        "evidence_id": EVIDENCE_ID,
                        "quote": "A retrieval evaluator selects corrective actions.",
                    }
                ]
            },
            sort_keys=True,
        ),
        allowed_evidence_ids=frozenset({EVIDENCE_ID}),
        scholargraph_context="Abstract-only graph synthesis.",
    )


def _generator(
    client: httpx.AsyncClient,
    *,
    budget: ApiCallBudget | None = None,
) -> OpenAICompatibleReportGenerator:
    return OpenAICompatibleReportGenerator(
        client=client,
        settings=ProviderSettings(
            base_url="https://provider.test",
            api_key=TEST_CREDENTIAL,
            timeout_seconds=20,
        ),
        model_profile="api-strong-gpt-5.6-terra",
        model="gpt-5.6-terra",
        protocol="responses",
        call_counter=ApiCallCounter(
            budget
            or ApiCallBudget(
                max_calls=1,
                max_input_token_upper_bound=30_000,
                max_output_tokens=1200,
                max_cost_cny=5,
            )
        ),
    )


def _response(draft: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "resp_report_test",
            "model": "gpt-5.6-terra",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(draft)}],
                }
            ],
            "usage": {
                "input_tokens": 900,
                "output_tokens": 220,
                "input_tokens_details": {"cached_tokens": 10},
                "output_tokens_details": {"reasoning_tokens": 20},
            },
        },
    )


def test_responses_report_is_strict_bounded_and_evidence_linked() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["idempotency"] = request.headers["idempotency-key"]
        seen["payload"] = json.loads(request.content)
        return _response(_draft())

    async def scenario() -> object:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _generator(client).generate(_request())

    result = asyncio.run(scenario())
    assert result.status == "succeeded"
    assert result.model_calls == 1
    assert result.provider_api_calls == 1
    assert result.input_tokens == 900
    assert EVIDENCE_ID in result.report
    assert "Abstract-only graph synthesis" not in result.report
    assert seen["url"] == "https://provider.test/v1/responses"
    assert seen["authorization"] == f"Bearer {TEST_CREDENTIAL}"
    assert str(seen["idempotency"]).startswith("m6-b3b4-b4-")
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["text"]["format"]["strict"] is True
    assert payload["max_output_tokens"] == 1200
    evidence_items = payload["text"]["format"]["schema"]["$defs"][
        "ReportFindingDraft"
    ]["properties"]["evidence_ids"]["items"]
    assert evidence_items == {"enum": [EVIDENCE_ID], "type": "string"}


def test_report_rejects_evidence_id_outside_allowlist() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: _response(_draft(evidence_id="evidence:invented"))
            )
        ) as client:
            await _generator(client).generate(_request())

    with pytest.raises(ProviderInferenceError, match="allowlist"):
        asyncio.run(scenario())


def test_insufficient_evidence_report_must_have_no_findings() -> None:
    invalid = {
        **_draft(),
        "answer_status": "insufficient_evidence",
    }

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: _response(invalid))
        ) as client:
            await _generator(client).generate(_request())

    with pytest.raises(ProviderInferenceError, match="invalid report draft"):
        asyncio.run(scenario())


def test_preflight_budget_blocks_report_network_call() -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        raise AssertionError("budget rejection must happen before report network access")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
            await _generator(
                client,
                budget=ApiCallBudget(
                    max_calls=1,
                    max_input_token_upper_bound=10,
                    max_output_tokens=1,
                    max_cost_cny=1,
                ),
            ).generate(_request())

    with pytest.raises(ProviderBudgetError, match="input token"):
        asyncio.run(scenario())


def test_provider_report_error_hides_body_and_credential() -> None:
    secret = TEST_CREDENTIAL

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    400,
                    content=f"{secret} private provider diagnostic".encode(),
                )
            )
        ) as client:
            await _generator(client).generate(_request())

    with pytest.raises(ProviderInferenceError) as caught:
        asyncio.run(scenario())
    assert secret not in str(caught.value)
    assert "diagnostic" not in str(caught.value)
