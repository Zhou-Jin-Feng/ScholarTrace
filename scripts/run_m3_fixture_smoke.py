"""Run the deterministic M3 interrupt, restart, adaptive search and Send smoke."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from scholartrace.contracts import (
    Budget,
    BudgetLimits,
    BudgetUsage,
    ResearchPlan,
    ResearchSubquestion,
)
from scholartrace.search.storage import write_json
from scholartrace.workflow import ApprovalDecision, SearchBackendResult, open_m3_workflow

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_DIR = ROOT / "artifacts" / "m3-workflow-fixture"
DEFAULT_SUMMARY = ROOT / "artifacts" / "reports" / "m3_workflow_fixture_smoke.json"


def _plan(task_id: str) -> ResearchPlan:
    return ResearchPlan(
        task_id=task_id,
        title="M3 deterministic workflow fixture",
        question="How should adaptive RAG systems be evaluated?",
        objective="Exercise M3 control-flow reliability without a model provider.",
        subquestions=[
            ResearchSubquestion(
                subquestion_id="subq:retrieval",
                question="Which retrieval adaptations improve coverage?",
                evidence_required="fulltext",
                priority="critical",
            ),
            ResearchSubquestion(
                subquestion_id="subq:evidence",
                question="How is evidence provenance validated?",
                evidence_required="fulltext",
                priority="high",
            ),
        ],
        inclusion_criteria=["Public research record"],
        exclusion_criteria=["No retrievable evidence"],
        sources=["arxiv", "openalex", "crossref"],
        retrieval_cutoff=date(2026, 8, 29),
        budget=Budget(
            limits=BudgetLimits(
                max_rounds=2,
                max_queries=2,
                max_candidate_papers=10,
                max_fulltext_papers=3,
                max_rag_calls_per_paper=2,
                max_concurrency=2,
                max_llm_input_tokens=10_000,
                max_llm_output_tokens=2_000,
                max_total_tokens=12_000,
                max_api_calls=4,
                max_model_calls=4,
                max_cost_cny=0,
                max_duration_seconds=60,
            ),
            usage=BudgetUsage(),
        ),
        status="draft",
    )


class FixtureCoordinator:
    def __init__(self, plan: ResearchPlan) -> None:
        self.plan = plan
        self.calls = 0

    async def create_plan(
        self, *, task_id: str, question: str, idempotency_key: str
    ) -> ResearchPlan:
        self.calls += 1
        if (
            task_id != self.plan.task_id
            or not question
            or not idempotency_key.endswith(":coordinator")
        ):
            raise ValueError("fixture Coordinator received an invalid task")
        return self.plan


class FixtureSearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(
        self,
        *,
        query: str,
        plan: ResearchPlan,
        round_index: int,
        idempotency_key: str,
    ) -> SearchBackendResult:
        self.queries.append(query)
        if not idempotency_key.endswith(f":search-call:{round_index}"):
            raise ValueError("fixture Search received an invalid idempotency key")
        if round_index == 0:
            return SearchBackendResult(
                candidate_paper_ids=["paper:b", "paper:a"],
                covered_subquestion_ids=["subq:retrieval"],
            )
        return SearchBackendResult(
            candidate_paper_ids=["paper:b", "paper:c"],
            covered_subquestion_ids=["subq:evidence"],
        )


class FixtureWorker:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls: list[str] = []

    async def analyze(
        self,
        *,
        task_id: str,
        canonical_paper_id: str,
        plan: ResearchPlan,
        idempotency_key: str,
    ) -> dict[str, object]:
        self.calls.append(idempotency_key)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            return {"paper_id": canonical_paper_id, "fixture": True}
        finally:
            self.active -= 1


async def _run(work_dir: Path) -> dict[str, object]:
    task_id = "task:m3-fixture-smoke"
    thread_id = "thread:m3-fixture-smoke"
    plan = _plan(task_id)
    coordinator = FixtureCoordinator(plan)
    search = FixtureSearch()
    worker = FixtureWorker()
    checkpoint = work_dir / "checkpoints.sqlite"
    artifacts = work_dir / "artifacts.sqlite"
    runtime = work_dir / "runtime.sqlite"

    started_at = datetime.now(UTC)
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
        interrupt_saved = "__interrupt__" in interrupted
        artifacts_before_restart = workflow.artifacts.count()
        events_before_restart = workflow.ledger.event_count(task_id)

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
            decision=ApprovalDecision(action="approve", reason="fixture approval"),
        )
        event_log = restarted.ledger.replay(task_id=task_id)
        replay_after = restarted.ledger.replay(
            task_id=task_id,
            last_event_id=event_log[1].event_id,
        )
        usage = restarted.ledger.usage(task_id)
        state_size_bytes = len(json.dumps(completed, default=str).encode("utf-8"))
        paper_refs = completed["paper_result_refs"]
        artifact_count = restarted.artifacts.count()

    completed_at = datetime.now(UTC)
    dynamic_route = len(search.queries) == 2 and search.queries[0] != search.queries[1]
    deterministic_merge = [ref.artifact_id for ref in paper_refs] == sorted(
        ref.artifact_id for ref in paper_refs
    )
    passed = all(
        (
            interrupt_saved,
            artifacts_before_restart == 1,
            events_before_restart == 2,
            coordinator.calls == 1,
            completed["status"] == "completed",
            completed["stop_reason"] == "coverage_complete",
            dynamic_route,
            len(worker.calls) == 3,
            worker.max_active == 2,
            usage.queries == 2,
            deterministic_merge,
            state_size_bytes < 16_384,
            bool(replay_after),
        )
    )
    return {
        "schema_version": "1.0",
        "generated_at": completed_at.isoformat(),
        "fixture_kind": "deterministic_m3_control_flow",
        "provider_calls": 0,
        "local_model_calls": 0,
        "coordinator_fixture_calls": coordinator.calls,
        "checkpoint_backend": "sqlite",
        "interrupt_saved": interrupt_saved,
        "resource_restart_before_resume": True,
        "dynamic_query_route": dynamic_route,
        "query_count": len(search.queries),
        "query_adjustment_reasons": ["initial_plan", "uncovered_subquestion"],
        "selected_paper_count": len(paper_refs),
        "paper_worker_calls": len(worker.calls),
        "max_observed_worker_concurrency": worker.max_active,
        "deterministic_merge": deterministic_merge,
        "artifact_count": artifact_count,
        "event_count": len(event_log),
        "replayed_event_count": len(replay_after),
        "state_size_bytes": state_size_bytes,
        "budget_usage": usage.model_dump(mode="json"),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
        "passed": passed,
        "notes": [
            "The api-strong profile remains disabled and no model provider was called.",
            "The Coordinator and tool responses are deterministic fixtures for control-flow tests.",
            "Checkpoint, Artifact Store and runtime event ledger use three separate SQLite files.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="run-", dir=args.work_dir) as temporary:
        summary = asyncio.run(_run(Path(temporary)))
    write_json(args.summary_output, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
