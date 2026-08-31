from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from scholartrace.contracts import Budget, BudgetLimits, BudgetUsage
from scholartrace.scholargraph.client import ScholarGraphClient
from scholartrace.scholargraph.models import CapabilitiesResponse, QueryMethod, QueryPurpose
from scholartrace.scholargraph.routing import (
    CapabilityRouter,
    ScholarGraphScope,
    ScholarGraphTool,
    ScholarGraphToolResult,
)

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = CapabilitiesResponse.model_validate_json(
    (ROOT / "tests" / "fixtures" / "m5" / "scholargraph_capabilities.json").read_bytes()
)


def _budget(**usage: object) -> Budget:
    return Budget(
        limits=BudgetLimits(max_api_calls=4, max_duration_seconds=600),
        usage=BudgetUsage.model_validate(usage),
    )


def test_basic_and_local_are_selected_from_structured_scope() -> None:
    router = CapabilityRouter()
    basic = router.decide(
        scope=ScholarGraphScope(topic="retrieval_augmented_generation"),
        capabilities=CAPABILITIES,
        budget=_budget(),
    )
    assert basic.action == "call"
    assert basic.method == QueryMethod.BASIC
    assert basic.timeout_seconds == 180

    local = router.decide(
        scope=ScholarGraphScope(
            topic="retrieval_augmented_generation",
            purpose=QueryPurpose.ENTITY_NEIGHBORHOOD,
        ),
        capabilities=CAPABILITIES,
        budget=_budget(),
    )
    assert local.action == "call"
    assert local.method == QueryMethod.LOCAL
    assert local.timeout_seconds == 300


def test_topic_year_fulltext_and_write_boundaries_skip_or_reject() -> None:
    router = CapabilityRouter()
    cases = (
        (ScholarGraphScope(topic="other"), "topic_out_of_scope", "skip"),
        (
            ScholarGraphScope(
                topic="retrieval_augmented_generation",
                publication_year_to=2026,
            ),
            "year_out_of_scope",
            "skip",
        ),
        (
            ScholarGraphScope(
                topic="retrieval_augmented_generation",
                publication_year_from=2026,
            ),
            "year_out_of_scope",
            "skip",
        ),
        (
            ScholarGraphScope(
                topic="retrieval_augmented_generation",
                requires_verified_full_text=True,
            ),
            "verified_full_text_required",
            "skip",
        ),
        (
            ScholarGraphScope(
                topic="retrieval_augmented_generation",
                requires_index_write=True,
            ),
            "index_write_not_supported",
            "reject",
        ),
    )
    for scope, reason, action in cases:
        decision = router.decide(
            scope=scope,
            capabilities=CAPABILITIES,
            budget=_budget(),
        )
        assert decision.reason_code == reason
        assert decision.action == action
        assert not decision.eligible


def test_long_disabled_methods_and_budget_exhaustion_fail_closed() -> None:
    router = CapabilityRouter()
    global_scope = ScholarGraphScope(
        topic="retrieval_augmented_generation",
        purpose=QueryPurpose.EXPERIMENT,
        requested_method=QueryMethod.GLOBAL,
    )
    assert (
        router.decide(
            scope=global_scope,
            capabilities=CAPABILITIES,
            budget=_budget(),
        ).reason_code
        == "long_method_not_approved"
    )
    assert (
        CapabilityRouter(allow_long_methods=True)
        .decide(
            scope=global_scope,
            capabilities=CAPABILITIES,
            budget=_budget(),
        )
        .reason_code
        == "method_disabled"
    )
    exhausted = router.decide(
        scope=ScholarGraphScope(topic="retrieval_augmented_generation"),
        capabilities=CAPABILITIES,
        budget=_budget(api_calls=4),
    )
    assert exhausted.reason_code == "api_budget_exhausted"


def test_tool_skips_without_network_and_falls_back_on_provider_failure() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            503,
            json={
                "schema_version": "1.0.0",
                "service_version": "1.2.0",
                "request_id": "m5-error",
                "status": "failed",
                "error": {
                    "code": "service_not_ready",
                    "message": "Service is not ready.",
                    "retryable": True,
                    "field": None,
                },
            },
        )

    async def scenario() -> tuple[ScholarGraphToolResult, ScholarGraphToolResult]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ) as http:
            tool = ScholarGraphTool(
                client=ScholarGraphClient(client=http),
                capabilities=CAPABILITIES,
            )
            skipped = await tool.execute(
                question="Unrelated question",
                scope=ScholarGraphScope(topic="other"),
                budget=_budget(),
            )
            failed = await tool.execute(
                question="What is RAG?",
                scope=ScholarGraphScope(topic="retrieval_augmented_generation"),
                budget=_budget(),
            )
            return skipped, failed

    skipped, failed = asyncio.run(scenario())
    assert skipped.decision.action == "skip"
    assert skipped.fallback_to_b3
    assert failed.error_code == "provider_error"
    assert failed.fallback_to_b3
    assert calls == 1
