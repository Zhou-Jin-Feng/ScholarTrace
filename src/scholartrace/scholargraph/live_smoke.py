"""Bounded live ScholarGraph Basic smoke with a sanitized public summary."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime

import httpx

from scholartrace.contracts import Budget, BudgetLimits
from scholartrace.scholargraph.client import ScholarGraphClient
from scholartrace.scholargraph.evaluation import EvaluationQuestion
from scholartrace.scholargraph.models import MethodMetrics, QueryMethod
from scholartrace.scholargraph.routing import (
    CapabilityRouter,
    ScholarGraphScope,
    ScholarGraphTool,
)


async def run_live_smoke(
    *,
    base_url: str,
    questions: Mapping[str, EvaluationQuestion],
    scopes: Mapping[str, ScholarGraphScope],
    seed_id: str = "sg-eligible-01",
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    if set(scopes) != set(questions):
        raise ValueError("M5 routing scopes must cover the frozen question sets")
    selected = questions.get(seed_id)
    if selected is None or selected.subset != "scholargraph_eligible_eval":
        raise ValueError("M5 live seed must belong to the eligible subset")
    started_at = datetime.now(UTC)
    async with httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as http:
        client = ScholarGraphClient(
            client=http,
            base_url=base_url,
            timeout_seconds=10,
            max_get_attempts=2,
        )
        live = await client.live()
        ready = await client.ready()
        capabilities = await client.capabilities()
        before = await client.metrics()
        router = CapabilityRouter()
        boundary_decisions = {
            question_id: router.decide(
                scope=scopes[question_id],
                capabilities=capabilities,
                budget=Budget(limits=BudgetLimits(max_api_calls=1)),
            )
            for question_id, question in questions.items()
            if question.subset == "boundary_eval"
        }
        result = await ScholarGraphTool(
            client=client,
            capabilities=capabilities,
            router=router,
        ).execute(
            question=selected.question,
            scope=scopes[seed_id],
            budget=Budget(
                limits=BudgetLimits(
                    max_api_calls=1,
                    max_duration_seconds=600,
                    max_cost_cny=0,
                )
            ),
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        )
        after = await client.metrics()
    before_basic = _method_metrics(before.methods, QueryMethod.BASIC)
    after_basic = _method_metrics(after.methods, QueryMethod.BASIC)
    request_delta = after_basic.request_count - before_basic.request_count
    token_delta = (
        after_basic.tokens_visible_lower_bound
        - before_basic.tokens_visible_lower_bound
    )
    boundary_pass = all(
        decision.action in {"skip", "reject"} and not decision.eligible
        for decision in boundary_decisions.values()
    )
    answer = result.answer or ""
    passed = all(
        (
            live.status == "live",
            ready.ready,
            result.decision.action == "call",
            result.decision.method == QueryMethod.BASIC,
            result.query_status in {"succeeded", "degraded"},
            bool(answer),
            not result.fallback_to_b3,
            result.attempts == 1,
            request_delta == 1,
            token_delta >= 0,
            boundary_pass,
        )
    )
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "fixture_kind": "bounded_scholargraph_basic_live_smoke",
        "quality_scope": "Online compatibility smoke; not a B3/B4 quality comparison.",
        "service_version": capabilities.service_version,
        "graphrag_version": capabilities.graphrag_version,
        "corpus_id": capabilities.corpus.corpus_id,
        "corpus_manifest_sha256": capabilities.corpus.corpus_manifest_sha256,
        "document_count": capabilities.corpus.document_count,
        "evidence_level": capabilities.corpus.evidence_level,
        "readiness_status": ready.status,
        "readiness_checks": ready.checks.model_dump(mode="json"),
        "method": result.decision.method,
        "provider_status": result.query_status,
        "provider_duration_seconds": round(result.provider_duration_seconds or 0, 3),
        "query_http_duration_seconds": round(result.duration_seconds, 3),
        "wall_duration_seconds": round(
            (datetime.now(UTC) - started_at).total_seconds(), 3
        ),
        "query_attempts": result.attempts,
        "answer_bytes": len(answer.encode("utf-8")),
        "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "source_ref_count": 0,
        "basic_request_count_delta": request_delta,
        "tokens_visible_lower_bound_delta": token_delta,
        "token_coverage": after_basic.token_coverage,
        "service_sample_window_size": after.sample_window_size,
        "service_basic_metrics_after": {
            "request_count": after_basic.request_count,
            "succeeded_count": after_basic.succeeded_count,
            "degraded_count": after_basic.degraded_count,
            "timeout_count": after_basic.timeout_count,
            "failed_count": after_basic.failed_count,
            "latency_p50_seconds": after_basic.latency_p50_seconds,
            "latency_p95_seconds": after_basic.latency_p95_seconds,
            "queue_p50_seconds": after_basic.queue_p50_seconds,
            "queue_p95_seconds": after_basic.queue_p95_seconds,
        },
        "boundary_question_count": len(boundary_decisions),
        "boundary_routing_pass": boundary_pass,
        "boundary_reason_codes": sorted(
            {decision.reason_code for decision in boundary_decisions.values()}
        ),
        "provider_reported_cost_cny": 0.0,
        "paid_model_api_calls": 0,
        "passed": passed,
        "notes": [
            "The public report stores no question, answer, prompt, or raw diagnostics.",
            "ScholarGraph output remains abstract-level auxiliary context.",
            "This smoke does not establish B4 quality benefit or default-enable approval.",
            "Global and DRIFT were not enabled or called.",
        ],
    }


def _method_metrics(
    metrics: list[MethodMetrics],
    method: QueryMethod,
) -> MethodMetrics:
    matches = [item for item in metrics if item.method == method]
    if len(matches) != 1:
        raise ValueError(f"ScholarGraph metrics missing unique {method.value} entry")
    return matches[0]
