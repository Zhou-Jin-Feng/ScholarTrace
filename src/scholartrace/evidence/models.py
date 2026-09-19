"""Strict M2 Consumer contracts for DocuMind retrieval and paper analysis."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from scholartrace.contracts import Claim, Evidence, NonBlank, Sha256, StableId
from scholartrace.documind_compatibility import DOCUMIND_VERSION_PATTERN, supports_documind_retrieve

DocuMindErrorCode = Literal[
    "document_not_found",
    "stale_document_index",
    "document_operation_in_progress",
    "document_index_unavailable",
    "request_too_large",
    "validation_error",
    "retrieval_service_unavailable",
    "service_unavailable",
    "retrieval_capacity_exceeded",
    "retrieval_timeout",
]
ChunkRef = Literal["chunk-1", "chunk-2", "chunk-3", "chunk-4", "chunk-5", "chunk-6"]
QuoteRef = Literal["quote-1", "quote-2", "quote-3", "quote-4", "quote-5", "quote-6"]
DOCUMIND_ERROR_CODES = frozenset(
    {
        "document_not_found",
        "stale_document_index",
        "document_operation_in_progress",
        "document_index_unavailable",
        "request_too_large",
        "validation_error",
        "retrieval_service_unavailable",
        "service_unavailable",
        "retrieval_capacity_exceeded",
        "retrieval_timeout",
    }
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocuMindRetrieveRequest(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    query: Annotated[str, Field(min_length=1, max_length=4000)]
    document_key: Sha256
    expected_index_id: Sha256
    top_k: int = Field(default=5, ge=1, le=20)
    retrieval_mode: Literal["dense"] = "dense"
    distance_threshold: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized


class RetrievalChunk(StrictModel):
    chunk_id: Sha256
    content: Annotated[str, Field(min_length=1, max_length=200_000)]
    content_sha256: Sha256
    source: Annotated[str, Field(min_length=1, max_length=2048)]
    page_number: int | None = Field(default=None, ge=1)
    distance: float = Field(ge=0, allow_inf_nan=False)
    rank: int = Field(ge=1, le=20)

    @model_validator(mode="after")
    def verify_content_hash(self) -> RetrievalChunk:
        actual = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_sha256 != actual:
            raise ValueError("retrieval chunk content hash mismatch")
        return self


class DocuMindRetrieveResponse(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    service_version: Annotated[str, Field(pattern=DOCUMIND_VERSION_PATTERN)]
    retrieval_version: Literal["dense-v1"]
    retrieval_mode: Literal["dense"]
    document_key: Sha256
    index_id: Sha256
    source_sha256: Sha256
    chunks: list[RetrievalChunk] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def verify_chunk_order(self) -> DocuMindRetrieveResponse:
        chunk_ids = [chunk.chunk_id for chunk in self.chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("retrieval chunk IDs must be unique")
        if [chunk.rank for chunk in self.chunks] != list(range(1, len(self.chunks) + 1)):
            raise ValueError("retrieval chunk ranks must be contiguous and ordered")
        return self


class DocuMindErrorDetail(StrictModel):
    code: DocuMindErrorCode
    message: Annotated[str, Field(min_length=1, max_length=1000)]
    request_id: str | None = Field(default=None, max_length=128)
    fields: list[dict[str, str]] | None = None


class DocuMindErrorEnvelope(StrictModel):
    error: DocuMindErrorDetail


class DocuMindReadiness(StrictModel):
    status: Annotated[str, Field(min_length=1, max_length=100)]
    version: Annotated[str, Field(min_length=1, max_length=100)]
    ready: bool
    components: dict[str, str] | None = None
    dependencies: dict[str, bool] | None = None
    error_type: str | None = Field(default=None, max_length=200)


def retrieval_is_ready(readiness: DocuMindReadiness, http_status: int) -> bool:
    if not supports_documind_retrieve(readiness.version) or http_status not in (200, 503):
        return False
    if readiness.components is not None:
        return readiness.components.get("retrieval") == "ready"
    # 3.0.0 readiness always exposes components; never infer it from liveness.
    return readiness.version != "3.0.0" and http_status == 200 and readiness.ready


class PaperCard(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    paper_card_id: StableId
    canonical_paper_id: StableId
    title: Annotated[str, Field(min_length=1, max_length=1000)]
    authors: Annotated[list[NonBlank], Field(min_length=1, max_length=200)]
    publication_year: int = Field(ge=1900, le=2100)
    question: Annotated[str, Field(min_length=2, max_length=4000)]
    summary: NonBlank
    contributions: Annotated[list[NonBlank], Field(min_length=1, max_length=20)]
    limitations: Annotated[list[NonBlank], Field(min_length=1, max_length=20)]
    claim_ids: Annotated[list[StableId], Field(min_length=1, max_length=100)]
    evidence_ids: Annotated[list[StableId], Field(min_length=1, max_length=100)]
    generator: Literal["ollama_qwen3_8b", "fixture"]
    model_profile_id: StableId | None = None
    input_sha256: Sha256
    generated_at: datetime


class RetrievalAudit(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    retrieval_run_id: StableId
    canonical_paper_id: StableId
    document_key: Sha256
    index_id: Sha256
    source_sha256: Sha256
    query_sha256: Sha256
    started_at: datetime
    completed_at: datetime
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    attempts: int = Field(ge=0, le=3)
    status: Literal["succeeded", "empty", "failed"]
    service_version: str | None = Field(default=None, max_length=100)
    retrieval_version: Literal["dense-v1"] | None = None
    chunk_count: int = Field(default=0, ge=0, le=20)
    error_code: DocuMindErrorCode | None = None

    @model_validator(mode="after")
    def verify_status(self) -> RetrievalAudit:
        if self.status == "failed" and self.error_code is None:
            raise ValueError("failed retrieval audit requires error_code")
        if self.status != "failed" and self.error_code is not None:
            raise ValueError("successful retrieval audit cannot contain error_code")
        return self


class ClaimDraft(StrictModel):
    text: NonBlank
    claim_type: Literal["fact", "result", "comparison", "causal", "inference"]
    chunk_ref: ChunkRef
    quote_ref: QuoteRef
    importance: Literal["critical", "supporting"] = "supporting"


class PaperAnalysisDraft(StrictModel):
    summary: NonBlank
    contributions: Annotated[list[NonBlank], Field(min_length=1, max_length=10)]
    limitations: Annotated[list[NonBlank], Field(min_length=1, max_length=10)]
    claims: Annotated[list[ClaimDraft], Field(min_length=1, max_length=8)]


class PaperAnalysisBundle(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    paper_card: PaperCard
    claims: Annotated[list[Claim], Field(min_length=1, max_length=100)]
    evidence: Annotated[list[Evidence], Field(min_length=1, max_length=100)]
    retrieval_audit: RetrievalAudit

    @model_validator(mode="after")
    def verify_single_paper_graph(self) -> PaperAnalysisBundle:
        paper_id = self.paper_card.canonical_paper_id
        if self.retrieval_audit.canonical_paper_id != paper_id:
            raise ValueError("paper card and retrieval audit paper IDs differ")
        evidence_by_id = {item.evidence_id: item for item in self.evidence}
        claims_by_id = {item.claim_id: item for item in self.claims}
        if len(evidence_by_id) != len(self.evidence):
            raise ValueError("evidence IDs must be unique")
        if len(claims_by_id) != len(self.claims):
            raise ValueError("claim IDs must be unique")
        if set(self.paper_card.evidence_ids) != set(evidence_by_id):
            raise ValueError("paper card evidence IDs do not match evidence")
        if set(self.paper_card.claim_ids) != set(claims_by_id):
            raise ValueError("paper card claim IDs do not match claims")
        if any(item.canonical_paper_id != paper_id for item in self.evidence):
            raise ValueError("cross-paper evidence is forbidden")
        for claim in self.claims:
            referenced = set(claim.evidence_ids) | set(claim.counter_evidence_ids)
            if not claim.evidence_ids:
                raise ValueError("M2 claims require supporting evidence")
            if not referenced <= set(evidence_by_id):
                raise ValueError("claim references evidence outside its paper bundle")
        return self


class EvidenceReportArtifact(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    report_id: StableId
    question: Annotated[str, Field(min_length=2, max_length=4000)]
    analyses: Annotated[list[PaperAnalysisBundle], Field(min_length=3, max_length=5)]
    content_markdown: Annotated[str, Field(min_length=1, max_length=500_000)]
    content_sha256: Sha256
    limitations: Annotated[list[NonBlank], Field(min_length=1, max_length=20)]
    generated_at: datetime

    @model_validator(mode="after")
    def verify_report_graph_and_hash(self) -> EvidenceReportArtifact:
        paper_ids = [item.paper_card.canonical_paper_id for item in self.analyses]
        if len(paper_ids) != len(set(paper_ids)):
            raise ValueError("report paper IDs must be unique")
        claim_ids = [claim.claim_id for item in self.analyses for claim in item.claims]
        evidence_ids = [
            evidence.evidence_id for item in self.analyses for evidence in item.evidence
        ]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("report claim IDs must be globally unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("report evidence IDs must be globally unique")
        actual = hashlib.sha256(self.content_markdown.encode("utf-8")).hexdigest()
        if actual != self.content_sha256:
            raise ValueError("report content hash mismatch")
        return self


EVIDENCE_CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        DocuMindRetrieveRequest,
        RetrievalChunk,
        DocuMindRetrieveResponse,
        DocuMindErrorEnvelope,
        DocuMindReadiness,
        PaperCard,
        RetrievalAudit,
        PaperAnalysisBundle,
        EvidenceReportArtifact,
    )
}
