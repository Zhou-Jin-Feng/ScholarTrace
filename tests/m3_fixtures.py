from __future__ import annotations

import asyncio
from datetime import date

from scholartrace.contracts import (
    Budget,
    BudgetLimits,
    BudgetUsage,
    ResearchPlan,
    ResearchSubquestion,
)
from scholartrace.workflow.models import SearchBackendResult


def make_plan(
    *,
    task_id: str = "task:m3-fixture",
    max_rounds: int = 2,
    max_queries: int = 2,
    max_fulltext_papers: int = 3,
) -> ResearchPlan:
    return ResearchPlan(
        task_id=task_id,
        title="M3 adaptive retrieval fixture",
        question="How should adaptive RAG systems be evaluated?",
        objective="Compare retrieval adaptation and evidence traceability.",
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
        inclusion_criteria=["Peer-reviewed or public preprint research"],
        exclusion_criteria=["Systems without retrievable evidence"],
        sources=["arxiv", "openalex", "crossref"],
        retrieval_cutoff=date(2026, 8, 29),
        budget=Budget(
            limits=BudgetLimits(
                max_rounds=max_rounds,
                max_queries=max_queries,
                max_candidate_papers=10,
                max_fulltext_papers=max_fulltext_papers,
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
        assert task_id == self.plan.task_id
        assert question
        assert idempotency_key.endswith(":coordinator")
        return self.plan


class AdaptiveFixtureSearch:
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
        assert idempotency_key.endswith(f":search-call:{round_index}")
        if round_index == 0:
            return SearchBackendResult(
                candidate_paper_ids=["paper:b", "paper:a"],
                covered_subquestion_ids=["subq:retrieval"],
            )
        return SearchBackendResult(
            candidate_paper_ids=["paper:b", "paper:c"],
            covered_subquestion_ids=["subq:evidence"],
        )


class TrackingWorker:
    def __init__(self, *, delay: float = 0.01) -> None:
        self.delay = delay
        self.calls: list[tuple[str, str]] = []
        self.active = 0
        self.max_active = 0

    async def analyze(
        self,
        *,
        task_id: str,
        canonical_paper_id: str,
        plan: ResearchPlan,
        idempotency_key: str,
    ) -> dict[str, object]:
        self.calls.append((canonical_paper_id, idempotency_key))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return {"paper_id": canonical_paper_id, "claim_count": 1}
        finally:
            self.active -= 1
