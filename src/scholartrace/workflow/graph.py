"""Persistent M3 LangGraph orchestration with bounded parallel workers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import aiosqlite
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Send, interrupt

from scholartrace.contracts import ArtifactRef, BudgetUsage, ResearchPlan
from scholartrace.workflow.coordinator import Coordinator
from scholartrace.workflow.graph_types import PaperWorker, SearchBackend
from scholartrace.workflow.models import (
    ApprovalDecision,
    PaperWorkerState,
    ResearchState,
    SearchRoundArtifact,
)
from scholartrace.workflow.search_agent import AdaptiveSearchAgent
from scholartrace.workflow.storage import (
    ArtifactStore,
    RuntimeLedger,
    WorkflowBudgetExceededError,
)
from scholartrace.workflow.workers import IdempotentPaperWorkerRunner


@dataclass(frozen=True, slots=True)
class WorkflowSettings:
    max_worker_concurrency: int = 2
    search_timeout_seconds: float = 30
    worker_timeout_seconds: float = 180
    recursion_limit: int = 50

    def __post_init__(self) -> None:
        if self.max_worker_concurrency < 1 or self.max_worker_concurrency > 16:
            raise ValueError("max_worker_concurrency must be between 1 and 16")
        if self.search_timeout_seconds <= 0 or self.worker_timeout_seconds <= 0:
            raise ValueError("workflow timeouts must be positive")


@dataclass(frozen=True, slots=True)
class CheckpointRetentionPolicy:
    completed_thread_days: int = 7
    delete_requires_explicit_call: bool = True


GraphType = CompiledStateGraph[ResearchState, None, ResearchState, ResearchState]


def build_research_graph(
    *,
    coordinator: Coordinator,
    search_agent: AdaptiveSearchAgent,
    search_backend: SearchBackend,
    paper_worker: PaperWorker,
    artifacts: ArtifactStore,
    ledger: RuntimeLedger,
    checkpointer: BaseCheckpointSaver[str],
    settings: WorkflowSettings,
) -> GraphType:
    worker_runner = IdempotentPaperWorkerRunner(
        worker=paper_worker,
        artifacts=artifacts,
        ledger=ledger,
        max_concurrency=settings.max_worker_concurrency,
        timeout_seconds=settings.worker_timeout_seconds,
    )

    def load_plan(state: ResearchState) -> ResearchPlan:
        return ResearchPlan.model_validate(artifacts.get_json(state["plan_ref"].artifact_id))

    async def create_plan(state: ResearchState) -> ResearchState:
        task_id = state["task_id"]
        artifact_id = f"artifact:m3:{task_id}:plan:draft"
        existing = artifacts.get_ref(artifact_id)
        if existing is not None:
            return {"plan_ref": existing, "status": "waiting_approval"}
        plan = await coordinator.create_plan(
            task_id=task_id,
            question=state["question"],
            idempotency_key=f"effect:m3:{task_id}:coordinator",
        )
        plan = plan.model_copy(update={"status": "waiting_approval"})
        ref = artifacts.put(
            artifact_id=artifact_id,
            artifact_type="research_plan",
            payload=plan,
        )
        ledger.charge(
            effect_key=f"effect:m3:{task_id}:coordinator",
            task_id=task_id,
            delta=BudgetUsage(model_calls=1),
            limits=plan.budget.limits,
        )
        ledger.append_event(
            stable_key=f"event:m3:{task_id}:plan-created",
            task_id=task_id,
            node="create_plan",
            kind="plan_created",
            artifact_id=ref.artifact_id,
        )
        return {"plan_ref": ref, "status": "waiting_approval"}

    def approve_plan(state: ResearchState) -> ResearchState:
        task_id = state["task_id"]
        plan = load_plan(state)
        ledger.append_event(
            stable_key=f"event:m3:{task_id}:approval-requested",
            task_id=task_id,
            node="wait_for_approval",
            kind="approval_required",
            artifact_id=state["plan_ref"].artifact_id,
        )
        raw_decision = interrupt(
            {
                "task_id": task_id,
                "plan_ref": state["plan_ref"].model_dump(mode="json"),
                "allowed_actions": ["approve", "modify", "reject"],
            }
        )
        decision = ApprovalDecision.model_validate(raw_decision)
        if decision.action == "reject":
            ledger.append_event(
                stable_key=f"event:m3:{task_id}:plan-rejected",
                task_id=task_id,
                node="wait_for_approval",
                kind="plan_rejected",
                payload={"reason": decision.reason or "not_provided"},
            )
            return {
                "status": "rejected",
                "stop_reason": "plan_rejected",
                "approval_reason": decision.reason,
            }
        approved_plan = decision.modified_plan if decision.action == "modify" else plan
        if approved_plan is None or approved_plan.task_id != task_id:
            raise ValueError("approved plan must belong to the active task")
        approved_plan = approved_plan.model_copy(update={"status": "approved"})
        approved_ref = artifacts.put(
            artifact_id=f"artifact:m3:{task_id}:plan:approved",
            artifact_type="research_plan",
            payload=approved_plan,
        )
        ledger.append_event(
            stable_key=f"event:m3:{task_id}:plan-approved",
            task_id=task_id,
            node="wait_for_approval",
            kind="plan_modified" if decision.action == "modify" else "plan_approved",
            artifact_id=approved_ref.artifact_id,
        )
        return {
            "plan_ref": approved_ref,
            "status": "running",
            "approval_reason": decision.reason,
        }

    def route_after_approval(state: ResearchState) -> str:
        return "finalize" if state.get("status") == "rejected" else "search"

    async def search(state: ResearchState) -> ResearchState:
        task_id = state["task_id"]
        plan = load_plan(state)
        history = [
            SearchRoundArtifact.model_validate(artifacts.get_json(ref.artifact_id))
            for ref in state.get("search_refs", [])
        ]
        action = search_agent.next_action(plan=plan, history=history)
        if action.kind == "stop":
            assert action.stop_reason is not None
            ledger.append_event(
                stable_key=f"event:m3:{task_id}:search-stop:{action.stop_reason}",
                task_id=task_id,
                node="search",
                kind="search_stopped",
                payload={"reason": action.stop_reason},
            )
            return {"stop_reason": action.stop_reason, "round_count": len(history)}

        round_index = len(history)
        artifact_id = f"artifact:m3:{task_id}:search:{round_index}"
        existing = artifacts.get_ref(artifact_id)
        if existing is not None:
            persisted = SearchRoundArtifact.model_validate(artifacts.get_json(artifact_id))
            return {
                "search_refs": [existing],
                "selected_paper_ids": persisted.new_unique_paper_ids,
                "round_count": round_index + 1,
                "stop_reason": None,
            }

        try:
            ledger.charge(
                effect_key=f"effect:m3:{task_id}:search-call:{round_index}",
                task_id=task_id,
                delta=BudgetUsage(queries=1, api_calls=1),
                limits=plan.budget.limits,
            )
        except WorkflowBudgetExceededError:
            ledger.append_event(
                stable_key=f"event:m3:{task_id}:search-budget:{round_index}",
                task_id=task_id,
                node="search",
                kind="budget_exhausted",
                payload={"round_index": round_index},
            )
            return {
                "stop_reason": "search_budget_exhausted",
                "round_count": round_index,
                "status": "degraded",
            }
        assert action.query is not None
        try:
            result = await asyncio.wait_for(
                search_backend.search(
                    query=action.query,
                    plan=plan,
                    round_index=round_index,
                    idempotency_key=f"effect:m3:{task_id}:search-call:{round_index}",
                ),
                timeout=settings.search_timeout_seconds,
            )
        except TimeoutError:
            ledger.append_event(
                stable_key=f"event:m3:{task_id}:search-timeout:{round_index}",
                task_id=task_id,
                node="search",
                kind="search_timed_out",
                payload={"round_index": round_index},
            )
            return {
                "stop_reason": "search_timed_out",
                "round_count": round_index,
                "status": "degraded",
            }
        prior_ids = {
            paper_id
            for round_artifact in history
            for paper_id in round_artifact.candidate_paper_ids
        }
        candidates = list(dict.fromkeys(result.candidate_paper_ids))
        raw_new_ids = [paper_id for paper_id in candidates if paper_id not in prior_ids]
        new_unique_ratio = len(raw_new_ids) / len(candidates) if candidates else 0
        current_usage = ledger.usage(task_id)
        remaining_candidates = max(
            0, plan.budget.limits.max_candidate_papers - current_usage.candidate_papers
        )
        new_ids = raw_new_ids[:remaining_candidates]
        accepted_new_ids = set(new_ids)
        candidates = [
            paper_id
            for paper_id in candidates
            if paper_id in prior_ids or paper_id in accepted_new_ids
        ]
        ledger.charge(
            effect_key=f"effect:m3:{task_id}:search-results:{round_index}",
            task_id=task_id,
            delta=BudgetUsage(candidate_papers=len(new_ids)),
            limits=plan.budget.limits,
        )
        assert action.adjustment_reason is not None
        search_round = SearchRoundArtifact(
            task_id=task_id,
            round_index=round_index,
            query=action.query,
            adjustment_reason=action.adjustment_reason,
            candidate_paper_ids=candidates,
            new_unique_paper_ids=new_ids,
            covered_subquestion_ids=sorted(set(result.covered_subquestion_ids)),
            new_unique_ratio=new_unique_ratio,
        )
        ref = artifacts.put(
            artifact_id=artifact_id,
            artifact_type="search_snapshot",
            payload=search_round,
        )
        ledger.append_event(
            stable_key=f"event:m3:{task_id}:search:{round_index}",
            task_id=task_id,
            node="search",
            kind="search_round_completed",
            artifact_id=ref.artifact_id,
            payload={
                "round_index": round_index,
                "adjustment_reason": action.adjustment_reason,
                "new_unique_count": len(new_ids),
            },
        )
        return {
            "search_refs": [ref],
            "selected_paper_ids": new_ids,
            "round_count": round_index + 1,
            "stop_reason": None,
        }

    def route_after_search(state: ResearchState) -> str | list[Send]:
        if state.get("stop_reason") is None:
            return "search"
        selected = state.get("selected_paper_ids", [])
        if not selected:
            return "finalize"
        plan = load_plan(state)
        bounded = sorted(selected)[: plan.budget.limits.max_fulltext_papers]
        return [
            Send(
                "paper_worker",
                PaperWorkerState(
                    task_id=state["task_id"],
                    thread_id=state["thread_id"],
                    canonical_paper_id=paper_id,
                    plan_ref=state["plan_ref"],
                ),
            )
            for paper_id in bounded
        ]

    async def analyze_paper(state: PaperWorkerState) -> ResearchState:
        task_id = state["task_id"]
        paper_id = state["canonical_paper_id"]
        plan = ResearchPlan.model_validate(artifacts.get_json(state["plan_ref"].artifact_id))
        ref, failed = await worker_runner.run(
            task_id=task_id,
            canonical_paper_id=paper_id,
            plan=plan,
        )
        return {"paper_result_refs": [ref], "failed_paper_ids": failed}

    def finalize(state: ResearchState) -> ResearchState:
        task_id = state["task_id"]
        if state.get("status") == "rejected":
            outcome = "rejected"
        elif state.get("status") == "degraded" or state.get("failed_paper_ids"):
            outcome = "degraded"
        else:
            outcome = "completed"
        ledger.append_event(
            stable_key=f"event:m3:{task_id}:final:{outcome}",
            task_id=task_id,
            node="finalize",
            kind="workflow_finished",
            payload={"outcome": outcome},
        )
        return {"status": outcome}

    builder = StateGraph(ResearchState)
    builder.add_node("create_plan", create_plan)
    builder.add_node("wait_for_approval", approve_plan)
    builder.add_node("search", search)
    builder.add_node("paper_worker", analyze_paper)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "create_plan")
    builder.add_edge("create_plan", "wait_for_approval")
    builder.add_conditional_edges(
        "wait_for_approval", route_after_approval, ["search", "finalize"]
    )
    builder.add_conditional_edges(
        "search", route_after_search, ["search", "paper_worker", "finalize"]
    )
    builder.add_edge("paper_worker", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


class M3Workflow:
    def __init__(
        self,
        *,
        graph: GraphType,
        checkpointer: AsyncSqliteSaver,
        artifacts: ArtifactStore,
        ledger: RuntimeLedger,
        settings: WorkflowSettings,
    ) -> None:
        self.graph = graph
        self.checkpointer = checkpointer
        self.artifacts = artifacts
        self.ledger = ledger
        self.settings = settings
        self.retention = CheckpointRetentionPolicy()

    def _config(self, thread_id: str) -> RunnableConfig:
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self.settings.recursion_limit,
            "max_concurrency": self.settings.max_worker_concurrency,
        }

    async def start(
        self, *, task_id: str, thread_id: str, question: str
    ) -> dict[str, object]:
        initial = ResearchState(
            graph_schema_version="1.0",
            task_id=task_id,
            thread_id=thread_id,
            question=question,
            started_at=datetime.now(UTC).isoformat(),
            status="created",
            search_refs=[],
            selected_paper_ids=[],
            paper_result_refs=[],
            failed_paper_ids=[],
            round_count=0,
            stop_reason=None,
            approval_reason=None,
        )
        result = await self.graph.ainvoke(initial, self._config(thread_id))
        return cast(dict[str, object], result)

    async def resume(
        self, *, thread_id: str, decision: ApprovalDecision
    ) -> dict[str, object]:
        result = await self.graph.ainvoke(
            Command(resume=decision.model_dump(mode="json")), self._config(thread_id)
        )
        return cast(dict[str, object], result)

    async def state(self, *, thread_id: str) -> dict[str, object]:
        snapshot = await self.graph.aget_state(self._config(thread_id))
        return cast(dict[str, object], snapshot.values)

    async def delete_thread(self, *, thread_id: str) -> None:
        """Apply the explicit M3 retention action for a completed thread."""

        state = await self.state(thread_id=thread_id)
        if state.get("status") not in {"completed", "degraded", "rejected"}:
            raise RuntimeError("only terminal workflow checkpoints can be deleted")
        await self.checkpointer.adelete_thread(thread_id)


@asynccontextmanager
async def open_m3_workflow(
    *,
    checkpoint_path: Path,
    artifact_path: Path,
    runtime_path: Path,
    coordinator: Coordinator,
    search_backend: SearchBackend,
    paper_worker: PaperWorker,
    search_agent: AdaptiveSearchAgent | None = None,
    settings: WorkflowSettings | None = None,
) -> AsyncIterator[M3Workflow]:
    """Open the persistent graph and its separate business stores."""

    resolved_paths = {
        checkpoint_path.resolve(),
        artifact_path.resolve(),
        runtime_path.resolve(),
    }
    if len(resolved_paths) != 3:
        raise ValueError("checkpoint, artifact and runtime storage must be separate")
    artifacts = ArtifactStore(artifact_path)
    ledger = RuntimeLedger(runtime_path)
    resolved_settings = settings or WorkflowSettings()
    try:
        serializer = JsonPlusSerializer(allowed_msgpack_modules=[ArtifactRef])
        async with aiosqlite.connect(str(checkpoint_path)) as connection:
            checkpointer = AsyncSqliteSaver(connection, serde=serializer)
            await checkpointer.setup()
            graph = build_research_graph(
                coordinator=coordinator,
                search_agent=search_agent or AdaptiveSearchAgent(),
                search_backend=search_backend,
                paper_worker=paper_worker,
                artifacts=artifacts,
                ledger=ledger,
                checkpointer=checkpointer,
                settings=resolved_settings,
            )
            yield M3Workflow(
                graph=graph,
                checkpointer=checkpointer,
                artifacts=artifacts,
                ledger=ledger,
                settings=resolved_settings,
            )
    finally:
        ledger.close()
        artifacts.close()
