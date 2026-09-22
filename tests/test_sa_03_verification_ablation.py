from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sa04_synthetic import make_synthetic_inputs

from scholartrace.contracts import Claim, Evidence, Verification
from scholartrace.verification.models import SemanticVerificationDraft
from scholartrace.verification.verifier import VerifierKind
from scholartrace.verification_ablation import (
    ABLATION_REPORT_SCHEMA_SHA256,
    AblationBudget,
    AblationError,
    AblationExecutionManifest,
    AblationGeneratedReport,
    DeterministicFixtureAblationReportGenerator,
    DeterministicFixtureAblationVerifier,
    VerificationAblationRunner,
    prepare_variant_input,
    public_payload,
    require_private_path,
)
from scholartrace.verification_ablation.models import (
    AblationClaimDisposition,
    AblationFrozenInput,
    AblationReportDraft,
    canonical_sha256,
    text_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_MANIFEST = ROOT / "evaluation" / "seeds" / "sa_02_verification_ablation_manifest.json"


def _inputs() -> dict[str, AblationFrozenInput]:
    return make_synthetic_inputs()


def _manifest(
    inputs: dict[str, AblationFrozenInput],
    generator: object,
) -> AblationExecutionManifest:
    public = json.loads(PUBLIC_MANIFEST.read_text(encoding="utf-8"))
    return AblationExecutionManifest(
        experiment_id="test:sa03",
        execution_mode="fixture",
        baseline_commit="3d095a79feff257b57b041de40b7b5ed73021f9e",
        model_profile=generator.model_profile,
        model_identifier=generator.model_identifier,
        provider_protocol=generator.provider_protocol,
        verifier_prompt_sha256=text_sha256("test-fixture-verifier"),
        report_prompt_sha256=generator.prompt_template_sha256,
        report_schema_sha256=ABLATION_REPORT_SCHEMA_SHA256,
        report_length_limit_chars=5_000,
        dataset_fingerprint_sha256=public["dataset_fingerprint_sha256"],
        question_input_sha256={
            question_id: frozen.input_sha256
            for question_id, frozen in inputs.items()
        },
        budget=AblationBudget(
            max_verifier_calls=100,
            max_report_calls=2,
            max_model_calls=0,
            max_provider_api_calls=0,
            max_input_tokens=0,
            max_output_tokens=0,
            max_reference_cost_cny=0,
            max_duration_seconds=60,
            max_context_characters=40_000,
        ),
    )


def _run(
    *,
    generator: object | None = None,
    verifier: DeterministicFixtureAblationVerifier | None = None,
    existing_rows: list[object] | None = None,
) -> tuple[object, DeterministicFixtureAblationVerifier, object]:
    inputs = _inputs()
    report_generator = generator or DeterministicFixtureAblationReportGenerator()
    semantic = verifier or DeterministicFixtureAblationVerifier()
    manifest = _manifest(inputs, report_generator)
    archive = asyncio.run(
        VerificationAblationRunner(
            report_generator=report_generator,
            verifier_backend=semantic,
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:sa03",
            existing_rows=existing_rows,
        )
    )
    return archive, semantic, report_generator


def test_synthetic_inputs_revalidate_deterministic_contract() -> None:
    inputs = _inputs()
    assert len(inputs) == 1
    assert all(item.deterministic_validation.outcome == "succeeded" for item in inputs.values())
    assert all(not item.semantic_verification_results_included for item in inputs.values())


def test_v_on_and_v_off_keep_experiment_state_separate() -> None:
    inputs = _inputs()
    first = next(iter(inputs.values()))
    claim_ids = [claim.claim_id for claim in first.claims]
    outcomes = {
        claim_ids[0]: SemanticVerificationDraft(
            status="unsupported",
            reason="Fixture marks one Claim unsupported.",
            recommended_action="remove",
        ),
        claim_ids[1]: SemanticVerificationDraft(
            status="partially_supported",
            reason="Fixture requires bounded wording.",
            recommended_action="weaken",
        ),
    }
    archive, verifier, generator = _run(
        verifier=DeterministicFixtureAblationVerifier(outcomes),
    )
    rows = {(row.result.question_id, row.result.variant): row for row in archive.rows}
    on = rows[(first.question_id, "V-on")]
    off = rows[(first.question_id, "V-off")]
    assert on.result.frozen_input_sha256 == off.result.frozen_input_sha256
    assert on.result.evidence_identity_sha256 == off.result.evidence_identity_sha256
    assert on.result.configuration_sha256 == off.result.configuration_sha256
    assert on.result.status == "succeeded"
    assert off.result.status == "succeeded"
    assert on.result.verifier_calls == len(first.claims)
    assert off.result.verifier_calls == 0
    assert verifier.calls == sorted(claim_ids)
    assert not off.verifications
    assert {item.status for item in off.dispositions} == {"unverified"}
    assert "[UNVERIFIED]" in off.report
    assert claim_ids[0] not in [item.claim_id for item in on.dispositions if item.included]
    assert generator.prompt_template_sha256 == generator.prompt_template_sha256


def test_v_off_unverified_is_not_a_formal_verification_status() -> None:
    with pytest.raises(ValidationError):
        Verification.model_validate(
            {
                "verification_id": "verification:test:unverified",
                "claim_id": "claim:test",
                "status": "unverified",
                "checked_evidence_ids": [],
                "reason": "not a production status",
                "recommended_action": "keep",
                "verifier": "fixture",
                "verified_at": "2026-09-21T00:00:00+00:00",
            }
        )


def test_prepared_status_mapping_and_schema_hash_are_frozen() -> None:
    inputs = _inputs()
    frozen = next(iter(inputs.values()))
    dispositions = tuple(
        AblationClaimDisposition(
            claim_id=claim.claim_id,
            status="unverified",
            included=True,
            marker="[UNVERIFIED]",
        )
        for claim in frozen.claims
    )
    prepared = prepare_variant_input(
        frozen=frozen,
        variant="V-off",
        dispositions=dispositions,
        max_context_characters=40_000,
    )
    payload = json.loads(prepared.prepared_context)
    assert {item["prepared_semantic_status"] for item in payload["claims"]} == {
        "unverified"
    }
    assert canonical_sha256(
        AblationReportDraft.model_json_schema(mode="validation")
    ) == ABLATION_REPORT_SCHEMA_SHA256


def test_input_drift_fails_before_verifier_or_report_call() -> None:
    inputs = _inputs()
    first_id, first = next(iter(inputs.items()))
    drifted = first.model_copy(update={"question": "drifted question"})
    generator = DeterministicFixtureAblationReportGenerator()
    verifier = DeterministicFixtureAblationVerifier()
    manifest = _manifest(inputs, generator)
    with pytest.raises(AblationError, match="content drifted"):
        asyncio.run(
            VerificationAblationRunner(
                report_generator=generator,
                verifier_backend=verifier,
                allow_fixture=True,
            ).run(
                manifest=manifest,
                inputs={first_id: drifted},
                run_id="run:test:drift",
            )
        )
    assert verifier.calls == []


class _FailingReportGenerator(DeterministicFixtureAblationReportGenerator):
    async def generate(self, request: object) -> AblationGeneratedReport:
        del request
        raise ValueError("fixture schema failure")


class _UnknownUsageReportGenerator(DeterministicFixtureAblationReportGenerator):
    async def generate(self, request: object) -> AblationGeneratedReport:
        return AblationGeneratedReport(report="raw fixture report", status="succeeded")


def test_failures_and_unknown_usage_remain_visible() -> None:
    with pytest.raises(AblationError, match="unknown cumulative usage"):
        _run(generator=_FailingReportGenerator())
    with pytest.raises(AblationError, match="unknown cumulative usage"):
        _run(generator=_UnknownUsageReportGenerator())


def test_resume_skips_existing_rows_and_private_public_boundary() -> None:
    complete, _, _ = _run()
    first_row = next(row for row in complete.rows if row.result.variant == "V-on")
    resumed, verifier, _ = _run(existing_rows=[first_row])
    assert len(resumed.rows) == 2
    assert verifier.calls == []
    resumed_v_on = next(row for row in resumed.rows if row.result.variant == "V-on")
    assert resumed_v_on.result.report_sha256 == first_row.result.report_sha256
    public = public_payload(resumed)
    serialized = json.dumps(public, ensure_ascii=False, sort_keys=True)
    assert "Fixture ablation report" not in serialized
    assert "verifications" not in serialized
    assert "raw_answers_stored_publicly" in serialized
    with pytest.raises(AblationError, match="below agent"):
        require_private_path(ROOT / "evaluation" / "reports" / "unsafe.json")


def test_cached_verifications_are_reused_without_backend_calls() -> None:
    complete, _, _ = _run()
    cached = {
        row.result.question_id: row.verifications
        for row in complete.rows
        if row.result.variant == "V-on"
    }
    inputs = _inputs()
    generator = DeterministicFixtureAblationReportGenerator()
    verifier = DeterministicFixtureAblationVerifier()
    manifest = _manifest(inputs, generator)
    archive = asyncio.run(
        VerificationAblationRunner(
            report_generator=generator,
            verifier_backend=verifier,
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:cached-verifications",
            existing_verifications=cached,
        )
    )
    assert verifier.calls == []
    assert all(
        row.result.verifier_attempted_calls == 0
        for row in archive.rows
        if row.result.variant == "V-on"
    )


def test_attempt_intents_are_recorded_before_condition_results() -> None:
    inputs = _inputs()
    generator = DeterministicFixtureAblationReportGenerator()
    verifier = DeterministicFixtureAblationVerifier()
    manifest = _manifest(inputs, generator)
    seen: list[object] = []
    archive = asyncio.run(
        VerificationAblationRunner(
            report_generator=generator,
            verifier_backend=verifier,
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:attempt-ledger",
            on_attempt=seen.append,
        )
    )
    expected = len(next(iter(inputs.values())).claims) + 2
    assert len(archive.attempts) == expected
    assert len(seen) >= expected
    assert {attempt.operation for attempt in archive.attempts} == {"verifier", "report"}
    assert all(attempt.state == "succeeded" for attempt in archive.attempts)
    assert all(attempt.request_identity_sha256 for attempt in archive.attempts)


def test_usage_aggregation_preserves_known_cost_subtotals_with_unknown_attempts() -> None:
    complete, _, _ = _run()
    source = next(row for row in complete.rows if row.result.variant == "V-on")
    known = source.model_copy(
        update={
            "result": source.result.model_copy(
                update={
                    "verifier_reference_cost_cny": 0.25,
                    "verifier_reserved_reference_cost_cny": 0.30,
                    "reference_cost_cny": 0.15,
                }
            )
        }
    )
    unknown = source.model_copy(
        update={
            "result": source.result.model_copy(
                update={
                    "verifier_reference_cost_cny": None,
                    "verifier_reserved_reference_cost_cny": None,
                    "reference_cost_cny": None,
                    "unknown_attempts": 2,
                    "unknown_reserved_reference_cost_cny": 0.10,
                }
            )
        }
    )

    usage = VerificationAblationRunner._usage_for_variant([known, unknown])

    assert usage.verifier_reference_cost_cny == pytest.approx(0.25)
    assert usage.verifier_reserved_reference_cost_cny == pytest.approx(0.30)
    assert usage.reference_cost_cny == pytest.approx(0.15)
    assert usage.unknown_attempts == 2
    assert usage.unknown_reserved_reference_cost_cny == pytest.approx(0.10)

    unknown_usage = usage.model_copy(
        update={
            "verifier_reference_cost_cny": None,
            "verifier_reserved_reference_cost_cny": None,
            "reference_cost_cny": None,
            "unknown_attempts": 1,
            "unknown_reserved_reference_cost_cny": 0.05,
        }
    )
    combined = VerificationAblationRunner._combined_usage(
        {"V-on": usage, "V-off": unknown_usage}
    )
    assert combined.verifier_reference_cost_cny == pytest.approx(0.25)
    assert combined.verifier_reserved_reference_cost_cny == pytest.approx(0.30)
    assert combined.reference_cost_cny == pytest.approx(0.15)
    assert combined.unknown_attempts == 3
    assert combined.unknown_reserved_reference_cost_cny == pytest.approx(0.15)


class _MeteredInvalidVerifier:
    verifier_kind: VerifierKind = "fixture"
    profile_id: str | None = None

    def __init__(self) -> None:
        self.records: list[object] = []
        self.call_counter = SimpleNamespace(
            attempted_calls=0,
            reserved_reference_cost_cny=0.0,
            actual_reference_cost_cny=0.0,
        )

    async def verify(
        self,
        *,
        claim: Claim,
        evidence: list[Evidence],
    ) -> SemanticVerificationDraft:
        del evidence
        self.call_counter.attempted_calls += 1
        self.call_counter.reserved_reference_cost_cny += 0.06
        self.call_counter.actual_reference_cost_cny += 0.05
        self.records.append(
            SimpleNamespace(
                claim_id=claim.claim_id,
                usage=SimpleNamespace(input_tokens=20, output_tokens=10),
                duration_seconds=0.1,
                reference_cost_cny=0.05,
            )
        )
        raise ValueError("fixture returned an invalid verification draft")


def test_verifier_known_usage_is_failed_when_business_output_is_invalid() -> None:
    inputs = _inputs()
    generator = DeterministicFixtureAblationReportGenerator()
    verifier = _MeteredInvalidVerifier()
    manifest = _manifest(inputs, generator)
    seen_attempts: list[object] = []

    archive = asyncio.run(
        VerificationAblationRunner(
            report_generator=generator,
            verifier_backend=verifier,
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:invalid-verifier-output",
            variant_order=("V-off", "V-on"),
            on_attempt=seen_attempts.append,
        )
    )

    attempt = next(
        item
        for item in reversed(seen_attempts)
        if item.operation == "verifier" and item.state != "intent"
    )
    failed_row = next(row for row in archive.rows if row.result.variant == "V-on")
    assert attempt.state == "failed"
    assert attempt.successful_calls == 0
    assert attempt.input_tokens == 20
    assert attempt.output_tokens == 10
    assert attempt.actual_reference_cost_cny == pytest.approx(0.05)
    assert failed_row.result.status == "failed"
    assert failed_row.result.verifier_reference_cost_cny == pytest.approx(0.05)
    assert failed_row.result.unknown_attempts == 0
