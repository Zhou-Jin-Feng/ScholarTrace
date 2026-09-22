"""Runner and deterministic fixture adapters for SA-03."""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from scholartrace.contracts import Claim, Evidence, Verification
from scholartrace.model_provider.plan_generator import ProviderInferenceError
from scholartrace.verification.models import SemanticVerificationDraft
from scholartrace.verification.validator import EvidenceValidator
from scholartrace.verification.verifier import SemanticVerifierBackend, VerifierRunner

from .ledger import check_dispatch, execution_usage
from .models import (
    ABLATION_REPORT_SCHEMA_SHA256,
    AblationAttemptRecord,
    AblationBudget,
    AblationClaimDisposition,
    AblationError,
    AblationExecutionError,
    AblationExecutionManifest,
    AblationFrozenInput,
    AblationGeneratedReport,
    AblationPreparedInput,
    AblationPrivateArchive,
    AblationPrivateRow,
    AblationReportGenerator,
    AblationReportRequest,
    AblationResult,
    AblationUsage,
    AblationVariant,
    AttemptOperation,
    ablation_request_identity_sha256,
    canonical_sha256,
    text_sha256,
)
from .provider import MeteredReportError

VARIANTS: tuple[AblationVariant, AblationVariant] = ("V-on", "V-off")


def evidence_identity_sha256(evidence: Sequence[Evidence]) -> str:
    return canonical_sha256(
        [
            {
                "evidence_id": item.evidence_id,
                "canonical_paper_id": item.canonical_paper_id,
                "content_sha256": item.content_sha256,
                "source_sha256": item.source_sha256,
            }
            for item in sorted(evidence, key=lambda row: row.evidence_id)
        ]
    )


def _status_marker(status: str) -> str | None:
    return {
        "partially_supported": "[PARTIALLY SUPPORTED]",
        "conflicted": "[CONFLICTED]",
        "unverified": "[UNVERIFIED]",
    }.get(status)


def _prepared_semantic_status(status: str) -> str:
    return {
        "supported": "verified_supported",
        "partially_supported": "verified_partially_supported",
        "conflicted": "verified_conflicted",
        "unsupported": "verified_unsupported",
        "unverified": "unverified",
    }[status]


def _from_verifications(
    claims: Sequence[Claim],
    verifications: Mapping[str, Verification],
) -> tuple[AblationClaimDisposition, ...]:
    dispositions: list[AblationClaimDisposition] = []
    for claim in sorted(claims, key=lambda item: item.claim_id):
        verification = verifications.get(claim.claim_id)
        if verification is None:
            raise AblationExecutionError(
                "verifier",
                "missing_verification",
                f"{claim.claim_id}: V-on verification coverage is incomplete",
            )
        dispositions.append(
            AblationClaimDisposition(
                claim_id=claim.claim_id,
                status=verification.status,
                included=verification.status != "unsupported",
                marker=_status_marker(verification.status),
            )
        )
    return tuple(dispositions)


def _unverified(claims: Sequence[Claim]) -> tuple[AblationClaimDisposition, ...]:
    return tuple(
        AblationClaimDisposition(
            claim_id=claim.claim_id,
            status="unverified",
            included=True,
            marker="[UNVERIFIED]",
        )
        for claim in sorted(claims, key=lambda item: item.claim_id)
    )


def prepare_variant_input(
    *,
    frozen: AblationFrozenInput,
    variant: AblationVariant,
    dispositions: Sequence[AblationClaimDisposition],
    max_context_characters: int,
) -> AblationPreparedInput:
    if max_context_characters < 1000:
        raise AblationError("ablation context limit must be at least 1000 characters")
    disposition_by_id = {item.claim_id: item for item in dispositions}
    if set(disposition_by_id) != {claim.claim_id for claim in frozen.claims}:
        raise AblationError(f"{frozen.question_id}: disposition coverage drifted")
    packets: list[dict[str, Any]] = []
    for claim in sorted(frozen.claims, key=lambda item: item.claim_id):
        disposition = disposition_by_id[claim.claim_id]
        if variant == "V-on" and not disposition.included:
            continue
        packets.append(
            {
                "claim_id": claim.claim_id,
                "text": claim.text,
                "claim_type": claim.claim_type,
                "importance": claim.importance,
                "evidence_ids": sorted(claim.evidence_ids),
                "counter_evidence_ids": sorted(claim.counter_evidence_ids),
                "verification_status": disposition.status,
                "prepared_semantic_status": _prepared_semantic_status(disposition.status),
                "included": disposition.included,
                "marker": disposition.marker,
                "verification_reason": (
                    "Independent semantic verification disabled by V-off."
                    if variant == "V-off"
                    else None
                ),
            }
        )
    payload = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa03-private-ablation-input",
        "question_id": frozen.question_id,
        "question": frozen.question,
        "variant": variant,
        "frozen_input_sha256": frozen.input_sha256,
        "claims": packets,
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "canonical_paper_id": item.canonical_paper_id,
                "quote": item.quote,
                "evidence_level": item.evidence_level,
                "section": item.section,
                "page_number": item.page_number,
                "content_sha256": item.content_sha256,
                "source_sha256": item.source_sha256,
            }
            for item in sorted(frozen.evidence, key=lambda row: row.evidence_id)
        ],
        "rules": {
            "only_listed_evidence_ids_are_citable": True,
            "deterministic_validation_already_passed": True,
            "v_off_never_maps_unverified_to_supported": True,
        },
    }
    context = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(context) > max_context_characters:
        raise AblationExecutionError(
            "input",
            "context_limit_exceeded",
            f"{frozen.question_id}: prepared context exceeds the frozen limit",
        )
    allowed_ids = frozenset(item.evidence_id for item in frozen.evidence)
    return AblationPreparedInput(
        question_id=frozen.question_id,
        variant=variant,
        frozen_input_sha256=frozen.input_sha256,
        evidence_identity_sha256=evidence_identity_sha256(frozen.evidence),
        prepared_context=context,
        prepared_context_sha256=text_sha256(context),
        allowed_evidence_ids=allowed_ids,
        dispositions=tuple(dispositions),
    )


def _validate_deterministic_artifact(frozen: AblationFrozenInput) -> None:
    recomputed = EvidenceValidator().validate(
        claims=frozen.claims,
        evidence=frozen.evidence,
        papers=frozen.papers,
        bindings=frozen.bindings,
        chunks_by_paper=frozen.chunks_by_paper,
        validated_at=frozen.deterministic_validation.generated_at,
    )
    if recomputed.model_dump(mode="json") != frozen.deterministic_validation.model_dump(
        mode="json"
    ):
        raise AblationError(f"{frozen.question_id}: deterministic validation artifact drifted")


