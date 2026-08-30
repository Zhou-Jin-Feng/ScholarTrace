"""Strict M4 validation, verification, and report-gate contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scholartrace.contracts import FollowUpRequest, StableId, Verification


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ValidationIssueCode = Literal[
    "claim_missing_evidence",
    "evidence_missing",
    "evidence_paper_missing",
    "content_hash_mismatch",
    "binding_missing",
    "document_key_mismatch",
    "index_id_mismatch",
    "source_hash_mismatch",
    "chunk_missing",
    "chunk_hash_mismatch",
    "quote_not_in_chunk",
    "char_range_mismatch",
    "page_mismatch",
    "numeric_mismatch",
    "version_target_missing",
    "citation_edge_missing",
    "lifecycle_incomplete",
]


class ValidationIssue(StrictModel):
    issue_id: StableId
    code: ValidationIssueCode
    severity: Literal["error", "warning"]
    message: Annotated[str, Field(min_length=1, max_length=500)]
    claim_id: StableId
    evidence_id: StableId | None = None
    paper_id: StableId | None = None


class ClaimValidation(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    validation_id: StableId
    claim_id: StableId
    passed: bool
    checked_evidence_ids: list[StableId] = Field(default_factory=list, max_length=200)
    issues: list[ValidationIssue] = Field(default_factory=list, max_length=1_000)
    validated_at: datetime

    @model_validator(mode="after")
    def verify_passed_matches_issues(self) -> ClaimValidation:
        has_error = any(issue.severity == "error" for issue in self.issues)
        if self.passed == has_error:
            raise ValueError("validation passed flag does not match error issues")
        if any(issue.claim_id != self.claim_id for issue in self.issues):
            raise ValueError("validation issue belongs to another claim")
        return self


class ValidationBundle(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    bundle_id: StableId
    results: list[ClaimValidation]
    outcome: Literal["succeeded", "degraded", "failed"]
    generated_at: datetime

    @model_validator(mode="after")
    def verify_unique_claims(self) -> ValidationBundle:
        claim_ids = [result.claim_id for result in self.results]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("validation bundle claim IDs must be unique")
        return self


class CitationRequirement(StrictModel):
    claim_id: StableId
    citing_paper_id: StableId
    cited_paper_id: StableId


class SemanticVerificationDraft(StrictModel):
    status: Literal["supported", "partially_supported", "unsupported", "conflicted"]
    reason: Annotated[str, Field(min_length=1, max_length=8000)]
    recommended_action: Literal["keep", "weaken", "follow_up", "remove"]


class ReportClaimDisposition(StrictModel):
    claim_id: StableId
    importance: Literal["critical", "supporting"]
    verification_status: Literal[
        "supported", "partially_supported", "unsupported", "conflicted"
    ]
    included: bool
    marker: str | None = Field(default=None, max_length=100)
    rendered_text: Annotated[str, Field(min_length=1, max_length=8200)]

    @model_validator(mode="after")
    def verify_visible_status(self) -> ReportClaimDisposition:
        if self.verification_status == "unsupported" and self.included:
            raise ValueError("unsupported claims cannot be included in a report")
        if self.included and self.verification_status != "supported" and not self.marker:
            raise ValueError("non-supported report claim requires a visible marker")
        return self


class ReportGateResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    dispositions: list[ReportClaimDisposition]
    blocked_critical_claim_ids: list[StableId] = Field(default_factory=list)
    report_safe: bool

    @model_validator(mode="after")
    def verify_critical_claim_gate(self) -> ReportGateResult:
        disposition_by_id = {item.claim_id: item for item in self.dispositions}
        for claim_id in self.blocked_critical_claim_ids:
            item = disposition_by_id.get(claim_id)
            if item is None or item.importance != "critical" or item.included:
                raise ValueError("blocked critical claim gate is inconsistent")
        return self


class M4ReliabilityResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    validation: ValidationBundle
    verifications: list[Verification]
    report_gate: ReportGateResult
    follow_up_requests: list[FollowUpRequest] = Field(default_factory=list, max_length=1)
    outcome: Literal["succeeded", "degraded", "failed"]


VERIFICATION_CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        ValidationIssue,
        ClaimValidation,
        ValidationBundle,
        CitationRequirement,
        SemanticVerificationDraft,
        ReportGateResult,
        M4ReliabilityResult,
    )
}
