from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sa04_synthetic import make_synthetic_inputs

from scholartrace.verification_ablation import (
    AblationBudget,
    AblationExecutionManifest,
    DeterministicFixtureAblationReportGenerator,
    DeterministicFixtureAblationVerifier,
    VerificationAblationRunner,
)
from scholartrace.verification_ablation.formal_checkpoint import FormalCheckpointRecorder
from scholartrace.verification_ablation.models import ablation_request_identity_sha256


def _manifest(inputs: dict[str, object]) -> AblationExecutionManifest:
    generator = DeterministicFixtureAblationReportGenerator()
    return AblationExecutionManifest(
        experiment_id="test:sa05:formal-checkpoint",
        execution_mode="fixture",
        baseline_commit="3d095a79feff257b57b041de40b7b5ed73021f9e",
        model_profile=generator.model_profile,
        model_identifier=generator.model_identifier,
        provider_protocol=generator.provider_protocol,
        verifier_prompt_sha256="a" * 64,
        report_prompt_sha256=generator.prompt_template_sha256,
        report_schema_sha256=generator.report_schema_sha256,
        report_length_limit_chars=5_000,
        dataset_fingerprint_sha256="b" * 64,
        question_input_sha256={qid: item.input_sha256 for qid, item in inputs.items()},
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


def test_formal_checkpoint_persists_intent_before_result(tmp_path: Path) -> None:
    inputs = make_synthetic_inputs()
    manifest = _manifest(inputs)
    checkpoint_root = tmp_path / "sa05-checkpoint"
    checkpoint = checkpoint_root / "formal-checkpoint.json"
    recorder = FormalCheckpointRecorder(
        path=checkpoint,
        manifest=manifest,
        run_id="run:test:formal-checkpoint",
    )
    events: list[str] = []

    def on_attempt(attempt: object) -> None:
        events.append("attempt")
        recorder.record_attempt(attempt)

    def on_row(row: object, usages: object) -> None:
        events.append("row")
        recorder.record_row(row, usages)

    generator = DeterministicFixtureAblationReportGenerator()
    verifier = DeterministicFixtureAblationVerifier()
    archive = asyncio.run(
        VerificationAblationRunner(
            report_generator=generator,
            verifier_backend=verifier,
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:formal-checkpoint",
            on_attempt=on_attempt,
            on_checkpoint=on_row,
        )
    )
    assert events[0] == "attempt"
    assert events[-1] == "row"
    restored = FormalCheckpointRecorder.load(checkpoint)
    assert len(restored.attempts) == len(archive.attempts)
    assert len(restored.rows) == len(archive.rows)
    assert restored.manifest_sha256 == manifest.stable_sha256()
    assert all(item.state == "succeeded" for item in restored.attempts)


def test_resume_reuses_request_success_and_blocks_unknown(tmp_path: Path) -> None:
    inputs = make_synthetic_inputs()
    manifest = _manifest(inputs)
    generator = DeterministicFixtureAblationReportGenerator()
    first_verifier = DeterministicFixtureAblationVerifier()
    complete = asyncio.run(
        VerificationAblationRunner(
            report_generator=generator,
            verifier_backend=first_verifier,
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:request-recovery",
        )
    )
    succeeded_verifier = next(item for item in complete.attempts if item.operation == "verifier")
    resumed_verifier = DeterministicFixtureAblationVerifier()
    resumed = asyncio.run(
        VerificationAblationRunner(
            report_generator=generator,
            verifier_backend=resumed_verifier,
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:request-recovery",
            existing_attempts=[succeeded_verifier],
        )
    )
    assert succeeded_verifier.subject_id not in resumed_verifier.calls
    assert len(resumed.attempts) >= len(complete.attempts)
    resumed_v_on = next(row for row in resumed.rows if row.result.variant == "V-on")
    assert resumed_v_on.result.verifier_calls == len(next(iter(inputs.values())).claims)

    unknown = succeeded_verifier.model_copy(
        update={
            "attempt_id": succeeded_verifier.attempt_id + ":unknown",
            "attempt_number": succeeded_verifier.attempt_number + 1,
            "previous_attempt_id": succeeded_verifier.attempt_id,
            "state": "unknown",
            "verification_result": None,
        }
    )
    blocked_verifier = DeterministicFixtureAblationVerifier()
    with pytest.raises(Exception, match="blocks automatic resend"):
        asyncio.run(
            VerificationAblationRunner(
                report_generator=generator,
                verifier_backend=blocked_verifier,
                allow_fixture=True,
            ).run(
                manifest=manifest,
                inputs=inputs,
                run_id="run:test:request-recovery",
                existing_attempts=[succeeded_verifier, unknown],
            )
        )
    assert blocked_verifier.calls == []

    known_failed = unknown.model_copy(update={"state": "failed"})
    with pytest.raises(Exception, match="blocks automatic resend"):
        asyncio.run(
            VerificationAblationRunner(
                report_generator=generator,
                verifier_backend=DeterministicFixtureAblationVerifier(),
                allow_fixture=True,
            ).run(
                manifest=manifest,
                inputs=inputs,
                run_id="run:test:request-recovery",
                existing_attempts=[succeeded_verifier, known_failed],
            )
        )


def test_resume_reuses_report_saved_before_condition_row() -> None:
    inputs = make_synthetic_inputs()
    manifest = _manifest(inputs)
    generator = DeterministicFixtureAblationReportGenerator()
    complete = asyncio.run(
        VerificationAblationRunner(
            report_generator=generator,
            verifier_backend=DeterministicFixtureAblationVerifier(),
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:report-recovery",
        )
    )

    class FailIfCalled(DeterministicFixtureAblationReportGenerator):
        async def generate(self, request: object):
            raise AssertionError(f"report request was repeated: {request}")

    recovered = asyncio.run(
        VerificationAblationRunner(
            report_generator=FailIfCalled(),
            verifier_backend=DeterministicFixtureAblationVerifier(),
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:report-recovery",
            existing_attempts=complete.attempts,
        )
    )
    assert len(recovered.rows) == 2


def test_resume_does_not_treat_failed_row_as_completed() -> None:
    inputs = make_synthetic_inputs()
    manifest = _manifest(inputs)
    generator = DeterministicFixtureAblationReportGenerator()
    complete = asyncio.run(
        VerificationAblationRunner(
            report_generator=generator,
            verifier_backend=DeterministicFixtureAblationVerifier(),
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:failed-row-recovery",
        )
    )
    failed = next(row for row in complete.rows if row.result.variant == "V-on").model_copy(
        update={
            "result": next(
                row for row in complete.rows if row.result.variant == "V-on"
            ).result.model_copy(update={"status": "failed", "error_code": "test_failure"})
        }
    )
    report_generator = DeterministicFixtureAblationReportGenerator()
    resumed = asyncio.run(
        VerificationAblationRunner(
            report_generator=report_generator,
            verifier_backend=DeterministicFixtureAblationVerifier(),
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:test:failed-row-recovery",
            existing_rows=[failed],
        )
    )
    assert len(resumed.rows) == 2
    assert all(row.result.status == "succeeded" for row in resumed.rows)


def test_budget_policy_migration_recomputes_and_validates_request_identity(
    tmp_path: Path,
) -> None:
    inputs = make_synthetic_inputs()
    source_manifest = _manifest(inputs)
    target_manifest = source_manifest.model_copy(
        update={
            "budget": source_manifest.budget.model_copy(
                update={"max_duration_seconds": 90}
            )
        }
    )
    checkpoint = tmp_path / "formal-checkpoint.json"
    recorder = FormalCheckpointRecorder(
        path=checkpoint,
        manifest=source_manifest,
        run_id="run:test:budget-migration",
    )
    archive = asyncio.run(
        VerificationAblationRunner(
            report_generator=DeterministicFixtureAblationReportGenerator(),
            verifier_backend=DeterministicFixtureAblationVerifier(),
            allow_fixture=True,
        ).run(
            manifest=source_manifest,
            inputs=inputs,
            run_id="run:test:budget-migration",
            on_attempt=recorder.record_attempt,
            on_checkpoint=recorder.record_row,
        )
    )
    assert archive.attempts

    migrated = FormalCheckpointRecorder.load(
        checkpoint,
        expected_manifest=target_manifest,
        allow_budget_policy_migration=True,
    )
    configuration_sha256 = target_manifest.configuration_sha256()
    for attempt in migrated.attempts:
        assert attempt.configuration_sha256 == configuration_sha256
        assert attempt.request_identity_sha256 == ablation_request_identity_sha256(
            run_id=attempt.run_id,
            question_id=attempt.question_id,
            variant=attempt.variant,
            operation=attempt.operation,
            subject_id=attempt.subject_id,
            attempt_number=attempt.attempt_number,
            configuration_sha256=configuration_sha256,
            frozen_input_sha256=attempt.frozen_input_sha256,
        )

    invalid = migrated.attempts[0].model_copy(
        update={"request_identity_sha256": "0" * 64}
    )
    with pytest.raises(ValueError, match="request identity"):
        migrated.record_attempt(invalid)

    legacy = migrated.attempts[0].model_copy(update={"request_identity_sha256": None})
    migrated.record_attempt(legacy)
    assert next(
        item for item in migrated.attempts if item.attempt_id == legacy.attempt_id
    ).request_identity_sha256 is None