def _snapshot(backend: SemanticVerifierBackend) -> dict[str, int | float | None]:
    records = getattr(backend, "records", None)
    counter = getattr(backend, "call_counter", None)
    counter_attempted = getattr(counter, "attempted_calls", None)
    counter_reserved = getattr(counter, "reserved_reference_cost_cny", None)
    counter_actual = getattr(counter, "actual_reference_cost_cny", None)
    if isinstance(records, list):
        input_tokens = 0
        output_tokens = 0
        cost = 0.0
        duration = 0.0
        for record in records:
            usage = getattr(record, "usage", None)
            input_tokens += int(getattr(usage, "input_tokens", 0))
            output_tokens += int(getattr(usage, "output_tokens", 0))
            cost += float(getattr(record, "reference_cost_cny", 0.0))
            duration += float(getattr(record, "duration_seconds", 0.0))
        return {
            "calls": len(records),
            "attempted_calls": (
                int(counter_attempted) if counter_attempted is not None else len(records)
            ),
            "reserved_reference_cost_cny": (
                float(counter_reserved) if counter_reserved is not None else 0.0
            ),
            "actual_reference_cost_cny": (
                float(counter_actual) if counter_actual is not None else cost
            ),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reference_cost_cny": cost,
            "duration_seconds": duration,
        }
    calls = getattr(backend, "calls", None)
    if isinstance(calls, list):
        return {
            "calls": len(calls),
            "attempted_calls": len(calls),
            "reserved_reference_cost_cny": 0.0,
            "actual_reference_cost_cny": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reference_cost_cny": 0.0,
            "duration_seconds": 0.0,
        }
    return {
        "calls": None,
        "attempted_calls": None,
        "reserved_reference_cost_cny": None,
        "actual_reference_cost_cny": None,
        "input_tokens": None,
        "output_tokens": None,
        "reference_cost_cny": None,
        "duration_seconds": None,
    }


def _delta(before: int | None, after: int | None) -> int | None:
    if before is None or after is None:
        return None
    return max(0, after - before)


def _verifier_delta(
    before: Mapping[str, int | float | None],
    after: Mapping[str, int | float | None],
) -> dict[str, int | float | None]:
    metrics = {
        "calls": _delta(
            int(before["calls"]) if before["calls"] is not None else None,
            int(after["calls"]) if after["calls"] is not None else None,
        ),
        "attempted_calls": _delta(
            int(before["attempted_calls"]) if before["attempted_calls"] is not None else None,
            int(after["attempted_calls"]) if after["attempted_calls"] is not None else None,
        ),
        "input_tokens": _delta(
            int(before["input_tokens"]) if before["input_tokens"] is not None else None,
            int(after["input_tokens"]) if after["input_tokens"] is not None else None,
        ),
        "output_tokens": _delta(
            int(before["output_tokens"]) if before["output_tokens"] is not None else None,
            int(after["output_tokens"]) if after["output_tokens"] is not None else None,
        ),
        "reference_cost_cny": (
            float(after["actual_reference_cost_cny"]) - float(before["actual_reference_cost_cny"])
            if before["actual_reference_cost_cny"] is not None
            and after["actual_reference_cost_cny"] is not None
            else None
        ),
        "reserved_reference_cost_cny": (
            float(after["reserved_reference_cost_cny"])
            - float(before["reserved_reference_cost_cny"])
            if before["reserved_reference_cost_cny"] is not None
            and after["reserved_reference_cost_cny"] is not None
            else None
        ),
        "duration_seconds": (
            float(after["duration_seconds"]) - float(before["duration_seconds"])
            if before["duration_seconds"] is not None and after["duration_seconds"] is not None
            else None
        ),
    }

    if (metrics["attempted_calls"] or 0) > (metrics["calls"] or 0):
        for key in ("input_tokens", "output_tokens", "reference_cost_cny"):
            metrics[key] = None
    return metrics


def _sum_int_optional(values: Sequence[int | None]) -> int | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _sum_float_optional(values: Sequence[float | None]) -> float | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _sum_known_float(values: Sequence[float | None]) -> float:
    """Sum measured amounts; unknown exposure is tracked by separate ledger fields."""

    return sum(value for value in values if value is not None)


def _metric_int(metrics: Mapping[str, int | float | None], key: str) -> int | None:
    value = metrics[key]
    return int(value) if value is not None else None


def _metric_float(metrics: Mapping[str, int | float | None], key: str) -> float | None:
    value = metrics[key]
    return float(value) if value is not None else None


def _safe_provider_diagnostics(exc: ProviderInferenceError) -> dict[str, Any] | None:
    diagnostics = exc.diagnostics
    if not isinstance(diagnostics, dict):
        return None
    safe: dict[str, Any] = {}
    allowed = {
        "category",
        "status_code",
        "response_bytes",
        "response_sha256",
        "error_paths",
        "output_item_count",
        "response_status",
        "usage_present",
    }
    for key, value in diagnostics.items():
        if key not in allowed:
            continue
        if (
            isinstance(value, (str, int, float, bool))
            or value is None
            or (
                isinstance(value, list)
                and all(isinstance(item, (str, int, float, bool)) or item is None for item in value)
            )
        ):
            safe[key] = value
    return safe or None


