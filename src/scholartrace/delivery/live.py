"""Explicit production composition root for the research delivery route.

This module intentionally has no environment discovery and no network side
effects at import time.  Callers must provide factories that already bind the
approved journal, policy and transport identity to every adapter.  A fixture
pipeline is rejected rather than being promoted by a boolean switch.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Literal, Protocol

from scholartrace.contracts import ResearchPlan
from scholartrace.delivery.authorization import RuntimePolicy
from scholartrace.delivery.effects import EffectJournal
from scholartrace.delivery.models import ResearchPlanView
from scholartrace.delivery.research import ResearchPipeline, ResearchResult
from scholartrace.workflow.models import PersistedEvent
from scholartrace.workflow.storage import RuntimeLedger


class PlanFactory(Protocol):
    def __call__(
        self, *, task_id: str, question: str, journal: EffectJournal,
        ledger: RuntimeLedger, cancel_event: threading.Event,
    ) -> object: ...


class PipelineFactory(Protocol):
    def __call__(
        self, *, journal: EffectJournal, ledger: RuntimeLedger,
        cancel_event: threading.Event, on_event: Callable[[PersistedEvent], None],
    ) -> ResearchPipeline: ...


class LiveComposition:
    """A service-compatible, explicitly assembled live runner.

    The factories are the security boundary: they must construct the model,
    academic, PDF and DocuMind clients with the approved metered transports.
    This root only accepts a live ResearchPipeline and never substitutes the
    offline fixture route.
    """

    def __init__(
        self, *, policy: RuntimePolicy, year_from: int,
        plan_factory: PlanFactory, pipeline_factory: PipelineFactory,
        evidence_kind: Literal["synthetic", "live"] = "live",
    ) -> None:
        if year_from < 1900:
            raise ValueError("live composition year_from is outside the supported range")
        self.policy = policy
        self.evidence_kind = evidence_kind
        self.year_from = year_from
        self._plan_factory = plan_factory
        self._pipeline_factory = pipeline_factory

    def generate_plan(
        self, *, task_id: str, question: str, journal: EffectJournal,
        ledger: RuntimeLedger, cancel_event: threading.Event,
    ) -> ResearchPlan:
        generated = self._plan_factory(
            task_id=task_id, question=question, journal=journal,
            ledger=ledger, cancel_event=cancel_event,
        )
        if not isinstance(generated, ResearchPlan):
            raise TypeError("live plan factory must return ResearchPlan")
        if generated.task_id != task_id:
            raise ValueError("live plan factory returned a plan for another task")
        return generated

    def run(
        self, *, question: str, plan: ResearchPlanView, journal: EffectJournal,
        ledger: RuntimeLedger, cancel_event: threading.Event,
        on_event: Callable[[PersistedEvent], None],
    ) -> ResearchResult:
        pipeline = self._pipeline_factory(
            journal=journal, ledger=ledger, cancel_event=cancel_event, on_event=on_event,
        )
        if pipeline.fixture_mode or pipeline.execution_kind != "live":
            raise RuntimeError("live composition rejected a fixture research pipeline")
        result = asyncio.run(pipeline.run(
            question=question, plan=plan, cancel_event=cancel_event,
        ))
        if result.execution_kind != "live":
            raise RuntimeError("live composition returned a non-live research result")
        return result
