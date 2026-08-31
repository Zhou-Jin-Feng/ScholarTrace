"""Run one approved paid B3/B4 pilot over real DocuMind Evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import TypeAdapter

from scholartrace.contracts import ModelRoutingPolicy, Verification
from scholartrace.model_provider import (
    ApiCallBudget,
    ApiCallCounter,
    OpenAICompatibleReportGenerator,
    OpenAICompatibleSemanticVerifier,
    ProviderInferenceError,
    read_dotenv,
    resolve_provider_settings,
)
from scholartrace.scholargraph.blind_review import prepare_blind_review
from scholartrace.scholargraph.client import ScholarGraphClient
from scholartrace.scholargraph.evaluation import (
    QuestionSetHashes,
    load_question_sets,
    question_set_sha256,
)
from scholartrace.scholargraph.evaluation_input import (
    PreparedEvidenceInput,
    prepare_evidence_input,
)
from scholartrace.scholargraph.experiment import (
    B3B4ExecutionManifest,
    B3B4ExperimentRunner,
    RunBudgetEnvelope,
    evidence_input_sha256,
    public_experiment_payload,
    write_experiment_artifacts,
)
from scholartrace.scholargraph.pilot_input import load_m2_pilot_artifacts
from scholartrace.scholargraph.routing import (
    CapabilityRouter,
    ScholarGraphScope,
    ScholarGraphTool,
)
from scholartrace.search.storage import write_json, write_text
from scholartrace.verification.gate import VerificationReportGate
from scholartrace.verification.models import ReportGateResult
from scholartrace.verification.verifier import VerifierRunner

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"
SCOPES = ROOT / "evaluation" / "seeds" / "m5_scholargraph_routing_scopes.json"
M2_REPORT = ROOT / "artifacts" / "m2-evidence-live" / "evidence_report.json"
M2_PAPERS = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"
MODEL_POLICY = ROOT / "contracts" / "examples" / "m0_bundle.json"
PUBLIC_OUTPUT = ROOT / "evaluation" / "reports" / "m6_b3_b4_paid_pilot.json"
PRIVATE_DIR = ROOT / "agent" / "过程记录" / "M6-B3B4-paid-pilot"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve-paid-calls", action="store_true")
    parser.add_argument("--max-cost-cny", type=float, required=True)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--scholargraph-url", default="http://127.0.0.1:8002")
    parser.add_argument("--question-id", default="sg-eligible-02")
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--public-output", type=Path, default=PUBLIC_OUTPUT)
    parser.add_argument("--private-dir", type=Path, default=PRIVATE_DIR)
    parser.add_argument("--resume-after-report-failure", action="store_true")
    parser.add_argument(
        "--prior-attempt-reference-upper-bound-cny",
        type=float,
        default=0,
    )
    return parser.parse_args()


def _enabled_policy(model: str) -> ModelRoutingPolicy:
    payload = json.loads(MODEL_POLICY.read_text("utf-8"))["ModelRoutingPolicy"]
    for profile in payload["profiles"]:
        if profile["profile_id"] == "api-strong":
            profile.update(
                {
                    "provider": "custom-openai-compatible",
                    "model_name": model,
                    "model_version": model,
                    "context_window": 400_000,
                    "enabled": True,
                }
            )
    payload["paid_routes_enabled"] = True
    return ModelRoutingPolicy.model_validate(payload)


async def run(args: argparse.Namespace) -> dict[str, object]:
    if not args.approve_paid_calls:
        raise ValueError("paid pilot requires --approve-paid-calls; no request was made")
    if args.max_cost_cny <= 0:
        raise ValueError("approved pilot budget must be positive")
    if args.resume_after_report_failure and not (
        0 < args.prior_attempt_reference_upper_bound_cny < args.max_cost_cny
    ):
        raise ValueError("resume requires a bounded prior-attempt cost estimate")
    dotenv = read_dotenv(args.env_file)
    settings = resolve_provider_settings(
        cli_values={
            "base_url": args.base_url,
            "api_key": None,
            "timeout_seconds": None,
            "model": args.model,
        },
        environment=os.environ,
        dotenv_values=dotenv,
    )
    model = settings.model or "gpt-5.6-terra"
    if model != "gpt-5.6-terra":
        raise ValueError("paid B3/B4 pilot is frozen to gpt-5.6-terra")
    if not settings.api_key.strip():
        raise ValueError("SCHOLARTRACE_API_KEY is required; no request was made")
    if args.private_dir.exists() and not args.resume_after_report_failure:
        raise ValueError("private pilot directory already exists; refusing duplicate paid run")
    private_input = args.private_dir / "verified_input.json"
    private_run = args.private_dir / "private_run.json"
    if args.resume_after_report_failure and (
        not private_input.is_file() or private_run.exists()
    ):
        raise ValueError("resume requires verified input and no completed private run")

    all_questions = load_question_sets(ELIGIBLE, BOUNDARY)
    question = all_questions.get(args.question_id)
    if question is None or question.subset != "scholargraph_eligible_eval":
        raise ValueError("pilot question must be one frozen ScholarGraph eligible question")
    scope_payload = json.loads(SCOPES.read_text("utf-8"))["scopes"]
    all_scopes = TypeAdapter(dict[str, ScholarGraphScope]).validate_python(scope_payload)
    scope = all_scopes[args.question_id]
    artifacts = load_m2_pilot_artifacts(
        report_path=M2_REPORT,
        paper_fixture_path=M2_PAPERS,
    )

    verifier_cap = args.max_cost_cny / 2
    report_cap = (
        args.max_cost_cny - args.prior_attempt_reference_upper_bound_cny
        if args.resume_after_report_failure
        else args.max_cost_cny - verifier_cap
    )
    verifier_counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=len(artifacts.claims),
            max_input_token_upper_bound=10_000,
            max_output_tokens=500,
            max_cost_cny=verifier_cap,
        )
    )
    report_counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=2,
            max_input_token_upper_bound=30_000,
            max_output_tokens=1200,
            max_cost_cny=report_cap,
        )
    )
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as provider_http:
        verifier_backend: OpenAICompatibleSemanticVerifier | None = None
        if args.resume_after_report_failure:
            cached = json.loads(private_input.read_text("utf-8"))
            if (
                cached.get("question_id") != question.id
                or cached.get("source_report_sha256") != artifacts.source_report_sha256
                or cached.get("paper_pool_sha256") != artifacts.paper_pool_sha256
            ):
                raise ValueError("cached verified input drifted from the pilot source")
            verifications = [
                Verification.model_validate(item) for item in cached["verifications"]
            ]
            gate = ReportGateResult.model_validate(cached["report_gate"])
            allowed = frozenset(cached["allowed_evidence_ids"])
            prepared = PreparedEvidenceInput(
                evidence_context=cached["evidence_context"],
                allowed_evidence_ids=allowed,
                input_sha256=cached["input_sha256"],
                included_claim_count=sum(item.included for item in gate.dispositions),
                evidence_count=len(allowed),
            )
            if prepared.input_sha256 != evidence_input_sha256(
                prepared.evidence_context,
                prepared.allowed_evidence_ids,
            ):
                raise ValueError("cached verified Evidence input hash is invalid")
        else:
            verifier_backend = OpenAICompatibleSemanticVerifier(
                client=provider_http,
                settings=settings,
                profile_id="api-strong",
                model=model,
                protocol="responses",
                call_counter=verifier_counter,
                timeout_seconds=args.timeout_seconds,
            )
            verifications = await VerifierRunner(
                backend=verifier_backend,
                policy=_enabled_policy(model),
            ).verify(
                claims=artifacts.claims,
                evidence=artifacts.evidence,
                validations=artifacts.validation.results,
                verified_at=datetime.now(UTC),
            )
            gate = VerificationReportGate().apply(
                claims=artifacts.claims,
                verifications=verifications,
            )
            prepared = prepare_evidence_input(
                question_id=question.id,
                paper_pool_sha256=artifacts.paper_pool_sha256,
                claims=artifacts.claims,
                evidence=artifacts.evidence,
                verifications=verifications,
                report_gate=gate,
            )
            if prepared.included_claim_count < 1 or prepared.evidence_count < 1:
                raise ProviderInferenceError("semantic verification left no report-safe Evidence")
            write_json(
                private_input,
                {
                    "schema_version": "1.0",
                    "purpose": "scholartrace-m6-private-paid-pilot-input",
                    "question_id": question.id,
                    "source_report_sha256": artifacts.source_report_sha256,
                    "paper_pool_sha256": artifacts.paper_pool_sha256,
                    "validation": artifacts.validation.model_dump(mode="json"),
                    "verifications": [
                        item.model_dump(mode="json") for item in verifications
                    ],
                    "report_gate": gate.model_dump(mode="json"),
                    "evidence_context": prepared.evidence_context,
                    "allowed_evidence_ids": sorted(prepared.allowed_evidence_ids),
                    "input_sha256": prepared.input_sha256,
                },
            )
        eligible_seed = args.private_dir / "pilot_eligible.jsonl"
        boundary_seed = args.private_dir / "pilot_boundary.jsonl"
        write_text(
            eligible_seed,
            json.dumps(question.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
            + "\n",
        )
        write_text(boundary_seed, "")
        hashes = QuestionSetHashes(
            eligible_sha256=question_set_sha256(eligible_seed),
            boundary_sha256=question_set_sha256(boundary_seed),
        )

        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as graph_http:
            graph_client = ScholarGraphClient(
                client=graph_http,
                base_url=args.scholargraph_url,
                timeout_seconds=10,
                max_get_attempts=2,
            )
            live = await graph_client.live()
            ready = await graph_client.ready()
            capabilities = await graph_client.capabilities()
            if live.status != "live" or not ready.ready:
                raise RuntimeError("ScholarGraph is not ready for the paid pilot")
            graph = ScholarGraphTool(
                client=graph_client,
                capabilities=capabilities,
                router=CapabilityRouter(),
            )
            generator = OpenAICompatibleReportGenerator(
                client=provider_http,
                settings=settings,
                model_profile="api-strong",
                model=model,
                protocol="responses",
                call_counter=report_counter,
                timeout_seconds=args.timeout_seconds,
            )
            per_variant_cost_cap = report_cap / 2
            manifest = B3B4ExecutionManifest(
                experiment_id="m6-b3-b4-paid-pilot-v1",
                model_profile=generator.model_profile,
                model_identifier=generator.model_identifier,
                provider_protocol=generator.provider_protocol,
                prompt_template_sha256=generator.prompt_template_sha256,
                paper_pool_sha256=artifacts.paper_pool_sha256,
                question_input_sha256={question.id: prepared.input_sha256},
                report_length_limit=5000,
                budget=RunBudgetEnvelope(
                    max_model_calls=1,
                    max_provider_api_calls=1,
                    max_scholargraph_calls=1,
                    max_llm_input_tokens=30_000,
                    max_llm_output_tokens=1200,
                    max_cost_cny=per_variant_cost_cap,
                    max_duration_seconds=600,
                ),
                question_sets=hashes,
            )
            archive = await B3B4ExperimentRunner(
                report_generator=generator,
                scholargraph=graph,
            ).run(
                manifest=manifest,
                questions={question.id: question},
                scopes={question.id: scope},
                evidence_contexts={question.id: prepared.evidence_context},
                allowed_evidence_ids={question.id: prepared.allowed_evidence_ids},
                b3_run_id="run:m6:paid-pilot:b3",
                b4_run_id="run:m6:paid-pilot:b4",
            )

    review_path = args.private_dir / "blind_review.csv"
    mapping_path = args.private_dir / "private_mapping.json"
    write_experiment_artifacts(
        archive=archive,
        private_path=private_run,
        public_path=args.public_output,
    )
    review = prepare_blind_review(
        archive=archive,
        questions={question.id: question},
        review_path=review_path,
        mapping_path=mapping_path,
        seed=20260831,
    )
    comparison = {
        "question_count": 1,
        "eligible_count": 1,
        "boundary_count": 0,
        "quality_scores_present": False,
        "default_enable_decision": "keep_disabled_insufficient_quality_evidence",
        "limitations": [
            "A one-question pilot is not the frozen 12-question comparison.",
            "Blind scores have not been imported.",
            "The full comparison contract requires eligible and boundary coverage.",
        ],
    }
    verifier_cost = (
        sum(item.reference_cost_cny for item in verifier_backend.records)
        if verifier_backend is not None
        else None
    )
    report_cost = archive.b3_usage.reference_cost_cny + archive.b4_usage.reference_cost_cny
    total_cost = verifier_cost + report_cost if verifier_cost is not None else None
    total_cost_upper_bound = (
        total_cost
        if total_cost is not None
        else args.prior_attempt_reference_upper_bound_cny + report_cost
    )
    statuses = Counter(item.status for item in verifications)
    verifier_records = verifier_backend.records if verifier_backend is not None else []
    public = public_experiment_payload(archive)
    public.update(
        {
            "pilot_kind": "one_question_paid_b3_b4_with_real_documind_evidence",
            "quality_scope": (
                "Paid protocol, semantic-verification, cost, and blind-review pilot; "
                "one question cannot establish default-enable quality benefit."
            ),
            "approved_total_reference_cost_cny": args.max_cost_cny,
            "provider_billed_cost_cny": None,
            "provider_billing_observability": "unavailable_in_response",
            "prior_attempt_reference_upper_bound_cny": round(
                args.prior_attempt_reference_upper_bound_cny, 6
            ),
            "verifier": {
                "calls": len(verifications),
                "input_tokens": (
                    sum(item.usage.input_tokens for item in verifier_records)
                    if verifier_records
                    else None
                ),
                "output_tokens": (
                    sum(item.usage.output_tokens for item in verifier_records)
                    if verifier_records
                    else None
                ),
                "duration_seconds": (
                    round(sum(item.duration_seconds for item in verifier_records), 3)
                    if verifier_records
                    else None
                ),
                "reference_cost_cny": (
                    round(verifier_cost, 6) if verifier_cost is not None else None
                ),
                "reference_cost_upper_bound_cny": round(verifier_cap, 6),
                "usage_observability": (
                    "complete"
                    if verifier_records
                    else "not_persisted_before_first_report_failure"
                ),
                "status_counts": dict(sorted(statuses.items())),
            },
            "report_reference_cost_cny": round(report_cost, 6),
            "total_reference_cost_cny": (
                round(total_cost, 6) if total_cost is not None else None
            ),
            "total_reference_cost_upper_bound_cny": round(
                total_cost_upper_bound, 6
            ),
            "documind_source_report_sha256": artifacts.source_report_sha256,
            "paper_pool_sha256": artifacts.paper_pool_sha256,
            "deterministic_validation_outcome": artifacts.validation.outcome,
            "included_claim_count": prepared.included_claim_count,
            "evidence_count": prepared.evidence_count,
            "blind_review": review,
            "unscored_comparison": comparison,
            "raw_prompt_stored_publicly": False,
            "raw_response_stored_publicly": False,
            "passed": (
                total_cost_upper_bound <= args.max_cost_cny
                and archive.b3_usage.provider_api_calls == 1
                and archive.b4_usage.provider_api_calls == 1
                and archive.b4_usage.scholargraph_calls == 1
                and all(row.result.status == "succeeded" for row in archive.b3 + archive.b4)
            ),
        }
    )
    write_json(args.public_output, public)
    return public


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(run(args))
    except (ProviderInferenceError, RuntimeError, ValueError) as exc:
        failure = {
            "schema_version": "1.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "pilot_kind": "one_question_paid_b3_b4_with_real_documind_evidence",
            "passed": False,
            "error_code": type(exc).__name__,
            "error_message": str(exc),
            "failure_stage": (
                "after_verified_input"
                if (args.private_dir / "verified_input.json").is_file()
                else "before_verified_input"
            ),
            "raw_prompt_stored_publicly": False,
            "raw_response_stored_publicly": False,
        }
        write_json(args.public_output, failure)
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True))
        return 1
    summary = {
        "passed": result["passed"],
        "verifier_calls": result["verifier"]["calls"],
        "report_calls": (
            result["runs"]["b3"]["usage"]["provider_api_calls"]
            + result["runs"]["b4"]["usage"]["provider_api_calls"]
        ),
        "scholargraph_calls": result["runs"]["b4"]["usage"]["scholargraph_calls"],
        "total_reference_cost_cny": result["total_reference_cost_cny"],
        "total_reference_cost_upper_bound_cny": result[
            "total_reference_cost_upper_bound_cny"
        ],
        "default_enable_decision": result["unscored_comparison"][
            "default_enable_decision"
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