class VerificationAblationRunner:
    """Run paired V-on/V-off conditions without changing the M4 product path."""

    def __init__(
        self,
        *,
        report_generator: AblationReportGenerator,
        verifier_backend: SemanticVerifierBackend | None = None,
        verifier_policy: Any | None = None,
        allow_fixture: bool = False,
    ) -> None:
        self.report_generator = report_generator
        self.verifier_backend = verifier_backend
        self.verifier_policy = verifier_policy
        self.allow_fixture = allow_fixture

    async def run(
        self,
        *,
        manifest: AblationExecutionManifest,
        inputs: Mapping[str, AblationFrozenInput],
        run_id: str,
        existing_rows: Sequence[AblationPrivateRow] | None = None,
        existing_verifications: Mapping[str, list[Verification]] | None = None,
        existing_attempts: Sequence[AblationAttemptRecord] | None = None,
        on_checkpoint: Callable[[AblationPrivateRow, Mapping[AblationVariant, AblationUsage]], None]
        | None = None,
        on_attempt: Callable[[AblationAttemptRecord], None] | None = None,
        variant_order: Sequence[AblationVariant] = ("V-on", "V-off"),
        retry_unknown: bool = False,
    ) -> AblationPrivateArchive:
        self._preflight(manifest, inputs, variant_order)
        rows_by_key = self._index_existing(existing_rows or [], manifest, inputs, run_id)
        attempts = list(existing_attempts or [])
        # Legacy rows have aggregate usage but no request records. Never count both.
        request_keys = {(a.question_id, a.variant) for a in attempts if a.subject_id is not None}
        legacy_rows = [
            row
            for row in rows_by_key.values()
            if (row.result.question_id, row.result.variant) not in request_keys
        ]
        legacy_keys = {(row.result.question_id, row.result.variant) for row in legacy_rows}
        ledger_legacy = self._usage_from_rows(legacy_rows)

        def ledger_attempts() -> list[AblationAttemptRecord]:
            return [a for a in attempts if (a.question_id, a.variant) not in legacy_keys]

        for question_id in sorted(inputs):
            for variant in variant_order:
                key = (question_id, variant)
                if key in rows_by_key:
                    continue
                frozen = inputs[question_id]
                cached = list((existing_verifications or {}).get(question_id, []))
                condition_attempts = [
                    item
                    for item in attempts
                    if item.question_id == question_id and item.variant == variant
                ]

                def latest(
                    operation: AttemptOperation,
                    subject_id: str,
                    candidates_source: list[AblationAttemptRecord] = condition_attempts,
                ) -> AblationAttemptRecord | None:
                    candidates = [
                        item
                        for item in candidates_source
                        if item.operation == operation and item.subject_id == subject_id
                    ]
                    return max(candidates, key=lambda item: item.attempt_number, default=None)

                verifier_subjects = {
                    item.subject_id
                    for item in condition_attempts
                    if item.operation == "verifier" and item.subject_id is not None
                }
                recovered_verifications: dict[str, Verification] = {}
                recovered_verifier_attempts: list[AblationAttemptRecord] = []
                for subject_id in verifier_subjects:
                    recovered = latest("verifier", subject_id)
                    if (
                        recovered is not None
                        and recovered.state == "succeeded"
                        and recovered.verification_result is not None
                    ):
                        recovered_verifications[subject_id] = recovered.verification_result
                        recovered_verifier_attempts.append(recovered)
                cached.extend(
                    value
                    for subject, value in recovered_verifications.items()
                    if subject not in {item.claim_id for item in cached}
                )
                request_attempts: dict[str, AblationAttemptRecord] = {}
                if variant == "V-on":
                    validations = {
                        item.claim_id: item for item in frozen.deterministic_validation.results
                    }
                    cached_ids = {item.claim_id for item in cached}
                    for claim in sorted(frozen.claims, key=lambda item: item.claim_id):
                        validation = validations[claim.claim_id]
                        if not validation.passed or claim.claim_id in cached_ids:
                            continue
                        previous = latest("verifier", claim.claim_id)
                        if (
                            previous is not None
                            and previous.state in {"dispatched", "failed", "unknown"}
                            and not retry_unknown
                        ):
                            raise AblationError(
                                "unknown verifier request blocks automatic resend: "
                                f"{previous.attempt_id}"
                            )
                        if previous is not None and previous.state == "intent":
                            attempt = previous
                        else:
                            number = (previous.attempt_number + 1) if previous else 1
                            request_identity = ablation_request_identity_sha256(
                                run_id=run_id,
                                question_id=question_id,
                                variant=variant,
                                operation="verifier",
                                subject_id=claim.claim_id,
                                attempt_number=number,
                                configuration_sha256=manifest.configuration_sha256(),
                                frozen_input_sha256=frozen.input_sha256,
                            )
                            attempt = AblationAttemptRecord(
                                attempt_id=f"{run_id}:{question_id}:{variant}:verifier:{claim.claim_id}:attempt-{number}",
                                request_identity_sha256=request_identity,
                                subject_id=claim.claim_id,
                                attempt_number=number,
                                previous_attempt_id=(previous.attempt_id if previous else None),
                                run_id=run_id,
                                question_id=question_id,
                                variant=variant,
                                operation="verifier",
                                state="intent",
                                frozen_input_sha256=frozen.input_sha256,
                                configuration_sha256=manifest.configuration_sha256(),
                                created_at=datetime.now(UTC),
                                attempted_calls=0,
                                successful_calls=0,
                            )
                            attempts.append(attempt)
                            condition_attempts.append(attempt)
                            if on_attempt is not None:
                                on_attempt(attempt)
                        request_attempts[claim.claim_id] = attempt

                recovered_report: AblationGeneratedReport | None = None
                report_previous = latest("report", "report")
                if (
                    report_previous is not None
                    and report_previous.state == "succeeded"
                    and report_previous.generated_report is not None
                ):
                    recovered_report = report_previous.generated_report
                    report_attempt = report_previous
                else:
                    if (
                        report_previous is not None
                        and report_previous.state
                        in {
                            "dispatched",
                            "failed",
                            "unknown",
                        }
                        and not retry_unknown
                    ):
                        raise AblationError(
                            "unknown report request blocks automatic resend: "
                            f"{report_previous.attempt_id}"
                        )
                    if report_previous is not None and report_previous.state == "intent":
                        report_attempt = report_previous
                    else:
                        number = (report_previous.attempt_number + 1) if report_previous else 1
                        request_identity = ablation_request_identity_sha256(
                            run_id=run_id,
                            question_id=question_id,
                            variant=variant,
                            operation="report",
                            subject_id="report",
                            attempt_number=number,
                            configuration_sha256=manifest.configuration_sha256(),
                            frozen_input_sha256=frozen.input_sha256,
                        )
                        report_attempt = AblationAttemptRecord(
                            attempt_id=f"{run_id}:{question_id}:{variant}:report:attempt-{number}",
                            request_identity_sha256=request_identity,
                            subject_id="report",
                            attempt_number=number,
                            previous_attempt_id=(
                                report_previous.attempt_id if report_previous else None
                            ),
                            run_id=run_id,
                            question_id=question_id,
                            variant=variant,
                            operation="report",
                            state="intent",
                            frozen_input_sha256=frozen.input_sha256,
                            configuration_sha256=manifest.configuration_sha256(),
                            created_at=datetime.now(UTC),
                            attempted_calls=0,
                            successful_calls=0,
                        )
                        attempts.append(report_attempt)
                        condition_attempts.append(report_attempt)
                        if on_attempt is not None:
                            on_attempt(report_attempt)

                def persist_attempt(updated: AblationAttemptRecord) -> None:
                    nonlocal attempts
                    previous = next(a for a in attempts if a.attempt_id == updated.attempt_id)
                    if updated.state == "dispatched":
                        backend = (
                            self.verifier_backend
                            if updated.operation == "verifier"
                            else self.report_generator
                        )
                        counter = getattr(backend, "call_counter", None)
                        provider_budget = getattr(counter, "budget", None)
                        if manifest.execution_mode == "production" and provider_budget is None:
                            raise AblationExecutionError(
                                "budget", "missing_reservation", "provider reservation is missing"
                            )
                        input_upper = output_upper = 0
                        reserve = 0.0
                        if manifest.execution_mode == "production":
                            assert provider_budget is not None
                            input_upper = provider_budget.max_input_token_upper_bound
                            output_upper = provider_budget.max_output_tokens
                            reserve = provider_budget.estimate_reference_cost_cny(
                                input_upper, output_upper
                            )
                        updated = updated.model_copy(
                            update={
                                "reserved_input_tokens": input_upper,
                                "reserved_output_tokens": output_upper,
                                "reserved_reference_cost_cny": reserve,
                            }
                        )
                        try:
                            check_dispatch(manifest, ledger_attempts(), ledger_legacy, updated)
                        except AblationExecutionError:
                            skipped = previous.model_copy(
                                update={
                                    "state": "skipped",
                                    "attempted_calls": 0,
                                    "successful_calls": 0,
                                    "error_code": "budget_not_dispatched",
                                }
                            )
                            attempts = [
                                skipped if a.attempt_id == skipped.attempt_id else a
                                for a in attempts
                            ]
                            if on_attempt is not None:
                                on_attempt(skipped)
                            raise
                    else:
                        updated = updated.model_copy(
                            update={
                                "reserved_input_tokens": previous.reserved_input_tokens,
                                "reserved_output_tokens": previous.reserved_output_tokens,
                                "reserved_reference_cost_cny": previous.reserved_reference_cost_cny,
                            }
                        )
                    attempts = [
                        updated if item.attempt_id == updated.attempt_id else item
                        for item in attempts
                    ]
                    if on_attempt is not None:
                        on_attempt(updated)

                row = await self._run_one(
                    manifest=manifest,
                    frozen=frozen,
                    variant=variant,
                    run_id=run_id,
                    usages=self._usage_from_rows(rows_by_key.values()),
                    cached_verifications=cached,
                    request_attempts=request_attempts,
                    report_attempt=report_attempt,
                    recovered_report=recovered_report,
                    recovered_verifier_attempts=recovered_verifier_attempts,
                    on_attempt=persist_attempt,
                )
                rows_by_key[key] = row
                if on_checkpoint is not None:
                    on_checkpoint(row, self._usage_from_rows(rows_by_key.values()))
                remaining = any(
                    (candidate_id, candidate_variant) not in rows_by_key
                    for candidate_id in inputs
                    for candidate_variant in VARIANTS
                )
                if remaining:
                    self._assert_dispatchable(
                        self._usage_from_rows(rows_by_key.values()), manifest.budget
                    )
        result_usage_by_variant: dict[AblationVariant, AblationUsage] = {
            variant: self._usage_for_variant(
                [row for row in rows_by_key.values() if row.result.variant == variant]
            )
            for variant in VARIANTS
        }
        return AblationPrivateArchive(
            manifest=manifest,
            manifest_sha256=manifest.stable_sha256(),
            usage_by_variant=execution_usage(manifest, ledger_attempts(), ledger_legacy),
            result_usage_by_variant=result_usage_by_variant,
            rows=[rows_by_key[key] for key in sorted(rows_by_key)],
            attempts=attempts,
        )

    def _preflight(
        self,
        manifest: AblationExecutionManifest,
        inputs: Mapping[str, AblationFrozenInput],
        variant_order: Sequence[AblationVariant],
    ) -> None:
        if tuple(variant_order) not in (("V-on", "V-off"), ("V-off", "V-on")):
            raise AblationError("variant order must contain exactly V-on and V-off")
        if set(inputs) != set(manifest.question_input_sha256):
            raise AblationError("manifest and frozen input question coverage differ")
        expected = (
            self.report_generator.model_profile,
            self.report_generator.model_identifier,
            self.report_generator.provider_protocol,
            self.report_generator.prompt_template_sha256,
            self.report_generator.report_schema_sha256,
        )
        actual = (
            manifest.model_profile,
            manifest.model_identifier,
            manifest.provider_protocol,
            manifest.report_prompt_sha256,
            manifest.report_schema_sha256,
        )
        if expected != actual:
            raise AblationError("report generator identity drifted from the manifest")
        for question_id, frozen in sorted(inputs.items()):
            payload = frozen.model_dump(mode="json", exclude={"input_sha256"})
            if canonical_sha256(payload) != frozen.input_sha256:
                raise AblationError(f"{question_id}: frozen input content drifted")
            _validate_deterministic_artifact(frozen)
            if frozen.input_sha256 != manifest.question_input_sha256[question_id]:
                raise AblationError(f"{question_id}: input hash drifted before execution")

    @staticmethod
    def _index_existing(
        rows: Iterable[AblationPrivateRow],
        manifest: AblationExecutionManifest,
        inputs: Mapping[str, AblationFrozenInput],
        run_id: str,
    ) -> dict[tuple[str, AblationVariant], AblationPrivateRow]:
        indexed: dict[tuple[str, AblationVariant], AblationPrivateRow] = {}
        config_hash = manifest.configuration_sha256()
        for row in rows:
            result = row.result
            key = (result.question_id, result.variant)
            if key in indexed or result.question_id not in inputs:
                raise AblationError("existing row has duplicate or unknown identity")
            if (
                result.run_id != run_id
                or result.configuration_sha256 != config_hash
                or result.frozen_input_sha256 != inputs[result.question_id].input_sha256
            ):
                raise AblationError("existing row drifted from the manifest")
            indexed[key] = row
        return indexed

    @staticmethod
    def _usage_from_rows(
        rows: Iterable[AblationPrivateRow],
    ) -> dict[AblationVariant, AblationUsage]:
        return {
            variant: VerificationAblationRunner._usage_for_variant(
                [row for row in rows if row.result.variant == variant]
            )
            for variant in VARIANTS
        }

    @staticmethod
    def _usage_for_variant(rows: Sequence[AblationPrivateRow]) -> AblationUsage:
        return AblationUsage(
            unknown_attempts=sum(row.result.unknown_attempts for row in rows),
            unknown_reserved_reference_cost_cny=sum(
                row.result.unknown_reserved_reference_cost_cny for row in rows
            ),
            verifier_attempted_calls=sum(row.result.verifier_attempted_calls for row in rows),
            verifier_calls=sum(row.result.verifier_calls for row in rows),
            verifier_input_tokens=(
                _sum_int_optional([row.result.verifier_input_tokens for row in rows]) if rows else 0
            ),
            verifier_output_tokens=(
                _sum_int_optional([row.result.verifier_output_tokens for row in rows])
                if rows
                else 0
            ),
            verifier_reference_cost_cny=(
                _sum_known_float([row.result.verifier_reference_cost_cny for row in rows])
                if rows
                else 0
            ),
            verifier_reserved_reference_cost_cny=(
                _sum_known_float([row.result.verifier_reserved_reference_cost_cny for row in rows])
                if rows
                else 0
            ),
            verifier_duration_seconds=(
                _sum_float_optional([row.result.verifier_duration_seconds for row in rows])
                if rows
                else 0
            ),
            report_calls=len(rows),
            model_calls=(
                _sum_int_optional([row.result.model_calls for row in rows]) if rows else 0
            ),
            provider_api_calls=(
                _sum_int_optional([row.result.provider_api_calls for row in rows]) if rows else 0
            ),
            input_tokens=(
                _sum_int_optional([row.result.input_tokens for row in rows]) if rows else 0
            ),
            output_tokens=(
                _sum_int_optional([row.result.output_tokens for row in rows]) if rows else 0
            ),
            reference_cost_cny=(
                _sum_known_float([row.result.reference_cost_cny for row in rows]) if rows else 0
            ),
            duration_seconds=(
                _sum_float_optional([row.result.duration_seconds for row in rows]) if rows else 0
            ),
        )

    @staticmethod
    def _combined_usage(
        usages: Mapping[AblationVariant, AblationUsage],
    ) -> AblationUsage:
        entries = [usages[variant] for variant in VARIANTS]
        return AblationUsage(
            unknown_attempts=sum(item.unknown_attempts for item in entries),
            unknown_reserved_reference_cost_cny=sum(
                item.unknown_reserved_reference_cost_cny for item in entries
            ),
            verifier_attempted_calls=sum(item.verifier_attempted_calls for item in entries),
            verifier_calls=sum(item.verifier_calls for item in entries),
            verifier_input_tokens=_sum_int_optional(
                [item.verifier_input_tokens for item in entries]
            ),
            verifier_output_tokens=_sum_int_optional(
                [item.verifier_output_tokens for item in entries]
            ),
            verifier_reference_cost_cny=_sum_known_float(
                [item.verifier_reference_cost_cny for item in entries]
            ),
            verifier_reserved_reference_cost_cny=_sum_known_float(
                [item.verifier_reserved_reference_cost_cny for item in entries]
            ),
            verifier_duration_seconds=_sum_float_optional(
                [item.verifier_duration_seconds for item in entries]
            ),
            report_calls=sum(item.report_calls for item in entries),
            model_calls=_sum_int_optional([item.model_calls for item in entries]),
            provider_api_calls=_sum_int_optional([item.provider_api_calls for item in entries]),
            input_tokens=_sum_int_optional([item.input_tokens for item in entries]),
            output_tokens=_sum_int_optional([item.output_tokens for item in entries]),
            reference_cost_cny=_sum_known_float([item.reference_cost_cny for item in entries]),
            duration_seconds=_sum_float_optional([item.duration_seconds for item in entries]),
        )

    @staticmethod
    def _assert_dispatchable(
        usages: Mapping[AblationVariant, AblationUsage],
        budget: AblationBudget | None = None,
    ) -> None:
        fields = (
            "model_calls",
            "provider_api_calls",
            "input_tokens",
            "output_tokens",
            "reference_cost_cny",
            "duration_seconds",
            "verifier_input_tokens",
            "verifier_output_tokens",
            "verifier_reference_cost_cny",
            "verifier_reserved_reference_cost_cny",
            "verifier_duration_seconds",
        )
        for variant, usage in usages.items():
            for field in fields:
                if getattr(usage, field) is None:
                    if (
                        budget is not None
                        and budget.unknown_usage_policy == "bounded_reserve"
                        and usage.unknown_attempts > 0
                        and usage.unknown_reserved_reference_cost_cny > 0
                    ):
                        continue
                    raise AblationError(
                        f"unknown cumulative usage blocks further dispatch: {variant}.{field}"
                    )

    async def _run_one(
        self,
        *,
        manifest: AblationExecutionManifest,
        frozen: AblationFrozenInput,
        variant: AblationVariant,
        run_id: str,
        usages: Mapping[AblationVariant, AblationUsage],
        cached_verifications: list[Verification] | None = None,
        request_attempts: Mapping[str, AblationAttemptRecord] | None = None,
        report_attempt: AblationAttemptRecord | None = None,
        recovered_report: AblationGeneratedReport | None = None,
        recovered_verifier_attempts: Sequence[AblationAttemptRecord] = (),
        on_attempt: Callable[[AblationAttemptRecord], None] | None = None,
    ) -> AblationPrivateRow:
        started = time.perf_counter()
        config_hash = manifest.configuration_sha256()
        evidence_hash = evidence_identity_sha256(frozen.evidence)
        verifications: list[Verification] = []
        dispositions: tuple[AblationClaimDisposition, ...] = ()
        prepared: AblationPreparedInput | None = None
        generated: AblationGeneratedReport | None = None
        verifier_before: dict[str, int | float | None] | None = None
        verifier_attempted_calls = sum(item.attempted_calls for item in recovered_verifier_attempts)
        verifier_calls = sum(item.successful_calls for item in recovered_verifier_attempts)
        verifier_input_tokens: int | None = _sum_int_optional(
            [item.input_tokens for item in recovered_verifier_attempts]
        )
        verifier_output_tokens: int | None = _sum_int_optional(
            [item.output_tokens for item in recovered_verifier_attempts]
        )
        verifier_reference_cost_cny: float | None = _sum_known_float(
            [item.actual_reference_cost_cny for item in recovered_verifier_attempts]
        )
        verifier_reserved_reference_cost_cny: float | None = _sum_known_float(
            [item.reserved_reference_cost_cny for item in recovered_verifier_attempts]
        )
        verifier_duration_seconds: float | None = _sum_float_optional(
            [item.duration_seconds for item in recovered_verifier_attempts]
        )
        unknown_attempts = 0
        unknown_reserved_reference_cost_cny = 0.0
        report_dispatched = False
        error_stage: str | None = None
        error_code: str | None = None
        error_message: str | None = None
        error_diagnostics: dict[str, Any] | None = None
        verifier_request_before: dict[str, dict[str, int | float | None]] = {}
        business_completed_verifier_subjects: set[str] = set()

        def persist(updated: AblationAttemptRecord) -> None:
            if on_attempt is not None:
                on_attempt(updated)

        def on_verifier_dispatch(claim: Claim) -> None:
            attempt = (request_attempts or {}).get(claim.claim_id)
            if attempt is None:
                return
            persist(
                attempt.model_copy(
                    update={
                        "state": "dispatched",
                        "started_at": datetime.now(UTC),
                        "attempted_calls": 1,
                    }
                )
            )
            verifier_request_before[claim.claim_id] = _snapshot(self.verifier_backend)  # type: ignore[arg-type]

        def on_verifier_result(results: list[Verification]) -> None:
            if not results:
                return
            verification = results[-1]
            attempt = (request_attempts or {}).get(verification.claim_id)
            before = verifier_request_before.get(verification.claim_id)
            if attempt is None or before is None or self.verifier_backend is None:
                return
            metrics = _verifier_delta(before, _snapshot(self.verifier_backend))
            persist(
                attempt.model_copy(
                    update={
                        "state": "succeeded",
                        "started_at": attempt.started_at or datetime.now(UTC),
                        "completed_at": datetime.now(UTC),
                        "attempted_calls": int(metrics["attempted_calls"] or 1),
                        "successful_calls": 1,
                        "input_tokens": _metric_int(metrics, "input_tokens"),
                        "output_tokens": _metric_int(metrics, "output_tokens"),
                        "reserved_reference_cost_cny": _metric_float(
                            metrics, "reserved_reference_cost_cny"
                        ),
                        "actual_reference_cost_cny": _metric_float(metrics, "reference_cost_cny"),
                        "duration_seconds": _metric_float(metrics, "duration_seconds"),
                        "verification_result": verification,
                    }
                )
            )
            business_completed_verifier_subjects.add(verification.claim_id)

        try:
            if variant == "V-on":
                if self.verifier_backend is None:
                    raise AblationExecutionError(
                        "verifier",
                        "verifier_not_configured",
                        "V-on requires a semantic verifier backend",
                    )
                verifier_before = _snapshot(self.verifier_backend)
                verifications = await VerifierRunner(
                    backend=self.verifier_backend,
                    policy=self.verifier_policy,
                    allow_fixture=self.allow_fixture,
                ).verify(
                    claims=frozen.claims,
                    evidence=frozen.evidence,
                    validations=frozen.deterministic_validation.results,
                    verified_at=datetime.now(UTC),
                    existing=cached_verifications,
                    on_dispatch=on_verifier_dispatch,
                    on_result=on_verifier_result,
                )
                after = _snapshot(self.verifier_backend)
                metrics = _verifier_delta(verifier_before, after)
                verifier_calls += (
                    int(metrics["calls"])
                    if metrics["calls"] is not None
                    else len(verifications) - len(recovered_verifier_attempts)
                )
                verifier_attempted_calls += (
                    int(metrics["attempted_calls"])
                    if metrics["attempted_calls"] is not None
                    else len(verifications) - len(recovered_verifier_attempts)
                )
                verifier_input_tokens = _sum_int_optional(
                    [verifier_input_tokens, _metric_int(metrics, "input_tokens")]
                )
                verifier_output_tokens = _sum_int_optional(
                    [verifier_output_tokens, _metric_int(metrics, "output_tokens")]
                )
                verifier_reference_cost_cny = _sum_known_float(
                    [
                        verifier_reference_cost_cny,
                        _metric_float(metrics, "reference_cost_cny"),
                    ]
                )
                verifier_reserved_reference_cost_cny = _sum_known_float(
                    [
                        verifier_reserved_reference_cost_cny,
                        _metric_float(metrics, "reserved_reference_cost_cny"),
                    ]
                )
                verifier_duration_seconds = _sum_float_optional(
                    [
                        verifier_duration_seconds,
                        _metric_float(metrics, "duration_seconds"),
                    ]
                )
                dispositions = _from_verifications(
                    frozen.claims,
                    {item.claim_id: item for item in verifications},
                )
            else:
                dispositions = _unverified(frozen.claims)
            prepared = prepare_variant_input(
                frozen=frozen,
                variant=variant,
                dispositions=dispositions,
                max_context_characters=manifest.budget.max_context_characters,
            )
            if recovered_report is not None:
                generated = recovered_report
            else:
                if report_attempt is not None:
                    persist(
                        report_attempt.model_copy(
                            update={
                                "state": "dispatched",
                                "started_at": datetime.now(UTC),
                                "attempted_calls": 1,
                            }
                        )
                    )
                report_dispatched = True
                generated = await self.report_generator.generate(
                    AblationReportRequest(
                        question_id=frozen.question_id,
                        split=frozen.split,
                        question=frozen.question,
                        variant=variant,
                        frozen_input_sha256=frozen.input_sha256,
                        evidence_identity_sha256=evidence_hash,
                        configuration_sha256=config_hash,
                        report_length_limit_chars=manifest.report_length_limit_chars,
                        prepared_context=prepared.prepared_context,
                        allowed_evidence_ids=prepared.allowed_evidence_ids,
                        dispositions=prepared.dispositions,
                    )
                )
                if report_attempt is not None:
                    persist(
                        report_attempt.model_copy(
                            update={
                                "state": "succeeded",
                                "started_at": report_attempt.started_at or datetime.now(UTC),
                                "completed_at": datetime.now(UTC),
                                "attempted_calls": generated.provider_api_calls or 1,
                                "successful_calls": generated.model_calls or 0,
                                "input_tokens": generated.input_tokens,
                                "output_tokens": generated.output_tokens,
                                "actual_reference_cost_cny": generated.reference_cost_cny,
                                "duration_seconds": generated.duration_seconds,
                                "generated_report": generated,
                            }
                        )
                    )
            if len(generated.report) > manifest.report_length_limit_chars:
                raise AblationExecutionError(
                    "report",
                    "report_length_exceeded",
                    "generated report exceeded the frozen length limit",
                )
            self._validate_generated_usage(generated)
            self._check_budget(
                manifest.budget,
                variant=variant,
                usages=usages,
                verifier_attempted_calls=verifier_attempted_calls,
                verifier_model_calls=(
                    verifier_attempted_calls if manifest.execution_mode == "production" else 0
                ),
                verifier_input_tokens=verifier_input_tokens,
                verifier_output_tokens=verifier_output_tokens,
                verifier_reference_cost_cny=verifier_reference_cost_cny,
                verifier_reserved_reference_cost_cny=verifier_reserved_reference_cost_cny,
                verifier_duration_seconds=verifier_duration_seconds,
                unknown_attempts=unknown_attempts,
                unknown_reserved_reference_cost_cny=unknown_reserved_reference_cost_cny,
                generated=generated,
                elapsed_seconds=time.perf_counter() - started,
            )
        except AblationExecutionError as exc:
            error_stage, error_code, error_message = exc.stage, exc.code, str(exc)
            if generated is None:
                generated = AblationGeneratedReport(
                    report="",
                    status="failed",
                    model_calls=0,
                    provider_api_calls=0,
                    input_tokens=0,
                    output_tokens=0,
                    reference_cost_cny=0,
                    duration_seconds=0,
                )
        except MeteredReportError as exc:
            error_stage, error_code, error_message = (
                "provider",
                type(exc.__cause__).__name__ if exc.__cause__ is not None else type(exc).__name__,
                str(exc),
            )
            error_diagnostics = _safe_provider_diagnostics(exc)
            generated = exc.generated
            if report_attempt is not None:
                persist(
                    report_attempt.model_copy(
                        update={
                            "state": "failed",
                            "started_at": report_attempt.started_at or datetime.now(UTC),
                            "completed_at": datetime.now(UTC),
                            "attempted_calls": generated.provider_api_calls or 1,
                            "successful_calls": 0,
                            "input_tokens": generated.input_tokens,
                            "output_tokens": generated.output_tokens,
                            "actual_reference_cost_cny": generated.reference_cost_cny,
                            "duration_seconds": generated.duration_seconds,
                            "error_code": error_code,
                            "generated_report": generated,
                        }
                    )
                )
        except TimeoutError as exc:
            error_stage, error_code, error_message = "provider", "timeout", str(exc) or "timeout"
            generated = AblationGeneratedReport(report="", status="timeout")
            if report_dispatched and report_attempt is not None:
                persist(
                    report_attempt.model_copy(
                        update={
                            "state": "unknown",
                            "started_at": report_attempt.started_at or datetime.now(UTC),
                            "completed_at": datetime.now(UTC),
                            "attempted_calls": 1,
                            "successful_calls": 0,
                            "error_code": "timeout",
                        }
                    )
                )
        except ProviderInferenceError as exc:
            error_stage, error_code, error_message = "provider", type(exc).__name__, str(exc)
            error_diagnostics = _safe_provider_diagnostics(exc)
            not_sent = bool(
                exc.diagnostics and exc.diagnostics.get("category") == "request_not_sent"
            )
            generated = AblationGeneratedReport(report="", status="failed")
            if report_dispatched and report_attempt is not None:
                if not_sent:
                    generated = AblationGeneratedReport(
                        report="",
                        status="failed",
                        model_calls=0,
                        provider_api_calls=0,
                        input_tokens=0,
                        output_tokens=0,
                        reference_cost_cny=0,
                        duration_seconds=0,
                    )
                persist(
                    report_attempt.model_copy(
                        update={
                            "state": "skipped" if not_sent else "unknown",
                            "started_at": report_attempt.started_at or datetime.now(UTC),
                            "completed_at": datetime.now(UTC),
                            "attempted_calls": 0 if not_sent else 1,
                            "successful_calls": 0,
                            "error_code": error_code,
                        }
                    )
                )
                report_dispatched = not not_sent
        except Exception as exc:  # noqa: BLE001 - failure must become a visible row
            error_stage, error_code, error_message = "execution", type(exc).__name__, str(exc)
            generated = AblationGeneratedReport(report="", status="failed")

        if self.verifier_backend is not None:
            for claim_id, attempt in (request_attempts or {}).items():
                before = verifier_request_before.get(claim_id)
                if before is None:
                    continue
                current = _snapshot(self.verifier_backend)
                metrics = _verifier_delta(before, current)
                attempted = int(metrics["attempted_calls"] or 0)
                if claim_id in business_completed_verifier_subjects:
                    continue
                persist(
                    attempt.model_copy(
                        update={
                            "state": (
                                "skipped"
                                if attempted == 0
                                else "unknown"
                                if error_code == "timeout"
                                else "failed"
                            ),
                            "started_at": attempt.started_at or datetime.now(UTC),
                            "completed_at": datetime.now(UTC),
                            "attempted_calls": attempted,
                            "successful_calls": 0,
                            "input_tokens": _metric_int(metrics, "input_tokens"),
                            "output_tokens": _metric_int(metrics, "output_tokens"),
                            "reserved_reference_cost_cny": _metric_float(
                                metrics, "reserved_reference_cost_cny"
                            ),
                            "actual_reference_cost_cny": _metric_float(
                                metrics, "reference_cost_cny"
                            ),
                            "duration_seconds": _metric_float(metrics, "duration_seconds"),
                            "error_code": error_code,
                        }
                    )
                )

        if variant == "V-on" and self.verifier_backend is not None and verifier_before is not None:
            metrics = _verifier_delta(verifier_before, _snapshot(self.verifier_backend))
            verifier_calls = sum(item.successful_calls for item in recovered_verifier_attempts) + (
                int(metrics["calls"]) if metrics["calls"] is not None else 0
            )
            verifier_attempted_calls = sum(
                item.attempted_calls for item in recovered_verifier_attempts
            ) + (int(metrics["attempted_calls"]) if metrics["attempted_calls"] is not None else 0)
            verifier_input_tokens = _sum_int_optional(
                [
                    _sum_int_optional([item.input_tokens for item in recovered_verifier_attempts]),
                    _metric_int(metrics, "input_tokens"),
                ]
            )
            verifier_output_tokens = _sum_int_optional(
                [
                    _sum_int_optional([item.output_tokens for item in recovered_verifier_attempts]),
                    _metric_int(metrics, "output_tokens"),
                ]
            )
            verifier_reference_cost_cny = _sum_known_float(
                [
                    _sum_known_float(
                        [item.actual_reference_cost_cny for item in recovered_verifier_attempts]
                    ),
                    _metric_float(metrics, "reference_cost_cny"),
                ]
            )
            verifier_reserved_reference_cost_cny = _sum_known_float(
                [
                    _sum_known_float(
                        [item.reserved_reference_cost_cny for item in recovered_verifier_attempts]
                    ),
                    _metric_float(metrics, "reserved_reference_cost_cny"),
                ]
            )
            verifier_duration_seconds = _sum_float_optional(
                [
                    _sum_float_optional(
                        [item.duration_seconds for item in recovered_verifier_attempts]
                    ),
                    _metric_float(metrics, "duration_seconds"),
                ]
            )

        if verifier_attempted_calls > verifier_calls:
            unknown_attempts += verifier_attempted_calls - verifier_calls
            unknown_reserved_reference_cost_cny += (
                verifier_attempted_calls - verifier_calls
            ) * manifest.budget.unknown_attempt_reserve_cny
        if (
            report_dispatched
            and error_code is not None
            and generated is not None
            and generated.model_calls is None
        ):
            unknown_attempts += 1
            unknown_reserved_reference_cost_cny += manifest.budget.unknown_attempt_reserve_cny

        assert generated is not None
        elapsed = time.perf_counter() - started
        if elapsed > manifest.budget.max_duration_seconds and error_code is None:
            error_stage, error_code, error_message = (
                "budget",
                "duration_budget_exceeded",
                "condition exceeded the frozen duration budget",
            )
        result_status = generated.status
        if error_code is not None:
            result_status = "timeout" if error_code == "timeout" else "failed"
        disposition_counts = {
            str(key): value for key, value in Counter(item.status for item in dispositions).items()
        }
        report = generated.report
        return AblationPrivateRow(
            result=AblationResult(
                run_id=run_id,
                variant=variant,
                question_id=frozen.question_id,
                split=frozen.split,
                status=result_status,
                frozen_input_sha256=frozen.input_sha256,
                evidence_identity_sha256=evidence_hash,
                prepared_context_sha256=(
                    prepared.prepared_context_sha256 if prepared is not None else None
                ),
                configuration_sha256=config_hash,
                report_sha256=text_sha256(report),
                total_claim_count=len(frozen.claims),
                included_claim_count=sum(item.included for item in dispositions),
                evidence_count=len(frozen.evidence),
                disposition_counts=disposition_counts,
                verifier_attempted_calls=verifier_attempted_calls,
                verifier_calls=verifier_calls,
                verifier_input_tokens=verifier_input_tokens,
                verifier_output_tokens=verifier_output_tokens,
                verifier_reference_cost_cny=verifier_reference_cost_cny,
                verifier_reserved_reference_cost_cny=verifier_reserved_reference_cost_cny,
                verifier_duration_seconds=verifier_duration_seconds,
                unknown_attempts=unknown_attempts,
                unknown_reserved_reference_cost_cny=unknown_reserved_reference_cost_cny,
                model_calls=generated.model_calls,
                provider_api_calls=generated.provider_api_calls,
                input_tokens=generated.input_tokens,
                output_tokens=generated.output_tokens,
                duration_seconds=generated.duration_seconds,
                reference_cost_cny=generated.reference_cost_cny,
                error_stage=error_stage,
                error_code=error_code,
                error_diagnostics=error_diagnostics,
            ),
            report=report,
            dispositions=list(dispositions),
            verifications=verifications,
            error_message=error_message,
        )

    @staticmethod
    def _validate_generated_usage(generated: AblationGeneratedReport) -> None:
        if generated.status not in {"succeeded", "degraded"}:
            return
        fields = (
            generated.input_tokens,
            generated.output_tokens,
            generated.model_calls,
            generated.provider_api_calls,
            generated.duration_seconds,
            generated.reference_cost_cny,
        )
        if any(value is None for value in fields):
            raise AblationExecutionError(
                "usage",
                "unknown_usage",
                "successful report returned incomplete usage; no zero was substituted",
            )

    @staticmethod
    def _check_budget(
        budget: AblationBudget,
        *,
        variant: AblationVariant,
        usages: Mapping[AblationVariant, AblationUsage],
        verifier_attempted_calls: int,
        verifier_model_calls: int,
        verifier_input_tokens: int | None,
        verifier_output_tokens: int | None,
        verifier_reference_cost_cny: float | None,
        verifier_reserved_reference_cost_cny: float | None,
        verifier_duration_seconds: float | None,
        unknown_attempts: int,
        unknown_reserved_reference_cost_cny: float,
        generated: AblationGeneratedReport,
        elapsed_seconds: float,
    ) -> None:
        values = (
            generated.model_calls,
            generated.provider_api_calls,
            generated.input_tokens,
            generated.output_tokens,
            generated.reference_cost_cny,
        )
        if any(value is None for value in values):
            raise AblationExecutionError(
                "usage",
                "unknown_usage",
                "budget check cannot substitute zero for unknown usage",
            )
        if verifier_attempted_calls > 0 and any(
            value is None
            for value in (
                verifier_input_tokens,
                verifier_output_tokens,
                verifier_reference_cost_cny,
                verifier_reserved_reference_cost_cny,
                verifier_duration_seconds,
            )
        ):
            raise AblationExecutionError(
                "usage",
                "unknown_usage",
                "Verifier usage is incomplete; no zero was substituted",
            )
        if unknown_attempts > 0 and budget.unknown_usage_policy == "stop":
            raise AblationExecutionError(
                "usage",
                "unknown_usage",
                "unknown usage policy is stop; no further dispatch is allowed",
            )
        model_calls = generated.model_calls
        provider_api_calls = generated.provider_api_calls
        input_tokens = generated.input_tokens
        output_tokens = generated.output_tokens
        reference_cost_cny = generated.reference_cost_cny
        assert model_calls is not None
        assert provider_api_calls is not None
        assert input_tokens is not None
        assert output_tokens is not None
        assert reference_cost_cny is not None
        previous = (
            VerificationAblationRunner._combined_usage(usages)
            if budget.budget_scope == "shared"
            else usages[variant]
        )
        VerificationAblationRunner._assert_dispatchable({variant: previous}, budget)
        verifier_input_tokens = verifier_input_tokens or 0
        verifier_output_tokens = verifier_output_tokens or 0
        verifier_cost = float(verifier_reference_cost_cny or 0.0)
        verifier_reserved = float(verifier_reserved_reference_cost_cny or 0.0)
        verifier_duration = float(verifier_duration_seconds or 0.0)
        checks = (
            (
                previous.verifier_attempted_calls + verifier_attempted_calls,
                budget.max_verifier_calls,
                "verifier calls",
            ),
            (previous.report_calls + 1, budget.max_report_calls, "report calls"),
            (
                (previous.model_calls or 0) + verifier_model_calls + model_calls,
                budget.max_model_calls,
                "model calls",
            ),
            (
                (previous.provider_api_calls or 0) + verifier_model_calls + provider_api_calls,
                budget.max_provider_api_calls,
                "provider API calls",
            ),
            (
                (previous.input_tokens or 0)
                + (previous.verifier_input_tokens or 0)
                + verifier_input_tokens
                + input_tokens,
                budget.max_input_tokens,
                "input tokens",
            ),
            (
                (previous.output_tokens or 0)
                + (previous.verifier_output_tokens or 0)
                + verifier_output_tokens
                + output_tokens,
                budget.max_output_tokens,
                "output tokens",
            ),
        )
        for actual, maximum, label in checks:
            if actual > maximum:
                raise AblationExecutionError(
                    "budget",
                    "budget_exceeded",
                    f"{label} budget exceeded",
                )
        actual_cost = (
            (previous.reference_cost_cny or 0)
            + (previous.verifier_reference_cost_cny or 0)
            + verifier_cost
            + reference_cost_cny
            + unknown_reserved_reference_cost_cny
        )
        reserved_cost = (
            (previous.reference_cost_cny or 0)
            + (previous.verifier_reserved_reference_cost_cny or 0)
            + verifier_reserved
            + reference_cost_cny
            + unknown_reserved_reference_cost_cny
        )
        if max(actual_cost, reserved_cost) > budget.max_reference_cost_cny:
            raise AblationExecutionError(
                "budget",
                "budget_exceeded",
                "reference cost budget exceeded",
            )
        report_duration = float(generated.duration_seconds or 0.0)
        if (previous.duration_seconds or 0) + (
            previous.verifier_duration_seconds or 0
        ) + verifier_duration + report_duration > budget.max_duration_seconds:
            raise AblationExecutionError("budget", "budget_exceeded", "duration budget exceeded")


