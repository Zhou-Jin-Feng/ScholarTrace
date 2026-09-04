from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from scholartrace.api.app import create_app


def _client(tmp_path: Path) -> httpx.AsyncClient:
    app = create_app(root=Path(__file__).resolve().parents[1], data_dir=tmp_path)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_m6_success_task_approval_events_and_exports(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as client:
            payload = {
                "title": "M6 delivery demo",
                "question": "How does retrieval evidence improve research reports?",
                "demo_mode": "success",
            }
            created = await client.post(
                "/api/v1/research/tasks",
                json=payload,
                headers={"Idempotency-Key": "m6-test-1"},
            )
            repeated = await client.post(
                "/api/v1/research/tasks",
                json=payload,
                headers={"Idempotency-Key": "m6-test-1"},
            )
            assert created.status_code == 201
            assert repeated.status_code == 201
            assert created.json()["task_id"] == repeated.json()["task_id"]
            task_id = created.json()["task_id"]
            assert created.json()["status"] == "waiting_approval"

            approved = await client.post(
                f"/api/v1/research/tasks/{task_id}/approve",
                json={"action": "approve", "reason": "test approval"},
            )
            assert approved.status_code == 200
            assert approved.json()["status"] == "completed"
            assert approved.json()["phase"] == "done"
            assert approved.json()["artifact_count"] == 4
            assert approved.json()["event_count"] >= 8

            artifacts = await client.get(f"/api/v1/research/tasks/{task_id}/artifacts")
            assert artifacts.status_code == 200
            assert {item["artifact_type"] for item in artifacts.json()} == {"report"}
            for format_name in ("json", "markdown", "html", "pdf"):
                response = await client.get(
                    f"/api/v1/research/tasks/{task_id}/report?format={format_name}"
                )
                assert response.status_code == 200
                assert response.headers["x-content-sha256"]
                if format_name == "pdf":
                    assert response.content.startswith(b"%PDF-1.4")
                elif format_name == "json":
                    assert json.loads(response.text)["schema_version"] == "1.0"
                else:
                    assert response.content

            events = await client.get(f"/api/v1/research/tasks/{task_id}/events")
            assert events.status_code == 200
            assert "workflow_finished" in events.text
            assert "exports_ready" in events.text
            first_event_id = events.text.split("id: ", maxsplit=1)[1].splitlines()[0]
            replay = await client.get(
                f"/api/v1/research/tasks/{task_id}/events",
                headers={"Last-Event-ID": first_event_id},
            )
            assert replay.status_code == 200
            assert first_event_id not in replay.text
            follow = await client.get(
                f"/api/v1/research/tasks/{task_id}/events?follow=true",
                headers={"Last-Event-ID": first_event_id},
            )
            assert follow.status_code == 200
            assert "event: exports_ready" in follow.text
            assert 'event: stream_end\ndata: {"reason":"terminal"}' in follow.text

    asyncio.run(scenario())


def test_m6_degraded_and_production_unavailable_paths_are_visible(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as client:
            modes = (("degraded", "degraded"), ("production_unavailable", "degraded"))
            for mode, expected_status in modes:
                created = await client.post(
                    "/api/v1/research/tasks",
                    json={"question": f"Test {mode} handling", "demo_mode": mode},
                )
                task_id = created.json()["task_id"]
                result = await client.post(
                    f"/api/v1/research/tasks/{task_id}/approve",
                    json={"action": "approve"},
                )
                assert result.status_code == 200
                assert result.json()["status"] == expected_status
                assert result.json()["degradations"]

    asyncio.run(scenario())


def test_m6_reject_path_does_not_execute_demo(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as client:
            created = await client.post(
                "/api/v1/research/tasks",
                json={"question": "Test rejection handling"},
            )
            task_id = created.json()["task_id"]
            result = await client.post(
                f"/api/v1/research/tasks/{task_id}/approve",
                json={"action": "reject", "reason": "out of scope"},
            )
            assert result.status_code == 200
            assert result.json()["status"] == "rejected"
            assert result.json()["phase"] == "done"
            assert result.json()["degradations"] == ["out of scope"]

    asyncio.run(scenario())


def test_m6_api_contracts_idempotency_conflict_and_configured_storage(
    tmp_path: Path, monkeypatch
) -> None:
    configured_data_dir = tmp_path / "persistent-data"
    monkeypatch.setenv("SCHOLARTRACE_DATA_DIR", str(configured_data_dir))
    app = create_app(root=tmp_path / "project")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            health = await client.get(
                "/api/v1/health/live", headers={"X-Request-ID": "request-contract-1"}
            )
            assert health.status_code == 200
            assert health.headers["x-request-id"] == "request-contract-1"
            assert health.json()["status"] == "live"

            evaluation = await client.get("/api/v1/evaluation/m6")
            assert evaluation.status_code == 200
            assert [item["phase"] for item in evaluation.json()["phases"]] == [
                "B0",
                "B1",
                "B2",
                "B3",
                "B4",
            ]

            first = await client.post(
                "/api/v1/research/tasks",
                json={"question": "A contract-safe research question"},
                headers={"Idempotency-Key": "contract-key-1"},
            )
            assert first.status_code == 201
            conflict = await client.post(
                "/api/v1/research/tasks",
                json={"question": "A different research question"},
                headers={"Idempotency-Key": "contract-key-1"},
            )
            assert conflict.status_code == 409

            invalid = await client.post(
                "/api/v1/research/tasks",
                json={"question": "x", "unexpected": True},
            )
            assert invalid.status_code == 422

            missing_events = await client.get(
                "/api/v1/research/tasks/task:m6:missing/events?follow=true"
            )
            assert missing_events.status_code == 404
            assert missing_events.json()["detail"] == "task not found"

    try:
        assert app.state.m6_service.store.path == configured_data_dir / "tasks.sqlite"
        asyncio.run(scenario())
    finally:
        app.state.m6_service.close()
