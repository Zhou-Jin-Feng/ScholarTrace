"""Typed durable stage replay with task-wide deadline and cooperative cancellation."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from decimal import Decimal
from typing import TypeVar

from pydantic import TypeAdapter

from scholartrace.delivery.authorization import CallContext
from scholartrace.delivery.effects import EffectCancelledError, EffectJournal
from scholartrace.delivery.models import ResearchPlanView

T = TypeVar("T")
ACTIVE_RESEARCH_STAGE: ContextVar[str | None] = ContextVar("research_stage", default=None)


class ResearchStages:
    def __init__(
        self,
        journal: EffectJournal,
        *,
        plan: ResearchPlanView,
        question: str,
        cancel_event: threading.Event,
        context_factory: Callable[[str], CallContext] | None = None,
    ) -> None:
        self.journal = journal
        self.plan = plan
        self.request = {"plan": plan.model_dump(mode="json"), "question": question}
        self.cancel_event = cancel_event
        self.deadline: float | None = None
        self.context_factory = context_factory
        self.max_cny: Decimal | float
        self.max_calls: int
        self.max_cny, self.max_calls = (
            journal.approved_limits(plan.task_id) if context_factory else
            (plan.budget_plan.max_cny, plan.budget_plan.max_api_calls)
        )

    async def start(self) -> None:
        async def started() -> dict[str, float]:
            return {"started_at": time.time()}

        context = None if self.context_factory is None else self.context_factory("start")
        record = await self.journal.run(
            task_id=self.plan.task_id,
            key="research:start" if context is None else context.effect_key(),
            request=self.request,
            operation=started,
            max_cny=self.max_cny,
            max_calls=self.max_calls,
            context=context,
            cancel_event=self.cancel_event,
        )
        self.deadline = float(record["started_at"]) + self.plan.budget_plan.max_wall_clock_seconds
        if self.context_factory is not None:
            self.deadline = min(self.deadline, self.journal.approved_deadline(
                self.plan.task_id
            ).timestamp())

    async def run(
        self, stage: str, adapter: TypeAdapter[T], operation: Callable[[], Awaitable[T]]
    ) -> T:
        async def execute() -> dict[str, object]:
            if self.deadline is None:
                raise RuntimeError("research stage session was not started")
            remaining = self.deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("approved research deadline elapsed")
            task = asyncio.ensure_future(operation())
            try:
                while not task.done():
                    if self.cancel_event.is_set():
                        raise EffectCancelledError("research operation interrupted by cancellation")
                    if time.time() >= self.deadline:
                        raise TimeoutError("approved research deadline elapsed")
                    await asyncio.wait(
                        {task}, timeout=min(0.05, max(0, self.deadline - time.time()))
                    )
                return {"value": adapter.dump_python(task.result(), mode="json")}
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        context = None if self.context_factory is None else self.context_factory(stage)
        token = ACTIVE_RESEARCH_STAGE.set(stage)
        try:
            value = await self.journal.run(
                task_id=self.plan.task_id,
                key=f"research:{stage}" if context is None else context.effect_key(),
                request=self.request,
                operation=execute,
                max_cny=self.max_cny,
                max_calls=self.max_calls,
                cancel_event=self.cancel_event,
                context=context,
            )
        finally:
            ACTIVE_RESEARCH_STAGE.reset(token)
        return adapter.validate_python(value["value"])
