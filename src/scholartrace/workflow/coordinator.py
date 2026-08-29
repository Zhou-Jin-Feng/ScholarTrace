"""Coordinator interfaces and the fail-closed paid-profile gate."""

from __future__ import annotations

from typing import Protocol

from scholartrace.contracts import ModelRoutingPolicy, ResearchPlan


class CoordinatorUnavailableError(RuntimeError):
    """The approved Coordinator model profile is not available."""


class PlanGenerator(Protocol):
    async def generate_plan(
        self,
        *,
        task_id: str,
        question: str,
        idempotency_key: str,
    ) -> ResearchPlan: ...


class Coordinator(Protocol):
    async def create_plan(
        self,
        *,
        task_id: str,
        question: str,
        idempotency_key: str,
    ) -> ResearchPlan: ...


class PolicyGatedCoordinator:
    """Run a Coordinator only when its declared paid route is explicitly enabled."""

    def __init__(self, *, policy: ModelRoutingPolicy, generator: PlanGenerator) -> None:
        self.policy = policy
        self.generator = generator

    async def create_plan(
        self,
        *,
        task_id: str,
        question: str,
        idempotency_key: str,
    ) -> ResearchPlan:
        route = next((item for item in self.policy.routes if item.node == "coordinator"), None)
        profiles = {item.profile_id: item for item in self.policy.profiles}
        profile = profiles.get(route.default_profile_id) if route is not None else None
        if (
            route is None
            or profile is None
            or profile.profile_id != "api-strong"
            or profile.kind != "api"
            or not profile.enabled
            or not self.policy.paid_routes_enabled
        ):
            raise CoordinatorUnavailableError(
                "Coordinator requires an enabled api-strong profile; local fallback is forbidden"
            )
        plan = await self.generator.generate_plan(
            task_id=task_id,
            question=question,
            idempotency_key=idempotency_key,
        )
        if plan.task_id != task_id:
            raise ValueError("Coordinator returned a plan for a different task")
        return plan
