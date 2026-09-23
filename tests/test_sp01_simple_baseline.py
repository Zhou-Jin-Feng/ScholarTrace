from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest
from sa04_synthetic import make_synthetic_inputs

from scholartrace.verification_ablation import (
    AblationFrozenInput,
    DeterministicFixtureAblationReportGenerator,
    SimpleBaselineArchive,
    SimpleBaselineManifest,
    SimpleBaselineRunner,
)
from scholartrace.verification_ablation.models import AblationError, canonical_sha256
from scholartrace.verification_ablation.simple_baseline import public_payload


def _manifest(inputs: Mapping[str, AblationFrozenInput]) -> SimpleBaselineManifest:
    item = next(iter(inputs.values()))
    return SimpleBaselineManifest(
        experiment_id="test-sp01-simple-baseline",
        execution_mode="fixture",
        baseline_commit="c" * 40,
        source_tree_sha256="d" * 64,
        model_profile="fixture",
        model_identifier="fixture",
        provider_protocol="fixture",
        report_prompt_sha256=DeterministicFixtureAblationReportGenerator.prompt_template_sha256,
        report_schema_sha256=DeterministicFixtureAblationReportGenerator.report_schema_sha256,
        report_length_limit_chars=5_000,
        dataset_fingerprint_sha256="e" * 64,
        question_input_sha256={item.question_id: item.input_sha256},
        max_context_characters=40_000,
    )


def test_simple_baseline_runs_one_report_without_verifier_calls() -> None:
    inputs = make_synthetic_inputs()
    manifest = _manifest(inputs)
    archive = asyncio.run(
        SimpleBaselineRunner(
            report_generator=DeterministicFixtureAblationReportGenerator()
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="test-run",
        )
    )

    assert archive.usage.report_calls == 1
    assert archive.usage.model_calls == 0
    assert archive.usage.provider_api_calls == 0
    row = archive.rows[0]
    assert row.status == "succeeded"
    assert row.report_calls == 1
    assert row.included_claim_count == row.total_claim_count
    assert row.disposition_counts == {"unverified": row.total_claim_count}
    assert all(item.status == "unverified" for item in row.dispositions)
    assert "evidence:synthetic:01" in row.report


def test_simple_baseline_public_payload_redacts_raw_content() -> None:
    inputs = make_synthetic_inputs()
    manifest = _manifest(inputs)
    archive = asyncio.run(
        SimpleBaselineRunner(
            report_generator=DeterministicFixtureAblationReportGenerator()
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="test-redaction",
        )
    )

    payload = public_payload(archive)
    serialized = str(payload)
    assert payload["raw_reports_stored_publicly"] is False
    assert payload["claim_text_stored_publicly"] is False
    assert payload["evidence_quotes_stored_publicly"] is False
    assert "The synthetic method improves exact match" not in serialized
    assert "The synthetic method improves exact match" in archive.rows[0].report
    assert "report" not in payload["runs"][0]
    assert "dispositions" not in payload["runs"][0]


def test_simple_baseline_rejects_manifest_input_drift() -> None:
    inputs = make_synthetic_inputs()
    manifest = _manifest(inputs)
    drifted = dict(inputs)
    drifted["other-question"] = next(iter(inputs.values()))

    with pytest.raises(AblationError, match="do not match manifest"):
        asyncio.run(
            SimpleBaselineRunner(
                report_generator=DeterministicFixtureAblationReportGenerator()
            ).run(
                manifest=manifest,
                inputs=drifted,
                run_id="test-drift",
            )
        )


def test_simple_baseline_loads_legacy_non_streaming_manifest() -> None:
    inputs = make_synthetic_inputs()
    archive = asyncio.run(
        SimpleBaselineRunner(
            report_generator=DeterministicFixtureAblationReportGenerator()
        ).run(
            manifest=_manifest(inputs),
            inputs=inputs,
            run_id="test-legacy-non-streaming",
        )
    )
    payload = archive.model_dump(mode="json")
    payload["manifest"].pop("streaming")
    payload["manifest_sha256"] = canonical_sha256(payload["manifest"])

    restored = SimpleBaselineArchive.model_validate(payload)

    assert restored.manifest.streaming is False
