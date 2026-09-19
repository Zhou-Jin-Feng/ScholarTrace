"""Run the approved full paid M6 B3/B4 comparison with resumable checkpoints."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import TypeAdapter

from scholartrace.contracts import Budget, BudgetLimits, ModelRoutingPolicy, Verification
from scholartrace.model_provider import (
    ApiCallBudget,
    ApiCallCounter,
    OpenAICompatibleReportGenerator,
    OpenAICompatibleSemanticVerifier,
    ProviderInferenceError,
    read_dotenv,
    resolve_provider_settings,
)
from scholartrace.model_provider.verifier import VerifierCallRecord
from scholartrace.scholargraph.blind_review import prepare_blind_review
from scholartrace.scholargraph.client import ScholarGraphClient
from scholartrace.scholargraph.evaluation import (
    EvaluationQuestion,
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
    PrivateEvaluationRow,
    RunBudgetEnvelope,
    VariantRunUsage,
    evidence_input_sha256,
    public_experiment_payload,
    write_experiment_artifacts,
)
from scholartrace.scholargraph.models import CapabilitiesResponse, QueryMethod
from scholartrace.scholargraph.pilot_input import (
    M2PilotArtifacts,
    load_m2_pilot_artifacts,
)
from scholartrace.scholargraph.routing import (
    CapabilityRouter,
    ScholarGraphScope,
    ScholarGraphTool,
)
from scholartrace.search.storage import write_json
from scholartrace.verification.gate import VerificationReportGate
from scholartrace.verification.verifier import VerifierRunner

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"
SCOPES = ROOT / "evaluation" / "seeds" / "m5_scholargraph_routing_scopes.json"
SOURCE_PLAN = ROOT / "evaluation" / "seeds" / "m6_b3_b4_evidence_sources.json"
COVERAGE_REPORT = ROOT / "artifacts" / "reports" / "m6_b3_b4_evidence_coverage.json"
M2_REPORT = ROOT / "artifacts" / "m2-evidence-live" / "evidence_report.json"
M2_PAPERS = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"
M6_EVIDENCE = ROOT / "artifacts" / "m6-b3-b4-evidence"
MODEL_POLICY = ROOT / "contracts" / "examples" / "m0_bundle.json"
PILOT_INPUT = ROOT / "agent" / "过程记录" / "M6-B3B4-paid-pilot" / "verified_input.json"
PUBLIC_OUTPUT = ROOT / "artifacts" / "reports" / "m6_b3_b4_paid_full.json"
PRIVATE_DIR = ROOT / "agent" / "过程记录" / "M6-B3B4-paid-full"
MAX_EVIDENCE_CONTEXT_CHARACTERS = 25_000
REPORT_SERIALIZED_INPUT_UPPER_BOUND = 40_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve-paid-calls", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--max-report-cost-cny", type=float, required=True)
    parser.add_argument("--max-verifier-cost-cny", type=float, required=True)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--scholargraph-url", default="http://127.0.0.1:8002")
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--public-output", type=Path, default=PUBLIC_OUTPUT)
    parser.add_argument("--private-dir", type=Path, default=PRIVATE_DIR)
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


def _load_packets() -> dict[str, M2PilotArtifacts]:
    packets = {
        "sg-eligible-02": load_m2_pilot_artifacts(
            report_path=M2_REPORT,
            paper_fixture_path=M2_PAPERS,
        )
    }
    for question_id in (
        "sg-eligible-01",
        "sg-eligible-03",
        "sg-eligible-04",
        "sg-eligible-05",
        "sg-eligible-06",
    ):
        question_dir = M6_EVIDENCE / question_id
        packets[question_id] = load_m2_pilot_artifacts(
            report_path=question_dir / "evidence_report.json",
            paper_fixture_path=question_dir / "paper_fixture.json",
        )
    return packets


def _preflight_frozen_inputs(
    *,
    questions: dict[str, EvaluationQuestion],
    packets: dict[str, M2PilotArtifacts],
    scopes: dict[str, ScholarGraphScope],
) -> list[Verification]:
    eligible_ids = {
        question_id
        for question_id, question in questions.items()
        if question.subset == "scholargraph_eligible_eval"
    }
    boundary_ids = set(questions) - eligible_ids
    if len(eligible_ids) != 6 or len(boundary_ids) != 6:
        raise ValueError("frozen M6 question set must contain 6 eligible and 6 boundary rows")
    if set(packets) != eligible_ids:
        raise ValueError("real Evidence packets do not cover the exact eligible question set")
    if set(scopes) != set(questions):
        raise ValueError("routing scopes do not cover the exact frozen question set")

    source_plan = json.loads(SOURCE_PLAN.read_text("utf-8"))
    planned = {str(row["question_id"]): row for row in source_plan["questions"]}
    expected_planned = eligible_ids - {"sg-eligible-02"}
    if set(planned) != expected_planned:
        raise ValueError("Evidence source plan drifted from the remaining eligible questions")

    coverage = json.loads(COVERAGE_REPORT.read_text("utf-8"))
    actual_source_plan_sha256 = hashlib.sha256(SOURCE_PLAN.read_bytes()).hexdigest()
    if (
        coverage.get("passed") is not True
        or coverage.get("question_count") != 5
        or coverage.get("source_plan_sha256") != actual_source_plan_sha256
    ):
        raise ValueError("public Evidence coverage report drifted or did not pass")
    coverage_rows = {str(row["question_id"]): row for row in coverage.get("questions", [])}
    if set(coverage_rows) != expected_planned:
        raise ValueError("public Evidence coverage rows drifted from the source plan")

    for question_id in sorted(expected_planned):
        packet = packets[question_id]
        plan_row = planned[question_id]
        coverage_row = coverage_rows[question_id]
        paper_ids = sorted(paper.canonical_paper_id for paper in packet.papers)
        if (
            plan_row.get("question") != questions[question_id].question
            or sorted(plan_row.get("paper_ids", [])) != paper_ids
            or sorted(coverage_row.get("paper_ids", [])) != paper_ids
            or coverage_row.get("paper_pool_sha256") != packet.paper_pool_sha256
            or coverage_row.get("source_report_sha256") != packet.source_report_sha256
            or coverage_row.get("claim_count") != len(packet.claims)
            or coverage_row.get("evidence_count") != len(packet.evidence)
        ):
            raise ValueError(f"{question_id}: Evidence packet drifted from its frozen audit")

    pilot = json.loads(PILOT_INPUT.read_text("utf-8"))
    pilot_packet = packets["sg-eligible-02"]
    if (
        pilot.get("question_id") != "sg-eligible-02"
        or pilot.get("source_report_sha256") != pilot_packet.source_report_sha256
        or pilot.get("paper_pool_sha256") != pilot_packet.paper_pool_sha256
    ):
        raise ValueError("paid pilot Verification input drifted from M2 Evidence")
    pilot_verifications = [
        Verification.model_validate(item) for item in pilot.get("verifications", [])
    ]
    if len({item.claim_id for item in pilot_verifications}) != len(pilot_verifications) or {
        item.claim_id for item in pilot_verifications
    } != {item.claim_id for item in pilot_packet.claims}:
        raise ValueError("paid pilot Verification coverage drifted from the Claim set")
    return pilot_verifications


def _preflight_capability_routes(
    *,
    questions: dict[str, EvaluationQuestion],
    scopes: dict[str, ScholarGraphScope],
    capabilities: CapabilitiesResponse,
) -> None:
    router = CapabilityRouter()
    for question_id, question in sorted(questions.items()):
        decision = router.decide(
            scope=scopes[question_id],
            capabilities=capabilities,
            budget=Budget(
                limits=BudgetLimits(
                    max_api_calls=1,
                    max_cost_cny=0,
                    max_duration_seconds=7200,
                )
            ),
        )
        if question.subset == "scholargraph_eligible_eval":
            if decision.action != "call" or decision.method != QueryMethod.BASIC:
                raise ValueError(f"{question_id}: eligible route is not frozen to Basic")
        elif decision.action not in {"skip", "reject"}:
            raise ValueError(f"{question_id}: boundary route would make a network call")


async def _preflight_scholargraph(
    *,
    base_url: str,
    timeout_seconds: float,
    questions: dict[str, EvaluationQuestion],
    scopes: dict[str, ScholarGraphScope],
) -> None:
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as graph_http:
        client = ScholarGraphClient(
            client=graph_http,
            base_url=base_url,
            timeout_seconds=min(timeout_seconds, 90),
            max_get_attempts=2,
        )
        live = await client.live()
        ready = await client.ready()
        capabilities = await client.capabilities()
    if live.status != "live" or not ready.ready:
        raise RuntimeError("ScholarGraph is not ready for the full paid run")
    _preflight_capability_routes(
        questions=questions,
        scopes=scopes,
        capabilities=capabilities,
    )


def _global_paper_pool_sha256(
    packets: dict[str, M2PilotArtifacts],
    question_hashes: QuestionSetHashes,
) -> str:
    payload = {
        "schema_version": "1.0",
        "eligible": {
            question_id: {
                "paper_pool_sha256": packet.paper_pool_sha256,
                "source_report_sha256": packet.source_report_sha256,
            }
            for question_id, packet in sorted(packets.items())
        },
        "boundary_input": "explicit-empty-evidence-v1",
        "source_plan_sha256": hashlib.sha256(SOURCE_PLAN.read_bytes()).hexdigest(),
        "eligible_seed_sha256": question_hashes.eligible_sha256,
        "boundary_seed_sha256": question_hashes.boundary_sha256,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _record_payload(record: VerifierCallRecord) -> dict[str, object]:
    return {
        "claim_id": record.claim_id,
        "usage": {
            "input_tokens": record.usage.input_tokens,
            "output_tokens": record.usage.output_tokens,
            "cached_input_tokens": record.usage.cached_input_tokens,
            "reasoning_output_tokens": record.usage.reasoning_output_tokens,
        },
        "duration_seconds": round(record.duration_seconds, 6),
        "reference_cost_cny": round(record.reference_cost_cny, 6),
    }


def _empty_boundary_input(
    *,
    question_id: str,
    reason_code: str,
    paper_pool_sha256: str,
) -> PreparedEvidenceInput:
    context = json.dumps(
        {
            "schema_version": "1.0",
            "purpose": "scholartrace-b3-b4-private-boundary-empty-input",
            "question_id": question_id,
            "paper_pool_sha256": paper_pool_sha256,
            "claims": [],
            "evidence": [],
            "boundary_reason_code": reason_code,
            "rules": {
                "out_of_scope_or_insufficient_only": True,
                "no_evidence_ids_are_citable": True,
                "scholargraph_must_not_be_called": True,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    allowed = frozenset[str]()
    return PreparedEvidenceInput(
        evidence_context=context,
        allowed_evidence_ids=allowed,
        input_sha256=evidence_input_sha256(context, allowed),
        included_claim_count=0,
        evidence_count=0,
    )


def _initial_verifier_progress(
    *,
    model: str,
    global_pool_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "purpose": "scholartrace-m6-private-verifier-progress",
        "model": model,
        "prompt_template_sha256": OpenAICompatibleSemanticVerifier.prompt_template_sha256,
        "global_paper_pool_sha256": global_pool_sha256,
        "counter": {
            "attempted_calls": 0,
            "reserved_reference_cost_cny": 0.0,
            "actual_reference_cost_cny": 0.0,
        },
        "active_claim_id": None,
        "records": [],
        "questions": {},
    }


def _load_verifier_progress(
    *,
    path: Path,
    model: str,
    global_pool_sha256: str,
) -> dict[str, Any]:
    if not path.exists():
        return _initial_verifier_progress(
            model=model,
            global_pool_sha256=global_pool_sha256,
        )
    progress = TypeAdapter(dict[str, Any]).validate_json(path.read_text("utf-8"))
    expected = (
        progress.get("purpose") == "scholartrace-m6-private-verifier-progress"
        and progress.get("model") == model
        and progress.get("prompt_template_sha256")
        == OpenAICompatibleSemanticVerifier.prompt_template_sha256
        and progress.get("global_paper_pool_sha256") == global_pool_sha256
    )
    if not expected:
        raise ValueError("private Verifier progress drifted from the frozen run")
    return progress


def _write_verified_input(
    *,
    path: Path,
    question_id: str,
    packet: M2PilotArtifacts,
    verifications: list[Verification],
    prepared: PreparedEvidenceInput,
    gate: object,
    usage_records: list[dict[str, object]],
    usage_observability: str,
) -> None:
    from scholartrace.verification.models import ReportGateResult

    if not isinstance(gate, ReportGateResult):
        raise TypeError("M6 verified input received an invalid report gate")
    write_json(
        path,
        {
            "schema_version": "1.0",
            "purpose": "scholartrace-m6-private-full-evaluation-input",
            "question_id": question_id,
            "source_report_sha256": packet.source_report_sha256,
            "question_paper_pool_sha256": packet.paper_pool_sha256,
            "validation": packet.validation.model_dump(mode="json"),
            "verifications": [item.model_dump(mode="json") for item in verifications],
            "report_gate": gate.model_dump(mode="json"),
            "evidence_context": prepared.evidence_context,
            "allowed_evidence_ids": sorted(prepared.allowed_evidence_ids),
            "input_sha256": prepared.input_sha256,
            "included_claim_count": prepared.included_claim_count,
            "evidence_count": prepared.evidence_count,
            "verifier_usage_records": usage_records,
            "usage_observability": usage_observability,
        },
    )


async def _prepare_inputs(
    *,
    args: argparse.Namespace,
    settings: object,
    model: str,
    packets: dict[str, M2PilotArtifacts],
    questions: dict[str, EvaluationQuestion],
    pilot_verifications: list[Verification],
    global_pool_sha256: str,
    private_dir: Path,
) -> tuple[dict[str, PreparedEvidenceInput], dict[str, Any]]:
    from scholartrace.model_provider.settings import ProviderSettings

    if not isinstance(settings, ProviderSettings):
        raise TypeError("M6 full run received invalid Provider settings")
    progress_path = private_dir / "verifier_progress.json"
    progress = _load_verifier_progress(
        path=progress_path,
        model=model,
        global_pool_sha256=global_pool_sha256,
    )
    counter_payload = progress["counter"]
    verifier_counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=61,
            max_input_token_upper_bound=10_000,
            max_output_tokens=500,
            max_cost_cny=args.max_verifier_cost_cny,
        ),
        attempted_calls=int(counter_payload["attempted_calls"]),
        reserved_reference_cost_cny=float(counter_payload["reserved_reference_cost_cny"]),
        actual_reference_cost_cny=float(
            counter_payload.get(
                "actual_reference_cost_cny",
                sum(float(item["reference_cost_cny"]) for item in progress["records"]),
            )
        ),
    )
    all_records: list[dict[str, object]] = list(progress["records"])
    prepared_inputs: dict[str, PreparedEvidenceInput] = {}

    def persist_reservation(claim_id: str, attempted: int, reserved: float) -> None:
        progress["active_claim_id"] = claim_id
        progress["counter"] = {
            "attempted_calls": attempted,
            "reserved_reference_cost_cny": round(reserved, 6),
            "actual_reference_cost_cny": round(
                verifier_counter.actual_reference_cost_cny,
                6,
            ),
        }
        write_json(progress_path, progress)

    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as provider_http:
        backend = OpenAICompatibleSemanticVerifier(
            client=provider_http,
            settings=settings,
            profile_id="api-strong",
            model=model,
            protocol="responses",
            call_counter=verifier_counter,
            timeout_seconds=args.timeout_seconds,
            on_reserve=persist_reservation,
        )
        for question_id in sorted(packets):
            packet = packets[question_id]
            if question_id == "sg-eligible-02":
                verifications = pilot_verifications
                usage_records: list[dict[str, object]] = []
                usage_observability = "reused_paid_pilot_exact_usage_unavailable"
            else:
                question_progress = progress["questions"].get(question_id, {})
                if question_progress and (
                    question_progress.get("source_report_sha256") != packet.source_report_sha256
                    or question_progress.get("question_paper_pool_sha256")
                    != packet.paper_pool_sha256
                ):
                    raise ValueError("private Verifier question progress drifted")
                existing = [
                    Verification.model_validate(item)
                    for item in question_progress.get("verifications", [])
                ]
                packet_claim_ids = {item.claim_id for item in packet.claims}

                def checkpoint(
                    rows: list[Verification],
                    *,
                    question_id: str = question_id,
                    packet: M2PilotArtifacts = packet,
                ) -> None:
                    nonlocal all_records
                    known = {str(item["claim_id"]): item for item in all_records}
                    for record in backend.records:
                        known[record.claim_id] = _record_payload(record)
                    all_records = [known[key] for key in sorted(known)]
                    progress["records"] = all_records
                    progress["active_claim_id"] = None
                    progress["counter"] = {
                        "attempted_calls": verifier_counter.attempted_calls,
                        "reserved_reference_cost_cny": round(
                            verifier_counter.reserved_reference_cost_cny,
                            6,
                        ),
                        "actual_reference_cost_cny": round(
                            verifier_counter.actual_reference_cost_cny,
                            6,
                        ),
                    }
                    progress["questions"][question_id] = {
                        "source_report_sha256": packet.source_report_sha256,
                        "question_paper_pool_sha256": packet.paper_pool_sha256,
                        "verifications": [item.model_dump(mode="json") for item in rows],
                    }
                    write_json(progress_path, progress)

                verifications = await VerifierRunner(
                    backend=backend,
                    policy=_enabled_policy(model),
                ).verify(
                    claims=packet.claims,
                    evidence=packet.evidence,
                    validations=packet.validation.results,
                    verified_at=datetime.now(UTC),
                    existing=existing,
                    on_result=checkpoint,
                )
                if {item.claim_id for item in verifications} != packet_claim_ids:
                    raise ProviderInferenceError("Verifier did not cover the exact Claim set")
                usage_records = [
                    item for item in all_records if str(item["claim_id"]) in packet_claim_ids
                ]
                usage_observability = "complete_successful_calls_with_reservation_checkpoints"

            gate = VerificationReportGate().apply(
                claims=packet.claims,
                verifications=verifications,
            )
            prepared = prepare_evidence_input(
                question_id=question_id,
                paper_pool_sha256=global_pool_sha256,
                claims=packet.claims,
                evidence=packet.evidence,
                verifications=verifications,
                report_gate=gate,
                max_context_characters=MAX_EVIDENCE_CONTEXT_CHARACTERS,
            )
            if prepared.included_claim_count < 1 or prepared.evidence_count < 1:
                raise ProviderInferenceError(
                    f"{question_id}: semantic verification left no report-safe Evidence"
                )
            prepared_inputs[question_id] = prepared
            _write_verified_input(
                path=private_dir / "verified_inputs" / f"{question_id}.json",
                question_id=question_id,
                packet=packet,
                verifications=verifications,
                prepared=prepared,
                gate=gate,
                usage_records=usage_records,
                usage_observability=usage_observability,
            )

    for question_id, question in questions.items():
        if question.subset != "boundary_eval":
            continue
        prepared_inputs[question_id] = _empty_boundary_input(
            question_id=question_id,
            reason_code=question.reason_code or "boundary",
            paper_pool_sha256=global_pool_sha256,
        )
    if set(prepared_inputs) != set(questions):
        raise ValueError("prepared Evidence inputs do not cover all 12 frozen questions")
    write_json(
        private_dir / "prepared_input_manifest.json",
        {
            "schema_version": "1.0",
            "purpose": "scholartrace-m6-private-prepared-input-manifest",
            "global_paper_pool_sha256": global_pool_sha256,
            "questions": {
                question_id: {
                    "input_sha256": prepared.input_sha256,
                    "allowed_evidence_count": len(prepared.allowed_evidence_ids),
                    "included_claim_count": prepared.included_claim_count,
                    "evidence_count": prepared.evidence_count,
                }
                for question_id, prepared in sorted(prepared_inputs.items())
            },
        },
    )
    return prepared_inputs, progress


def _zero_usage() -> VariantRunUsage:
    return VariantRunUsage(
        model_calls=0,
        provider_api_calls=0,
        scholargraph_calls=0,
        input_tokens=0,
        output_tokens=0,
        reference_cost_cny=0,
        duration_seconds=0,
    )


async def run(args: argparse.Namespace) -> dict[str, object]:
    if not args.preflight_only and not args.approve_paid_calls:
        raise ValueError("full paid run requires --approve-paid-calls; no request was made")
    if args.max_report_cost_cny != 22.5:
        raise ValueError("M6 full report budget is frozen to 22.5 CNY")
    if args.max_verifier_cost_cny != 12:
        raise ValueError("M6 full Verifier budget is frozen to 12 CNY")
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
        raise ValueError("M6 full paid run is frozen to gpt-5.6-terra")
    if not args.preflight_only and not settings.api_key.strip():
        raise ValueError("SCHOLARTRACE_API_KEY is required; no request was made")

    questions = load_question_sets(ELIGIBLE, BOUNDARY)
    question_hashes = QuestionSetHashes(
        eligible_sha256=question_set_sha256(ELIGIBLE),
        boundary_sha256=question_set_sha256(BOUNDARY),
    )
    packets = _load_packets()
    scope_payload = json.loads(SCOPES.read_text("utf-8"))["scopes"]
    scopes = TypeAdapter(dict[str, ScholarGraphScope]).validate_python(scope_payload)
    pilot_verifications = _preflight_frozen_inputs(
        questions=questions,
        packets=packets,
        scopes=scopes,
    )
    global_pool_sha256 = _global_paper_pool_sha256(packets, question_hashes)
    await _preflight_scholargraph(
        base_url=args.scholargraph_url,
        timeout_seconds=args.timeout_seconds,
        questions=questions,
        scopes=scopes,
    )
    if args.preflight_only:
        return {
            "passed": True,
            "preflight_only": True,
            "provider_api_calls": 0,
            "eligible_real_evidence_inputs": len(packets),
            "boundary_explicit_empty_inputs": sum(
                question.subset == "boundary_eval" for question in questions.values()
            ),
            "model": model,
            "global_paper_pool_sha256": global_pool_sha256,
        }

    args.private_dir.mkdir(parents=True, exist_ok=True)
    if (args.private_dir / "private_run.json").exists():
        raise ValueError("completed private full run already exists; refusing duplicate calls")
    prepared, verifier_progress = await _prepare_inputs(
        args=args,
        settings=settings,
        model=model,
        packets=packets,
        questions=questions,
        pilot_verifications=pilot_verifications,
        global_pool_sha256=global_pool_sha256,
        private_dir=args.private_dir,
    )

    report_counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=24,
            max_input_token_upper_bound=REPORT_SERIALIZED_INPUT_UPPER_BOUND,
            max_output_tokens=1200,
            max_cost_cny=args.max_report_cost_cny,
        )
    )
    progress_path = args.private_dir / "report_progress.json"
    progress: dict[str, Any] = {
        "schema_version": "1.0",
        "purpose": "scholartrace-m6-private-report-progress",
        "active_report": None,
        "counter": {
            "attempted_calls": 0,
            "reserved_reference_cost_cny": 0.0,
            "actual_reference_cost_cny": 0.0,
        },
        "b3_usage": _zero_usage().model_dump(mode="json"),
        "b4_usage": _zero_usage().model_dump(mode="json"),
        "b3": [],
        "b4": [],
    }

    async with (
        httpx.AsyncClient(trust_env=False, follow_redirects=False) as provider_http,
        httpx.AsyncClient(trust_env=False, follow_redirects=False) as graph_http,
    ):
        graph_client = ScholarGraphClient(
            client=graph_http,
            base_url=args.scholargraph_url,
            timeout_seconds=90,
            max_get_attempts=2,
        )
        live = await graph_client.live()
        ready = await graph_client.ready()
        capabilities = await graph_client.capabilities()
        if live.status != "live" or not ready.ready:
            raise RuntimeError("ScholarGraph is not ready for the full paid run")
        graph = ScholarGraphTool(
            client=graph_client,
            capabilities=capabilities,
            router=CapabilityRouter(),
        )

        def persist_report_reservation(
            question_id: str,
            variant: str,
            attempted: int,
            reserved: float,
        ) -> None:
            progress["active_report"] = {
                "question_id": question_id,
                "variant": variant,
            }
            progress["counter"] = {
                "attempted_calls": attempted,
                "reserved_reference_cost_cny": round(reserved, 6),
                "actual_reference_cost_cny": round(
                    report_counter.actual_reference_cost_cny,
                    6,
                ),
            }
            write_json(progress_path, progress)

        generator = OpenAICompatibleReportGenerator(
            client=provider_http,
            settings=settings,
            model_profile="api-strong",
            model=model,
            protocol="responses",
            call_counter=report_counter,
            timeout_seconds=args.timeout_seconds,
            on_reserve=persist_report_reservation,
        )
        manifest = B3B4ExecutionManifest(
            experiment_id="m6-b3-b4-paid-full-v1",
            model_profile=generator.model_profile,
            model_identifier=generator.model_identifier,
            provider_protocol=generator.provider_protocol,
            prompt_template_sha256=generator.prompt_template_sha256,
            paper_pool_sha256=global_pool_sha256,
            question_input_sha256={
                question_id: item.input_sha256 for question_id, item in sorted(prepared.items())
            },
            report_length_limit=5000,
            budget=RunBudgetEnvelope(
                max_model_calls=12,
                max_provider_api_calls=12,
                max_scholargraph_calls=6,
                max_llm_input_tokens=360_000,
                max_llm_output_tokens=14_400,
                max_cost_cny=args.max_report_cost_cny / 2,
                max_duration_seconds=7200,
            ),
            question_sets=question_hashes,
        )
        progress["manifest"] = manifest.model_dump(mode="json")
        progress["manifest_sha256"] = manifest.stable_sha256()
        if progress_path.exists():
            existing_progress = json.loads(progress_path.read_text("utf-8"))
            if existing_progress.get("manifest_sha256") != manifest.stable_sha256():
                raise ValueError("private report progress drifted from the frozen manifest")
            if existing_progress.get("active_report") is not None:
                raise ValueError("previous report attempt has an uncertain Provider outcome")
            progress = existing_progress
            counter_payload = progress["counter"]
            report_counter.attempted_calls = int(counter_payload["attempted_calls"])
            report_counter.reserved_reference_cost_cny = float(
                counter_payload["reserved_reference_cost_cny"]
            )
            report_counter.actual_reference_cost_cny = float(
                counter_payload.get(
                    "actual_reference_cost_cny",
                    VariantRunUsage.model_validate(progress["b3_usage"]).reference_cost_cny
                    + VariantRunUsage.model_validate(progress["b4_usage"]).reference_cost_cny,
                )
            )
        else:
            write_json(progress_path, progress)
        existing_b3 = [PrivateEvaluationRow.model_validate(item) for item in progress["b3"]]
        existing_b4 = [PrivateEvaluationRow.model_validate(item) for item in progress["b4"]]
        b3_usage = VariantRunUsage.model_validate(progress["b3_usage"])
        b4_usage = VariantRunUsage.model_validate(progress["b4_usage"])

        def checkpoint(
            variant: str,
            row: PrivateEvaluationRow,
            current_b3: VariantRunUsage,
            current_b4: VariantRunUsage,
        ) -> None:
            key = "b3" if variant == "B3" else "b4"
            rows = {item["result"]["question_id"]: item for item in progress[key]}
            rows[row.result.question_id] = row.model_dump(mode="json")
            progress[key] = [rows[item] for item in sorted(rows)]
            progress["b3_usage"] = current_b3.model_dump(mode="json")
            progress["b4_usage"] = current_b4.model_dump(mode="json")
            progress["active_report"] = None
            progress["counter"] = {
                "attempted_calls": report_counter.attempted_calls,
                "reserved_reference_cost_cny": round(
                    report_counter.reserved_reference_cost_cny,
                    6,
                ),
                "actual_reference_cost_cny": round(
                    report_counter.actual_reference_cost_cny,
                    6,
                ),
            }
            write_json(progress_path, progress)

        archive = await B3B4ExperimentRunner(
            report_generator=generator,
            scholargraph=graph,
        ).run(
            manifest=manifest,
            questions=questions,
            scopes=scopes,
            evidence_contexts={
                question_id: item.evidence_context for question_id, item in prepared.items()
            },
            allowed_evidence_ids={
                question_id: item.allowed_evidence_ids for question_id, item in prepared.items()
            },
            b3_run_id="run:m6:paid-full:b3",
            b4_run_id="run:m6:paid-full:b4",
            existing_b3=existing_b3,
            existing_b4=existing_b4,
            existing_b3_usage=b3_usage,
            existing_b4_usage=b4_usage,
            on_checkpoint=checkpoint,
        )

    private_run = args.private_dir / "private_run.json"
    write_experiment_artifacts(
        archive=archive,
        private_path=private_run,
        public_path=args.public_output,
    )
    blind_review = prepare_blind_review(
        archive=archive,
        questions=questions,
        review_path=args.private_dir / "blind_review.csv",
        mapping_path=args.private_dir / "private_mapping.json",
        seed=20260831,
    )
    verifier_records = list(verifier_progress["records"])
    verifier_cost = sum(float(item["reference_cost_cny"]) for item in verifier_records)
    verifier_input = sum(int(item["usage"]["input_tokens"]) for item in verifier_records)
    verifier_output = sum(int(item["usage"]["output_tokens"]) for item in verifier_records)
    report_cost = archive.b3_usage.reference_cost_cny + archive.b4_usage.reference_cost_cny
    public = public_experiment_payload(archive)
    public.update(
        {
            "run_kind": "full_paid_b3_b4_with_real_documind_evidence",
            "quality_scope": (
                "Full paid execution and blind-review packet; quality conclusions require "
                "human scores and score import."
            ),
            "approved_report_reference_cost_cny": args.max_report_cost_cny,
            "approved_verifier_reference_cost_cny": args.max_verifier_cost_cny,
            "provider_billed_cost_cny": None,
            "provider_billing_observability": "unavailable_in_response",
            "verifier": {
                "new_successful_calls": len(verifier_records),
                "reused_pilot_question_count": 1,
                "input_tokens": verifier_input,
                "output_tokens": verifier_output,
                "reference_cost_cny": round(verifier_cost, 6),
                "reserved_reference_cost_cny": verifier_progress["counter"][
                    "reserved_reference_cost_cny"
                ],
                "successful_call_usage_observability": "complete",
                "reused_pilot_usage_observability": "exact_usage_unavailable",
            },
            "reports": {
                "calls": archive.b3_usage.provider_api_calls + archive.b4_usage.provider_api_calls,
                "input_tokens": archive.b3_usage.input_tokens + archive.b4_usage.input_tokens,
                "output_tokens": archive.b3_usage.output_tokens + archive.b4_usage.output_tokens,
                "reference_cost_cny": round(report_cost, 6),
            },
            "total_new_reference_cost_cny": round(verifier_cost + report_cost, 6),
            "eligible_real_evidence_inputs": 6,
            "boundary_explicit_empty_inputs": 6,
            "blind_review": blind_review,
            "quality_scores_present": False,
            "default_enable_decision": "keep_disabled_pending_human_blind_review",
            "raw_prompt_stored_publicly": False,
            "raw_response_stored_publicly": False,
            "passed": (
                archive.b3_usage.provider_api_calls == 12
                and archive.b4_usage.provider_api_calls == 12
                and archive.b4_usage.scholargraph_calls == 6
                and archive.b3_usage.reference_cost_cny <= args.max_report_cost_cny / 2
                and archive.b4_usage.reference_cost_cny <= args.max_report_cost_cny / 2
                and verifier_cost <= args.max_verifier_cost_cny
                and all(row.result.status == "succeeded" for row in archive.b3 + archive.b4)
            ),
        }
    )
    write_json(args.public_output, public)
    return public


def main() -> int:
    args = parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
