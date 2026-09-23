from __future__ import annotations

import asyncio
from typing import cast

from sa04_synthetic import make_synthetic_inputs

from scholartrace.verification.verifier import SemanticVerifierBackend
from scholartrace.verification_ablation import (
    ABLATION_REPORT_SCHEMA_SHA256,
    AblationBudget,
    AblationExecutionManifest,
    DeterministicFixtureAblationReportGenerator,
    DeterministicFixtureAblationVerifier,
    VerificationAblationRunner,
)
from scholartrace.verification_ablation.models import AblationReportGenerator, text_sha256
from scholartrace.verification_ablation.stage_two_selection import should_verify


def test_selector_runner_keeps_skipped_claims_unverified() -> None:
    inputs = make_synthetic_inputs()
    generator = DeterministicFixtureAblationReportGenerator()
    manifest = AblationExecutionManifest(
        experiment_id="test:sp03-selector",
        execution_mode="fixture",
        baseline_commit="c" * 40,
        model_profile=generator.model_profile,
        model_identifier=generator.model_identifier,
        provider_protocol=generator.provider_protocol,
        verifier_prompt_sha256=text_sha256("sp03-test-verifier"),
        report_prompt_sha256=generator.prompt_template_sha256,
        report_schema_sha256=ABLATION_REPORT_SCHEMA_SHA256,
        report_length_limit_chars=5_000,
        dataset_fingerprint_sha256="d" * 64,
        question_input_sha256={
            question_id: item.input_sha256 for question_id, item in inputs.items()
        },
        budget=AblationBudget(
            max_verifier_calls=10,
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
    verifier = DeterministicFixtureAblationVerifier()
    archive = asyncio.run(
        VerificationAblationRunner(
            report_generator=cast(AblationReportGenerator, generator),
            verifier_backend=cast(SemanticVerifierBackend, verifier),
            allow_fixture=True,
            verification_selector=should_verify,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:sp03-selector",
        )
    )

    frozen = next(iter(inputs.values()))
    selected = {claim.claim_id for claim in frozen.claims if should_verify(frozen, claim)}
    on = next(row for row in archive.rows if row.result.variant == "V-on")
    dispositions = {item.claim_id: item for item in on.dispositions}
    assert archive.result_usage_by_variant["V-on"].verifier_calls == len(selected)
    assert set(verifier.calls) == selected
    assert all(
        dispositions[claim.claim_id].status == "unverified"
        for claim in frozen.claims
        if claim.claim_id not in selected
    )
    assert archive.result_usage_by_variant["V-off"].verifier_calls == 0
