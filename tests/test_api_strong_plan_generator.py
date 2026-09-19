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
    base_url: str = "https://provider.test",
) -> OpenAICompatiblePlanGenerator:
    test_credential = "private-" + "test-key"
    return OpenAICompatiblePlanGenerator(
        client=http,
        settings=ProviderSettings(
            base_url=base_url,
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


def test_deepseek_chat_uses_json_object_and_max_tokens() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen["payload"] = payload
        return httpx.Response(
            200,
            json={
                "id": "deepseek_test",
                "model": "deepseek-flash",
                "choices": [{"message": {"content": json.dumps(_draft())}}],
                "usage": {"prompt_tokens": 700, "completion_tokens": 180},
            },
        )

    async def scenario() -> object:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            generator = _generator(
                http,
                protocol="chat_completions",
                base_url="https://api.deepseek.com/v1",
            )
            generator.model = "deepseek-flash"
            return await generator.generate_plan_with_usage(
                task_id="task:deepseek-chat",
                question="How should adaptive RAG systems validate evidence provenance?",
                idempotency_key="effect:deepseek-chat:coordinator",
            )

    result = asyncio.run(scenario())
    assert result.response_model == "deepseek-flash"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["max_tokens"] == 900
    assert "max_completion_tokens" not in payload
    assert payload["response_format"] == {"type": "json_object"}
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert "exactly these keys" in messages[-1]["content"]
    assert "exactly 2 subquestions" in messages[-1]["content"]
    assert "Both criteria fields must be JSON arrays" in messages[-1]["content"]


def test_deepseek_json_object_normalizes_scalar_criteria_lists() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        payload = _draft()
        payload["inclusion_criteria"] = "Public research with reproducible evaluation"
        payload["exclusion_criteria"] = "Uncitable marketing material"
        return httpx.Response(
            200,
            json={
                "id": "deepseek_scalar_criteria",
                "model": "deepseek-flash",
                "choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"prompt_tokens": 700, "completion_tokens": 180},
            },
        )

    async def scenario() -> object:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            generator = _generator(
                http,
                protocol="chat_completions",
                base_url="https://api.deepseek.com/v1",
            )
            generator.model = "deepseek-flash"
            return await generator.generate_plan_with_usage(
                task_id="task:deepseek-scalar-criteria",
                question="How should adaptive RAG systems validate evidence provenance?",
                idempotency_key="effect:deepseek-scalar-criteria:coordinator",
            )

    result = asyncio.run(scenario())
    assert result.plan.inclusion_criteria == ["Public research with reproducible evaluation"]
    assert result.plan.exclusion_criteria == ["Uncitable marketing material"]


def test_invalid_plan_exposes_only_safe_validation_diagnostics() -> None:
    private_model_content = "private-model-content-must-not-leak"

    def handler(_: httpx.Request) -> httpx.Response:
        invalid = _draft()
        invalid["sources"] = ["unsupported-source"]
        invalid["private_field"] = private_model_content
        return httpx.Response(
            200,
            json={
                "id": "deepseek_invalid_plan",
                "model": "deepseek-flash",
                "choices": [{"message": {"content": json.dumps(invalid)}}],
                "usage": {"prompt_tokens": 700, "completion_tokens": 180},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            generator = _generator(
                http,
                protocol="chat_completions",
                base_url="https://api.deepseek.com/v1",
            )
            generator.model = "deepseek-flash"
            await generator.generate_plan_with_usage(
                task_id="task:deepseek-invalid-plan",
                question="How should adaptive RAG systems validate evidence provenance?",
                idempotency_key="effect:deepseek-invalid-plan:coordinator",
            )

    with pytest.raises(ProviderInferenceError) as caught:
        asyncio.run(scenario())
    diagnostics = caught.value.diagnostics
    assert diagnostics is not None
    assert diagnostics["kind"] == "model_validation"
    assert "sources.0" in diagnostics["paths"]
    assert "private_field" in diagnostics["paths"]
    assert private_model_content not in str(diagnostics)


def test_truncated_chat_completion_exposes_only_safe_finish_diagnostic() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "id": "deepseek_truncated_plan",
                        "model": "deepseek-flash",
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {"content": "{\"title\": \"truncated"},
                            }
                        ],
                        "usage": {"prompt_tokens": 700, "completion_tokens": 800},
                    },
                )
            )
        ) as http:
            generator = _generator(
                http,
                protocol="chat_completions",
                base_url="https://api.deepseek.com/v1",
            )
            generator.model = "deepseek-flash"
            await generator.generate_plan_with_usage(
                task_id="task:deepseek-truncated-plan",
                question="How should adaptive RAG systems validate evidence provenance?",
                idempotency_key="effect:deepseek-truncated-plan:coordinator",
            )

    with pytest.raises(ProviderInferenceError) as caught:
        asyncio.run(scenario())
    assert caught.value.diagnostics == {"kind": "completion", "finish_reason": "length"}


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
