"""Strict contracts for the independent SA-03 verification ablation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scholartrace.contracts import Claim, DocuMindBinding, Evidence, Paper, Verification
from scholartrace.evidence.models import RetrievalChunk
from scholartrace.verification.models import ValidationBundle

AblationVariant = Literal["V-on", "V-off"]
AblationStatus = Literal["succeeded", "degraded", "failed", "timeout"]
AblationClaimStatus = Literal[
    "supported",
    "partially_supported",
    "unsupported",
    "conflicted",
    "unverified",
]
ProviderProtocol = Literal["responses", "chat_completions", "fixture"]
ReasoningEffort = Literal[
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
]
AttemptOperation = Literal["verifier", "report"]
AttemptState = Literal[
    "intent",
    "dispatched",
    "succeeded",
    "failed",
    "unknown",
    "skipped",
]


class AblationError(ValueError):
    """The independent ablation input, configuration, or archive is unsafe."""


class AblationExecutionError(RuntimeError):
    """A single condition failed and must remain visible in the archive."""

    def __init__(self, stage: str, code: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code


class AblationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def canonical_sha256(payload: Any) -> str:
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def ablation_request_identity_sha256(
    *,
    run_id: str,
    question_id: str,
    variant: AblationVariant,
    operation: AttemptOperation,
    subject_id: str | None,
    attempt_number: int,
    configuration_sha256: str,
    frozen_input_sha256: str,
) -> str:
    """Return the durable identity for one concrete Provider request attempt."""

    return canonical_sha256(
        {
            "run_id": run_id,
            "question_id": question_id,
            "variant": variant,
            "operation": operation,
            "subject_id": subject_id,
            "attempt_number": attempt_number,
            "configuration_sha256": configuration_sha256,
            "frozen_input_sha256": frozen_input_sha256,
        }
    )


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AblationSourceRef(AblationModel):
    artifact_id: str
    report_path: str
    report_id: str
    report_question: str
    report_sha256: str
    report_content_sha256: str


class AblationFrozenInput(AblationModel):
    """One SA-02 question before semantic verification or report gating."""

    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["scholartrace-sa02-pre-semantic-verification-input"]
    question_id: str
    split: Literal["pilot", "formal"]
    question: str
    categories: list[str]
    source: AblationSourceRef
    papers: list[Paper]
    bindings: list[DocuMindBinding]
    chunks_by_paper: dict[str, list[RetrievalChunk]]
    claims: list[Claim]
    evidence: list[Evidence]
    deterministic_validation: ValidationBundle
    semantic_verification_results_included: Literal[False] = False
    input_sha256: str

    @model_validator(mode="after")
    def validate_frozen_input(self) -> AblationFrozenInput:
        if self.deterministic_validation.outcome != "succeeded":
            raise AblationError("SA-02 deterministic validation is not succeeded")
        if any(not item.passed for item in self.deterministic_validation.results):
            raise AblationError("SA-02 input contains a failed deterministic validation")
        claim_ids = [item.claim_id for item in self.claims]
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(claim_ids) != len(set(claim_ids)):
            raise AblationError(f"{self.question_id}: duplicate Claim identity")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise AblationError(f"{self.question_id}: duplicate Evidence identity")
        if set(item.claim_id for item in self.deterministic_validation.results) != set(claim_ids):
            raise AblationError(f"{self.question_id}: validation Claim coverage drifted")
        payload = self.model_dump(mode="json", exclude={"input_sha256"})
        if canonical_sha256(payload) != self.input_sha256:
            raise AblationError(f"{self.question_id}: frozen input hash mismatch")
        return self

    @classmethod
    def from_path(cls, path: Any) -> AblationFrozenInput:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class AblationBudget(AblationModel):
    budget_scope: Literal["per_condition", "shared"] = "per_condition"
    unknown_usage_policy: Literal["stop", "bounded_reserve"] = "stop"
    unknown_attempt_reserve_cny: float = Field(
        default=0, ge=0, allow_inf_nan=False
    )
    max_verifier_calls: int = Field(ge=0)
    max_report_calls: int = Field(ge=0)
    max_model_calls: int = Field(ge=0)
    max_provider_api_calls: int = Field(ge=0)
    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)
    max_reference_cost_cny: float = Field(ge=0, allow_inf_nan=False)
    max_duration_seconds: float = Field(ge=0, allow_inf_nan=False)
    max_context_characters: int = Field(ge=1000)


class AblationExecutionManifest(AblationModel):
    schema_version: Literal["1.0"] = "1.0"
    experiment_id: str
    execution_mode: Literal["fixture", "production"]
    baseline_commit: str
    model_profile: str
    model_identifier: str
    provider_protocol: ProviderProtocol
    verifier_prompt_sha256: str
    report_prompt_sha256: str
    report_schema_sha256: str
    report_length_limit_chars: int = Field(ge=1, le=100_000)
    automatic_retry: Literal[False] = False
    reasoning_effort: ReasoningEffort | None = None
    provider_hostname: str | None = None
    structured_output_mode: str | None = None
    request_timeout_seconds: float = Field(default=180, ge=1, allow_inf_nan=False)
    max_response_bytes: int = Field(default=1_000_000, ge=1024)
    dataset_fingerprint_sha256: str
    question_input_sha256: dict[str, str] = Field(min_length=1)
    budget: AblationBudget

    def stable_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))

    def configuration_sha256(self) -> str:
        return canonical_sha256(
            {
                "model_profile": self.model_profile,
                "model_identifier": self.model_identifier,
                "provider_protocol": self.provider_protocol,
                "verifier_prompt_sha256": self.verifier_prompt_sha256,
                "report_prompt_sha256": self.report_prompt_sha256,
                "report_schema_sha256": self.report_schema_sha256,
                "report_length_limit_chars": self.report_length_limit_chars,
                "automatic_retry": self.automatic_retry,
                "reasoning_effort": self.reasoning_effort,
                "provider_hostname": self.provider_hostname,
                "structured_output_mode": self.structured_output_mode,
                "request_timeout_seconds": self.request_timeout_seconds,
                "max_response_bytes": self.max_response_bytes,
                "budget": self.budget.model_dump(mode="json"),
            }
        )

    def execution_identity_sha256(self) -> str:
        """Hash model/prompt/runtime identity while excluding mutable budget policy."""

        payload = self.model_dump(mode="json")
        payload.pop("budget", None)
        return canonical_sha256(payload)


class AblationClaimDisposition(AblationModel):
    claim_id: str
    status: AblationClaimStatus
    included: bool
    marker: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_marker(self) -> AblationClaimDisposition:
        expected = {
            "partially_supported": "[PARTIALLY SUPPORTED]",
            "conflicted": "[CONFLICTED]",
            "unverified": "[UNVERIFIED]",
        }.get(self.status)
        if self.status == "unsupported" and self.included:
            raise AblationError("unsupported Claim cannot be included")
        if self.status not in {"supported", "unsupported"} and self.marker != expected:
            raise AblationError("non-supported Claim must carry its experiment marker")
        if self.status in {"supported", "unverified"} and not self.included:
            raise AblationError("supported or unverified Claim cannot be silently excluded")
        if self.status == "unsupported" and self.marker is not None:
            raise AblationError("unsupported Claim cannot carry a report marker")
        return self


@dataclass(frozen=True, slots=True)
class AblationPreparedInput:
    question_id: str
    variant: AblationVariant
    frozen_input_sha256: str
    evidence_identity_sha256: str
    prepared_context: str
    prepared_context_sha256: str
    allowed_evidence_ids: frozenset[str]
    dispositions: tuple[AblationClaimDisposition, ...]


@dataclass(frozen=True, slots=True)
class AblationReportRequest:
    question_id: str
    split: str
    question: str
    variant: AblationVariant
    frozen_input_sha256: str
    evidence_identity_sha256: str
    configuration_sha256: str
    report_length_limit_chars: int
    prepared_context: str
    allowed_evidence_ids: frozenset[str]
    dispositions: tuple[AblationClaimDisposition, ...]


class AblationReportFindingDraft(AblationModel):
    claim_id: str
    claim: str = Field(min_length=3, max_length=1_500)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    support_status: Literal[
        "verified_supported",
        "verified_partially_supported",
        "verified_conflicted",
        "unverified",
    ]


class AblationReportDraft(AblationModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=300)
    answer_status: Literal["answered", "insufficient_evidence", "out_of_scope"]
    findings: list[AblationReportFindingDraft] = Field(max_length=8)
    limitations: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_answer_shape(self) -> AblationReportDraft:
        if self.answer_status == "answered" and not self.findings:
            raise AblationError("answered ablation reports require findings")
        if self.answer_status != "answered" and self.findings:
            raise AblationError("non-answer ablation reports cannot contain findings")
        if any(not item.strip() or len(item) > 1_000 for item in self.limitations):
            raise AblationError("ablation report limitations are invalid")
        return self


ABLATION_REPORT_SCHEMA_SHA256 = canonical_sha256(
    AblationReportDraft.model_json_schema(mode="validation")
)


class AblationGeneratedReport(AblationModel):
    report: str = Field(max_length=100_000)
    status: AblationStatus
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    model_calls: int | None = Field(default=None, ge=0)
    provider_api_calls: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reference_cost_cny: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_success_payload(self) -> AblationGeneratedReport:
        if self.status in {"succeeded", "degraded"} and not self.report.strip():
            raise AblationError("successful ablation report must contain text")
        return self


class AblationReportGenerator(Protocol):
    model_profile: str
    model_identifier: str
    provider_protocol: ProviderProtocol
    prompt_template_sha256: str
    report_schema_sha256: str

    async def generate(self, request: AblationReportRequest) -> AblationGeneratedReport: ...


class AblationUsage(AblationModel):
    unknown_attempts: int = Field(default=0, ge=0)
    unknown_reserved_reference_cost_cny: float = Field(
        default=0, ge=0, allow_inf_nan=False
    )
    verifier_attempted_calls: int = Field(ge=0)
    verifier_calls: int = Field(ge=0)
    verifier_input_tokens: int | None = Field(default=None, ge=0)
    verifier_output_tokens: int | None = Field(default=None, ge=0)
    verifier_reference_cost_cny: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    verifier_reserved_reference_cost_cny: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    verifier_duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    report_calls: int = Field(ge=0)
    model_calls: int | None = Field(default=None, ge=0)
    provider_api_calls: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reference_cost_cny: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class AblationResult(AblationModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    variant: AblationVariant
    question_id: str
    split: Literal["pilot", "formal"]
    status: AblationStatus
    frozen_input_sha256: str
    evidence_identity_sha256: str
    prepared_context_sha256: str | None
    configuration_sha256: str
    report_sha256: str
    total_claim_count: int = Field(ge=0)
    included_claim_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    disposition_counts: dict[str, int]
    verifier_attempted_calls: int = Field(ge=0)
    verifier_calls: int = Field(ge=0)
    verifier_input_tokens: int | None = Field(default=None, ge=0)
    verifier_output_tokens: int | None = Field(default=None, ge=0)
    verifier_reference_cost_cny: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    verifier_reserved_reference_cost_cny: float | None = Field(
        default=None, ge=0, allow_inf_nan=False
    )
    verifier_duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    unknown_attempts: int = Field(default=0, ge=0)
    unknown_reserved_reference_cost_cny: float = Field(
        default=0, ge=0, allow_inf_nan=False
    )
    model_calls: int | None = Field(default=None, ge=0)
    provider_api_calls: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reference_cost_cny: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    error_stage: str | None = None
    error_code: str | None = None
    error_diagnostics: dict[str, Any] | None = None


class AblationAttemptRecord(AblationModel):
    attempt_id: str
    request_identity_sha256: str | None = None
    subject_id: str | None = None
    attempt_number: int = Field(default=1, ge=1)
    previous_attempt_id: str | None = None
    run_id: str
    question_id: str
    variant: AblationVariant
    operation: AttemptOperation
    state: AttemptState
    frozen_input_sha256: str
    configuration_sha256: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempted_calls: int = Field(ge=0)
    successful_calls: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reserved_reference_cost_cny: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reserved_input_tokens: int | None = Field(default=None, ge=0)
    reserved_output_tokens: int | None = Field(default=None, ge=0)
    actual_reference_cost_cny: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    error_code: str | None = None
    verification_result: Verification | None = None
    generated_report: AblationGeneratedReport | None = None


class AblationPrivateRow(AblationModel):
    """Raw report and verifier reasons; serialize only below ``agent/``."""

    result: AblationResult
    report: str = Field(max_length=100_000)
    dispositions: list[AblationClaimDisposition]
    verifications: list[Verification] = Field(default_factory=list)
    error_message: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_report_hash(self) -> AblationPrivateRow:
        if text_sha256(self.report) != self.result.report_sha256:
            raise AblationError("private ablation report hash mismatch")
        if self.result.variant == "V-off" and self.verifications:
            raise AblationError("V-off cannot contain formal Verification records")
        return self


class AblationPrivateArchive(AblationModel):
    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["scholartrace-sa03-private-answers"] = (
        "scholartrace-sa03-private-answers"
    )
    manifest: AblationExecutionManifest
    manifest_sha256: str
    usage_by_variant: dict[AblationVariant, AblationUsage]
    result_usage_by_variant: dict[AblationVariant, AblationUsage] = Field(default_factory=dict)
    rows: list[AblationPrivateRow]
    attempts: list[AblationAttemptRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_archive(self) -> AblationPrivateArchive:
        if self.manifest.stable_sha256() != self.manifest_sha256:
            raise AblationError("ablation manifest hash mismatch")
        identities = [(row.result.question_id, row.result.variant) for row in self.rows]
        if len(identities) != len(set(identities)):
            raise AblationError("ablation archive contains duplicate question/variant rows")
        expected = {
            (question_id, variant)
            for question_id in self.manifest.question_input_sha256
            for variant in ("V-on", "V-off")
        }
        if set(identities) != expected:
            raise AblationError("ablation archive does not have complete paired coverage")
        attempt_ids = [attempt.attempt_id for attempt in self.attempts]
        if len(attempt_ids) != len(set(attempt_ids)):
            raise AblationError("ablation archive contains duplicate attempt identities")
        return self


def load_frozen_inputs(path: Any) -> dict[str, AblationFrozenInput]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected_purpose = "scholartrace-sa02-private-frozen-pre-verification-inputs"
    if payload.get("purpose") != expected_purpose:
        raise AblationError("unexpected SA-02 frozen input purpose")
    inputs = [
        AblationFrozenInput.model_validate(item)
        for item in payload.get("questions", [])
    ]
    indexed = {item.question_id: item for item in inputs}
    if len(indexed) != len(inputs) or not indexed:
        raise AblationError("SA-02 frozen input question identities are invalid")
    return dict(sorted(indexed.items()))