class DeterministicFixtureAblationVerifier:
    """Zero-cost semantic verifier with explicit, testable Claim outcomes."""

    verifier_kind: Literal["fixture"] = "fixture"
    profile_id: str | None = None

    def __init__(self, outcomes: Mapping[str, SemanticVerificationDraft] | None = None) -> None:
        self.outcomes = dict(outcomes or {})
        self.calls: list[str] = []

    async def verify(
        self,
        *,
        claim: Claim,
        evidence: list[Evidence],
    ) -> SemanticVerificationDraft:
        del evidence
        self.calls.append(claim.claim_id)
        return self.outcomes.get(
            claim.claim_id,
            SemanticVerificationDraft(
                status="supported",
                reason="Fixture verifier defaulted to bounded support.",
                recommended_action="keep",
            ),
        )


class DeterministicFixtureAblationReportGenerator:
    """Render a deterministic report without model or external calls."""

    model_profile = "fixture"
    model_identifier = "fixture"
    provider_protocol: Literal["fixture"] = "fixture"
    prompt_template_sha256 = text_sha256("scholartrace-sa03-fixture-report-prompt-v1")
    report_schema_sha256 = ABLATION_REPORT_SCHEMA_SHA256

    async def generate(self, request: AblationReportRequest) -> AblationGeneratedReport:
        payload = json.loads(request.prepared_context)
        lines = [
            f"Fixture ablation report for {request.question_id}.",
            f"Condition: {request.variant}.",
            "Infrastructure-only output; no quality conclusion.",
        ]
        for claim in payload["claims"]:
            if claim["included"]:
                evidence_ids = ",".join(claim["evidence_ids"])
                lines.append(
                    f"- {claim['marker'] or '[SUPPORTED]'} {claim['claim_id']} "
                    f"({evidence_ids}) {claim['text']}"
                )
        return AblationGeneratedReport(
            report="\n".join(lines),
            status="succeeded",
            input_tokens=0,
            output_tokens=0,
            model_calls=0,
            provider_api_calls=0,
            duration_seconds=0,
            reference_cost_cny=0,
        )
