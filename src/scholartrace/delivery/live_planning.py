"""Production Coordinator assembly over the approved durable model transport."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from datetime import date

import httpx

from scholartrace.contracts import BudgetLimits, ResearchPlan
from scholartrace.delivery.authorization import AuthorizationError, CallContext, RuntimePolicy
from scholartrace.delivery.effects import EffectJournal
from scholartrace.delivery.metering import MeteredModelTransport, ModelCallPolicy
from scholartrace.model_provider.plan_generator import (
    ApiCallBudget,
    ApiCallCounter,
    OpenAICompatiblePlanGenerator,
)
from scholartrace.model_provider.settings import ProviderSettings
from scholartrace.workflow.storage import RuntimeLedger


class MeteredPlanFactory:
    """Construct and close one explicitly configured Coordinator HTTP session.

    Transport injection replaces only HTTP in engineering tests. Parsing,
    request construction, authorization, accounting and replay remain real.
    Neither construction nor import discovers credentials or sends requests.
    """

    def __init__(
        self, *, policy: RuntimePolicy, api_key: str,
        plan_limits: BudgetLimits, retrieval_cutoff: date,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("explicit Coordinator credential is required")
        self.policy = policy
        self._api_key = api_key
        self._limits = plan_limits.model_copy(deep=True)
        self._cutoff = retrieval_cutoff
        self._transport_factory = transport_factory or (
            lambda: httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        )

    def __call__(
        self, *, task_id: str, question: str, journal: EffectJournal,
        ledger: RuntimeLedger, cancel_event: threading.Event,
    ) -> ResearchPlan:
        approved = journal.approved_policy(task_id)
        if approved.digest() != self.policy.digest() or "question" not in approved.data_fields:
            raise AuthorizationError("Coordinator differs from approved policy or data scope")
        phase = journal.active_phase(task_id, "planning")
        maximum, calls = journal.approved_limits(task_id)
        if (self._limits.max_cost_cny > maximum or self._limits.max_api_calls > calls
                or self._limits.max_fulltext_papers > approved.max_papers):
            raise AuthorizationError("proposed plan limits exceed the task authorization")
        journal.require_projection_synced(task_id)
        remote = approved.remote
        if remote.protocol == "ollama":
            raise AuthorizationError("Coordinator requires the approved remote model")
        protocol = remote.protocol
        context = CallContext(
            authorization_id=phase.authorization_id,
            operation_id=f"coordinator:g{phase.generation}", call_kind="remote_model",
            policy_sha256=approved.digest(),
        )

        async def generate() -> ResearchPlan:
            transport = MeteredModelTransport(
                inner=self._transport_factory(), journal=journal, task_id=task_id,
                cancel_event=cancel_event, context=context,
                policy=ModelCallPolicy(
                    endpoint=remote.endpoint, model=remote.model,
                    model_version=remote.model_version,
                    input_cny_per_million=float(remote.input_cny_per_million),
                    output_cny_per_million=float(remote.output_cny_per_million),
                    max_input_tokens=remote.max_input_tokens,
                    max_output_tokens=remote.max_output_tokens,
                    max_cny=float(maximum), max_calls=calls,
                ),
            )
            async with httpx.AsyncClient(
                transport=transport, follow_redirects=False, trust_env=False,
            ) as client:
                generator = OpenAICompatiblePlanGenerator(
                    client=client,
                    settings=ProviderSettings(
                        base_url=remote.endpoint.split("/v1/", 1)[0],
                        api_key=self._api_key, model=remote.model, timeout_seconds=120,
                    ),
                    model=remote.model, protocol=protocol,
                    call_counter=ApiCallCounter(ApiCallBudget(
                        max_calls=phase.max_remote_calls,
                        max_input_token_upper_bound=remote.max_input_tokens,
                        max_output_tokens=remote.max_output_tokens,
                        max_cost_cny=float(phase.max_cny),
                        reference_input_usd_per_million=float(remote.input_cny_per_million),
                        reference_output_usd_per_million=float(remote.output_cny_per_million),
                        reference_usd_to_cny=1,
                    )),
                    plan_limits=self._limits.model_copy(deep=True),
                    retrieval_cutoff=self._cutoff,
                )
                return await generator.generate_plan(
                    task_id=task_id, question=question,
                    idempotency_key=context.effect_key(),
                )

        plan = asyncio.run(generate())
        if not set(plan.sources) <= set(approved.allowed_search_providers):
            raise AuthorizationError("Coordinator proposed sources outside the approved scope")
        if any(item.evidence_required != "fulltext" for item in plan.subquestions):
            raise AuthorizationError("research execution requires fulltext subquestions")
        return plan
