"""Bounded sequential ScholarGraph Basic repeat with sanitized aggregates."""

from __future__ import annotations

import statistics
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TypedDict, cast

import httpx

from scholartrace.scholargraph.evaluation import EvaluationQuestion
from scholartrace.scholargraph.live_smoke import run_live_smoke
from scholartrace.scholargraph.routing import ScholarGraphScope


class _CapacityRun(TypedDict):
    run_index: int
    provider_status: str
    provider_duration_seconds: float
    query_http_duration_seconds: float
    wall_duration_seconds: float
    query_attempts: int
    answer_bytes: int
    answer_sha256: str
    basic_request_count_delta: int
    tokens_visible_lower_bound_delta: int
    boundary_routing_pass: bool
    passed: bool


async def run_basic_repeat_capacity(
    *,
    base_url: str,
    questions: Mapping[str, EvaluationQuestion],
    scopes: Mapping[str, ScholarGraphScope],
    seed_id: str = "sg-eligible-01",
    repeat_count: int = 3,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    if repeat_count < 3 or repeat_count > 10:
        raise ValueError("ScholarGraph repeat count must be between 3 and 10")

    runs: list[_CapacityRun] = []
    for run_index in range(1, repeat_count + 1):
        summary = await run_live_smoke(
            base_url=base_url,
            questions=questions,
            scopes=scopes,
            seed_id=seed_id,
            transport=transport,
        )
        runs.append(
            {
                "run_index": run_index,
                "provider_status": cast(str, summary["provider_status"]),
                "provider_duration_seconds": cast(
                    float, summary["provider_duration_seconds"]
                ),
                "query_http_duration_seconds": cast(
                    float, summary["query_http_duration_seconds"]
                ),
                "wall_duration_seconds": cast(float, summary["wall_duration_seconds"]),
                "query_attempts": cast(int, summary["query_attempts"]),
                "answer_bytes": cast(int, summary["answer_bytes"]),
                "answer_sha256": cast(str, summary["answer_sha256"]),
                "basic_request_count_delta": cast(
                    int, summary["basic_request_count_delta"]
                ),
                "tokens_visible_lower_bound_delta": cast(
                    int, summary["tokens_visible_lower_bound_delta"]
                ),
                "boundary_routing_pass": cast(
                    bool, summary["boundary_routing_pass"]
                ),
                "passed": cast(bool, summary["passed"]),
            }
        )

    provider_durations = [item["provider_duration_seconds"] for item in runs]
    query_durations = [item["query_http_duration_seconds"] for item in runs]
    wall_durations = [item["wall_duration_seconds"] for item in runs]
    success_count = sum(item["provider_status"] == "succeeded" for item in runs)
    degraded_count = sum(item["provider_status"] == "degraded" for item in runs)
    request_delta = sum(item["basic_request_count_delta"] for item in runs)
    token_delta = sum(item["tokens_visible_lower_bound_delta"] for item in runs)
    passed = all(
        (
            success_count == repeat_count,
            degraded_count == 0,
            request_delta == repeat_count,
            all(item["query_attempts"] == 1 for item in runs),
            all(item["boundary_routing_pass"] for item in runs),
            all(item["passed"] for item in runs),
        )
    )
    last_summary = summary
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "fixture_kind": "bounded_sequential_scholargraph_basic_repeat",
        "quality_scope": (
            "Three sequential local Basic repeats for reliability and latency; "
            "not a concurrent load test or B3/B4 quality comparison."
        ),
        "service_version": last_summary["service_version"],
        "graphrag_version": last_summary["graphrag_version"],
        "corpus_id": last_summary["corpus_id"],
        "corpus_manifest_sha256": last_summary["corpus_manifest_sha256"],
        "method": "basic",
        "repeat_count": repeat_count,
        "concurrency": 1,
        "success_count": success_count,
        "degraded_count": degraded_count,
        "failure_count": repeat_count - success_count - degraded_count,
        "success_rate": round(success_count / repeat_count, 6),
        "provider_duration_p50_seconds": _percentile(provider_durations, 0.5),
        "provider_duration_p95_seconds": _percentile(provider_durations, 0.95),
        "query_http_duration_p50_seconds": _percentile(query_durations, 0.5),
        "query_http_duration_p95_seconds": _percentile(query_durations, 0.95),
        "wall_duration_p50_seconds": _percentile(wall_durations, 0.5),
        "wall_duration_p95_seconds": _percentile(wall_durations, 0.95),
        "basic_request_count_delta": request_delta,
        "tokens_visible_lower_bound_delta": token_delta,
        "answer_hash_unique_count": len({item["answer_sha256"] for item in runs}),
        "service_sample_window_size": last_summary["service_sample_window_size"],
        "service_basic_metrics_after": last_summary["service_basic_metrics_after"],
        "boundary_routing_pass": all(item["boundary_routing_pass"] for item in runs),
        "provider_reported_cost_cny": 0.0,
        "paid_model_api_calls": 0,
        "runs": runs,
        "passed": passed,
        "notes": [
            "No question, answer, prompt, raw diagnostics, or credentials are stored.",
            "Service queue metrics use the Provider rolling sample window, not only these repeats.",
            "Sequential concurrency=1 matches the bounded single-GPU closeout scope.",
            "Global and DRIFT were not enabled or called.",
        ],
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot summarize an empty capacity run")
    if len(values) == 1:
        return round(values[0], 6)
    if percentile == 0.5:
        return round(statistics.median(values), 6)
    if percentile == 0.95:
        return round(statistics.quantiles(values, n=20, method="inclusive")[18], 6)
    raise ValueError("only P50 and P95 are supported")
