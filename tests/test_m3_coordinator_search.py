from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from m3_fixtures import make_plan

from scholartrace.contracts import ModelRoutingPolicy
from scholartrace.workflow.coordinator import (
    CoordinatorUnavailableError,
    PolicyGatedCoordinator,
)
from scholartrace.workflow.models import SearchRoundArtifact
from scholartrace.workflow.search_agent import AdaptiveSearchAgent

ROOT = Path(__file__).resolve().parents[1]


class UnexpectedGenerator:
    async def generate_plan(self, **_: object) -> object:
        raise AssertionError("disabled Coordinator must not call the provider")


def test_coordinator_fails_closed_when_api_strong_is_disabled() -> None:
    bundle = json.loads((ROOT / "contracts/examples/m0_bundle.json").read_text("utf-8"))
    coordinator = PolicyGatedCoordinator(
        policy=ModelRoutingPolicy.model_validate(bundle["ModelRoutingPolicy"]),
        generator=UnexpectedGenerator(),  # type: ignore[arg-type]
    )

    with pytest.raises(CoordinatorUnavailableError, match="local fallback is forbidden"):
        asyncio.run(
            coordinator.create_plan(
                task_id="task:m3-disabled",
                question="Should this call an unconfigured provider?",
                idempotency_key="effect:m3:disabled:coordinator",
            )
        )


def test_search_agent_changes_query_for_uncovered_subquestion() -> None:
    plan = make_plan()
    agent = AdaptiveSearchAgent()
    first = agent.next_action(plan=plan, history=[])
    history = [
        SearchRoundArtifact(
            task_id=plan.task_id,
            round_index=0,
            query=first.query or "missing",
            adjustment_reason="initial_plan",
            candidate_paper_ids=["paper:a", "paper:b"],
            new_unique_paper_ids=["paper:a", "paper:b"],
            covered_subquestion_ids=["subq:retrieval"],
            new_unique_ratio=1,
        )
    ]

    second = agent.next_action(plan=plan, history=history)

    assert first.kind == "search"
    assert second.kind == "search"
    assert second.adjustment_reason == "uncovered_subquestion"
    assert second.query != first.query
    assert "evidence provenance" in (second.query or "")


def test_search_agent_stops_on_saturation_without_new_coverage() -> None:
    plan = make_plan(max_rounds=3, max_queries=3)
    agent = AdaptiveSearchAgent(saturation_ratio=0.2)
    history = [
        SearchRoundArtifact(
            task_id=plan.task_id,
            round_index=0,
            query="first",
            adjustment_reason="initial_plan",
            candidate_paper_ids=["paper:a"],
            new_unique_paper_ids=["paper:a"],
            covered_subquestion_ids=["subq:retrieval"],
            new_unique_ratio=1,
        ),
        SearchRoundArtifact(
            task_id=plan.task_id,
            round_index=1,
            query="second",
            adjustment_reason="uncovered_subquestion",
            candidate_paper_ids=["paper:a"],
            new_unique_paper_ids=[],
            covered_subquestion_ids=["subq:retrieval"],
            new_unique_ratio=0,
        ),
    ]

    decision = agent.next_action(plan=plan, history=history)

    assert decision.kind == "stop"
    assert decision.stop_reason == "saturated"
