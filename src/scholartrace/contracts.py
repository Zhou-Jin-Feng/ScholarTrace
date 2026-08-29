"""Versioned business contracts shared by ScholarTrace workflow components."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SchemaVersion = Literal["1.0"]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
StableId = Annotated[
    str,
    Field(min_length=3, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$"),
]
NonBlank = Annotated[str, Field(min_length=1, max_length=8000)]


class ContractModel(BaseModel):
    """Reject unknown fields so contract drift fails closed."""

    model_config = ConfigDict(extra="forbid")
    schema_version: SchemaVersion = "1.0"


class ArtifactRef(ContractModel):
    artifact_id: StableId
    artifact_type: Literal[
        "research_plan",
        "paper_card",
        "evidence",
        "claim",
        "verification",
        "citation_graph",
        "tool_run",
        "report",
        "search_snapshot",
        "baseline_report",
    ]
    content_sha256: Sha256
    storage_uri: Annotated[str, Field(min_length=1, max_length=2048)]
    created_at: datetime


class BudgetLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_rounds: int = Field(default=2, ge=1, le=5)
    max_queries: int = Field(default=12, ge=1, le=100)
    max_candidate_papers: int = Field(default=50, ge=1, le=500)
    max_fulltext_papers: int = Field(default=15, ge=1, le=100)
    max_rag_calls_per_paper: int = Field(default=6, ge=1, le=50)
    max_concurrency: int = Field(default=4, ge=1, le=16)
    max_llm_input_tokens: int = Field(default=160_000, ge=1)
    max_llm_output_tokens: int = Field(default=40_000, ge=1)
    max_total_tokens: int | None = Field(default=None, ge=1)
    max_api_calls: int = Field(default=16, ge=0, le=200)
    max_model_calls: int = Field(default=60, ge=0, le=500)
    max_cost_cny: float = Field(default=10.0, ge=0, allow_inf_nan=False)
    max_duration_seconds: int = Field(default=900, ge=30, le=86400)


class BudgetUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: int = Field(default=0, ge=0)
    candidate_papers: int = Field(default=0, ge=0)
    fulltext_papers: int = Field(default=0, ge=0)
    rag_calls: int = Field(default=0, ge=0)
    llm_input_tokens: int = Field(default=0, ge=0)
    llm_output_tokens: int = Field(default=0, ge=0)
    api_calls: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    external_cost_cny: float = Field(default=0, ge=0, allow_inf_nan=False)
    local_gpu_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    elapsed_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)


class Budget(ContractModel):
    limits: BudgetLimits = Field(default_factory=BudgetLimits)
    usage: BudgetUsage = Field(default_factory=BudgetUsage)


class ResearchSubquestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subquestion_id: StableId
    question: NonBlank
    evidence_required: Literal["fulltext", "abstract", "metadata"]
    priority: Literal["critical", "high", "normal"] = "normal"


class ResearchPlan(ContractModel):
    task_id: StableId
    title: Annotated[str, Field(min_length=3, max_length=300)]
    question: NonBlank
    objective: NonBlank
    subquestions: Annotated[list[ResearchSubquestion], Field(min_length=1, max_length=20)]
    inclusion_criteria: Annotated[list[NonBlank], Field(min_length=1, max_length=20)]
    exclusion_criteria: Annotated[list[NonBlank], Field(min_length=1, max_length=20)]
    sources: Annotated[
        list[Literal["arxiv", "openalex", "crossref", "semantic_scholar"]],
        Field(min_length=1),
    ]
    retrieval_cutoff: date
    budget: Budget
    status: Literal["draft", "waiting_approval", "approved", "rejected"]


class PaperSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["arxiv", "openalex", "crossref", "semantic_scholar"]
    source_id: Annotated[str, Field(min_length=1, max_length=512)]
    retrieved_at: datetime
    record_sha256: Sha256


class Paper(ContractModel):
    canonical_paper_id: StableId
    title: Annotated[str, Field(min_length=1, max_length=1000)]
    normalized_title: Annotated[str, Field(min_length=1, max_length=1000)]
    authors: Annotated[list[NonBlank], Field(min_length=1, max_length=200)]
    publication_year: int = Field(ge=1900, le=2100)
    doi: str | None = Field(default=None, max_length=512)
    arxiv_id: str | None = Field(default=None, max_length=64)
    openalex_id: str | None = Field(default=None, max_length=64)
    semantic_scholar_id: str | None = Field(default=None, max_length=128)
    abstract: str | None = Field(default=None, max_length=100_000)
    access_level: Literal["fulltext", "abstract", "metadata"]
    version_of: StableId | None = None
    sources: Annotated[list[PaperSource], Field(min_length=1)]

    @model_validator(mode="after")
    def require_external_identity(self) -> Paper:
        if not any((self.doi, self.arxiv_id, self.openalex_id, self.semantic_scholar_id)):
            raise ValueError("paper requires at least one external identifier")
        return self


class DocuMindBinding(ContractModel):
    canonical_paper_id: StableId
    document_key: Sha256
    index_id: Sha256
    source_sha256: Sha256
    documind_version: Annotated[str, Field(pattern=r"^2\.[1-9][0-9]*\.[0-9]+$")]
    retrieval_schema_version: Literal["1.0"]


class Evidence(ContractModel):
    evidence_id: StableId
    canonical_paper_id: StableId
    quote: NonBlank
    evidence_level: Literal["fulltext", "abstract", "metadata"]
    content_sha256: Sha256
    chunk_content_sha256: Sha256 | None = None
    document_key: Sha256 | None = None
    index_id: Sha256 | None = None
    section: str | None = Field(default=None, max_length=500)
    page_number: int | None = Field(default=None, ge=1)
    chunk_id: Sha256 | None = None
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    source_url: str | None = Field(default=None, max_length=2048)
    source_sha256: Sha256 | None = None
    parser_version: str | None = Field(default=None, max_length=100)
    retrieval_run_id: StableId | None = None

    @model_validator(mode="after")
    def enforce_fulltext_provenance(self) -> Evidence:
        quote_sha256 = hashlib.sha256(self.quote.encode("utf-8")).hexdigest()
        if self.content_sha256 != quote_sha256:
            raise ValueError("evidence content_sha256 must match quote")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end <= self.char_start
        ):
            raise ValueError("char_end must be greater than char_start")
        if self.evidence_level == "fulltext":
            required = (
                self.document_key,
                self.index_id,
                self.chunk_id,
                self.chunk_content_sha256,
                self.source_sha256,
                self.retrieval_run_id,
            )
            if any(value is None for value in required):
                raise ValueError("fulltext evidence requires DocuMind provenance")
        return self


class Claim(ContractModel):
    claim_id: StableId
    text: NonBlank
    claim_type: Literal["fact", "result", "comparison", "causal", "inference"]
    evidence_ids: list[StableId] = Field(default_factory=list, max_length=100)
    counter_evidence_ids: list[StableId] = Field(default_factory=list, max_length=100)
    origin: Literal["author_stated", "cross_paper_synthesis", "system_inferred"]
    importance: Literal["critical", "supporting"] = "supporting"


class Verification(ContractModel):
    verification_id: StableId
    claim_id: StableId
    status: Literal["supported", "partially_supported", "unsupported", "conflicted"]
    checked_evidence_ids: Annotated[list[StableId], Field(min_length=1, max_length=200)]
    reason: NonBlank
    recommended_action: Literal["keep", "weaken", "follow_up", "remove"]
    verifier: Literal["deterministic", "model", "human"]
    verified_at: datetime


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: StableId
    kind: Literal["deterministic", "local", "api"]
    provider: Annotated[str, Field(min_length=1, max_length=100)]
    model_name: Annotated[str, Field(min_length=1, max_length=200)]
    model_version: Annotated[str, Field(min_length=1, max_length=200)]
    model_digest: str | None = Field(default=None, max_length=200)
    quantization: str | None = Field(default=None, max_length=100)
    context_window: int | None = Field(default=None, ge=1)
    enabled: bool


class NodeModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: Literal[
        "query_rewrite",
        "abstract_screening",
        "paper_card",
        "claim_extraction",
        "coordinator",
        "critical_verifier",
        "synthesis",
        "scholargraph",
    ]
    default_profile_id: StableId
    fallback_profile_id: StableId | None = None
    max_attempts: int = Field(default=2, ge=1, le=3)
    fallback_reason_allowlist: list[
        Literal[
            "invalid_structured_output",
            "critical_claim",
            "cross_paper_conflict",
            "partial_support",
        ]
    ] = Field(default_factory=list)


class ModelRoutingPolicy(ContractModel):
    policy_id: StableId
    profiles: Annotated[list[ModelProfile], Field(min_length=1)]
    routes: Annotated[list[NodeModelRoute], Field(min_length=1)]
    paid_routes_enabled: bool = False
    no_automatic_price_tier_upgrade: Literal[True] = True

    @model_validator(mode="after")
    def routes_reference_declared_profiles(self) -> ModelRoutingPolicy:
        profile_ids = {profile.profile_id for profile in self.profiles}
        for route in self.routes:
            if route.default_profile_id not in profile_ids:
                raise ValueError("route references an unknown default profile")
            if route.fallback_profile_id and route.fallback_profile_id not in profile_ids:
                raise ValueError("route references an unknown fallback profile")
        if self.paid_routes_enabled:
            enabled_api = any(
                profile.kind == "api" and profile.enabled for profile in self.profiles
            )
            if not enabled_api:
                raise ValueError("paid routes require an enabled API profile")
        return self


class ModelUsageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: StableId
    profile_id: StableId
    provider: Annotated[str, Field(min_length=1, max_length=100)]
    model_name: Annotated[str, Field(min_length=1, max_length=200)]
    model_version: Annotated[str, Field(min_length=1, max_length=200)]
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    call_count: int = Field(default=1, ge=1)
    retry_count: int = Field(default=0, ge=0)
    structured_repair_count: int = Field(default=0, ge=0)
    cache_hit: bool = False
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    queue_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    local_gpu_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    billed_cost_original: float = Field(default=0, ge=0, allow_inf_nan=False)
    billed_currency: Annotated[str, Field(min_length=3, max_length=3)] = "CNY"
    billed_cost_cny: float = Field(default=0, ge=0, allow_inf_nan=False)


class ServiceBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: Literal["scholartrace", "documind", "scholargraph"]
    version: Annotated[str, Field(min_length=1, max_length=100)]
    git_commit: Annotated[str, Field(pattern=r"^[a-f0-9]{7,40}$")]
    contract_version: Annotated[str, Field(min_length=1, max_length=32)]
    capability_id: str | None = Field(default=None, max_length=200)
    worktree_dirty: bool = False
    source_tree_sha256: Sha256 | None = None


class RunManifest(ContractModel):
    run_id: StableId
    task_id: StableId
    started_at: datetime
    completed_at: datetime | None = None
    retrieval_cutoff: date
    service_baselines: Annotated[list[ServiceBaseline], Field(min_length=1)]
    model_policy_ref: ArtifactRef
    model_usage: list[ModelUsageRecord] = Field(default_factory=list)
    price_snapshot_at: datetime | None = None
    price_snapshot_sha256: Sha256 | None = None
    prompt_set_sha256: Sha256
    evaluation_seed_sha256: Sha256
    data_snapshot_refs: list[ArtifactRef] = Field(default_factory=list)
    budget: Budget
    outcome: Literal["running", "succeeded", "degraded", "failed"]


CONTRACT_MODELS: dict[str, type[ContractModel]] = {
    model.__name__: model
    for model in (
        ArtifactRef,
        Budget,
        ResearchPlan,
        Paper,
        DocuMindBinding,
        Evidence,
        Claim,
        Verification,
        ModelRoutingPolicy,
        RunManifest,
    )
}
