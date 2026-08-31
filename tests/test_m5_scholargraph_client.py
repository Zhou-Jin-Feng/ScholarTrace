from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from scholartrace.scholargraph.client import (
    ScholarGraphClient,
    ScholarGraphClientError,
    ScholarGraphProtocolError,
    ScholarGraphQueryResult,
)
from scholartrace.scholargraph.models import CapabilitiesResponse, QueryRequest

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = json.loads(
    (ROOT / "tests" / "fixtures" / "m5" / "scholargraph_capabilities.json").read_text(
        "utf-8"
    )
)


def _query_response(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "service_version": "1.2.0",
        "request_id": "m5-fixture",
        "graphrag_version": "3.1.2",
        "corpus_id": "openalex-rag-abstracts-2020-2025-v1",
        "method": "basic",
        "purpose": "general",
        "status": "succeeded",
        "duration_seconds": 1.25,
        "evidence_level": "abstract",
        "answer": "Abstract-level auxiliary context.",
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
    payload.update(updates)
    return payload


def _run(
    handler: Callable[[httpx.Request], httpx.Response], operation: str
) -> CapabilitiesResponse | ScholarGraphQueryResult:
    async def scenario() -> CapabilitiesResponse | ScholarGraphQueryResult:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ) as http:
            client = ScholarGraphClient(
                client=http,
                timeout_seconds=1,
                backoff_seconds=0,
                query_grace_seconds=1,
            )
            if operation == "capabilities":
                return await client.capabilities()
            return await client.query(QueryRequest(question="What is RAG?"))

    return asyncio.run(scenario())


def test_capabilities_and_query_parse_strict_provider_responses() -> None:
    capabilities = _run(lambda request: httpx.Response(200, json=CAPABILITIES), "capabilities")
    assert isinstance(capabilities, CapabilitiesResponse)
    assert capabilities.service_version == "1.2.0"

    result = _run(lambda request: httpx.Response(200, json=_query_response()), "query")
    assert isinstance(result, ScholarGraphQueryResult)
    assert result.response.status == "succeeded"
    assert result.attempts == 1


def test_query_rejects_contract_drift_and_unverified_sources() -> None:
    with pytest.raises(ScholarGraphProtocolError):
        _run(
            lambda request: httpx.Response(
                200, json=_query_response(service_version="1.3.0")
            ),
            "query",
        )
    with pytest.raises(ScholarGraphProtocolError):
        _run(
            lambda request: httpx.Response(
                200,
                json=_query_response(
                    source_refs=[{"document_id": "doc-1", "openalex_id": None, "title": None}]
                ),
            ),
            "query",
        )


def test_query_does_not_retry_expensive_failures() -> None:
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

    with pytest.raises(ScholarGraphClientError) as caught:
        _run(handler, "query")
    assert caught.value.code == "service_not_ready"
    assert caught.value.attempts == 1
    assert calls == 1


def test_get_probe_retries_but_rejects_oversized_response() -> None:
    calls = 0

    def retry_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                503,
                json={
                    "schema_version": "1.0.0",
                    "service_version": "1.2.0",
                    "request_id": "m5-retry",
                    "status": "failed",
                    "error": {
                        "code": "service_not_ready",
                        "message": "Service is not ready.",
                        "retryable": True,
                        "field": None,
                    },
                },
            )
        return httpx.Response(200, json=CAPABILITIES)

    _run(retry_handler, "capabilities")
    assert calls == 2

    async def oversized() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"x" * 2048)
            ),
            trust_env=False,
        ) as http:
            client = ScholarGraphClient(client=http, max_response_bytes=1024)
            await client.capabilities()

    with pytest.raises(ScholarGraphProtocolError):
        asyncio.run(oversized())


def test_traceparent_is_validated_before_network_call() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=_query_response())
            ),
            trust_env=False,
        ) as http:
            client = ScholarGraphClient(client=http)
            await client.query(QueryRequest(question="What is RAG?"), traceparent="invalid")

    with pytest.raises(ValueError, match="traceparent"):
        asyncio.run(scenario())
