from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import pytest

from scholartrace.api.app import create_app
from scholartrace.api.security import DeploymentMode, SecurityPolicy

TOKEN = "m10-private-token-1234"


def test_security_policy_requires_strong_token_for_private_mode() -> None:
    policy = SecurityPolicy.from_environment(deployment_mode="trusted_private", auth_token=TOKEN)
    assert policy.mode == DeploymentMode.TRUSTED_PRIVATE
    assert policy.auth_required is True
    with pytest.raises(ValueError, match="requires SCHOLARTRACE_AUTH_TOKEN"):
        SecurityPolicy.from_environment(deployment_mode="trusted_private", auth_token="")
    with pytest.raises(ValueError, match="at least 16"):
        SecurityPolicy.from_environment(deployment_mode="loopback", auth_token="short")
    with pytest.raises(ValueError, match="loopback or trusted_private"):
        SecurityPolicy.from_environment(deployment_mode="public")


def test_trusted_private_api_protects_task_and_report_surface(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="scholartrace.delivery")
    app = create_app(
        root=tmp_path,
        data_dir=tmp_path / "data",
        deployment_mode="trusted_private",
        auth_token=TOKEN,
    )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            live = await client.get("/api/v1/health/live")
            assert live.status_code == 200
            denied = await client.post(
                "/api/v1/research/tasks", json={"question": "Protected task"}
            )
            assert denied.status_code == 401
            assert denied.headers["www-authenticate"] == "Bearer"
            created = await client.post(
                "/api/v1/research/tasks",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={"question": "Protected task"},
            )
            assert created.status_code == 201
            task_id = created.json()["task_id"]
            denied_events = await client.get(f"/api/v1/research/tasks/{task_id}/events")
            assert denied_events.status_code == 401
            wrong_events = await client.get(
                f"/api/v1/research/tasks/{task_id}/events?access_token=wrong-token-value"
            )
            assert wrong_events.status_code == 401
            query_events = await client.get(
                f"/api/v1/research/tasks/{task_id}/events?access_token={TOKEN}"
            )
            assert query_events.status_code == 200
            denied_summary = await client.get(
                f"/api/v1/research/tasks/{task_id}?access_token={TOKEN}"
            )
            assert denied_summary.status_code == 401
            approved = await client.post(
                f"/api/v1/research/tasks/{task_id}/approve",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={"action": "approve"},
            )
            assert approved.status_code == 200
            report = await client.get(
                f"/api/v1/research/tasks/{task_id}/report?format=markdown&access_token={TOKEN}"
            )
            assert report.status_code == 200
            assert report.headers["x-content-sha256"]

    try:
        asyncio.run(scenario())
    finally:
        app.state.m6_service.close()
    assert TOKEN not in caplog.text
    assert "access_token" not in caplog.text


def test_loopback_default_keeps_local_demo_compatible(tmp_path: Path) -> None:
    app = create_app(
        root=tmp_path,
        data_dir=tmp_path / "data",
        deployment_mode="loopback",
        auth_token="",
    )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ready = await client.get("/api/v1/health/ready")
            assert ready.status_code == 200
            assert ready.json()["deployment_mode"] == "loopback"
            assert ready.json()["auth_required"] is False
            created = await client.post("/api/v1/research/tasks", json={"question": "Local task"})
            assert created.status_code == 201

    try:
        asyncio.run(scenario())
    finally:
        app.state.m6_service.close()
