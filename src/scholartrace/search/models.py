"""Strict M1 contracts for academic-source search and reproducible snapshots."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scholartrace.contracts import Paper, Sha256, StableId

SourceName = Literal["arxiv", "openalex", "crossref", "semantic_scholar"]
SourceStatus = Literal["succeeded", "empty", "failed", "skipped"]
PublicErrorCode = Literal[
    "authentication_required",
    "budget_exhausted",
    "client_error",
    "invalid_response",
    "network_error",
    "rate_limited",
    "response_too_large",
    "service_unavailable",
    "timed_out",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchRequest(StrictModel):
    query: Annotated[str, Field(min_length=2, max_length=1000)]
    max_results: int = Field(default=10, ge=1, le=100)
    from_year: int | None = Field(default=None, ge=1900, le=2100)
    to_year: int | None = Field(default=None, ge=1900, le=2100)
    published_before: date | None = None

    @model_validator(mode="after")
    def validate_year_range(self) -> SearchRequest:
        if (
            self.from_year is not None
            and self.to_year is not None
            and self.from_year > self.to_year
        ):
            raise ValueError("from_year must not be greater than to_year")
        return self


class SourcePolicy(StrictModel):
    max_network_requests: int = Field(default=3, ge=1, le=100)
    min_interval_seconds: float = Field(default=0, ge=0, le=60, allow_inf_nan=False)
    timeout_seconds: float = Field(default=20, gt=0, le=300, allow_inf_nan=False)
    max_attempts: int = Field(default=3, ge=1, le=5)
    base_backoff_seconds: float = Field(default=0.25, ge=0, le=30, allow_inf_nan=False)
    max_backoff_seconds: float = Field(default=4, ge=0, le=120, allow_inf_nan=False)
    jitter_ratio: float = Field(default=0.1, ge=0, le=1, allow_inf_nan=False)
    max_response_bytes: int = Field(default=2_000_000, ge=1024, le=20_000_000)

    @model_validator(mode="after")
    def validate_retry_budget(self) -> SourcePolicy:
        if self.max_network_requests < self.max_attempts:
            raise ValueError("max_network_requests must cover max_attempts")
        return self


class PaperCandidate(StrictModel):
    candidate_id: StableId
    source: SourceName
    source_id: Annotated[str, Field(min_length=1, max_length=512)]
    title: Annotated[str, Field(min_length=1, max_length=1000)]
    authors: Annotated[list[str], Field(min_length=1, max_length=200)]
    publication_year: int = Field(ge=1900, le=2100)
    publication_date: date | None = None
    version_date: date | None = None
    doi: str | None = Field(default=None, max_length=512)
    arxiv_id: str | None = Field(default=None, max_length=64)
    arxiv_version: int | None = Field(default=None, ge=1, le=999)
    openalex_id: str | None = Field(default=None, max_length=64)
    semantic_scholar_id: str | None = Field(default=None, max_length=128)
    abstract: str | None = Field(default=None, max_length=100_000)
    access_level: Literal["abstract", "metadata"]
    source_url: str | None = Field(default=None, max_length=2048)
    retrieved_at: datetime
    record_sha256: Sha256


class SourceRequestRecord(StrictModel):
    request_id: StableId
    source: SourceName
    public_url: Annotated[str, Field(min_length=1, max_length=2048)]
    public_params: dict[str, str]
    started_at: datetime
    completed_at: datetime
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    cache_hit: bool
    attempts: int = Field(ge=0, le=10)
    status: SourceStatus
    http_status: int | None = Field(default=None, ge=100, le=599)
    error_code: PublicErrorCode | None = None
    public_reason: str | None = Field(default=None, max_length=500)
    response_sha256: Sha256 | None = None
    candidate_count: int = Field(default=0, ge=0, le=10_000)
    provider_reported_cost_usd: float = Field(default=0, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_status_details(self) -> SourceRequestRecord:
        if self.status == "failed" and self.error_code is None:
            raise ValueError("failed source request requires error_code")
        if self.status in {"succeeded", "empty"} and self.error_code is not None:
            raise ValueError("successful source request cannot contain error_code")
        return self


class SourceSearchResult(StrictModel):
    source: SourceName
    request: SourceRequestRecord
    candidates: list[PaperCandidate] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def validate_candidate_count(self) -> SourceSearchResult:
        if self.request.candidate_count != len(self.candidates):
            raise ValueError("request candidate_count must match candidates")
        if self.request.status == "succeeded" and not self.candidates:
            raise ValueError("succeeded source result requires candidates")
        if self.request.status in {"empty", "failed", "skipped"} and self.candidates:
            raise ValueError("non-success source result cannot contain candidates")
        return self


class MergeDecision(StrictModel):
    decision_id: StableId
    action: Literal["merged", "version_linked", "kept_separate"]
    left_ids: Annotated[list[StableId], Field(min_length=1)]
    right_ids: Annotated[list[StableId], Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    review_required: bool = False


class RankedPaper(StrictModel):
    paper: Paper
    score: float = Field(ge=0, allow_inf_nan=False)
    matched_query_terms: list[str] = Field(default_factory=list, max_length=100)


class SearchSnapshot(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    snapshot_id: StableId
    query: Annotated[str, Field(min_length=2, max_length=1000)]
    generated_at: datetime
    source_results: Annotated[list[SourceSearchResult], Field(min_length=1)]
    ranked_papers: list[RankedPaper]
    merge_decisions: list[MergeDecision]
    candidate_set_sha256: Sha256
    outcome: Literal["succeeded", "degraded", "failed"]


class BaselineArtifact(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    artifact_id: StableId
    baseline: Literal["B0", "B1"]
    generator: Literal["deterministic_extractive", "ollama_qwen3_8b"]
    model_profile_id: StableId | None = None
    query: Annotated[str, Field(min_length=2, max_length=1000)]
    input_snapshot_sha256: Sha256
    candidate_paper_ids: list[StableId]
    content_markdown: Annotated[str, Field(min_length=1, max_length=200_000)]
    content_sha256: Sha256
    limitations: Annotated[list[str], Field(min_length=1, max_length=20)]
    generated_at: datetime


SEARCH_CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        SearchRequest,
        SourcePolicy,
        PaperCandidate,
        SourceRequestRecord,
        SourceSearchResult,
        MergeDecision,
        RankedPaper,
        SearchSnapshot,
        BaselineArtifact,
    )
}
