from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from scholartrace.scholargraph.blind_review import (
    REVIEW_FIELDS,
    import_blind_scores,
    prepare_blind_review,
)
from scholartrace.scholargraph.evaluation import (
    QuestionSetHashes,
    file_sha256,
    load_question_sets,
)
from scholartrace.scholargraph.experiment import (
    B3B4ExecutionManifest,
    B3B4ExperimentRunner,
    DeterministicFixtureReportGenerator,
    DeterministicFixtureScholarGraph,
    ExperimentError,
    RunBudgetEnvelope,
    evidence_input_sha256,
    public_experiment_payload,
    write_experiment_artifacts,
)
from scholartrace.scholargraph.models import CapabilitiesResponse
from scholartrace.scholargraph.routing import CapabilityRouter, ScholarGraphScope

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"
SCOPES = ROOT / "evaluation" / "seeds" / "m5_scholargraph_routing_scopes.json"
CAPABILITIES = ROOT / "tests" / "fixtures" / "m5" / "scholargraph_capabilities.json"
PAPER_POOL = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"
QUESTIONS = load_question_sets(ELIGIBLE, BOUNDARY)
HASHES = QuestionSetHashes(
    eligible_sha256=file_sha256(ELIGIBLE),
    boundary_sha256=file_sha256(BOUNDARY),
)


def _scopes() -> dict[str, ScholarGraphScope]:
    payload = json.loads(SCOPES.read_text("utf-8"))
    return TypeAdapter(dict[str, ScholarGraphScope]).validate_python(payload["scopes"])


