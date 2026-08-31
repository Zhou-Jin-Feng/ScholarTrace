from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import TypeAdapter

from scholartrace.scholargraph.capacity import run_basic_repeat_capacity
from scholartrace.scholargraph.evaluation import load_question_sets
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


def test_three_repeat_capacity_is_sanitized_and_aggregated() -> None:
    query_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal query_calls
        path = request.url.path
        if path == "/api/v1/health/live":
            return httpx.Response(
                200,
                json={
                    "schema_version": "1.0.0",
                    "service_version": "1.2.0",
                    "request_id": "live",
                    "status": "live",
                },
            )
        if path == "/api/v1/health/ready":
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
        if path == "/api/v1/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        if path == "/api/v1/metrics":
            return httpx.Response(200, json=_metrics(query_calls))
        if path == "/api/v1/query":
            query_calls += 1
            return httpx.Response(200, json=_query_response(query_calls))
        raise AssertionError(f"unexpected path: {path}")

    scopes = TypeAdapter(dict[str, ScholarGraphScope]).validate_python(
        json.loads(SCOPES.read_text("utf-8"))["scopes"]
    )
    summary = asyncio.run(
        run_basic_repeat_capacity(
            base_url="http://testserver",
            questions=load_question_sets(ELIGIBLE, BOUNDARY),
            scopes=scopes,
            transport=httpx.MockTransport(handler),
        )
    )
    serialized = json.dumps(summary, ensure_ascii=False)
    assert summary["passed"]
    assert summary["repeat_count"] == 3
    assert summary["success_count"] == 3
    assert summary["basic_request_count_delta"] == 3
    assert summary["tokens_visible_lower_bound_delta"] == 57
    assert summary["provider_duration_p50_seconds"] == 2.0
    assert summary["provider_duration_p95_seconds"] == 2.9
    assert summary["service_basic_metrics_after"]["queue_p95_seconds"] == 0
    assert query_calls == 3
    assert "private capacity answer" not in serialized
    assert "What evidence compares" not in serialized


def test_repeat_count_is_bounded() -> None:
    scopes = TypeAdapter(dict[str, ScholarGraphScope]).validate_python(
        json.loads(SCOPES.read_text("utf-8"))["scopes"]
    )
    with pytest.raises(ValueError, match="between 3 and 10"):
        asyncio.run(
            run_basic_repeat_capacity(
                base_url="http://testserver",
                questions=load_question_sets(ELIGIBLE, BOUNDARY),
                scopes=scopes,
                repeat_count=2,
            )
        )


def _metrics(request_count: int) -> dict[str, object]:
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
                "latency_p50_seconds": 2 if request_count else None,
                "latency_p95_seconds": 3 if request_count else None,
                "queue_p50_seconds": 0 if request_count else None,
                "queue_p95_seconds": 0 if request_count else None,
                "tokens_visible_lower_bound": request_count * 19,
                "token_coverage": "visible_lower_bound" if request_count else "unavailable",
            }
        ],
    }


def _query_response(run_index: int) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "service_version": "1.2.0",
        "request_id": f"query-{run_index}",
        "graphrag_version": "3.1.2",
        "corpus_id": "openalex-rag-abstracts-2020-2025-v1",
        "method": "basic",
        "purpose": "general",
        "status": "succeeded",
        "duration_seconds": float(run_index),
        "evidence_level": "abstract",
        "answer": f"private capacity answer {run_index}",
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
    }
