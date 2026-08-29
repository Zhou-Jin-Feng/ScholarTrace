"""Deterministic first-pass adaptation and saturation policy for search rounds."""

from __future__ import annotations

from scholartrace.contracts import ResearchPlan
from scholartrace.workflow.models import SearchAction, SearchRoundArtifact


class AdaptiveSearchAgent:
    """Choose a bounded next query from persisted intermediate results."""

    def __init__(self, *, saturation_ratio: float = 0.2) -> None:
        if saturation_ratio < 0 or saturation_ratio > 1:
            raise ValueError("saturation_ratio must be between zero and one")
        self.saturation_ratio = saturation_ratio

    def next_action(
        self,
        *,
        plan: ResearchPlan,
        history: list[SearchRoundArtifact],
    ) -> SearchAction:
        covered = {
            subquestion_id
            for item in history
            for subquestion_id in item.covered_subquestion_ids
        }
        required = {item.subquestion_id for item in plan.subquestions}
        if history and required.issubset(covered):
            return SearchAction(kind="stop", stop_reason="coverage_complete")
        if len(history) >= plan.budget.limits.max_rounds:
            return SearchAction(kind="stop", stop_reason="max_rounds")
        if len(history) >= plan.budget.limits.max_queries:
            return SearchAction(kind="stop", stop_reason="query_budget_exhausted")
        if history:
            latest = history[-1]
            newly_covered = set(latest.covered_subquestion_ids)
            previously_covered = {
                subquestion_id
                for item in history[:-1]
                for subquestion_id in item.covered_subquestion_ids
            }
            if (
                latest.new_unique_ratio < self.saturation_ratio
                and not newly_covered.difference(previously_covered)
            ):
                return SearchAction(kind="stop", stop_reason="saturated")
            target = next(item for item in plan.subquestions if item.subquestion_id not in covered)
            return SearchAction(
                kind="search",
                query=f"{plan.question} AND {target.question}",
                adjustment_reason="uncovered_subquestion",
            )
        initial_terms = " OR ".join(item.question for item in plan.subquestions[:3])
        return SearchAction(
            kind="search",
            query=f"{plan.question} AND ({initial_terms})",
            adjustment_reason="initial_plan",
        )
