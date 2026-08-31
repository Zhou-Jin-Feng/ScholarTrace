from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx
import pytest

from scholartrace.contracts import BudgetLimits
from scholartrace.model_provider import (
    ApiCallBudget,
    ApiCallCounter,
    OpenAICompatiblePlanGenerator,
    ProviderBudgetError,
    ProviderInferenceError,
    ProviderSettings,
)


def _draft() -> dict[str, object]:
    return {
        "title": "Adaptive retrieval evidence plan",
        "objective": "Evaluate retrieval adaptation and evidence verification.",
        "subquestions": [
            {
                "question": "Which signals trigger retrieval adaptation?",
                "evidence_required": "fulltext",
                "priority": "critical",
            },
            {
                "question": "How is evidence attribution evaluated?",
                "evidence_required": "fulltext",
                "priority": "high",
            },
        ],
        "inclusion_criteria": ["Public research with reproducible evaluation"],
        "exclusion_criteria": ["Uncitable marketing material"],
        "sources": ["arxiv", "openalex", "crossref"],
    }


def _generator(
    http: httpx.AsyncClient,
    *,
    protocol: str = "responses",
    budget: ApiCallBudget | None = None,
) -> OpenAICompatiblePlanGenerator:
    test_credential = "private-" + "test-key"
    return OpenAICompatiblePlanGenerator(
        client=http,
        settings=ProviderSettings(
            base_url="https://provider.test",
            api_key=test_credential,
            timeout_seconds=20,
        ),
        model="gpt-5.6-terra",
        protocol=protocol,  # type: ignore[arg-type]
        call_counter=ApiCallCounter(
            budget
            or ApiCallBudget(
                max_calls=1,
                max_input_token_upper_bound=20_000,
                max_output_tokens=900,
                max_cost_cny=5,
            )
        ),
        plan_limits=BudgetLimits(
            max_llm_input_tokens=12_000,
            max_llm_output_tokens=900,
            max_total_tokens=12_900,
            max_api_calls=1,
            max_model_calls=1,
            max_cost_cny=5,
            max_duration_seconds=180,
        ),
        retrieval_cutoff=date(2026, 8, 30),
    )


def test_responses_protocol_generates_strict_deterministically_bounded_plan() -> None:
    seen: dict[str, object] = {}
    test_credential = "private-" + "test-key"

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["idempotency"] = request.headers["idempotency-key"]
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "model": "gpt-5.6-terra",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(_draft())}
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 700,
                    "output_tokens": 180,
                    "input_tokens_details": {"cached_tokens": 20},
                    "output_tokens_details": {"reasoning_tokens": 30},
                },
            },
        )

    async def scenario() -> object:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await _generator(http).generate_plan_with_usage(
                task_id="task:api-smoke",
                question="How should adaptive RAG systems validate evidence provenance?",
                idempotency_key="effect:m6:api-smoke:coordinator",
            )

    result = asyncio.run(scenario())
    assert result.plan.task_id == "task:api-smoke"
    assert result.plan.status == "draft"
    assert result.plan.budget.limits.max_api_calls == 1
    assert result.plan.budget.usage.api_calls == 0
    assert result.usage.input_tokens == 700
    assert result.usage.cached_input_tokens == 20
    assert result.usage.reasoning_output_tokens == 30
    assert seen["url"] == "https://provider.test/v1/responses"
    assert seen["authorization"] == f"Bearer {test_credential}"
    assert seen["idempotency"] == "effect:m6:api-smoke:coordinator"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["max_output_tokens"] == 900
    assert payload["text"]["format"]["strict"] is True


def test_chat_completions_protocol_is_supported_without_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert str(request.url) == "https://provider.test/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_test",
                "model": "gpt-5.6-terra",
                "choices": [{"message": {"role": "assistant", "content": json.dumps(_draft())}}],
                "usage": {
                    "prompt_tokens": 650,
                    "completion_tokens": 160,
                    "prompt_tokens_details": {"cached_tokens": 0},
                    "completion_tokens_details": {"reasoning_tokens": 15},
                },
            },
        )

    async def scenario() -> object:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await _generator(http, protocol="chat_completions").generate_plan_with_usage(
                task_id="task:chat-smoke",
                question="How should adaptive RAG systems validate evidence provenance?",
                idempotency_key="effect:m6:chat-smoke:coordinator",
            )

    result = asyncio.run(scenario())
    assert result.protocol == "chat_completions"
    assert result.usage.output_tokens == 160
    assert calls == 1


def test_preflight_budget_blocks_network_call() -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        raise AssertionError("budget rejection must happen before network access")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
            await _generator(
                http,
                budget=ApiCallBudget(
                    max_calls=1,
                    max_input_token_upper_bound=10,
                    max_output_tokens=1,
                    max_cost_cny=5,
                ),
            ).generate_plan_with_usage(
                task_id="task:blocked",
                question="How should adaptive RAG systems validate evidence provenance?",
                idempotency_key="effect:m6:blocked:coordinator",
            )

    with pytest.raises(ProviderBudgetError, match="input token"):
        asyncio.run(scenario())


def test_next_reservation_uses_actual_cost_when_provider_usage_exceeds_proxy() -> None:
    counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=2,
            max_input_token_upper_bound=100,
            max_output_tokens=10,
            max_cost_cny=0.006,
        )
    )
    counter.reserve(input_token_upper_bound=100)
    counter.record_actual(0.004)
    assert counter.actual_reference_cost_cny == 0.004
    with pytest.raises(ProviderBudgetError, match="reference cost"):
        counter.reserve(input_token_upper_bound=100)


def test_provider_error_hides_response_body_and_key() -> None:
    test_credential = "private-" + "test-key"

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    400,
                    content=(test_credential + " and private provider diagnostics").encode(),
                )
            )
        ) as http:
            await _generator(http).generate_plan_with_usage(
                task_id="task:provider-error",
                question="How should adaptive RAG systems validate evidence provenance?",
                idempotency_key="effect:m6:provider-error:coordinator",
            )

    with pytest.raises(ProviderInferenceError) as caught:
        asyncio.run(scenario())
    assert test_credential not in str(caught.value)
    assert "diagnostics" not in str(caught.value)


def test_response_model_must_match_selected_model() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "model": "gpt-5.6-luna",
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    },
                )
            )
        ) as http:
            await _generator(http).generate_plan_with_usage(
                task_id="task:model-mismatch",
                question="How should adaptive RAG systems validate evidence provenance?",
                idempotency_key="effect:m6:model-mismatch:coordinator",
            )

    with pytest.raises(ProviderInferenceError, match="did not match"):
        asyncio.run(scenario())
