"""Production research assembly with explicit configuration and owned sessions."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import date
from pathlib import Path
from typing import Literal

import httpx

from scholartrace.contracts import BudgetLimits, ModelProfile, ModelRoutingPolicy, NodeModelRoute
from scholartrace.delivery.authorization import AuthorizationError, CallContext, RuntimePolicy
from scholartrace.delivery.effects import EffectJournal
from scholartrace.delivery.live_http import ResearchHttpTransport
from scholartrace.delivery.live_models import ExecutionModels, ExecutionSynthesis, ExecutionVerifier
from scholartrace.delivery.live_planning import MeteredPlanFactory
from scholartrace.delivery.models import ResearchPlanView
from scholartrace.delivery.research import ResearchPipeline, ResearchResult
from scholartrace.evidence.bindings import DocuMindBindingRepository
from scholartrace.evidence.client import DocuMindClient
from scholartrace.evidence.pipeline import M2EvidencePipeline
from scholartrace.search.cache import MemoryResponseCache
from scholartrace.search.http import AcademicHttpClient
from scholartrace.search.models import SourcePolicy
from scholartrace.search.pipeline import SearchPipeline
from scholartrace.search.providers import (
    AcademicSource,
    ArxivSource,
    CrossrefSource,
    OpenAlexSource,
    SemanticScholarSource,
)
from scholartrace.verification.pipeline import M4ReliabilityPipeline
from scholartrace.verification.verifier import VerifierRunner
from scholartrace.workflow.models import PersistedEvent
from scholartrace.workflow.storage import ArtifactStore, RuntimeLedger


class ProductionResearchRunner:
    """Assemble existing adapters, with HTTP injection solely for engineering validation."""

    def __init__(
        self,
        *,
        policy: RuntimePolicy,
        api_key: str,
        data_dir: Path,
        year_from: int,
        retrieval_cutoff: date,
        plan_limits: BudgetLimits,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        self.policy, self.year_from = policy, year_from
        self.evidence_kind: Literal["synthetic", "live"] = (
            "synthetic" if transport_factory is not None else "live"
        )
        if not 1900 <= year_from <= retrieval_cutoff.year:
            raise ValueError("research year range is invalid")
        if policy.local is None or policy.documind_url is None:
            raise ValueError("research requires local analysis and DocuMind configuration")
        self._api_key, self._data_dir = api_key, data_dir
        self._transport_factory = transport_factory or (
            lambda: httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        )
        self.generate_plan = MeteredPlanFactory(
            policy=policy,
            api_key=api_key,
            plan_limits=plan_limits,
            retrieval_cutoff=retrieval_cutoff,
            transport_factory=transport_factory,
        )

    def run(
        self,
        *,
        question: str,
        plan: ResearchPlanView,
        journal: EffectJournal,
        ledger: RuntimeLedger,
        cancel_event: threading.Event,
        on_event: Callable[[PersistedEvent], None],
    ) -> ResearchResult:
        if (
            journal.approved_policy(plan.task_id).digest() != self.policy.digest()
            or plan.details is None
            or plan.generated_by == "fixture"
        ):
            raise AuthorizationError("production research requires the detailed approved plan")
        phase = journal.active_phase(plan.task_id, "execution")
        if phase.plan_version != plan.plan_version or phase.plan_digest != plan.plan_digest:
            raise AuthorizationError("execution authorization does not match the reviewed plan")
        if plan.approval_state != "approved":
            raise AuthorizationError("research plan is not approved")
        return asyncio.run(self._run(question, plan, journal, ledger, cancel_event, on_event))

    async def _run(
        self,
        question: str,
        plan: ResearchPlanView,
        journal: EffectJournal,
        ledger: RuntimeLedger,
        cancel_event: threading.Event,
        on_event: Callable[[PersistedEvent], None],
    ) -> ResearchResult:
        task_dir = self._data_dir / hashlib.sha256(plan.task_id.encode()).hexdigest()
        task_dir.mkdir(parents=True, exist_ok=True)
        artifacts = ArtifactStore(task_dir / "artifacts.sqlite")
        try:
            async with AsyncExitStack() as stack:

                async def client(service: str) -> httpx.AsyncClient:
                    return await stack.enter_async_context(
                        httpx.AsyncClient(
                            transport=ResearchHttpTransport(
                                inner=self._transport_factory(),
                                journal=journal,
                                task_id=plan.task_id,
                                plan_version=plan.plan_version,
                                plan_digest=plan.plan_digest,
                                service=service,
                                cancel_event=cancel_event,
                            ),
                            trust_env=False,
                            follow_redirects=False,
                            timeout=180,
                        )
                    )

                sources: list[AcademicSource] = []
                constructors = {
                    "arxiv": ArxivSource,
                    "openalex": OpenAlexSource,
                    "crossref": CrossrefSource,
                    "semantic_scholar": SemanticScholarSource,
                }
                for provider in plan.source_scope.providers:
                    if provider not in self.policy.allowed_search_providers:
                        raise AuthorizationError("search provider is outside approved policy")
                    http = AcademicHttpClient(
                        source=provider,
                        client=await client(provider),
                        cache=MemoryResponseCache(),
                        policy=SourcePolicy(max_attempts=1, max_network_requests=1),
                    )
                    sources.append(constructors[provider](http))
                acquisition, documind = await client("arxiv_pdf"), await client("documind")
                models = ExecutionModels(
                    journal=journal,
                    task_id=plan.task_id,
                    plan_version=plan.plan_version,
                    plan_digest=plan.plan_digest,
                    api_key=self._api_key,
                    cancel_event=cancel_event,
                    transport_factory=self._transport_factory,
                )
                remote = self.policy.remote
                routing = ModelRoutingPolicy(
                    policy_id="delivery:" + self.policy.digest(),
                    paid_routes_enabled=True,
                    profiles=[
                        ModelProfile(
                            profile_id="api-strong",
                            kind="api",
                            provider="openai-compatible",
                            model_name=remote.model,
                            model_version=remote.model_version,
                            enabled=True,
                        )
                    ],
                    routes=[
                        NodeModelRoute(
                            node="critical_verifier",
                            default_profile_id="api-strong",
                            max_attempts=1,
                        )
                    ],
                )

                def context(stage: str) -> CallContext:
                    phase = journal.active_phase(plan.task_id, "execution")
                    return CallContext(
                        authorization_id=phase.authorization_id,
                        operation_id="stage:" + stage,
                        call_kind="stage",
                        policy_sha256=self.policy.digest(),
                        plan_version=plan.plan_version,
                        plan_digest=plan.plan_digest,
                    )

                assert self.policy.documind_url is not None
                pipeline = ResearchPipeline(
                    search=SearchPipeline(sources),
                    evidence=M2EvidencePipeline(
                        client=DocuMindClient(
                            client=documind, base_url=self.policy.documind_url, max_attempts=1
                        ),
                        bindings=DocuMindBindingRepository(task_dir / "bindings.sqlite"),
                        analyzer=models,
                        max_concurrency=1,
                    ),
                    reliability=M4ReliabilityPipeline(
                        verifier=VerifierRunner(
                            backend=ExecutionVerifier(models),
                            policy=routing,
                        )
                    ),
                    synthesis=ExecutionSynthesis(models),
                    acquisition_client=acquisition,
                    documind_client=documind,
                    documind_url=self.policy.documind_url,
                    download_dir=task_dir / "pdfs",
                    artifacts=artifacts,
                    ledger=ledger,
                    journal=journal,
                    context_factory=context,
                    execution_kind="live",
                    on_event=on_event,
                )
                return await pipeline.run(question=question, plan=plan, cancel_event=cancel_event)
        finally:
            artifacts.close()
