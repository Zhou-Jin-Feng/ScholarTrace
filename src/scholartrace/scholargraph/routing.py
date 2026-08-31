"""Deterministic capability routing for the fixed ScholarGraph corpus."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scholartrace.contracts import Budget
from scholartrace.scholargraph.client import (
    ScholarGraphClient,
    ScholarGraphClientError,
    ScholarGraphProtocolError,
)
from scholartrace.scholargraph.models import (
    CapabilitiesResponse,
    EvidenceLevel,
    QueryMethod,
    QueryPurpose,
    QueryRequest,
    QueryStatus,
)

RoutingReason = Literal[
    "eligible",
    "topic_out_of_scope",
    "year_out_of_scope",
    "full_text_required",
    "verified_full_text_required",
    "index_write_not_supported",
    "api_budget_exhausted",
    "wall_time_budget_exhausted",
    "method_not_declared",
    "method_disabled",
    "method_purpose_mismatch",
    "long_method_not_approved",
]
ToolErrorCode = Literal[
    "provider_error",
    "protocol_error",
    "query_failed",
    "query_timeout",
]


class RoutingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScholarGraphScope(RoutingModel):
    topic: Literal["retrieval_augmented_generation", "other"]
    publication_year_from: int | None = Field(default=None, ge=1900, le=2100)
    publication_year_to: int | None = Field(default=None, ge=1900, le=2100)
    required_evidence_level: EvidenceLevel = EvidenceLevel.ABSTRACT
    requires_verified_full_text: bool = False
    requires_index_write: bool = False
    purpose: QueryPurpose = QueryPurpose.GENERAL
    requested_method: QueryMethod | None = None

    def model_post_init(self, __context: object) -> None:
        del __context
        if (
            self.publication_year_from is not None
            and self.publication_year_to is not None
            and self.publication_year_to < self.publication_year_from
        ):
            raise ValueError("publication year range is reversed")


class CapabilityDecision(RoutingModel):
    action: Literal["call", "skip", "reject"]
    eligible: bool
    reason_code: RoutingReason
    public_reason: str = Field(min_length=1, max_length=300)
    corpus_id: Literal["openalex-rag-abstracts-2020-2025-v1"] = (
        "openalex-rag-abstracts-2020-2025-v1"
    )
    method: QueryMethod | None = None
    purpose: QueryPurpose
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)


class ScholarGraphToolResult(RoutingModel):
    decision: CapabilityDecision
    query_status: QueryStatus | None = None
    answer: str | None = Field(default=None, max_length=1_000_000)
    abstract_only: Literal[True] = True
    used_for_fulltext_evidence: Literal[False] = False
    fallback_to_b3: bool
    attempts: int = Field(default=0, ge=0, le=3)
    duration_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    provider_duration_seconds: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    error_code: ToolErrorCode | None = None


class CapabilityRouter:
    def __init__(self, *, allow_long_methods: bool = False) -> None:
        self.allow_long_methods = allow_long_methods

    def decide(
        self,
        *,
        scope: ScholarGraphScope,
        capabilities: CapabilitiesResponse,
        budget: Budget,
    ) -> CapabilityDecision:
        if scope.requires_index_write:
            return self._blocked(scope, "reject", "index_write_not_supported")
        if scope.requires_verified_full_text:
            return self._blocked(scope, "skip", "verified_full_text_required")
        if scope.required_evidence_level != EvidenceLevel.ABSTRACT:
            return self._blocked(scope, "skip", "full_text_required")
        if scope.topic != "retrieval_augmented_generation":
            return self._blocked(scope, "skip", "topic_out_of_scope")
        year_range = capabilities.corpus.publication_years
        if (
            scope.publication_year_from is not None
            and not year_range.start <= scope.publication_year_from <= year_range.end
            or scope.publication_year_to is not None
            and not year_range.start <= scope.publication_year_to <= year_range.end
        ):
            return self._blocked(scope, "skip", "year_out_of_scope")
        if budget.usage.api_calls >= budget.limits.max_api_calls:
            return self._blocked(scope, "skip", "api_budget_exhausted")

        method = scope.requested_method or self._default_method(scope.purpose)
        if method in {QueryMethod.GLOBAL, QueryMethod.DRIFT} and not self.allow_long_methods:
            return self._blocked(scope, "skip", "long_method_not_approved")
        method_capability = next(
            (item for item in capabilities.methods if item.method == method),
            None,
        )
        if method_capability is None:
            return self._blocked(scope, "skip", "method_not_declared")
        if not method_capability.online_allowed:
            return self._blocked(scope, "skip", "method_disabled")
        if scope.purpose not in method_capability.allowed_purposes:
            return self._blocked(scope, "skip", "method_purpose_mismatch")

        remaining_seconds = int(
            budget.limits.max_duration_seconds - budget.usage.elapsed_seconds
        )
        if remaining_seconds < method_capability.min_timeout_seconds:
            return self._blocked(scope, "skip", "wall_time_budget_exhausted")
        timeout_seconds = min(
            method_capability.default_timeout_seconds,
            method_capability.max_timeout_seconds,
            remaining_seconds,
        )
        return CapabilityDecision(
            action="call",
            eligible=True,
            reason_code="eligible",
            public_reason="The request is inside the fixed ScholarGraph capability boundary.",
            method=method,
            purpose=scope.purpose,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _default_method(purpose: QueryPurpose) -> QueryMethod:
        if purpose == QueryPurpose.ENTITY_NEIGHBORHOOD:
            return QueryMethod.LOCAL
        return QueryMethod.BASIC

    @staticmethod
    def _blocked(
        scope: ScholarGraphScope,
        action: Literal["skip", "reject"],
        reason: RoutingReason,
    ) -> CapabilityDecision:
        public_reasons = {
            "topic_out_of_scope": "The request is outside the fixed RAG topic.",
            "year_out_of_scope": "The request is outside the 2020-2025 corpus years.",
            "full_text_required": "ScholarGraph provides abstract evidence only.",
            "verified_full_text_required": "Verified full-text evidence requires DocuMind.",
            "index_write_not_supported": "ScholarGraph is a read-only service.",
            "api_budget_exhausted": "The task API-call budget is exhausted.",
            "wall_time_budget_exhausted": "The remaining wall-time budget is insufficient.",
            "method_not_declared": "The requested method is not declared by the service.",
            "method_disabled": "The requested method is disabled by the service policy.",
            "method_purpose_mismatch": "The requested method is not allowed for this purpose.",
            "long_method_not_approved": "Long ScholarGraph methods require a separate approval.",
            "eligible": "The request is eligible.",
        }
        return CapabilityDecision(
            action=action,
            eligible=False,
            reason_code=reason,
            public_reason=public_reasons[reason],
            purpose=scope.purpose,
        )


class ScholarGraphTool:
    def __init__(
        self,
        *,
        client: ScholarGraphClient,
        capabilities: CapabilitiesResponse,
        router: CapabilityRouter | None = None,
    ) -> None:
        self.client = client
        self.capabilities = capabilities
        self.router = router or CapabilityRouter()

    async def execute(
        self,
        *,
        question: str,
        scope: ScholarGraphScope,
        budget: Budget,
        traceparent: str | None = None,
    ) -> ScholarGraphToolResult:
        decision = self.router.decide(
            scope=scope,
            capabilities=self.capabilities,
            budget=budget,
        )
        if decision.action != "call":
            return ScholarGraphToolResult(
                decision=decision,
                fallback_to_b3=True,
            )
        if decision.method is None or decision.timeout_seconds is None:
            raise RuntimeError("eligible ScholarGraph decision is incomplete")
        request = QueryRequest(
            question=question,
            method=decision.method,
            purpose=decision.purpose,
            timeout_seconds=decision.timeout_seconds,
        )
        try:
            result = await self.client.query(request, traceparent=traceparent)
        except ScholarGraphProtocolError:
            return ScholarGraphToolResult(
                decision=decision,
                fallback_to_b3=True,
                error_code="protocol_error",
            )
        except ScholarGraphClientError as exc:
            return ScholarGraphToolResult(
                decision=decision,
                fallback_to_b3=True,
                attempts=exc.attempts,
                error_code="provider_error",
            )
        response = result.response
        failed = response.status in {QueryStatus.FAILED, QueryStatus.TIMEOUT}
        error_code: ToolErrorCode | None = None
        if response.status == QueryStatus.FAILED:
            error_code = "query_failed"
        elif response.status == QueryStatus.TIMEOUT:
            error_code = "query_timeout"
        return ScholarGraphToolResult(
            decision=decision,
            query_status=response.status,
            answer=None if failed else response.answer,
            fallback_to_b3=failed,
            attempts=result.attempts,
            duration_seconds=result.duration_seconds,
            provider_duration_seconds=response.duration_seconds,
            error_code=error_code,
        )
