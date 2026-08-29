"""Bounded HTTP execution with per-source budgets, rate limits, cache, and retries."""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from scholartrace.search.cache import (
    ResponseCache,
    cached_response,
    make_cache_key,
    safe_headers,
)
from scholartrace.search.errors import AcademicSourceError
from scholartrace.search.models import PublicErrorCode, SourceName, SourcePolicy

AsyncSleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class HttpPayload:
    status_code: int
    headers: dict[str, str]
    body: bytes
    retrieved_at: datetime
    duration_seconds: float
    cache_hit: bool
    attempts: int
    response_sha256: str


class RequestBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.used = 0
        self._lock = asyncio.Lock()

    async def consume(self) -> bool:
        async with self._lock:
            if self.used >= self.maximum:
                return False
            self.used += 1
            return True


class AsyncIntervalLimiter:
    def __init__(
        self,
        minimum_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: AsyncSleeper = asyncio.sleep,
    ) -> None:
        self.minimum_interval = minimum_interval
        self._clock = clock
        self._sleeper = sleeper
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            delay = max(0.0, self._next_allowed - now)
            if delay:
                await self._sleeper(delay)
                now = self._clock()
            self._next_allowed = max(now, self._next_allowed) + self.minimum_interval


class AcademicHttpClient:
    """One independent client policy for one academic source."""

    def __init__(
        self,
        *,
        source: SourceName,
        client: httpx.AsyncClient,
        cache: ResponseCache,
        policy: SourcePolicy,
        sleeper: AsyncSleeper = asyncio.sleep,
        random_source: random.Random | None = None,
    ) -> None:
        self.source = source
        self.client = client
        self.cache = cache
        self.policy = policy
        self.sleeper = sleeper
        self.random = random_source or random.Random()
        self.budget = RequestBudget(policy.max_network_requests)
        self.limiter = AsyncIntervalLimiter(
            policy.min_interval_seconds,
            sleeper=sleeper,
        )

    async def get(
        self,
        *,
        url: str,
        public_params: Mapping[str, str],
        private_params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpPayload:
        normalized_public = dict(sorted(public_params.items()))
        cache_key = make_cache_key(
            source=self.source,
            url=url,
            public_params=normalized_public,
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            return HttpPayload(
                status_code=cached.status_code,
                headers=cached.headers,
                body=cached.body,
                retrieved_at=cached.stored_at,
                duration_seconds=0,
                cache_hit=True,
                attempts=0,
                response_sha256=hashlib.sha256(cached.body).hexdigest(),
            )

        request_started = time.perf_counter()
        attempts = 0
        last_status: int | None = None
        while attempts < self.policy.max_attempts:
            if not await self.budget.consume():
                raise AcademicSourceError(
                    code="budget_exhausted",
                    public_reason=f"{self.source} request budget exhausted",
                    attempts=attempts,
                )
            attempts += 1
            await self.limiter.acquire()
            params = {**normalized_public, **dict(private_params or {})}
            try:
                response = await self.client.get(
                    url,
                    params=params,
                    headers=dict(headers or {}),
                    timeout=self.policy.timeout_seconds,
                )
                last_status = response.status_code
                body = await response.aread()
                if len(body) > self.policy.max_response_bytes:
                    raise AcademicSourceError(
                        code="response_too_large",
                        public_reason=f"{self.source} response exceeded configured size limit",
                        attempts=attempts,
                        http_status=response.status_code,
                    )
                if 200 <= response.status_code < 300:
                    retrieved_at = datetime.now(UTC)
                    response_headers = safe_headers(dict(response.headers))
                    entry = cached_response(
                        status_code=response.status_code,
                        headers=response_headers,
                        body=body,
                        stored_at=retrieved_at,
                    )
                    with suppress(OSError):
                        self.cache.set(cache_key, entry)
                    return HttpPayload(
                        status_code=response.status_code,
                        headers=response_headers,
                        body=body,
                        retrieved_at=retrieved_at,
                        duration_seconds=time.perf_counter() - request_started,
                        cache_hit=False,
                        attempts=attempts,
                        response_sha256=hashlib.sha256(body).hexdigest(),
                    )
                error = self._status_error(response.status_code, attempts)
                if (
                    not self._retryable_status(response.status_code)
                    or attempts >= self.policy.max_attempts
                ):
                    raise error
                await self.sleeper(self._retry_delay(attempts, response.headers.get("retry-after")))
            except AcademicSourceError:
                raise
            except httpx.TimeoutException as exc:
                if attempts >= self.policy.max_attempts:
                    raise AcademicSourceError(
                        code="timed_out",
                        public_reason=f"{self.source} request timed out",
                        attempts=attempts,
                        http_status=last_status,
                    ) from exc
                await self.sleeper(self._retry_delay(attempts, None))
            except httpx.TransportError as exc:
                if attempts >= self.policy.max_attempts:
                    raise AcademicSourceError(
                        code="network_error",
                        public_reason=f"{self.source} network request failed",
                        attempts=attempts,
                        http_status=last_status,
                    ) from exc
                await self.sleeper(self._retry_delay(attempts, None))

        raise AcademicSourceError(
            code="service_unavailable",
            public_reason=f"{self.source} exhausted retry attempts",
            attempts=attempts,
            http_status=last_status,
        )

    @staticmethod
    def _retryable_status(status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code <= 599

    def _status_error(self, status_code: int, attempts: int) -> AcademicSourceError:
        code: PublicErrorCode
        if status_code == 429:
            code = "rate_limited"
            reason = f"{self.source} rate limited the request"
        elif status_code in {401, 403}:
            code = "authentication_required"
            reason = f"{self.source} requires authentication or denied access"
        elif 500 <= status_code <= 599:
            code = "service_unavailable"
            reason = f"{self.source} service is unavailable"
        else:
            code = "client_error"
            reason = f"{self.source} rejected the request"
        return AcademicSourceError(
            code=code,
            public_reason=reason,
            attempts=attempts,
            http_status=status_code,
        )

    def _retry_delay(self, attempts: int, retry_after: str | None) -> float:
        if retry_after is not None:
            try:
                return min(float(retry_after), self.policy.max_backoff_seconds)
            except ValueError:
                pass
        base = min(
            self.policy.base_backoff_seconds * (2 ** (attempts - 1)),
            self.policy.max_backoff_seconds,
        )
        jitter = base * self.policy.jitter_ratio * self.random.random()
        return float(base + jitter)