def _manifest(generator: DeterministicFixtureReportGenerator) -> B3B4ExecutionManifest:
    evidence_contexts = _evidence_contexts()
    allowed_ids = _allowed_evidence_ids()
    return B3B4ExecutionManifest(
        experiment_id="m6-b3-b4-test-v1",
        model_profile=generator.model_profile,
        model_identifier=generator.model_identifier,
        provider_protocol=generator.provider_protocol,
        prompt_template_sha256=generator.prompt_template_sha256,
        paper_pool_sha256=file_sha256(PAPER_POOL),
        question_input_sha256={
            question_id: evidence_input_sha256(context, allowed_ids[question_id])
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
        question_sets=HASHES,
    )


def _evidence_contexts() -> dict[str, str]:
    return {
        question_id: f"Fixture-only DocuMind Evidence input for {question_id}."
        for question_id in QUESTIONS
    }


def _allowed_evidence_ids() -> dict[str, frozenset[str]]:
    return {
        question_id: frozenset({f"evidence:fixture:{question_id}"})
        for question_id in QUESTIONS
    }


def _run_fixture() -> tuple[object, DeterministicFixtureScholarGraph]:
    generator = DeterministicFixtureReportGenerator()
    graph = DeterministicFixtureScholarGraph(
        router=CapabilityRouter(),
        capabilities=CapabilitiesResponse.model_validate_json(
            CAPABILITIES.read_text("utf-8")
        ),
    )

    async def scenario() -> object:
        return await B3B4ExperimentRunner(
            report_generator=generator,
            scholargraph=graph,
        ).run(
            manifest=_manifest(generator),
            questions=QUESTIONS,
            scopes=_scopes(),
            evidence_contexts=_evidence_contexts(),
            allowed_evidence_ids=_allowed_evidence_ids(),
            b3_run_id="run:m6:test:b3",
            b4_run_id="run:m6:test:b4",
        )

    return asyncio.run(scenario()), graph


def test_runner_calls_only_six_eligible_basic_routes() -> None:
    archive, graph = _run_fixture()
    assert graph.simulated_query_calls == 6
    assert archive.b3_usage.scholargraph_calls == 0
    assert archive.b4_usage.scholargraph_calls == 6
    assert archive.b3_usage.provider_api_calls == 0
    assert archive.b4_usage.provider_api_calls == 0
    assert all(not row.result.scholargraph.called for row in archive.b3)
    eligible = [
        row
        for row in archive.b4
        if row.result.subset == "scholargraph_eligible_eval"
    ]
    boundary = [row for row in archive.b4 if row.result.subset == "boundary_eval"]
    assert all(row.result.scholargraph.method == "basic" for row in eligible)
    assert all(row.result.scholargraph.called for row in eligible)
    assert all(
        not row.result.scholargraph.called
        and row.result.scholargraph.decision in {"skipped", "rejected"}
        for row in boundary
    )


def test_public_artifact_excludes_raw_answers_and_mapping(tmp_path: Path) -> None:
    archive, _ = _run_fixture()
    private_path = tmp_path / "agent" / "private_run.json"
    public_path = tmp_path / "public_run.json"
    write_experiment_artifacts(
        archive=archive,
        private_path=private_path,
        public_path=public_path,
    )
    private = private_path.read_text("utf-8")
    public = public_path.read_text("utf-8")
    raw_answer = archive.b4[0].report
    assert raw_answer in private
    assert raw_answer not in public
    assert "label_a_variant" not in public
    assert public_experiment_payload(archive)["raw_answers_stored"] is False


def test_raw_answers_cannot_be_written_outside_agent(tmp_path: Path) -> None:
    archive, _ = _run_fixture()
    with pytest.raises(ExperimentError, match="agent"):
        write_experiment_artifacts(
            archive=archive,
            private_path=tmp_path / "private_run.json",
            public_path=tmp_path / "public_run.json",
        )


def test_generator_identity_drift_fails_before_execution() -> None:
    generator = DeterministicFixtureReportGenerator()
    manifest = _manifest(generator)
    generator.model_identifier = "fixture-drifted-v2"
    graph = DeterministicFixtureScholarGraph(
        router=CapabilityRouter(),
        capabilities=CapabilitiesResponse.model_validate_json(
            CAPABILITIES.read_text("utf-8")
        ),
    )

    async def scenario() -> object:
        return await B3B4ExperimentRunner(
            report_generator=generator,
            scholargraph=graph,
        ).run(
            manifest=manifest,
            questions=QUESTIONS,
            scopes=_scopes(),
            evidence_contexts=_evidence_contexts(),
            allowed_evidence_ids=_allowed_evidence_ids(),
            b3_run_id="run:m6:drift:b3",
            b4_run_id="run:m6:drift:b4",
        )

    with pytest.raises(ExperimentError, match="identity"):
        asyncio.run(scenario())
    assert graph.simulated_query_calls == 0


def test_evidence_input_drift_fails_before_execution() -> None:
    generator = DeterministicFixtureReportGenerator()
    graph = DeterministicFixtureScholarGraph(
        router=CapabilityRouter(),
        capabilities=CapabilitiesResponse.model_validate_json(
            CAPABILITIES.read_text("utf-8")
        ),
    )
    contexts = _evidence_contexts()
    contexts["sg-eligible-01"] += " drifted"

    async def scenario() -> object:
        return await B3B4ExperimentRunner(
            report_generator=generator,
            scholargraph=graph,
        ).run(
            manifest=_manifest(generator),
            questions=QUESTIONS,
            scopes=_scopes(),
            evidence_contexts=contexts,
            allowed_evidence_ids=_allowed_evidence_ids(),
            b3_run_id="run:m6:evidence-drift:b3",
            b4_run_id="run:m6:evidence-drift:b4",
        )

    with pytest.raises(ExperimentError, match="Evidence inputs drifted"):
        asyncio.run(scenario())
    assert graph.simulated_query_calls == 0


def test_runner_resumes_private_rows_and_checkpoints_only_missing_work() -> None:
    complete, _ = _run_fixture()
    generator = DeterministicFixtureReportGenerator()
    graph = DeterministicFixtureScholarGraph(
        router=CapabilityRouter(),
        capabilities=CapabilitiesResponse.model_validate_json(
            CAPABILITIES.read_text("utf-8")
        ),
    )
    checkpoints: list[tuple[str, str]] = []
    missing_id = "sg-eligible-06"
    existing_b3 = [row for row in complete.b3 if row.result.question_id != missing_id]
    existing_b4 = [row for row in complete.b4 if row.result.question_id != missing_id]

    async def scenario() -> object:
        return await B3B4ExperimentRunner(
            report_generator=generator,
            scholargraph=graph,
        ).run(
            manifest=_manifest(generator),
            questions=QUESTIONS,
            scopes=_scopes(),
            evidence_contexts=_evidence_contexts(),
            allowed_evidence_ids=_allowed_evidence_ids(),
            b3_run_id="run:m6:test:b3",
            b4_run_id="run:m6:test:b4",
            existing_b3=existing_b3,
            existing_b4=existing_b4,
            on_checkpoint=lambda variant, row, _b3, _b4: checkpoints.append(
                (variant, row.result.question_id)
            ),
        )

    resumed = asyncio.run(scenario())
    assert len(resumed.b3) == 12
    assert len(resumed.b4) == 12
    assert checkpoints == [("B3", missing_id), ("B4", missing_id)]
    assert graph.simulated_query_calls == 1
    for resumed_usage, complete_usage in (
        (resumed.b3_usage, complete.b3_usage),
        (resumed.b4_usage, complete.b4_usage),
    ):
        assert resumed_usage.model_calls == complete_usage.model_calls
        assert resumed_usage.provider_api_calls == complete_usage.provider_api_calls
        assert resumed_usage.scholargraph_calls == complete_usage.scholargraph_calls
        assert resumed_usage.input_tokens == complete_usage.input_tokens
        assert resumed_usage.output_tokens == complete_usage.output_tokens
        assert resumed_usage.reference_cost_cny == complete_usage.reference_cost_cny


def test_blind_review_score_import_and_tamper_gate(tmp_path: Path) -> None:
    archive, _ = _run_fixture()
    private_dir = tmp_path / "agent" / "review"
    review_path = private_dir / "review.csv"
    mapping_path = private_dir / "mapping.json"
    summary = prepare_blind_review(
        archive=archive,
        questions=QUESTIONS,
        review_path=review_path,
        mapping_path=mapping_path,
        seed=12345,
    )
    assert summary["row_count"] == 12
    mapping = json.loads(mapping_path.read_text("utf-8"))
    by_id = {entry["blind_id"]: entry for entry in mapping["entries"]}
    with review_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        entry = by_id[row["blind_id"]]
        row["score_a"] = "4" if entry["label_a_variant"] == "B4" else "2"
        row["score_b"] = "4" if entry["label_b_variant"] == "B4" else "2"
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    comparison = import_blind_scores(
        archive=archive,
        questions=QUESTIONS,
        review_path=review_path,
        mapping_path=mapping_path,
        question_set_hashes=HASHES,
    )
    assert comparison.eligible_quality_delta_mean == 2
    assert comparison.default_enable_decision == "human_review_required"

    rows[0]["answer_a"] += " tampered"
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ExperimentError, match="immutable"):
        import_blind_scores(
            archive=archive,
            questions=QUESTIONS,
            review_path=review_path,
            mapping_path=mapping_path,
            question_set_hashes=HASHES,
        )
