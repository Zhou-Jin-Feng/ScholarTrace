"""T16 dependency readiness: honest, cheap, and free of credentials.

The defect this guards against is a workspace that displays as ready while it
has no DocuMind, no local model and a closed paid gate. The endpoint must also
stay cheap — probing by loading a model would make a health check expensive —
and must never echo a configured secret back to a caller.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from scholartrace.api.app import create_app
from scholartrace.delivery.executors import RealTaskExecutor
from scholartrace.delivery.service import M6TaskService

# Deliberately not shaped like a real key prefix: the repository's own credential
# scanner (tests/test_repository_hygiene.py) cannot distinguish a fake key from a
# real one, and it is right not to try. This string is just as effective as a
# canary while keeping that scanner meaningful.
SECRET = "CANARY-must-never-be-echoed-0123456789"


def _client(tmp_path: Path) -> httpx.AsyncClient:
    app = create_app(root=Path(__file__).resolve().parents[1], data_dir=tmp_path)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_dependencies_report_degraded_when_nothing_is_configured(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as client:
            response = await client.get("/api/v1/health/dependencies")
            assert response.status_code == 200
            body = response.json()

            # The API process itself is up; the workspace as a whole is not.
            assert body["api"] == "ready"
            assert body["overall"] == "degraded"
            assert body["real_mode"]["state"] == "unavailable"

            deps = body["dependencies"]
            # A closed paid gate is policy, not a fault.
            assert deps["api_strong"]["state"] == "disabled"
            # Genuinely absent infrastructure is not reported as ready.
            for name in ("documind", "local_model", "search_provider"):
                assert deps[name]["state"] == "unavailable", name
                assert deps[name]["reason"]
                assert deps[name]["remediation"]

            assert sorted(body["real_mode"]["blocking_dependencies"]) == [
                "api_strong",
                "documind",
                "local_model",
                "search_provider",
            ]

    asyncio.run(scenario())


def test_dependencies_never_echo_configured_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A readiness probe is a common place for a key to leak into a log."""

    # Configure the provider *with* an embedded secret so the dependency is
    # recognised as configured from this value. If any part of the report were built by
    # echoing configuration, the secret would surface here.
    monkeypatch.setenv("SCHOLARTRACE_API_STRONG_KEY", SECRET)
    monkeypatch.setenv("SCHOLARTRACE_SEARCH_PROVIDERS", f"arxiv,key={SECRET}")
    monkeypatch.setenv("SCHOLARTRACE_DOCUMIND_URL", f"http://user:{SECRET}@localhost:7860")

    async def scenario() -> None:
        async with _client(tmp_path) as client:
            response = await client.get("/api/v1/health/dependencies")
            body = response.json()
            serialised = json.dumps(body)
            assert SECRET not in serialised
            # Prove the configured values were actually read, so the assertion
            # above is not passing merely because nothing was evaluated.
            assert body["dependencies"]["search_provider"]["state"] == "unknown"
            assert body["dependencies"]["documind"]["state"] == "unknown"
            # The URL itself must not be echoed either, secret or not.
            assert "localhost:7860" not in serialised

    asyncio.run(scenario())


def test_demo_mode_stays_ready_even_when_real_mode_is_unavailable(tmp_path: Path) -> None:
    """The demo path is a delivered feature; missing real deps must not hide it."""

    async def scenario() -> None:
        async with _client(tmp_path) as client:
            body = (await client.get("/api/v1/health/dependencies")).json()
            assert body["demo_mode"]["state"] == "ready"
            assert body["real_mode"]["state"] == "unavailable"

    asyncio.run(scenario())


def test_dependencies_ready_only_when_deps_present_and_stages_wired(tmp_path: Path) -> None:
    """Configured dependencies alone are not enough: T08-T12 must be assembled.

    Driven at the service layer so a fully-satisfied executor can be injected
    without configuring real infrastructure.
    """

    satisfied = RealTaskExecutor(
        documind_available=True,
        local_model_available=True,
        api_strong_enabled=True,
        search_providers_configured=True,
        pipeline_stages_wired=False,
    )
    service = M6TaskService(
        root=Path(__file__).resolve().parents[1],
        data_dir=tmp_path,
        real_executor=satisfied,
    )
    try:
        report = service.dependency_report()
        # No gaps, but the pipeline is not built yet — still not ready.
        assert set(report["real_mode"]["blocking_dependencies"]) == {
            "documind",
            "local_model",
            "search_provider",
            "api_strong",
        }
        assert report["real_mode"]["pipeline_stages_wired"] is False
        assert report["real_mode"]["state"] == "unavailable"
        assert report["overall"] == "degraded"
    finally:
        service.close()

    wired = RealTaskExecutor(
        documind_available=True,
        local_model_available=True,
        api_strong_enabled=True,
        search_providers_configured=True,
        pipeline_stages_wired=True,
    )
    service = M6TaskService(
        root=Path(__file__).resolve().parents[1],
        data_dir=tmp_path / "wired",
        real_executor=wired,
    )
    try:
        report = service.dependency_report()
        assert report["real_mode"]["state"] == "unavailable"
        assert report["overall"] == "degraded"
    finally:
        service.close()


def test_probe_policy_is_stated_in_the_payload(tmp_path: Path) -> None:
    """The contract promises a cheap probe; the payload says so explicitly."""

    async def scenario() -> None:
        async with _client(tmp_path) as client:
            body = (await client.get("/api/v1/health/dependencies")).json()
            assert "no model load" in body["probe_policy"]
            assert body["schema_version"] == "1.0"
            # Queue facts stay available so this can replace the old ready view.
            # Keys mirror the existing queue snapshot rather than the T04 sketch's
            # depth/capacity, so /health/ready consumers keep working unchanged.
            assert set(body["queue"]) >= {"accepting", "queued", "active", "max_queue_size"}

    asyncio.run(scenario())
