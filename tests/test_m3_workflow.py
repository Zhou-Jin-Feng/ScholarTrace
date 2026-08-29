from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from m3_fixtures import (
    AdaptiveFixtureSearch,
    FixtureCoordinator,
    TrackingWorker,
    make_plan,
)

from scholartrace.workflow import (
    ApprovalDecision,
    ArtifactStore,
    IdempotentPaperWorkerRunner,
    RuntimeLedger,
    SearchBackendResult,
    WorkflowSettings,
    open_m3_workflow,
)
from scholartrace.workflow.models import SearchRoundArtifact


def test_persistent_interrupt_resume_dynamic_route_and_parallel_merge(tmp_path: Path) -> None:
    async def scenario() -> None:
        task_id = "task:m3-persistent"
        thread_id = "thread:m3-persistent"
        plan = make_plan(task_id=task_id)
        coordinator = FixtureCoordinator(plan)
        search = AdaptiveFixtureSearch()
        worker = TrackingWorker(delay=0.02)
        checkpoint = tmp_path / "checkpoints.sqlite"
        artifacts = tmp_path / "artifacts.sqlite"
        runtime = tmp_path / "runtime.sqlite"

        async with open_m3_workflow(
            checkpoint_path=checkpoint,
            artifact_path=artifacts,
            runtime_path=runtime,
            coordinator=coordinator,
            search_backend=search,
            paper_worker=worker,
        ) as workflow:
            interrupted = await workflow.start(
                task_id=task_id,
                thread_id=thread_id,
                question=plan.question,
            )
            assert "__interrupt__" in interrupted
            assert workflow.artifacts.count() == 1
            assert workflow.ledger.event_count(task_id) == 2

        async with open_m3_workflow(
            checkpoint_path=checkpoint,
            artifact_path=artifacts,
            runtime_path=runtime,
            coordinator=coordinator,
            search_backend=search,
            paper_worker=worker,
        ) as restarted:
            completed = await restarted.resume(
                thread_id=thread_id,
                decision=ApprovalDecision(action="approve", reason="fixture approved"),
            )
            state_json = json.dumps(completed, default=str)
            refs = completed["paper_result_refs"]
            assert completed["status"] == "completed"
            assert completed["stop_reason"] == "coverage_complete"
            assert len(search.queries) == 2
            assert search.queries[0] != search.queries[1]
            assert worker.max_active == 2
            assert len(worker.calls) == 3
            assert [ref.artifact_id for ref in refs] == sorted(
                ref.artifact_id for ref in refs
            )
            assert "quote" not in state_json
            assert "claim_count" not in state_json
            assert len(state_json.encode("utf-8")) < 16_384
            assert restarted.ledger.usage(task_id).queries == 2

            search_refs = completed["search_refs"]
            rounds = [
                SearchRoundArtifact.model_validate(
                    restarted.artifacts.get_json(ref.artifact_id)
                )
                for ref in search_refs
            ]
            assert [item.adjustment_reason for item in rounds] == [
                "initial_plan",
                "uncovered_subquestion",
            ]
            assert restarted.ledger.replay(task_id=task_id)[-1].kind == "workflow_finished"

        assert coordinator.calls == 1
        assert checkpoint != artifacts != runtime

    asyncio.run(scenario())


def test_human_can_modify_plan_before_execution(tmp_path: Path) -> None:
    async def scenario() -> None:
        task_id = "task:m3-modified"
        original = make_plan(task_id=task_id, max_queries=2)
        modified = make_plan(task_id=task_id, max_queries=1).model_copy(
            update={"objective": "Human-modified objective."}
        )
        coordinator = FixtureCoordinator(original)
        search = AdaptiveFixtureSearch()
        worker = TrackingWorker(delay=0)
        async with open_m3_workflow(
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            artifact_path=tmp_path / "artifacts.sqlite",
            runtime_path=tmp_path / "runtime.sqlite",
            coordinator=coordinator,
            search_backend=search,
            paper_worker=worker,
        ) as workflow:
            await workflow.start(
                task_id=task_id,
                thread_id="thread:modified",
                question=original.question,
            )
            completed = await workflow.resume(
                thread_id="thread:modified",
                decision=ApprovalDecision(
                    action="modify",
                    modified_plan=modified,
                    reason="reduce query budget",
                ),
            )
            approved = workflow.artifacts.get_json(
                f"artifact:m3:{task_id}:plan:approved"
            )

            assert approved["objective"] == "Human-modified objective."
            assert completed["stop_reason"] == "query_budget_exhausted"
            assert len(search.queries) == 1

    asyncio.run(scenario())


def test_rejected_plan_runs_no_search_or_worker(tmp_path: Path) -> None:
    async def scenario() -> None:
        task_id = "task:m3-rejected"
        plan = make_plan(task_id=task_id)
        coordinator = FixtureCoordinator(plan)
        search = AdaptiveFixtureSearch()
        worker = TrackingWorker()
        async with open_m3_workflow(
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            artifact_path=tmp_path / "artifacts.sqlite",
            runtime_path=tmp_path / "runtime.sqlite",
            coordinator=coordinator,
            search_backend=search,
            paper_worker=worker,
        ) as workflow:
            await workflow.start(
                task_id=task_id,
                thread_id="thread:rejected",
                question=plan.question,
            )
            result = await workflow.resume(
                thread_id="thread:rejected",
                decision=ApprovalDecision(action="reject", reason="scope is wrong"),
            )

            assert result["status"] == "rejected"
            assert search.queries == []
            assert worker.calls == []

    asyncio.run(scenario())


