from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest

from scholartrace.contracts import DocuMindBinding
from scholartrace.evidence.client import (
    DocuMindClient,
    DocuMindClientError,
    DocuMindProtocolError,
    EvidenceScopeError,
)


def _binding() -> DocuMindBinding:
    return DocuMindBinding(
        canonical_paper_id="doi:10.1000/test",
        document_key="a" * 64,
        index_id="b" * 64,
        source_sha256="c" * 64,
        documind_version="2.2.0",
        retrieval_schema_version="1.0",
    )


def _response(**updates: object) -> dict[str, object]:
    content = "A retrieved evidence chunk."
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "service_version": "2.2.0",
        "retrieval_version": "dense-v1",
        "retrieval_mode": "dense",
        "document_key": "a" * 64,
        "index_id": "b" * 64,
        "source_sha256": "c" * 64,
        "chunks": [
            {
                "chunk_id": "d" * 64,
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "source": "paper.pdf",
                "page_number": 3,
                "distance": 0.42,
                "rank": 1,
            }
        ],
    }
    payload.update(updates)
    return payload


def _error(code: str) -> dict[str, object]:
    return {"error": {"code": code, "message": "Public failure", "request_id": "m2-test"}}


def test_client_returns_scoped_evidence_and_sanitized_audit() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_response(), request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await DocuMindClient(client=http).retrieve(
                canonical_paper_id=_binding().canonical_paper_id,
                binding=_binding(),
                query="What supports the claim?",
                top_k=3,
            )
        assert result.response.chunks[0].content.startswith("A retrieved")
        assert result.audit.status == "succeeded"
        assert result.audit.chunk_count == 1
        assert result.audit.attempts == 1
        assert result.audit.query_sha256 == hashlib.sha256(b"What supports the claim?").hexdigest()

    asyncio.run(scenario())
    assert captured["document_key"] == "a" * 64
    assert captured["expected_index_id"] == "b" * 64
    assert "documents" not in captured


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"document_key": "f" * 64}, "another document"),
        ({"index_id": "f" * 64}, "another index"),
        ({"source_sha256": "f" * 64}, "another source"),
        ({"service_version": "2.3.0"}, "version differs"),
    ],
)
def test_client_fails_closed_on_cross_scope_response(
    updates: dict[str, object], message: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response(**updates), request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(EvidenceScopeError, match=message):
                await DocuMindClient(client=http).retrieve(
                    canonical_paper_id=_binding().canonical_paper_id,
                    binding=_binding(),
                    query="query",
                )

    asyncio.run(scenario())


def test_client_retries_capacity_once_without_changing_scope() -> None:
    requests: list[dict[str, object]] = []
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                503,
                json=_error("retrieval_capacity_exceeded"),
                request=request,
            )
        return httpx.Response(200, json=_response(), request=request)

    async def sleeper(delay: float) -> None:
        waits.append(delay)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await DocuMindClient(client=http, sleeper=sleeper).retrieve(
                canonical_paper_id=_binding().canonical_paper_id,
                binding=_binding(),
                query="query",
            )
        assert result.audit.attempts == 2

    asyncio.run(scenario())
    assert requests[0] == requests[1]
    assert waits == [0.25]


def test_client_does_not_retry_stale_index() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(409, json=_error("stale_document_index"), request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(DocuMindClientError) as captured:
                await DocuMindClient(client=http).retrieve(
                    canonical_paper_id=_binding().canonical_paper_id,
                    binding=_binding(),
                    query="query",
                )
            assert captured.value.code == "stale_document_index"
            assert captured.value.attempts == 1

    asyncio.run(scenario())
    assert calls == 1


def test_client_retries_timeout_once_with_bounded_backoff() -> None:
    calls = 0
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("fixture timeout", request=request)

    async def sleeper(delay: float) -> None:
        waits.append(delay)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(DocuMindClientError) as captured:
                await DocuMindClient(client=http, sleeper=sleeper).retrieve(
                    canonical_paper_id=_binding().canonical_paper_id,
                    binding=_binding(),
                    query="query",
                )
            assert captured.value.code == "retrieval_timeout"
            assert captured.value.attempts == 2

    asyncio.run(scenario())
    assert calls == 2
    assert waits == [0.25]


def test_client_rejects_oversized_response_without_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"x" * 1025, request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DocuMindClient(client=http, max_response_bytes=1024)
            with pytest.raises(DocuMindProtocolError, match="size limit"):
                await client.retrieve(
                    canonical_paper_id=_binding().canonical_paper_id,
                    binding=_binding(),
                    query="query",
                )

    asyncio.run(scenario())
    assert calls == 1


def test_client_treats_empty_chunks_as_success_without_evidence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_response(chunks=[]), request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await DocuMindClient(client=http).retrieve(
                canonical_paper_id=_binding().canonical_paper_id,
                binding=_binding(),
                query="query",
            )
        assert result.response.chunks == []
        assert result.audit.status == "empty"

    asyncio.run(scenario())


def test_retrieval_readiness_requires_compatible_version_and_component() -> None:
    responses = [
        {"status": "ready", "version": "2.0.8", "ready": True},
        {
            "status": "degraded",
            "version": "2.2.0",
            "ready": False,
            "components": {"retrieval": "ready", "llm": "unavailable"},
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = responses.pop(0)
        status = 503 if payload["version"] == "2.2.0" else 200
        return httpx.Response(status, json=payload, request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DocuMindClient(client=http)
            first, first_status = await client.retrieval_ready()
            second, second_status = await client.retrieval_ready()
        assert first is False and first_status is not None
        assert second is True and second_status is not None

    asyncio.run(scenario())
