"""Bounded HTTP Consumer for the ScholarGraph read-only API."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from scholartrace.scholargraph.models import (
    CapabilitiesResponse,
    ErrorCode,
    ErrorResponse,
    LiveResponse,
    MetricsResponse,
    QueryMethod,
    QueryRequest,
    QueryResponse,
    ReadyResponse,
)

AsyncSleeper = Callable[[float], Awaitable[None]]
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
TRACEPARENT_PATTERN = re.compile(
    r"^(?!ff)[0-9a-f]{2}-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-[0-9a-f]{2}$"
)
ClientErrorCode = ErrorCode | Literal[
    "transport_error",
    "request_timeout",
    "response_too_large",
]
DEFAULT_QUERY_TIMEOUTS = {
    QueryMethod.BASIC: 180,
    QueryMethod.LOCAL: 300,
    QueryMethod.GLOBAL: 900,
    QueryMethod.DRIFT: 1800,
}


class ScholarGraphProtocolError(RuntimeError):
    """ScholarGraph returned data outside the frozen Consumer contract."""


class ScholarGraphClientError(RuntimeError):
    def __init__(
        self,
        *,
        code: ClientErrorCode,
        status_code: int | None,
        attempts: int,
        retryable: bool,
    ) -> None:
        super().__init__(str(code))
        self.code = code
        self.status_code = status_code
        self.attempts = attempts
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ScholarGraphHttpResult:
    body: bytes
    status_code: int
    attempts: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ScholarGraphQueryResult:
    response: QueryResponse
    attempts: int
    duration_seconds: float
    request_sha256: str


class ScholarGraphClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str = "http://127.0.0.1:8002",
        timeout_seconds: float = 10,
        max_get_attempts: int = 2,
        backoff_seconds: float = 0.25,
        query_grace_seconds: float = 15,
        max_response_bytes: int = 2_000_000,
        sleeper: AsyncSleeper = asyncio.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("ScholarGraph timeout must be positive")
        if max_get_attempts < 1 or max_get_attempts > 3:
            raise ValueError("ScholarGraph GET attempts must be between 1 and 3")
        if max_response_bytes < 1024:
            raise ValueError("ScholarGraph response limit must be at least 1024 bytes")
        if query_grace_seconds < 1 or query_grace_seconds > 60:
            raise ValueError("ScholarGraph query grace must be between 1 and 60 seconds")
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_get_attempts = max_get_attempts
        self.backoff_seconds = backoff_seconds
        self.query_grace_seconds = query_grace_seconds
        self.max_response_bytes = max_response_bytes
        self.sleeper = sleeper

    async def live(self) -> LiveResponse:
        result = await self._request("GET", "/api/v1/health/live", retry_get=True)
        return self._parse_success(result.body, LiveResponse)

    async def ready(self) -> ReadyResponse:
        result = await self._request("GET", "/api/v1/health/ready", retry_get=True)
        return self._parse_success(result.body, ReadyResponse)

    async def capabilities(self) -> CapabilitiesResponse:
        result = await self._request("GET", "/api/v1/capabilities", retry_get=True)
        return self._parse_success(result.body, CapabilitiesResponse)

    async def metrics(self) -> MetricsResponse:
        result = await self._request("GET", "/api/v1/metrics", retry_get=True)
        return self._parse_success(result.body, MetricsResponse)

    async def query(
        self,
        request: QueryRequest,
        *,
        traceparent: str | None = None,
    ) -> ScholarGraphQueryResult:
        if traceparent is not None and not TRACEPARENT_PATTERN.fullmatch(traceparent):
            raise ValueError("invalid W3C traceparent")
        serialized = request.model_dump_json(exclude_none=True).encode("utf-8")
        request_sha256 = hashlib.sha256(serialized).hexdigest()
        headers = {"X-Request-ID": f"m5-{request_sha256[:24]}"}
        if traceparent is not None:
            headers["traceparent"] = traceparent
        provider_timeout = request.timeout_seconds or DEFAULT_QUERY_TIMEOUTS[request.method]
        result = await self._request(
            "POST",
            "/api/v1/query",
            body=serialized,
            headers=headers,
            timeout_seconds=float(provider_timeout) + self.query_grace_seconds,
            retry_get=False,
        )
        return ScholarGraphQueryResult(
            response=self._parse_success(result.body, QueryResponse),
            attempts=result.attempts,
            duration_seconds=result.duration_seconds,
            request_sha256=request_sha256,
        )

    async def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        retry_get: bool,
    ) -> ScholarGraphHttpResult:
        attempts_allowed = self.max_get_attempts if retry_get else 1
        started = time.perf_counter()
        for attempt in range(1, attempts_allowed + 1):
            try:
                async with self.client.stream(
                    method,
                    f"{self.base_url}{path}",
                    content=body,
                    headers={"Content-Type": "application/json", **(headers or {})},
                    timeout=timeout_seconds or self.timeout_seconds,
                ) as response:
                    response_body = await self._bounded_body(response)
                if response.is_success:
                    return ScholarGraphHttpResult(
                        body=response_body,
                        status_code=response.status_code,
                        attempts=attempt,
                        duration_seconds=time.perf_counter() - started,
                    )
                error = self._parse_error(response_body, response.status_code, attempt)
                if not error.retryable or attempt >= attempts_allowed:
                    raise error
            except ScholarGraphProtocolError:
                raise
            except ScholarGraphClientError:
                raise
            except httpx.TimeoutException as exc:
                error = ScholarGraphClientError(
                    code="request_timeout",
                    status_code=None,
                    attempts=attempt,
                    retryable=True,
                )
                if attempt >= attempts_allowed:
                    raise error from exc
            except httpx.TransportError as exc:
                error = ScholarGraphClientError(
                    code="transport_error",
                    status_code=None,
                    attempts=attempt,
                    retryable=True,
                )
                if attempt >= attempts_allowed:
                    raise error from exc
            await self.sleeper(self.backoff_seconds * attempt)
        raise RuntimeError("ScholarGraph retry loop exited unexpectedly")

    async def _bounded_body(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.max_response_bytes:
                raise ScholarGraphProtocolError("ScholarGraph response exceeded size limit")
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _parse_success(body: bytes, model: type[ResponseModel]) -> ResponseModel:
        try:
            return model.model_validate_json(body)
        except (ValidationError, ValueError) as exc:
            raise ScholarGraphProtocolError(
                f"invalid ScholarGraph {model.__name__} response"
            ) from exc

    @staticmethod
    def _parse_error(body: bytes, status_code: int, attempts: int) -> ScholarGraphClientError:
        try:
            envelope = ErrorResponse.model_validate_json(body)
        except (ValidationError, ValueError) as exc:
            raise ScholarGraphProtocolError("invalid ScholarGraph error response") from exc
        return ScholarGraphClientError(
            code=envelope.error.code,
            status_code=status_code,
            attempts=attempts,
            retryable=envelope.error.retryable,
        )
