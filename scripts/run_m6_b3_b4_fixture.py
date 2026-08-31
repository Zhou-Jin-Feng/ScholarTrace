"""Run the zero-cost B3/B4 execution and blind-review infrastructure smoke."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydantic import TypeAdapter

from scholartrace.scholargraph.blind_review import prepare_blind_review
from scholartrace.scholargraph.evaluation import (
    B3B4Evaluator,
    QuestionSetHashes,
    file_sha256,
    load_question_sets,
    question_set_sha256,
)
from scholartrace.scholargraph.experiment import (
    B3B4ExecutionManifest,
    B3B4ExperimentRunner,
    DeterministicFixtureReportGenerator,
    DeterministicFixtureScholarGraph,
    RunBudgetEnvelope,
    evidence_input_sha256,
    public_experiment_payload,
    write_experiment_artifacts,
)
from scholartrace.scholargraph.models import CapabilitiesResponse
from scholartrace.scholargraph.routing import CapabilityRouter, ScholarGraphScope
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"
SCOPES = ROOT / "evaluation" / "seeds" / "m5_scholargraph_routing_scopes.json"
CAPABILITIES = ROOT / "tests" / "fixtures" / "m5" / "scholargraph_capabilities.json"
PAPER_POOL = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"
PUBLIC_OUTPUT = ROOT / "evaluation" / "reports" / "m6_b3_b4_fixture_infrastructure.json"
PRIVATE_DIR = ROOT / "agent" / "过程记录" / "M6-B3B4-fixture"
PRIVATE_OUTPUT = PRIVATE_DIR / "private_run.json"
REVIEW_OUTPUT = PRIVATE_DIR / "blind_review.csv"
MAPPING_OUTPUT = PRIVATE_DIR / "private_mapping.json"


async def run_fixture() -> dict[str, object]:
    questions = load_question_sets(ELIGIBLE, BOUNDARY)
    evidence_contexts = {
        question_id: f"Fixture-only DocuMind Evidence input for {question_id}."
        for question_id in questions
    }
    allowed_evidence_ids = {
        question_id: frozenset({f"evidence:fixture:{question_id}"})
        for question_id in questions
    }
    hashes = QuestionSetHashes(
        eligible_sha256=question_set_sha256(ELIGIBLE),
        boundary_sha256=question_set_sha256(BOUNDARY),
    )
    scopes_payload = json.loads(SCOPES.read_text("utf-8"))
    scopes = TypeAdapter(dict[str, ScholarGraphScope]).validate_python(
        scopes_payload["scopes"]
    )
    capabilities = CapabilitiesResponse.model_validate_json(
        CAPABILITIES.read_text("utf-8")
    )
    generator = DeterministicFixtureReportGenerator()
    graph = DeterministicFixtureScholarGraph(
        router=CapabilityRouter(),
        capabilities=capabilities,
    )
    manifest = B3B4ExecutionManifest(
        experiment_id="m6-b3-b4-fixture-v1",
        model_profile=generator.model_profile,
        model_identifier=generator.model_identifier,
        provider_protocol=generator.provider_protocol,
        prompt_template_sha256=generator.prompt_template_sha256,
        paper_pool_sha256=file_sha256(PAPER_POOL),
        question_input_sha256={
            question_id: evidence_input_sha256(
                context,
                allowed_evidence_ids[question_id],
            )
            for question_id, context in evidence_contexts.items()
        },
        report_length_limit=2000,
        budget=RunBudgetEnvelope(
            max_model_calls=0,
            max_provider_api_calls=0,
            max_scholargraph_calls=6,
            max_llm_input_tokens=0,
            max_llm_output_tokens=0,
            max_cost_cny=0,
            max_duration_seconds=600,
        ),
        question_sets=hashes,
    )
    archive = await B3B4ExperimentRunner(
        report_generator=generator,
        scholargraph=graph,
    ).run(
        manifest=manifest,
        questions=questions,
        scopes=scopes,
        evidence_contexts=evidence_contexts,
        allowed_evidence_ids=allowed_evidence_ids,
        b3_run_id="run:m6:fixture:b3",
        b4_run_id="run:m6:fixture:b4",
    )
    write_experiment_artifacts(
        archive=archive,
        private_path=PRIVATE_OUTPUT,
        public_path=PUBLIC_OUTPUT,
    )
    review = prepare_blind_review(
        archive=archive,
        questions=questions,
        review_path=REVIEW_OUTPUT,
        mapping_path=MAPPING_OUTPUT,
        seed=20260830,
    )
    comparison = B3B4Evaluator().compare(
        questions=questions,
        b3_results=[row.result for row in archive.b3],
        b4_results=[row.result for row in archive.b4],
        question_set_hashes=hashes,
    )
    public = public_experiment_payload(archive)
    public.update(
        {
            "fixture_kind": "zero_cost_execution_and_blind_review_infrastructure",
            "paid_model_api_calls": 0,
            "external_scholargraph_calls": 0,
            "simulated_scholargraph_calls": graph.simulated_query_calls,
            "blind_review": review,
            "unscored_comparison": comparison.model_dump(mode="json"),
            "passed": (
                graph.simulated_query_calls == comparison.eligible_count
                and comparison.boundary_routing_pass
                and comparison.default_enable_decision
                == "keep_disabled_insufficient_quality_evidence"
            ),
        }
    )
    write_json(PUBLIC_OUTPUT, public)
    return public


def main() -> int:
    result = asyncio.run(run_fixture())
    summary = {
        "question_count": len(result["runs"]["b3"]["results"]),
        "simulated_scholargraph_calls": result["simulated_scholargraph_calls"],
        "paid_model_api_calls": result["paid_model_api_calls"],
        "blind_review_rows": result["blind_review"]["row_count"],
        "default_enable_decision": result["unscored_comparison"][
            "default_enable_decision"
        ],
        "passed": result["passed"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
