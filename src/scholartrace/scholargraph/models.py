"""Strict ScholarGraph 1.2.0 Consumer contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictInt,
    StringConstraints,
    model_validator,
)

SCHEMA_VERSION = "1.0.0"
SERVICE_VERSION = "1.2.0"
GRAPHRAG_VERSION = "3.1.2"
CORPUS_ID = "openalex-rag-abstracts-2020-2025-v1"
CORPUS_VERSION = "formal-2026-08-26"
CORPUS_MANIFEST_SHA256 = (
    "168671c6f9f68ed2c8017e8d3d14396a455559a6a55628a7b0c40d32a80ea36c"
)

StrictText = Annotated[str, StringConstraints(strict=True)]
QuestionText = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=2000,
    ),
]


class QueryMethod(StrEnum):
    BASIC = "basic"
    LOCAL = "local"
    GLOBAL = "global"
    DRIFT = "drift"


class QueryPurpose(StrEnum):
    GENERAL = "general"
    ENTITY_NEIGHBORHOOD = "entity_neighborhood"
    EXPERIMENT = "experiment"
    OFFLINE_EVALUATION = "offline_evaluation"
    LONG_TASK_APPROVED = "long_task_approved"


class EvidenceLevel(StrEnum):
    ABSTRACT = "abstract"
    FULL_TEXT = "full_text"


class QueryStatus(StrEnum):
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    TIMEOUT = "timeout"
    FAILED = "failed"


class ErrorStatus(StrEnum):
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    CORPUS_NOT_FOUND = "corpus_not_found"
    EVIDENCE_LEVEL_UNSUPPORTED = "evidence_level_unsupported"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    METHOD_PURPOSE_MISMATCH = "method_purpose_mismatch"
    TIMEOUT_OUT_OF_RANGE = "timeout_out_of_range"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    SERVICE_NOT_READY = "service_not_ready"
    ROUTE_NOT_FOUND = "route_not_found"
    INTERNAL_ERROR = "internal_error"


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionedResponse(PublicModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    service_version: Literal["1.2.0"] = "1.2.0"
    request_id: StrictText = Field(min_length=1, max_length=128)


class LiveResponse(VersionedResponse):
    status: Literal["live"] = "live"


class ReadinessChecks(PublicModel):
    workspace_config: bool
    index_files: bool
    index_identity: bool
    container_runtime: bool


class ReadyResponse(VersionedResponse):
    status: Literal["ready", "not_ready"]
    checks: ReadinessChecks

    @property
    def ready(self) -> bool:
        return self.status == "ready" and all(
            self.checks.model_dump(mode="python").values()
        )


class YearRange(PublicModel):
    start: StrictInt
    end: StrictInt


class CorpusCapability(PublicModel):
    corpus_id: Literal["openalex-rag-abstracts-2020-2025-v1"] = (
        "openalex-rag-abstracts-2020-2025-v1"
    )
    topic: Literal["Retrieval-Augmented Generation"] = (
        "Retrieval-Augmented Generation"
    )
    language: Literal["en"] = "en"
    publication_years: YearRange = YearRange(start=2020, end=2025)
    document_count: Literal[198] = 198
    corpus_version: Literal["formal-2026-08-26"] = "formal-2026-08-26"
    corpus_manifest_sha256: Literal[
        "168671c6f9f68ed2c8017e8d3d14396a455559a6a55628a7b0c40d32a80ea36c"
    ] = "168671c6f9f68ed2c8017e8d3d14396a455559a6a55628a7b0c40d32a80ea36c"
    evidence_level: Literal["abstract"] = "abstract"
    limitations: list[StrictText]


class MethodCapability(PublicModel):
    method: QueryMethod
    default: bool
    online_allowed: bool
    allowed_purposes: list[QueryPurpose]
    default_timeout_seconds: StrictInt
    min_timeout_seconds: StrictInt
    max_timeout_seconds: StrictInt
    max_concurrency: StrictInt
    restriction_reason: StrictText | None = None

    @model_validator(mode="after")
    def validate_limits(self) -> MethodCapability:
        if not (
            1
            <= self.min_timeout_seconds
            <= self.default_timeout_seconds
            <= self.max_timeout_seconds
            <= 3600
        ):
            raise ValueError("ScholarGraph method timeout limits are inconsistent")
        if self.max_concurrency < 1:
            raise ValueError("ScholarGraph method concurrency must be positive")
        return self


class ServiceLimits(PublicModel):
    max_question_characters: Literal[2000] = 2000
    max_answer_bytes: StrictInt
    global_max_concurrency: StrictInt
    long_running_max_concurrency: StrictInt
    queue_timeout_seconds: float


class CapabilitiesResponse(VersionedResponse):
    graphrag_version: Literal["3.1.2"] = "3.1.2"
    default_method: Literal["basic"] = "basic"
    corpus: CorpusCapability
    methods: list[MethodCapability]
    limits: ServiceLimits

    @model_validator(mode="after")
    def validate_method_manifest(self) -> CapabilitiesResponse:
        methods = [item.method for item in self.methods]
        if len(methods) != len(set(methods)):
            raise ValueError("ScholarGraph method capabilities must be unique")
        if set(methods) != set(QueryMethod):
            raise ValueError("ScholarGraph method capability set is incomplete")
        defaults = [item for item in self.methods if item.default]
        if len(defaults) != 1 or defaults[0].method != QueryMethod.BASIC:
            raise ValueError("ScholarGraph Basic must be the only default method")
        return self


class QueryRequest(PublicModel):
    corpus_id: StrictText = Field(default=CORPUS_ID, min_length=1, max_length=128)
    question: QuestionText
    method: QueryMethod = QueryMethod.BASIC
    purpose: QueryPurpose = QueryPurpose.GENERAL
    timeout_seconds: StrictInt | None = Field(default=None, ge=1, le=3600)
    required_evidence_level: EvidenceLevel = EvidenceLevel.ABSTRACT


class SourceRef(PublicModel):
    document_id: StrictText = Field(min_length=1, max_length=256)
    openalex_id: StrictText | None = Field(default=None, max_length=64)
    title: StrictText | None = Field(default=None, max_length=1000)


class SafeDiagnostics(PublicModel):
    public_message: StrictText
    exit_code: StrictInt | None
    stderr_present: bool
    internal_error_events: StrictInt = Field(ge=0)
    warning_events: StrictInt = Field(ge=0)
    container_cleanup_succeeded: bool | None
    response_truncated: bool


class QueryResponse(VersionedResponse):
    graphrag_version: Literal["3.1.2"] = "3.1.2"
    corpus_id: Literal["openalex-rag-abstracts-2020-2025-v1"] = (
        "openalex-rag-abstracts-2020-2025-v1"
    )
    method: QueryMethod
    purpose: QueryPurpose
    status: QueryStatus
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    evidence_level: Literal["abstract"] = "abstract"
    answer: StrictText
    source_refs: list[SourceRef]
    diagnostics: SafeDiagnostics

    @model_validator(mode="after")
    def reject_unverified_source_refs(self) -> QueryResponse:
        if self.source_refs:
            raise ValueError("M5 does not accept unverified ScholarGraph source refs")
        return self


class MethodMetrics(PublicModel):
    method: QueryMethod
    request_count: StrictInt = Field(ge=0)
    succeeded_count: StrictInt = Field(ge=0)
    degraded_count: StrictInt = Field(ge=0)
    timeout_count: StrictInt = Field(ge=0)
    failed_count: StrictInt = Field(ge=0)
    success_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    degraded_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    latency_p50_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    latency_p95_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    queue_p50_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    queue_p95_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    tokens_visible_lower_bound: StrictInt = Field(ge=0)
    token_coverage: Literal["unavailable", "visible_lower_bound"]


class MetricsResponse(VersionedResponse):
    graphrag_version: Literal["3.1.2"] = "3.1.2"
    sample_window_size: StrictInt = Field(ge=1)
    methods: list[MethodMetrics]

    @model_validator(mode="after")
    def validate_method_metrics(self) -> MetricsResponse:
        methods = [item.method for item in self.methods]
        if len(methods) != len(set(methods)):
            raise ValueError("ScholarGraph method metrics must be unique")
        return self


class ErrorDetail(PublicModel):
    code: ErrorCode
    message: StrictText
    retryable: bool
    field: StrictText | None = None


class ErrorResponse(VersionedResponse):
    status: ErrorStatus
    error: ErrorDetail


class ScholarGraphConsumerResponse(
    RootModel[
        LiveResponse
        | ReadyResponse
        | CapabilitiesResponse
        | QueryResponse
        | MetricsResponse
        | ErrorResponse
    ]
):
    """Union exported as the checked-in ScholarGraph response contract."""


SCHOLARGRAPH_CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        QueryRequest,
        LiveResponse,
        ReadyResponse,
        CapabilitiesResponse,
        QueryResponse,
        MetricsResponse,
        ErrorResponse,
        ScholarGraphConsumerResponse,
    )
}
