"""Bounded DocuMind Schema 1.0 Consumer with strict evidence-scope checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from scholartrace.contracts import DocuMindBinding
from scholartrace.documind_compatibility import supports_documind_retrieve
from scholartrace.evidence.models import (
    DocuMindErrorCode,
    DocuMindErrorEnvelope,
    DocuMindReadiness,
    DocuMindRetrieveRequest,
    DocuMindRetrieveResponse,
    RetrievalAudit,
    retrieval_is_ready,
)

AsyncSleeper = Callable[[float], Awaitable[None]]
RETRYABLE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "retrieval_capacity_exceeded",
        "retrieval_timeout",
        "retrieval_service_unavailable",
        "service_unavailable",
    }
)


class DocuMindProtocolError(RuntimeError):
    """DocuMind returned a response that violates the frozen Consumer contract."""


class EvidenceScopeError(DocuMindProtocolError):
    """Retrieved evidence does not belong to the requested paper binding."""


class DocuMindClientError(RuntimeError):
    def __init__(
        self,
        *,
        code: DocuMindErrorCode,
        status_code: int | None,
        attempts: int,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    response: DocuMindRetrieveResponse
    audit: RetrievalAudit


class DocuMindClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str = "http://127.0.0.1:8001",
        timeout_seconds: float = 30,
        max_attempts: int = 2,
        backoff_seconds: float = 0.25,
        max_response_bytes: int = 2_000_000,
        sleeper: AsyncSleeper = asyncio.sleep,
    ) -> None:
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes < 1024:
            raise ValueError("max_response_bytes must be at least 1024")
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.max_response_bytes = max_response_bytes
        self.sleeper = sleeper

    async def retrieval_ready(self) -> tuple[bool, DocuMindReadiness | None]:
        try:
            response = await self.client.get(
                f"{self.base_url}/api/v1/health/ready",
                timeout=self.timeout_seconds,
            )
            readiness = DocuMindReadiness.model_validate_json(
                await self._bounded_response_body(response)
            )
        except (httpx.HTTPError, ValidationError, ValueError, json.JSONDecodeError):
            return False, None
        return retrieval_is_ready(readiness, response.status_code), readiness

    async def retrieve(
        self,
        *,
        canonical_paper_id: str,
        binding: DocuMindBinding,
        query: str,
        top_k: int = 5,
        distance_threshold: float | None = None,
    ) -> RetrievalResult:
        if binding.canonical_paper_id != canonical_paper_id:
            raise EvidenceScopeError("paper ID does not match DocuMind binding")
        request = DocuMindRetrieveRequest(
            query=query,
            document_key=binding.document_key,
            expected_index_id=binding.index_id,
            top_k=top_k,
            distance_threshold=distance_threshold,
        )
        query_sha256 = hashlib.sha256(request.query.encode("utf-8")).hexdigest()
        run_digest = hashlib.sha256(
            "\0".join(
                (
                    canonical_paper_id,
                    binding.document_key,
                    binding.index_id,
                    query_sha256,
                )
            ).encode("utf-8")
        ).hexdigest()
        retrieval_run_id = f"retrieval:m2:{run_digest[:24]}"
        request_id = f"m2-{run_digest[:24]}"
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        last_error: DocuMindClientError | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                async with self.client.stream(
                    "POST",
                    f"{self.base_url}/api/v1/retrieve",
                    headers={"X-Request-ID": request_id},
                    json=request.model_dump(mode="json"),
                    timeout=self.timeout_seconds,
                ) as http_response:
                    body = await self._bounded_response_body(http_response)
                if 200 <= http_response.status_code < 300:
                    response = self._parse_success(body)
                    self._verify_scope(binding, response)
                    completed_at = datetime.now(UTC)
                    return RetrievalResult(
                        response=response,
                        audit=RetrievalAudit(
                            retrieval_run_id=retrieval_run_id,
                            canonical_paper_id=canonical_paper_id,
                            document_key=binding.document_key,
                            index_id=binding.index_id,
                            source_sha256=binding.source_sha256,
                            query_sha256=query_sha256,
                            started_at=started_at,
                            completed_at=completed_at,
                            duration_seconds=time.perf_counter() - started,
                            attempts=attempt,
                            status="succeeded" if response.chunks else "empty",
                            service_version=response.service_version,
                            retrieval_version=response.retrieval_version,
                            chunk_count=len(response.chunks),
                        ),
                    )
                error = self._parse_error(body, http_response.status_code, attempt)
                last_error = error
                if error.code not in RETRYABLE_ERROR_CODES or attempt >= self.max_attempts:
                    raise error
            except EvidenceScopeError:
                raise
            except DocuMindProtocolError:
                raise
            except DocuMindClientError:
                raise
            except httpx.TimeoutException as exc:
                last_error = DocuMindClientError(
                    code="retrieval_timeout",
                    status_code=None,
                    attempts=attempt,
                )
                if attempt >= self.max_attempts:
                    raise last_error from exc
            except httpx.TransportError as exc:
                last_error = DocuMindClientError(
                    code="retrieval_service_unavailable",
                    status_code=None,
                    attempts=attempt,
                )
                if attempt >= self.max_attempts:
                    raise last_error from exc
            await self.sleeper(self.backoff_seconds * attempt)

        if last_error is not None:
            raise last_error
        raise RuntimeError("DocuMind retrieval retry loop exited unexpectedly")

    async def _bounded_response_body(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.max_response_bytes:
                raise DocuMindProtocolError("DocuMind response exceeded size limit")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _parse_success(body: bytes) -> DocuMindRetrieveResponse:
        try:
            return DocuMindRetrieveResponse.model_validate_json(body)
        except (ValidationError, ValueError) as exc:
            raise DocuMindProtocolError("invalid DocuMind retrieval response") from exc

    @staticmethod
    def _parse_error(body: bytes, status_code: int, attempts: int) -> DocuMindClientError:
        try:
            envelope = DocuMindErrorEnvelope.model_validate_json(body)
        except (ValidationError, ValueError) as exc:
            raise DocuMindProtocolError("invalid DocuMind error response") from exc
        return DocuMindClientError(
            code=envelope.error.code,
            status_code=status_code,
            attempts=attempts,
        )

    @staticmethod
    def _verify_scope(
        binding: DocuMindBinding,
        response: DocuMindRetrieveResponse,
    ) -> None:
        if response.document_key != binding.document_key:
            raise EvidenceScopeError("DocuMind returned another document")
        if response.index_id != binding.index_id:
            raise EvidenceScopeError("DocuMind returned another index")
        if response.source_sha256 != binding.source_sha256:
            raise EvidenceScopeError("DocuMind returned another source revision")
        if response.service_version != binding.documind_version:
            raise EvidenceScopeError("DocuMind service version differs from binding")

    @staticmethod
    def _supports_retrieve(version: str) -> bool:
        return supports_documind_retrieve(version)
