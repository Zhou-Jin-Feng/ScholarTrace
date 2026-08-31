from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from pydantic import TypeAdapter

from scholartrace.scholargraph.evaluation import load_question_sets
from scholartrace.scholargraph.live_smoke import run_live_smoke
from scholartrace.scholargraph.routing import ScholarGraphScope

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"
SCOPES = ROOT / "evaluation" / "seeds" / "m5_scholargraph_routing_scopes.json"
CAPABILITIES = json.loads(
    (ROOT / "tests" / "fixtures" / "m5" / "scholargraph_capabilities.json").read_text(
        "utf-8"
    )
)


def _metrics(request_count: int, tokens: int) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "service_version": "1.2.0",
        "request_id": f"metrics-{request_count}",
        "graphrag_version": "3.1.2",
        "sample_window_size": 100,
        "methods": [
            {
                "method": "basic",
                "request_count": request_count,
                "succeeded_count": request_count,
                "degraded_count": 0,
                "timeout_count": 0,
                "failed_count": 0,
                "success_rate": 1 if request_count else 0,
                "degraded_rate": 0,
                "latency_p50_seconds": 2.5 if request_count else None,
                "latency_p95_seconds": 2.5 if request_count else None,
                "queue_p50_seconds": 0 if request_count else None,
                "queue_p95_seconds": 0 if request_count else None,
                "tokens_visible_lower_bound": tokens,
                "token_coverage": "visible_lower_bound" if tokens else "unavailable",
            }
        ],
    }


def test_live_summary_is_bounded_sanitized_and_checks_boundary_routing() -> None:
    metrics_calls = 0
    query_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal metrics_calls, query_calls
        if request.url.path == "/api/v1/health/live":
            return httpx.Response(
                200,
                json={
                    "schema_version": "1.0.0",
                    "service_version": "1.2.0",
                    "request_id": "live",
                    "status": "live",
                },
            )
        if request.url.path == "/api/v1/health/ready":
            return httpx.Response(
                200,
                json={
                    "schema_version": "1.0.0",
                    "service_version": "1.2.0",
                    "request_id": "ready",
                    "status": "ready",
                    "checks": {
                        "workspace_config": True,
                        "index_files": True,
                        "index_identity": True,
                        "container_runtime": True,
                    },
                },
            )
        if request.url.path == "/api/v1/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        if request.url.path == "/api/v1/metrics":
            metrics_calls += 1
            return httpx.Response(200, json=_metrics(metrics_calls - 1, (metrics_calls - 1) * 120))
        if request.url.path == "/api/v1/query":
            query_calls += 1
            return httpx.Response(
                200,
                json={
                    "schema_version": "1.0.0",
                    "service_version": "1.2.0",
                    "request_id": "query",
                    "graphrag_version": "3.1.2",
                    "corpus_id": "openalex-rag-abstracts-2020-2025-v1",
                    "method": "basic",
                    "purpose": "general",
                    "status": "succeeded",
                    "duration_seconds": 2.5,
                    "evidence_level": "abstract",
                    "answer": "private answer body",
                    "source_refs": [],
                    "diagnostics": {
                        "public_message": "Query completed.",
                        "exit_code": 0,
                        "stderr_present": False,
                        "internal_error_events": 0,
                        "warning_events": 0,
                        "container_cleanup_succeeded": None,
                        "response_truncated": False,
                    },
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    scope_document = json.loads(SCOPES.read_text("utf-8"))
    scopes = TypeAdapter(dict[str, ScholarGraphScope]).validate_python(
        scope_document["scopes"]
    )
    summary = asyncio.run(
        run_live_smoke(
            base_url="http://testserver",
            questions=load_question_sets(ELIGIBLE, BOUNDARY),
            scopes=scopes,
            transport=httpx.MockTransport(handler),
        )
    )
    serialized = json.dumps(summary, ensure_ascii=False)
    assert summary["passed"]
    assert summary["boundary_question_count"] == 6
    assert summary["basic_request_count_delta"] == 1
    assert summary["tokens_visible_lower_bound_delta"] == 120
    assert summary["provider_duration_seconds"] == 2.5
    assert summary["service_basic_metrics_after"]["queue_p95_seconds"] == 0
    assert "private answer body" not in serialized
    assert "What evidence compares" not in serialized
    assert metrics_calls == 2
    assert query_calls == 1