def test_checkpoint_cleanup_rejects_active_workflow(tmp_path: Path) -> None:
    async def scenario() -> None:
        task_id = "task:m3-retention"
        plan = make_plan(task_id=task_id)
        async with open_m3_workflow(
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            artifact_path=tmp_path / "artifacts.sqlite",
            runtime_path=tmp_path / "runtime.sqlite",
            coordinator=FixtureCoordinator(plan),
            search_backend=AdaptiveFixtureSearch(),
            paper_worker=TrackingWorker(),
        ) as workflow:
            await workflow.start(
                task_id=task_id,
                thread_id="thread:retention",
                question=plan.question,
            )
            with pytest.raises(RuntimeError, match="terminal workflow"):
                await workflow.delete_thread(thread_id="thread:retention")

    asyncio.run(scenario())


def test_worker_timeout_isolated_as_degraded_result(tmp_path: Path) -> None:
    class OneRoundSearch:
        async def search(self, **_: object) -> SearchBackendResult:
            return SearchBackendResult(
                candidate_paper_ids=["paper:slow"],
                covered_subquestion_ids=["subq:retrieval", "subq:evidence"],
            )

    async def scenario() -> None:
        task_id = "task:m3-timeout"
        plan = make_plan(task_id=task_id)
        worker = TrackingWorker(delay=0.05)
        async with open_m3_workflow(
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            artifact_path=tmp_path / "artifacts.sqlite",
            runtime_path=tmp_path / "runtime.sqlite",
            coordinator=FixtureCoordinator(plan),
            search_backend=OneRoundSearch(),
            paper_worker=worker,
            settings=WorkflowSettings(worker_timeout_seconds=0.005),
        ) as workflow:
            await workflow.start(
                task_id=task_id,
                thread_id="thread:timeout",
                question=plan.question,
            )
            result = await workflow.resume(
                thread_id="thread:timeout", decision=ApprovalDecision(action="approve")
            )
            assert result["status"] == "degraded"
            assert result["failed_paper_ids"] == ["paper:slow"]
            events = workflow.ledger.replay(task_id=task_id)
            assert any(event.kind == "paper_worker_timed_out" for event in events)

    asyncio.run(scenario())


def test_duplicate_worker_delivery_has_one_effect_and_one_charge(tmp_path: Path) -> None:
    async def scenario() -> None:
        task_id = "task:m3-duplicate"
        plan = make_plan(task_id=task_id)
        worker = TrackingWorker(delay=0.01)
        artifacts = ArtifactStore(tmp_path / "artifacts.sqlite")
        ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
        try:
            runner = IdempotentPaperWorkerRunner(
                worker=worker,
                artifacts=artifacts,
                ledger=ledger,
                max_concurrency=2,
                timeout_seconds=1,
            )
            first, second = await asyncio.gather(
                runner.run(task_id=task_id, canonical_paper_id="paper:same", plan=plan),
                runner.run(task_id=task_id, canonical_paper_id="paper:same", plan=plan),
            )

            assert first == second
            assert len(worker.calls) == 1
            assert ledger.usage(task_id).fulltext_papers == 1
            assert ledger.event_count(task_id) == 1
            assert artifacts.count() == 1
        finally:
            ledger.close()
            artifacts.close()

    asyncio.run(scenario())


def test_search_timeout_stops_without_starting_workers(tmp_path: Path) -> None:
    class SlowSearch:
        async def search(self, **_: object) -> SearchBackendResult:
            await asyncio.sleep(0.05)
            return SearchBackendResult()

    async def scenario() -> None:
        task_id = "task:m3-search-timeout"
        plan = make_plan(task_id=task_id)
        worker = TrackingWorker()
        async with open_m3_workflow(
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            artifact_path=tmp_path / "artifacts.sqlite",
            runtime_path=tmp_path / "runtime.sqlite",
            coordinator=FixtureCoordinator(plan),
            search_backend=SlowSearch(),
            paper_worker=worker,
            settings=WorkflowSettings(search_timeout_seconds=0.005),
        ) as workflow:
            await workflow.start(
                task_id=task_id,
                thread_id="thread:search-timeout",
                question=plan.question,
            )
            result = await workflow.resume(
                thread_id="thread:search-timeout",
                decision=ApprovalDecision(action="approve"),
            )

            assert result["status"] == "degraded"
            assert result["stop_reason"] == "search_timed_out"
            assert worker.calls == []
            assert any(
                event.kind == "search_timed_out"
                for event in workflow.ledger.replay(task_id=task_id)
            )

    asyncio.run(scenario())


def test_graph_stops_before_search_when_api_budget_is_exhausted(tmp_path: Path) -> None:
    async def scenario() -> None:
        task_id = "task:m3-budget"
        base = make_plan(task_id=task_id)
        limits = base.budget.limits.model_copy(update={"max_api_calls": 0})
        plan = base.model_copy(
            update={"budget": base.budget.model_copy(update={"limits": limits})}
        )
        search = AdaptiveFixtureSearch()
        worker = TrackingWorker()
        async with open_m3_workflow(
            checkpoint_path=tmp_path / "checkpoints.sqlite",
            artifact_path=tmp_path / "artifacts.sqlite",
            runtime_path=tmp_path / "runtime.sqlite",
            coordinator=FixtureCoordinator(plan),
            search_backend=search,
            paper_worker=worker,
        ) as workflow:
            await workflow.start(
                task_id=task_id,
                thread_id="thread:budget",
                question=plan.question,
            )
            result = await workflow.resume(
                thread_id="thread:budget",
                decision=ApprovalDecision(action="approve"),
            )

            assert result["status"] == "degraded"
            assert result["stop_reason"] == "search_budget_exhausted"
            assert search.queries == []
            assert worker.calls == []
            assert any(
                event.kind == "budget_exhausted"
                for event in workflow.ledger.replay(task_id=task_id)
            )

    asyncio.run(scenario())
