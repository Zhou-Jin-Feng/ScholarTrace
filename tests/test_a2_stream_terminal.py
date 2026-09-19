"""T15: the event stream must close on every terminal path, including failure.

`exports_ready` is the last event on normal terminal paths and is what closes
the SSE stream. The gap this covers: if export rendering raises, an unguarded
failure emits no terminal event at all, so the stream stays open, the client
exhausts its retries, and a finished task displays as running forever.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from scholartrace.api.app import create_app
from scholartrace.api.events import SSE_TERMINAL_EVENT_KINDS
from scholartrace.delivery.models import DemoMode, TaskCreateRequest, TaskStatus
from scholartrace.delivery.service import M6TaskService

ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path) -> httpx.AsyncClient:
    app = create_app(root=ROOT, data_dir=tmp_path)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _kinds(service: M6TaskService, task_id: str) -> list[str]:
    return [str(event["kind"]) for event in service.events(task_id)]


def test_terminal_event_set_contains_both_closing_kinds() -> None:
    """Both the success and the failure closer must end the stream."""

    assert set(SSE_TERMINAL_EVENT_KINDS) == {"exports_ready", "task_terminal_no_exports"}


def test_export_failure_still_emits_a_terminal_event(tmp_path: Path) -> None:
    """A task that finishes but cannot render must not hang the stream."""

    service = M6TaskService(root=ROOT, data_dir=tmp_path)
    summary = service.create_task(
        TaskCreateRequest(
            title="T15",
            question="Does the stream close when rendering fails?",
            demo_mode=DemoMode.SUCCESS,
        )
    )
    task_id = str(summary["task_id"])
    try:
        # Put the task in the state the guard is meant to correct: a run that
        # reached `completed` and only then failed to render its report.
        service.store.update_task(task_id, status=TaskStatus.COMPLETED)
        service._write_exports_unguarded = lambda _task_id: (_ for _ in ()).throw(  # type: ignore[method-assign]
            RuntimeError("pdf renderer exploded")
        )
        result = service._write_exports(task_id)

        kinds = _kinds(service, task_id)
        assert "task_terminal_no_exports" in kinds
        assert "exports_ready" not in kinds
        # The failure is reported, not swallowed into a success-looking summary.
        assert any("exports could not be rendered" in note for note in result["degradations"])
        # A run with no report must not claim to have completed.
        assert result["status"] != TaskStatus.COMPLETED.value
        assert result["status"] == TaskStatus.DEGRADED.value
    finally:
        service.close()


def test_export_failure_does_not_overwrite_a_worse_status(tmp_path: Path) -> None:
    """Only a success claim is downgraded.

    `failed` and `cancelled` already describe the outcome accurately; relabelling
    them `degraded` would make a failed run look better than it was.
    """

    for original in (TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.REJECTED):
        service = M6TaskService(root=ROOT, data_dir=tmp_path / original.value)
        summary = service.create_task(
            TaskCreateRequest(
                title="T15 preserve",
                question="Is a worse status left alone?",
                demo_mode=DemoMode.SUCCESS,
            )
        )
        task_id = str(summary["task_id"])
        try:
            service.store.update_task(task_id, status=original)
            service._write_exports_unguarded = lambda _task_id: (_ for _ in ()).throw(  # type: ignore[method-assign]
                RuntimeError("renderer down")
            )
            result = service._write_exports(task_id)
            assert result["status"] == original.value, original
            # The terminal event still fires, so the stream still closes.
            assert "task_terminal_no_exports" in _kinds(service, task_id)
        finally:
            service.close()


def test_stream_closes_after_exports_ready_on_a_real_run(tmp_path: Path) -> None:
    """End-to-end: follow=true must terminate rather than block."""

    async def scenario() -> None:
        async with _client(tmp_path) as client:
            created = await client.post(
                "/api/v1/research/tasks",
                json={
                    "title": "T15 stream close",
                    "question": "Does the followed stream terminate on its own?",
                    "demo_mode": "success",
                },
                headers={"Idempotency-Key": "t15-close"},
            )
            assert created.status_code == 201
            task_id = str(created.json()["task_id"])

            approved = await client.post(
                f"/api/v1/research/tasks/{task_id}/approve",
                json={"action": "approve", "reason": "t15"},
            )
            assert approved.status_code == 200

            # Wait for the run to actually reach a terminal state.
            for _ in range(120):
                status = (await client.get(f"/api/v1/research/tasks/{task_id}")).json()["status"]
                if status in {"completed", "degraded", "failed", "cancelled", "rejected"}:
                    break
                await asyncio.sleep(0.25)
            else:  # pragma: no cover - would mean the demo run never finished
                pytest.fail(f"task did not reach a terminal state (last status={status})")

            # A followed stream over a finished task must end, not hang.
            body = await asyncio.wait_for(
                client.get(f"/api/v1/research/tasks/{task_id}/events?follow=true"),
                timeout=20,
            )
            assert body.status_code == 200
            text = body.text
            assert "exports_ready" in text
            assert "event: stream_end" in text

    asyncio.run(scenario())


def test_interrupted_is_a_declared_status(tmp_path: Path) -> None:
    """T04 designed `interrupted`; the backend enum must name it too.

    Detection at startup is T14 and is deliberately not implemented here — this
    only pins that the state exists so the contract and the UI can name it.
    """

    assert TaskStatus.INTERRUPTED.value == "interrupted"
    assert "interrupted" in {status.value for status in TaskStatus}


def test_non_terminal_kinds_do_not_close_the_stream() -> None:
    """Ordering guard: these precede `exports_ready` on terminal paths.

    If any were treated as terminal, the client would close before receiving
    the exports produced by its own run.
    """

    for kind in ("task_failed", "task_cancelled", "plan_rejected", "workflow_degraded"):
        assert kind not in SSE_TERMINAL_EVENT_KINDS, kind


def test_frontend_terminal_kinds_match_the_backend() -> None:
    """The hook's terminal set must name kinds the backend actually emits.

    The A1 skeleton listed `task_rejected` and `task_interrupted`, neither of
    which the backend emits. Reading the hook as text keeps the two sides
    pinned together without a JS runtime in the Python suite.
    """

    hook = (ROOT / "frontend/src/hooks/useTaskEvents.ts").read_text("utf-8")
    block = hook.split("const TERMINAL_KINDS", 1)[1].split("]", 1)[0]
    listed = {
        line.strip().strip(',"')
        for line in block.splitlines()
        if line.strip().startswith('"')
    }
    assert listed == set(SSE_TERMINAL_EVENT_KINDS), listed


def test_service_emits_exports_ready_last_on_every_terminal_path(tmp_path: Path) -> None:
    """`exports_ready` must be the final event, not merely present.

    This is what makes it safe to use as the sole normal stream closer.
    """

    async def scenario() -> None:
        async with _client(tmp_path) as client:
            for index, (mode, key) in enumerate(
                [("success", "t15-a"), ("degraded", "t15-b")]
            ):
                created = await client.post(
                    "/api/v1/research/tasks",
                    json={
                        "title": f"T15 ordering {index}",
                        "question": "Is exports_ready the final event?",
                        "demo_mode": mode,
                    },
                    headers={"Idempotency-Key": key},
                )
                assert created.status_code == 201, mode
                task_id = str(created.json()["task_id"])
                await client.post(
                    f"/api/v1/research/tasks/{task_id}/approve",
                    json={"action": "approve", "reason": "t15"},
                )
                for _ in range(120):
                    status = (
                        await client.get(f"/api/v1/research/tasks/{task_id}")
                    ).json()["status"]
                    if status in {"completed", "degraded", "failed", "cancelled"}:
                        break
                    await asyncio.sleep(0.25)

                # Read through the replay stream: that is the SSE contract itself.
                replay = await client.get(f"/api/v1/research/tasks/{task_id}/events")
                assert replay.status_code == 200
                kinds = [
                    line.split('"kind":"', 1)[1].split('"', 1)[0]
                    for line in replay.text.splitlines()
                    if '"kind":"' in line
                ]
                assert kinds, f"replay produced no events for {mode}"
                assert kinds[-1] == "exports_ready", (mode, kinds[-3:])

    asyncio.run(scenario())
