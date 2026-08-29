from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx
import pytest
import respx

from scholartrace.search.cache import JsonFileResponseCache, MemoryResponseCache
from scholartrace.search.errors import AcademicSourceError
from scholartrace.search.http import AcademicHttpClient
from scholartrace.search.models import SourcePolicy

ENDPOINT = "https://example.test/works"


def _policy(**updates: object) -> SourcePolicy:
    values: dict[str, object] = {
        "max_network_requests": 3,
        "max_attempts": 3,
        "base_backoff_seconds": 0,
        "max_backoff_seconds": 0,
        "jitter_ratio": 0,
    }
    values.update(updates)
    return SourcePolicy.model_validate(values)


def test_success_is_cached_without_private_parameters(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, bool, int, bool]:
        async with httpx.AsyncClient() as client:
            http = AcademicHttpClient(
                source="openalex",
                client=client,
                cache=JsonFileResponseCache(tmp_path),
                policy=_policy(),
            )
            first = await http.get(
                url=ENDPOINT,
                public_params={"search": "rag"},
                private_params={"api_key": "never-persist-this-key"},
            )
            second = await http.get(
                url=ENDPOINT,
                public_params={"search": "rag"},
                private_params={"api_key": "a-different-key"},
            )
            return first.attempts, first.cache_hit, second.attempts, second.cache_hit

    with respx.mock(assert_all_called=True) as router:
        route = router.get(re.compile(r"https://example\.test/works.*")).mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        assert asyncio.run(scenario()) == (1, False, 0, True)
        assert route.call_count == 1
    cache_text = "\n".join(path.read_text("utf-8") for path in tmp_path.rglob("*.json"))
    assert "never-persist-this-key" not in cache_text
    assert "a-different-key" not in cache_text


def test_rate_limit_retries_with_retry_after() -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"results": []}, request=request)

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    async def scenario() -> int:
        async with httpx.AsyncClient() as client:
            http = AcademicHttpClient(
                source="openalex",
                client=client,
                cache=MemoryResponseCache(),
                policy=_policy(),
                sleeper=sleeper,
            )
            return (await http.get(url=ENDPOINT, public_params={"search": "rag"})).attempts

    with respx.mock(assert_all_called=True) as router:
        router.get(re.compile(r"https://example\.test/works.*")).mock(side_effect=handler)
        assert asyncio.run(scenario()) == 2
    assert calls == 2
    assert delays == [0]


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [(httpx.Response(503), "service_unavailable"), (httpx.ReadTimeout("slow"), "timed_out")],
)
def test_bounded_retry_classifies_service_and_timeout_failures(
    failure: httpx.Response | Exception, expected_code: str
) -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient() as client:
            http = AcademicHttpClient(
                source="crossref",
                client=client,
                cache=MemoryResponseCache(),
                policy=_policy(max_network_requests=2, max_attempts=2),
            )
            with pytest.raises(AcademicSourceError) as captured:
                await http.get(url=ENDPOINT, public_params={"query.title": "rag"})
            assert captured.value.code == expected_code
            assert captured.value.attempts == 2

    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(failure, httpx.Response):
            return httpx.Response(failure.status_code, request=request)
        if isinstance(failure, httpx.ReadTimeout):
            raise httpx.ReadTimeout(str(failure), request=request)
        raise AssertionError("unexpected fixture")

    with respx.mock(assert_all_called=True) as router:
        router.get(re.compile(r"https://example\.test/works.*")).mock(side_effect=handler)
        asyncio.run(scenario())


def test_response_size_limit_fails_closed() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient() as client:
            http = AcademicHttpClient(
                source="arxiv",
                client=client,
                cache=MemoryResponseCache(),
                policy=_policy(max_response_bytes=1024),
            )
            with pytest.raises(AcademicSourceError, match="size limit") as captured:
                await http.get(url=ENDPOINT, public_params={"q": "rag"})
            assert captured.value.code == "response_too_large"

    with respx.mock(assert_all_called=True) as router:
        router.get(re.compile(r"https://example\.test/works.*")).mock(
            return_value=httpx.Response(200, content=b"x" * 1025)
        )
        asyncio.run(scenario())


def test_source_budgets_are_independent() -> None:
    async def scenario() -> tuple[int, int]:
        async with httpx.AsyncClient() as first_client, httpx.AsyncClient() as second_client:
            first = AcademicHttpClient(
                source="arxiv",
                client=first_client,
                cache=MemoryResponseCache(),
                policy=_policy(max_network_requests=1, max_attempts=1),
            )
            second = AcademicHttpClient(
                source="crossref",
                client=second_client,
                cache=MemoryResponseCache(),
                policy=_policy(max_network_requests=1, max_attempts=1),
            )
            await asyncio.gather(
                first.get(url=ENDPOINT, public_params={"source": "arxiv"}),
                second.get(url=ENDPOINT, public_params={"source": "crossref"}),
            )
            return first.budget.used, second.budget.used

    with respx.mock(assert_all_called=True) as router:
        router.get(re.compile(r"https://example\.test/works.*")).mock(
            return_value=httpx.Response(200, json={})
        )
        assert asyncio.run(scenario()) == (1, 1)
