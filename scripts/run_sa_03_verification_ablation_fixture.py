"""Run the SA-03 V-on/V-off infrastructure fixture with zero external calls."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from scholartrace.verification.models import SemanticVerificationDraft
from scholartrace.verification_ablation import (
    AblationBudget,
    AblationExecutionManifest,
    DeterministicFixtureAblationReportGenerator,
    DeterministicFixtureAblationVerifier,
    VerificationAblationRunner,
    load_frozen_inputs,
    write_artifacts,
)
from scholartrace.verification_ablation.models import canonical_sha256, text_sha256

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = ROOT / "agent" / "verification-ablation" / "SA-02" / "frozen_inputs.json"
DEFAULT_PUBLIC_MANIFEST = (
    ROOT / "evaluation" / "seeds" / "sa_02_verification_ablation_manifest.json"
)
DEFAULT_PRIVATE = ROOT / "agent" / "verification-ablation" / "SA-03" / "fixture_archive.json"
DEFAULT_PUBLIC = ROOT / "evaluation" / "reports" / "sa_03_verification_ablation_fixture.json"


def _fixture_outcomes(inputs: dict[str, object]) -> dict[str, SemanticVerificationDraft]:
    outcomes: dict[str, SemanticVerificationDraft] = {}
    for frozen in inputs.values():
        claims = frozen.claims
        for claim in claims:
            bucket = int(claim.claim_id[-1], 16) % 10
            if bucket == 0:
                outcomes[claim.claim_id] = SemanticVerificationDraft(
                    status="unsupported",
                    reason="Deterministic fixture marks this Claim unsupported.",
                    recommended_action="remove",
                )
            elif bucket in {1, 2}:
                outcomes[claim.claim_id] = SemanticVerificationDraft(
                    status="partially_supported",
                    reason="Deterministic fixture requires bounded wording.",
                    recommended_action="weaken",
                )
    return outcomes


def _manifest(
    inputs: dict[str, object],
    public_manifest: dict[str, object],
) -> AblationExecutionManifest:
    generator = DeterministicFixtureAblationReportGenerator()
    return AblationExecutionManifest(
        experiment_id="scholartrace-sa03-verification-ablation-fixture-v1",
        execution_mode="fixture",
        baseline_commit="3d095a79feff257b57b041de40b7b5ed73021f9e",
        model_profile=generator.model_profile,
        model_identifier=generator.model_identifier,
        provider_protocol=generator.provider_protocol,
        verifier_prompt_sha256=text_sha256("scholartrace-sa03-fixture-verifier-v1"),
        report_prompt_sha256=generator.prompt_template_sha256,
        report_schema_sha256=generator.report_schema_sha256,
        report_length_limit_chars=5_000,
        dataset_fingerprint_sha256=str(public_manifest["dataset_fingerprint_sha256"]),
        question_input_sha256={
            question_id: frozen.input_sha256
            for question_id, frozen in inputs.items()
        },
        budget=AblationBudget(
            max_verifier_calls=100,
            max_report_calls=len(inputs) * 2,
            max_model_calls=0,
            max_provider_api_calls=0,
            max_input_tokens=0,
            max_output_tokens=0,
            max_reference_cost_cny=0,
            max_duration_seconds=600,
            max_context_characters=40_000,
        ),
    )


async def run(args: argparse.Namespace) -> dict[str, object]:
    inputs = load_frozen_inputs(args.inputs)
    public_manifest = json.loads(args.public_manifest.read_text(encoding="utf-8"))
    generator = DeterministicFixtureAblationReportGenerator()
    verifier = DeterministicFixtureAblationVerifier(_fixture_outcomes(inputs))
    manifest = _manifest(inputs, public_manifest)
    archive = await VerificationAblationRunner(
        report_generator=generator,
        verifier_backend=verifier,
        verifier_policy=None,
        allow_fixture=True,
    ).run(
        manifest=manifest,
        inputs=inputs,
        run_id="run:sa03:fixture",
    )
    write_artifacts(
        archive=archive,
        private_path=args.private,
        public_path=args.public,
    )
    return {
        "passed": True,
        "question_count": len(inputs),
        "row_count": len(archive.rows),
        "verifier_calls": len(verifier.calls),
        "model_calls": 0,
        "provider_api_calls": 0,
        "public_output": str(args.public),
        "quality_scope": "fixture_infrastructure_only",
        "dataset_fingerprint_sha256": manifest.dataset_fingerprint_sha256,
        "archive_sha256": canonical_sha256(archive.model_dump(mode="json")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--public-manifest", type=Path, default=DEFAULT_PUBLIC_MANIFEST)
    parser.add_argument("--private", type=Path, default=DEFAULT_PRIVATE)
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
