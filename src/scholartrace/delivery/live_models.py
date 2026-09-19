"""Execution model adapters with per-business-operation durable accounting."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Literal

import httpx

from scholartrace.contracts import Claim, DocuMindBinding, Evidence, Paper
from scholartrace.delivery.authorization import AuthorizationError, CallContext
from scholartrace.delivery.effects import EffectJournal
from scholartrace.delivery.metering import (
    MeteredLocalTransport,
    MeteredModelTransport,
    ModelCallPolicy,
)
from scholartrace.evidence.analysis import GeneratedPaperAnalysis, OllamaPaperAnalyzer
from scholartrace.evidence.client import RetrievalResult
from scholartrace.model_provider.plan_generator import ApiCallBudget, ApiCallCounter
from scholartrace.model_provider.report_generator import OpenAICompatibleReportGenerator
from scholartrace.model_provider.settings import ProviderSettings
from scholartrace.model_provider.verifier import OpenAICompatibleSemanticVerifier
from scholartrace.scholargraph.experiment import GeneratedReport, ReportGenerationRequest
from scholartrace.verification.models import SemanticVerificationDraft


class ExecutionModels:
    """Bound one approved execution plan to the existing three model adapters.

    Each adapter owns a short-lived HTTP session. The journal, not the auxiliary
    provider counter, owns task-wide usage and uncertain side effects.
    """

    def __init__(
        self,
        *,
        journal: EffectJournal,
        task_id: str,
        plan_version: int,
        plan_digest: str,
        api_key: str,
        cancel_event: threading.Event,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        self.journal, self.task_id = journal, task_id
        self.plan_version, self.plan_digest = plan_version, plan_digest
        self._api_key, self.cancel_event = api_key, cancel_event
        self._transport_factory = transport_factory or (
            lambda: httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        )
        if not api_key.strip():
            raise ValueError("explicit execution model credential is required")

    def context(self, operation: str, *, local: bool = False) -> CallContext:
        phase = self.journal.active_phase(self.task_id, "execution")
        if phase.plan_version != self.plan_version or phase.plan_digest != self.plan_digest:
            raise AuthorizationError("model operation differs from the approved execution plan")
        return CallContext(
            authorization_id=phase.authorization_id,
            operation_id=operation,
            call_kind="local_model" if local else "remote_model",
            policy_sha256=phase.policy_sha256,
            plan_version=self.plan_version,
            plan_digest=self.plan_digest,
        )

    @asynccontextmanager
    async def remote(
        self, operation: str, fields: set[str]
    ) -> AsyncIterator[tuple[httpx.AsyncClient, ProviderSettings, ApiCallCounter]]:
        context = self.context(operation)
        policy = self.journal.approved_policy(self.task_id)
        if not fields <= set(policy.data_fields):
            raise AuthorizationError("execution model data fields were not approved")
        remote = policy.remote
        maximum, calls = self.journal.approved_limits(self.task_id)
        transport = MeteredModelTransport(
            inner=self._transport_factory(),
            journal=self.journal,
            task_id=self.task_id,
            context=context,
            cancel_event=self.cancel_event,
            policy=ModelCallPolicy(
                endpoint=remote.endpoint,
                model=remote.model,
                model_version=remote.model_version,
                input_cny_per_million=float(remote.input_cny_per_million),
                output_cny_per_million=float(remote.output_cny_per_million),
                max_input_tokens=remote.max_input_tokens,
                max_output_tokens=remote.max_output_tokens,
                max_cny=float(maximum),
                max_calls=calls,
            ),
        )
        async with httpx.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False
        ) as client:
            yield (
                client,
                ProviderSettings(
                    base_url=remote.endpoint.split("/v1/", 1)[0],
                    api_key=self._api_key,
                    model=remote.model,
                    timeout_seconds=180,
                ),
                ApiCallCounter(
                    ApiCallBudget(
                        max_calls=calls,
                        max_input_token_upper_bound=remote.max_input_tokens,
                        max_output_tokens=remote.max_output_tokens,
                        max_cost_cny=float(maximum),
                        reference_input_usd_per_million=float(remote.input_cny_per_million),
                        reference_output_usd_per_million=float(remote.output_cny_per_million),
                        reference_usd_to_cny=1,
                    )
                ),
            )

    async def analyze(
        self, *, paper: Paper, binding: DocuMindBinding, retrieval: RetrievalResult, question: str
    ) -> GeneratedPaperAnalysis:
        context = self.context(
            "analyze:"
            + hashlib.sha256((paper.canonical_paper_id + "\0" + question).encode()).hexdigest(),
            local=True,
        )
        policy = self.journal.approved_policy(self.task_id)
        local = policy.local
        if local is None:
            raise AuthorizationError("local analysis model is not configured")
        if local.max_output_tokens < 2048:
            raise AuthorizationError("approved local output limit cannot cover paper analysis")
        transport = MeteredLocalTransport(
            inner=self._transport_factory(),
            journal=self.journal,
            task_id=self.task_id,
            context=context,
            model_version=local.model_version,
            cancel_event=self.cancel_event,
        )
        async with httpx.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False
        ) as client:
            return await OllamaPaperAnalyzer(
                client=client,
                base_url=local.endpoint.removesuffix("/api/chat"),
                model=local.model,
                model_version=local.model_version,
                max_attempts=1,
            ).analyze(paper=paper, binding=binding, retrieval=retrieval, question=question)


class ExecutionVerifier:
    verifier_kind: Literal["model", "human", "fixture"] = "model"
    profile_id = "api-strong"

    def __init__(self, models: ExecutionModels) -> None:
        self.models = models

    async def verify(self, *, claim: Claim, evidence: list[Evidence]) -> SemanticVerificationDraft:
        operation = "verify:" + hashlib.sha256(claim.claim_id.encode()).hexdigest()
        async with self.models.remote(operation, {"claims", "evidence_quotes"}) as (
            client,
            settings,
            counter,
        ):
            remote = self.models.journal.approved_policy(self.models.task_id).remote
            if remote.protocol == "ollama":
                raise AuthorizationError("critical verification requires remote model")
            return await OpenAICompatibleSemanticVerifier(
                client=client,
                settings=settings,
                profile_id=self.profile_id,
                model=remote.model,
                protocol=remote.protocol,
                call_counter=counter,
            ).verify(claim=claim, evidence=evidence)


class ExecutionSynthesis:
    model_profile = "api-strong"
    prompt_template_sha256 = OpenAICompatibleReportGenerator.prompt_template_sha256

    def __init__(self, models: ExecutionModels) -> None:
        self.models = models
        remote = models.journal.approved_policy(models.task_id).remote
        self.model_identifier = remote.model
        if remote.protocol == "ollama":
            raise AuthorizationError("synthesis requires remote model")
        self.provider_protocol: Literal[
            "responses", "chat_completions", "fixture"
        ] = remote.protocol

    async def generate(self, request: ReportGenerationRequest) -> GeneratedReport:
        if request.question_id != self.models.task_id:
            raise AuthorizationError("synthesis request belongs to another task")
        async with self.models.remote(
            "synthesis",
            {
                "question",
                "claims",
                "evidence_quotes",
                "verification_results",
            },
        ) as (client, settings, counter):
            remote = self.models.journal.approved_policy(self.models.task_id).remote
            if remote.protocol == "ollama":
                raise AuthorizationError("synthesis requires remote model")
            return await OpenAICompatibleReportGenerator(
                client=client,
                settings=settings,
                model_profile=self.model_profile,
                model=remote.model,
                protocol=remote.protocol,
                call_counter=counter,
            ).generate(request)
