from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import httpx
import pytest

from scholartrace.api.app import create_app
from scholartrace.delivery.models import ApprovalRequest, TaskCreateRequest, TaskStatus
from scholartrace.delivery.queue import BoundedTaskExecutor, QueueFullError
from scholartrace.delivery.service import M6TaskService


def test_bounded_executor_applies_backpressure_and_cooperative_cancel() -> None:
    executor = BoundedTaskExecutor(max_workers=1, max_queue_size=1)
    first_started = threading.Event()
    release_first = threading.Event()
    observed_cancel: list[bool] = []

    def first(_: threading.Event) -> None:
        first_started.set()
        release_first.wait(2)

    def second(cancel_event: threading.Event) -> None:
        observed_cancel.append(cancel_event.is_set())

    try:
        first_submission = executor.submit("task:first", first)
        assert first_started.wait(1)
        second_submission = executor.submit("task:second", second)
        with pytest.raises(QueueFullError):
            executor.submit("task:third", second)
        assert executor.snapshot()["queued"] == 1
        assert executor.cancel("task:second") is second_submission
        release_first.set()
        assert first_submission.finished.wait(1)
        assert second_submission.finished.wait(1)
        assert observed_cancel == [True]
        assert executor.snapshot()["active"] == 0
    finally:
        release_first.set()
        assert executor.shutdown(timeout_seconds=2)


def test_service_cancel_marks_task_and_exports_terminal_state(tmp_path: Path) -> None:
    service = M6TaskService(
        root=tmp_path,
        data_dir=tmp_path / "data",
        queue_capacity=1,
        completion_wait_seconds=0.02,
    )
    started = threading.Event()

    def slow_demo(
        task_id: str, *, cancel_event: threading.Event | None = None
    ) -> dict[str, object]:
        started.set()
        while cancel_event is None or not cancel_event.is_set():
            if cancel_event is None:
                break
            cancel_event.wait(0.01)
        return service._finish_cancelled(task_id, reason="cancelled by queue test")

    service._run_demo = slow_demo  # type: ignore[method-assign]
    try:
        created = service.create_task(
            TaskCreateRequest(question="A slow cancellation test question")
        )
        approved = service.approve_task(created["task_id"], ApprovalRequest(action="approve"))
        assert approved["status"] in {TaskStatus.QUEUED.value, TaskStatus.RUNNING.value}
        assert started.wait(1)
        cancelled = service.cancel_task(created["task_id"])
        assert cancelled["status"] == TaskStatus.CANCELLED.value
        assert cancelled["phase"] == "done"
        assert cancelled["artifact_count"] == 4
        kinds = [event.kind for event in service.ledger.replay(task_id=created["task_id"])]
        assert "task_cancel_requested" in kinds
        assert "task_cancelled" in kinds
        assert "exports_ready" in kinds
    finally:
        service.close()


def test_service_reports_queue_snapshot_and_shutdown_drains(tmp_path: Path) -> None:
    service = M6TaskService(root=tmp_path, data_dir=tmp_path / "data")
    try:
        snapshot = service.queue_snapshot()
        assert snapshot["accepting"] is True
        assert snapshot["max_workers"] == 1
        assert snapshot["max_queue_size"] == 4
    finally:
        service.close()
    assert service.queue_snapshot()["accepting"] is False


def test_queue_health_and_cancel_api_are_explicit(tmp_path: Path) -> None:
    app = create_app(
        root=tmp_path,
        data_dir=tmp_path / "api-data",
        completion_wait_seconds=0.02,
    )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ready = await client.get("/api/v1/health/ready")
            assert ready.status_code == 200
            assert ready.json()["status"] == "ready"
            created = await client.post(
                "/api/v1/research/tasks", json={"question": "Cancel before approval"}
            )
            task_id = created.json()["task_id"]
            cancelled = await client.post(f"/api/v1/research/tasks/{task_id}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            assert cancelled.json()["artifact_count"] == 4

    try:
        asyncio.run(scenario())
    finally:
        app.state.m6_service.close()
