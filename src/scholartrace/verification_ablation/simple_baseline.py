"""Single-pass synthesis baseline for the stage-two comparison."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from scholartrace.search.storage import write_json

from .models import (
    AblationClaimDisposition,
    AblationError,
    AblationFrozenInput,
    AblationGeneratedReport,
    AblationModel,
    AblationReportRequest,
    AblationStatus,
    ProviderProtocol,
    ReasoningEffort,
    canonical_sha256,
    text_sha256,
)
from .reporting import require_private_path
from .runner import prepare_variant_input


class SimpleBaselineManifest(AblationModel):
    """Stable identity and limits for one single-pass baseline configuration."""

    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["scholartrace-sp01-simple-baseline-manifest"] = (
        "scholartrace-sp01-simple-baseline-manifest"
    )
    experiment_id: str
    execution_mode: Literal["fixture", "production"]
    baseline_commit: str
    source_tree_sha256: str
    model_profile: str
    model_identifier: str
    provider_protocol: ProviderProtocol
    report_prompt_sha256: str
    report_schema_sha256: str
    report_length_limit_chars: int = Field(ge=1, le=100_000)
    automatic_retry: Literal[False] = False
    reasoning_effort: ReasoningEffort | None = None
    provider_hostname: str | None = None
    structured_output_mode: str | None = None
    streaming: bool = False
    dataset_fingerprint_sha256: str
    question_input_sha256: dict[str, str] = Field(min_length=1)
    max_context_characters: int = Field(default=40_000, ge=1_000)

    def canonical_identity_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        if payload["streaming"] is False:
            payload.pop("streaming")
        return payload

    def stable_sha256(self) -> str:
        return canonical_sha256(self.canonical_identity_payload())

    def configuration_sha256(self) -> str:
        payload = self.canonical_identity_payload()
        payload.pop("dataset_fingerprint_sha256", None)
        payload.pop("question_input_sha256", None)
        return canonical_sha256(payload)


class SimpleBaselineUsage(AblationModel):
    """Resource accounting for the single report request per input."""

    unknown_attempts: int = Field(default=0, ge=0)
    unknown_reserved_reference_cost_cny: float = Field(
        default=0, ge=0, allow_inf_nan=False
    )
    report_calls: int = Field(ge=0)
    model_calls: int | None = Field(default=None, ge=0)
    provider_api_calls: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reference_cost_cny: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class SimpleBaselineRow(AblationModel):
    """Private row containing one baseline report and its trace metadata."""

    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    baseline_id: str
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
    unknown_attempts: int = Field(default=0, ge=0)
    unknown_reserved_reference_cost_cny: float = Field(
        default=0, ge=0, allow_inf_nan=False
    )
    report_calls: int = Field(ge=0)
    model_calls: int | None = Field(default=None, ge=0)
    provider_api_calls: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reference_cost_cny: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    error_stage: str | None = None
    error_code: str | None = None
    error_diagnostics: dict[str, Any] | None = None
    report: str = Field(max_length=100_000)
    dispositions: list[AblationClaimDisposition]

    @model_validator(mode="after")
    def validate_row(self) -> SimpleBaselineRow:
        if self.status in {"succeeded", "degraded"} and not self.report.strip():
            raise AblationError("successful simple baseline row must contain a report")
        if text_sha256(self.report) != self.report_sha256:
            raise AblationError("simple baseline report hash mismatch")
        if len(self.dispositions) != self.total_claim_count:
            raise AblationError("simple baseline disposition count mismatch")
        if self.included_claim_count != sum(item.included for item in self.dispositions):
            raise AblationError("simple baseline included Claim count mismatch")
        if dict(Counter(item.status for item in self.dispositions)) != self.disposition_counts:
            raise AblationError("simple baseline disposition summary mismatch")
        return self


class SimpleBaselineArchive(AblationModel):
    """Private stage-one baseline archive; raw reports remain below agent/."""

    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["scholartrace-sp01-simple-baseline-private"] = (
        "scholartrace-sp01-simple-baseline-private"
    )
    manifest: SimpleBaselineManifest
    manifest_sha256: str
    usage: SimpleBaselineUsage
    rows: list[SimpleBaselineRow]

    @model_validator(mode="after")
    def validate_archive(self) -> SimpleBaselineArchive:
        if self.manifest.stable_sha256() != self.manifest_sha256:
            raise AblationError("simple baseline manifest hash mismatch")
        ids = [row.question_id for row in self.rows]
        if len(ids) != len(set(ids)):
            raise AblationError("simple baseline archive contains duplicate questions")
        expected = set(self.manifest.question_input_sha256)
        if set(ids) != expected:
            raise AblationError("simple baseline archive does not cover the manifest")
        for row in self.rows:
            if row.frozen_input_sha256 != self.manifest.question_input_sha256[row.question_id]:
                raise AblationError("simple baseline row input identity mismatch")
        return self


class SimpleBaselineReportGenerator(Protocol):
    """The baseline intentionally reuses the existing strict report adapter."""

    async def generate(self, request: AblationReportRequest) -> AblationGeneratedReport: ...


@dataclass(slots=True)
class SimpleBaselineRunner:
    """Run exactly one report request per frozen input without semantic verification."""

    report_generator: SimpleBaselineReportGenerator

    async def run(
        self,
        *,
        manifest: SimpleBaselineManifest,
        inputs: Mapping[str, AblationFrozenInput],
        run_id: str,
        configuration_sha256_override: str | None = None,
    ) -> SimpleBaselineArchive:
        if set(inputs) != set(manifest.question_input_sha256):
            raise AblationError("simple baseline inputs do not match manifest questions")
        configuration_sha256 = (
            configuration_sha256_override or manifest.configuration_sha256()
        )
        rows: list[SimpleBaselineRow] = []
        total_model_calls: list[int | None] = []
        total_provider_calls: list[int | None] = []
        total_input_tokens: list[int | None] = []
        total_output_tokens: list[int | None] = []
        total_duration = 0.0
        total_reference_cost = 0.0
        reference_cost_unknown = False

        for question_id in sorted(inputs):
            frozen = inputs[question_id]
            if frozen.input_sha256 != manifest.question_input_sha256[question_id]:
                raise AblationError(f"{question_id}: simple baseline input hash mismatch")
            dispositions = tuple(
                AblationClaimDisposition(
                    claim_id=claim.claim_id,
                    status="unverified",
                    included=True,
                    marker="[UNVERIFIED]",
                )
                for claim in sorted(frozen.claims, key=lambda item: item.claim_id)
            )
            prepared = prepare_variant_input(
                frozen=frozen,
                variant="V-off",
                dispositions=dispositions,
                max_context_characters=manifest.max_context_characters,
            )
            request = AblationReportRequest(
                question_id=frozen.question_id,
                split=frozen.split,
                question=frozen.question,
                variant="V-off",
                frozen_input_sha256=frozen.input_sha256,
                evidence_identity_sha256=prepared.evidence_identity_sha256,
                configuration_sha256=configuration_sha256,
                report_length_limit_chars=manifest.report_length_limit_chars,
                prepared_context=prepared.prepared_context,
                allowed_evidence_ids=prepared.allowed_evidence_ids,
                dispositions=prepared.dispositions,
            )
            started = datetime.now(UTC)
            generated: AblationGeneratedReport | None = None
            error_stage: str | None = None
            error_code: str | None = None
            error_diagnostics: dict[str, Any] | None = None
            try:
                generated = await self.report_generator.generate(request)
                if len(generated.report) > manifest.report_length_limit_chars:
                    raise AblationError("simple baseline report exceeded configured length")
            except Exception as exc:  # noqa: BLE001 - preserve one visible row per input
                error_stage = "report"
                error_code = type(exc).__name__
                raw_diagnostics = getattr(exc, "diagnostics", None)
                allowed_diagnostic_keys = {
                    "category",
                    "status_code",
                    "response_bytes",
                    "response_sha256",
                    "error_paths",
                    "output_item_count",
                    "response_status",
                    "usage_present",
                }
                error_diagnostics = {
                    "exception_type": type(exc).__name__,
                    "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
                    **(
                        {
                            key: value
                            for key, value in raw_diagnostics.items()
                            if key in allowed_diagnostic_keys
                            and (
                                isinstance(value, (str, int, float, bool))
                                or value is None
                                or (
                                    isinstance(value, list)
                                    and all(
                                        isinstance(item, (str, int, float, bool))
                                        for item in value
                                    )
                                )
                            )
                        }
                        if isinstance(raw_diagnostics, dict)
                        else {}
                    ),
                }
                status: AblationStatus = "failed"
                report = ""
            else:
                status = generated.status
                report = generated.report
            finished = datetime.now(UTC)
            duration = (
                generated.duration_seconds
                if generated is not None and generated.duration_seconds is not None
                else max((finished - started).total_seconds(), 0.0)
            )
            model_calls = generated.model_calls if generated is not None else None
            provider_calls = generated.provider_api_calls if generated is not None else None
            input_tokens = generated.input_tokens if generated is not None else None
            output_tokens = generated.output_tokens if generated is not None else None
            reference_cost = generated.reference_cost_cny if generated is not None else None
            total_model_calls.append(model_calls)
            total_provider_calls.append(provider_calls)
            total_input_tokens.append(input_tokens)
            total_output_tokens.append(output_tokens)
            total_duration += duration
            if reference_cost is None:
                reference_cost_unknown = True
            else:
                total_reference_cost += reference_cost
            rows.append(
                SimpleBaselineRow(
                    run_id=run_id,
                    baseline_id=manifest.experiment_id,
                    question_id=frozen.question_id,
                    split=frozen.split,
                    status=status,
                    frozen_input_sha256=frozen.input_sha256,
                    evidence_identity_sha256=prepared.evidence_identity_sha256,
                    prepared_context_sha256=prepared.prepared_context_sha256,
                    configuration_sha256=configuration_sha256,
                    report_sha256=text_sha256(report),
                    total_claim_count=len(frozen.claims),
                    included_claim_count=len(dispositions),
                    evidence_count=len(frozen.evidence),
                    disposition_counts=dict(Counter(item.status for item in dispositions)),
                    report_calls=1,
                    model_calls=model_calls,
                    provider_api_calls=provider_calls,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    duration_seconds=duration,
                    reference_cost_cny=reference_cost,
                    error_stage=error_stage,
                    error_code=error_code,
                    error_diagnostics=error_diagnostics,
                    report=report,
                    dispositions=list(dispositions),
                )
            )

        def sum_optional(values: list[int | None]) -> int | None:
            if any(value is None for value in values):
                return None
            return sum(int(value) for value in values if value is not None)

        usage = SimpleBaselineUsage(
            report_calls=len(rows),
            model_calls=sum_optional(total_model_calls),
            provider_api_calls=sum_optional(total_provider_calls),
            input_tokens=sum_optional(total_input_tokens),
            output_tokens=sum_optional(total_output_tokens),
            duration_seconds=total_duration,
            reference_cost_cny=None if reference_cost_unknown else total_reference_cost,
        )
        archive = SimpleBaselineArchive(
            manifest=manifest,
            manifest_sha256=manifest.stable_sha256(),
            usage=usage,
            rows=rows,
        )
        return archive


def public_payload(archive: SimpleBaselineArchive) -> dict[str, Any]:
    """Return a redacted summary suitable for evaluation/reports/."""

    return {
        "schema_version": "1.0",
        "purpose": "scholartrace-sp01-simple-baseline-sanitized-run",
        "comparison_role": "single-pass-synthesis-baseline",
        "manifest": archive.manifest.model_dump(mode="json"),
        "manifest_sha256": archive.manifest_sha256,
        "usage": archive.usage.model_dump(mode="json"),
        "runs": [
            row.model_dump(
                mode="json",
                exclude={"report", "dispositions"},
            )
            for row in archive.rows
        ],
        "raw_reports_stored_publicly": False,
        "claim_text_stored_publicly": False,
        "evidence_quotes_stored_publicly": False,
        "notes": [
            (
                "The baseline reads the same frozen Evidence and Claim packet and "
                "performs one report request per question."
            ),
            "All baseline Claims retain the experiment-only unverified state.",
            (
                "This fixture run validates entry, input identity, accounting, and "
                "redaction; it does not establish quality."
            ),
        ],
    }

def write_artifacts(
    *,
    archive: SimpleBaselineArchive,
    private_path: Any,
    public_path: Any,
) -> None:
    private_destination = require_private_path(private_path)
    public_destination = public_path.resolve()
    if private_destination == public_destination:
        raise AblationError("simple baseline private and public paths must differ")
    write_json(private_destination, archive.model_dump(mode="json"))
    write_json(public_destination, public_payload(archive))
