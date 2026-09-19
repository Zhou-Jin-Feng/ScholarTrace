from __future__ import annotations

import asyncio
from pathlib import Path

from a2_core_fixtures import offline_pipeline

from scholartrace.delivery.models import ApprovalRequest, TaskCreateRequest, TaskPhase, TaskStatus
from scholartrace.delivery.service import M6TaskService

ROOT = Path(__file__).resolve().parents[1]


def test_restart_marks_abandoned_running_task_interrupted_without_replaying(tmp_path):
    service = M6TaskService(root=ROOT, data_dir=tmp_path)
    task_id = service.create_task(TaskCreateRequest(question="Synthetic recovery task"))["task_id"]
    service.store.update_task(task_id, status=TaskStatus.RUNNING, phase=TaskPhase.EVIDENCE)
    service.close()
    resumed = M6TaskService(root=ROOT, data_dir=tmp_path)
    try:
        task = resumed.summary(task_id)
        assert task["status"] == "interrupted"
        assert task["phase"] == "evidence"
        assert resumed.queue_snapshot()["submitted"] == 0
        assert any(e["kind"] == "task_interrupted" for e in resumed.events(task_id))
        assert resumed.cancel_task(task_id)["status"] == "cancelled"
    finally:
        resumed.close()


def test_explicit_resume_requires_exact_plan_and_reuses_persisted_offline_result(tmp_path):
    calls = []

    def runner(question, plan, cancel_event):
        async def run():
            async with offline_pipeline(tmp_path / "research") as (pipeline, backend):
                result = await pipeline.run(question=question, plan=plan, cancel_event=cancel_event)
                calls.extend(backend.events)
                return result

        return asyncio.run(run())

    service = M6TaskService(
        root=ROOT,
        data_dir=tmp_path / "delivery",
        offline_research_runner=runner,
        completion_wait_seconds=3,
    )
    task_id = service.create_task(TaskCreateRequest(question="What determines retrieval?"))[
        "task_id"
    ]
    service.acknowledge_plan_cost(task_id, acknowledged_max_cny=0)
    plan = service.generate_plan(task_id)
    decision = ApprovalRequest(
        action="approve", plan_version=plan["plan_version"], plan_digest=plan["plan_digest"]
    )
    service.approve_task(task_id, decision)
    before = len(calls)
    # Emulate lost task-state acknowledgement after durable research completion.
    service.store.update_task(task_id, status=TaskStatus.RUNNING)
    service.close()
    resumed = M6TaskService(
        root=ROOT,
        data_dir=tmp_path / "delivery",
        offline_research_runner=runner,
        completion_wait_seconds=3,
    )
    try:
        import pytest

        from scholartrace.delivery.service import TaskStateError

        with pytest.raises(TaskStateError):
            resumed.resume_task(task_id, plan_version=1, plan_digest="0" * 64)
        result = resumed.resume_task(
            task_id, plan_version=plan["plan_version"], plan_digest=plan["plan_digest"]
        )
        assert result["status"] == "degraded"
        assert len(calls) == before
    finally:
        resumed.close()
