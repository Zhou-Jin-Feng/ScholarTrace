from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from test_task_authorization import NOW, policy

from scholartrace.delivery.readiness import DependencyProbes
from scholartrace.delivery.service import M6TaskService, TaskStateError


def probe_policy():
    return policy(documind_url="http://127.0.0.1:8010", local={
        "endpoint": "http://127.0.0.1:11434/api/chat", "model": "local-synthetic",
        "model_version": "fixture-v1", "protocol": "ollama", "input_cny_per_million": "0",
        "output_cny_per_million": "0", "price_observed_at": NOW.isoformat(),
        "max_input_tokens": 1000, "max_output_tokens": 20,
    })


def test_metadata_only_cache_expiry_and_503_retrieval_ready():
    now = [100.0]
    probes = DependencyProbes(probe_policy(), clock=lambda: now[0])
    calls = []
    assert probes.snapshot()["local_model"]["state"] == "unknown"

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/v1/health/ready":
            return httpx.Response(503, json={"version": "3.0.0", "ready": False,
                "status": "degraded", "components": {"retrieval": "ready", "generation": "error"}})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "local-synthetic"}]})
        return httpx.Response(200, json={"data": [{"id": "synthetic"}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            for _ in range(2):
                report = await probes.refresh(client)
                assert report["documind"]["state"] == "ready"
                assert report["local_model"]["state"] == "ready"
                assert report["api_strong"]["state"] == "ready"
                assert report["api_strong"]["approved"] is False
                assert report["search_provider"]["state"] == "unknown"
            assert len(calls) == 3
            now[0] += 31
            assert probes.snapshot()["documind"]["state"] == "unknown"
            await probes.refresh(client)
            assert len(calls) == 6
    asyncio.run(run())
    assert {method for method, _ in calls} == {"GET"}
    assert {path for _, path in calls} == {"/api/v1/health/ready", "/api/tags", "/v1/models"}


@pytest.mark.parametrize("fault", ["401", "redirect", "malformed", "large", "timeout", "version"])
def test_metadata_faults_are_bounded_and_do_not_echo_raw_errors(fault):
    probes = DependencyProbes(probe_policy(), timeout_seconds=0.02)
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        if fault == "timeout":
            await asyncio.sleep(0.1)
        if fault == "401":
            return httpx.Response(401, text="CANARY-private-response")
        if fault == "redirect":
            return httpx.Response(302, headers={"location": "https://unexpected.invalid"})
        if fault == "large":
            return httpx.Response(200, content=b"x" * 262_145)
        if fault == "version":
            return httpx.Response(200, json={"version": "2.9.0", "ready": True,
                                            "status": "ready",
                                            "components": {"retrieval": "ready"}})
        return httpx.Response(200, text="CANARY-private-response")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await probes.refresh(client)
            assert all(item["state"] != "ready" for item in result.values())
            assert "CANARY" not in json.dumps(result)
            await probes.refresh(client)
    asyncio.run(run())
    assert len(calls) == 3


def test_concurrent_refresh_does_not_duplicate_requests():
    probes = DependencyProbes(probe_policy())
    calls = []

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            calls.append(request.url.path)
            entered.set()
            await release.wait()
            return httpx.Response(401)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = asyncio.create_task(probes.refresh(client))
            await entered.wait()
            second = await probes.refresh(client)
            assert second["api_strong"]["state"] == "unknown"
            assert len(calls) == 1
            release.set()
            await first
    asyncio.run(run())
    assert len(calls) == 3


def test_service_probe_then_draining_preserves_cache_without_new_http(tmp_path, monkeypatch):
    service = M6TaskService(root=Path(__file__).resolve().parents[1], data_dir=tmp_path,
                            runtime_policy=probe_policy())
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.method == "GET"
        assert "authorization" not in request.headers
        return httpx.Response(401)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            report = await service.probe_dependencies(client)
            assert report["real_mode"]["state"] == "unavailable"
            assert len(calls) == 3
            snapshot = service.queue_snapshot()
            monkeypatch.setattr(service, "queue_snapshot", lambda: snapshot | {"accepting": False})
            with pytest.raises(TaskStateError, match="draining"):
                await service.probe_dependencies(client)
            assert len(calls) == 3
            assert service.dependency_report()["api"] == "draining"

    try:
        asyncio.run(run())
    finally:
        service.close()
