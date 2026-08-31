"""Zero-cost M5 evaluator smoke; it does not claim B4 quality benefit."""

from __future__ import annotations

import hashlib
from pathlib import Path

from scholartrace.scholargraph.evaluation import (
    B3B4EvaluationResult,
    B3B4Evaluator,
    EvaluationConditions,
    QuestionSetHashes,
    ScholarGraphEvaluationAudit,
    file_sha256,
    load_question_sets,
)
from scholartrace.scholargraph.models import QueryMethod, QueryStatus


def run_fixture_smoke(
    *,
    eligible_path: Path,
    boundary_path: Path,
) -> dict[str, object]:
    questions = load_question_sets(eligible_path, boundary_path)
    conditions = EvaluationConditions(
        model_profile="fixture-no-model",
        paper_pool_sha256="a" * 64,
        budget_limit_cny=0,
        report_length_limit=2000,
    )
    b3: list[B3B4EvaluationResult] = []
    b4: list[B3B4EvaluationResult] = []
    for question_id, question in sorted(questions.items()):
        b3.append(
            _result(
                run_id="run:m5:fixture:b3",
                variant="B3",
                question_id=question_id,
                subset=question.subset,
                conditions=conditions,
                latency_seconds=1,
                graph=ScholarGraphEvaluationAudit(called=False, decision="disabled"),
            )
        )
        eligible = question.subset == "scholargraph_eligible_eval"
        b4.append(
            _result(
                run_id="run:m5:fixture:b4",
                variant="B4",
                question_id=question_id,
                subset=question.subset,
                conditions=conditions,
                latency_seconds=2 if eligible else 1,
                graph=(
                    ScholarGraphEvaluationAudit(
                        called=True,
                        decision="called",
                        method=QueryMethod.BASIC,
                        status=QueryStatus.SUCCEEDED,
                    )
                    if eligible
                    else ScholarGraphEvaluationAudit(called=False, decision="skipped")
                ),
            )
        )
    report = B3B4Evaluator().compare(
        questions=questions,
        b3_results=b3,
        b4_results=b4,
        question_set_hashes=QuestionSetHashes(
            eligible_sha256=file_sha256(eligible_path),
            boundary_sha256=file_sha256(boundary_path),
        ),
    )
    return {
        "schema_version": "1.0",
        "fixture_kind": "deterministic_m5_comparability_and_routing",
        "provider_calls": 0,
        "model_provider_calls": 0,
        "question_count": report.question_count,
        "eligible_count": report.eligible_count,
        "boundary_count": report.boundary_count,
        "comparability_pass": report.comparability_pass,
        "boundary_routing_pass": report.boundary_routing_pass,
        "b4_scholargraph_calls": report.b4.scholargraph_calls,
        "eligible_quality_delta_mean": report.eligible_quality_delta_mean,
        "default_enable_decision": report.default_enable_decision,
        "passed": (
            report.comparability_pass
            and report.boundary_routing_pass
            and report.b4.scholargraph_calls == report.eligible_count
            and report.default_enable_decision
            == "keep_disabled_insufficient_quality_evidence"
        ),
        "notes": [
            "Fixture rows validate the evaluator; they are not model answers.",
            "No quality benefit is claimed without paired blind-review scores.",
            "ScholarGraph remains disabled by default.",
        ],
    }


def _result(
    *,
    run_id: str,
    variant: str,
    question_id: str,
    subset: str,
    conditions: EvaluationConditions,
    latency_seconds: float,
    graph: ScholarGraphEvaluationAudit,
) -> B3B4EvaluationResult:
    content = f"{run_id}\0{question_id}\0fixture"
    return B3B4EvaluationResult.model_validate(
        {
            "run_id": run_id,
            "variant": variant,
            "question_id": question_id,
            "subset": subset,
            "conditions": conditions.model_dump(mode="json"),
            "status": "succeeded" if graph.called else "skipped",
            "quality_score": None,
            "coverage_score": None,
            "latency_seconds": latency_seconds,
            "tokens_visible_lower_bound": 0,
            "report_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "scholargraph": graph.model_dump(mode="json"),
        }
    )
