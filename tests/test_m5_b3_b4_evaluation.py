from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from scholartrace.scholargraph.evaluation import (
    B3B4EvaluationResult,
    B3B4Evaluator,
    EvaluationConditions,
    EvaluationError,
    QuestionSetHashes,
    ScholarGraphEvaluationAudit,
    file_sha256,
    load_question_sets,
)
from scholartrace.scholargraph.fixture_smoke import run_fixture_smoke

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"
QUESTIONS = load_question_sets(ELIGIBLE, BOUNDARY)
HASHES = QuestionSetHashes(
    eligible_sha256=file_sha256(ELIGIBLE),
    boundary_sha256=file_sha256(BOUNDARY),
)


def _conditions(**updates: object) -> EvaluationConditions:
    payload: dict[str, object] = {
        "model_profile": "local-qwen3-8b",
        "paper_pool_sha256": "a" * 64,
        "budget_limit_cny": 0,
        "report_length_limit": 2000,
    }
    payload.update(updates)
    return EvaluationConditions.model_validate(payload)


def _result(
    *,
    question_id: str,
    variant: str,
    conditions: EvaluationConditions | None = None,
    quality_score: float | None = 3,
    graph_called: bool | None = None,
) -> B3B4EvaluationResult:
    question = QUESTIONS[question_id]
    eligible = question.subset == "scholargraph_eligible_eval"
    called = (variant == "B4" and eligible) if graph_called is None else graph_called
    graph = (
        ScholarGraphEvaluationAudit(
            called=True,
            decision="called",
            method="basic",
            status="succeeded",
        )
        if called
        else ScholarGraphEvaluationAudit(
            called=False,
            decision="disabled" if variant == "B3" else "skipped",
        )
    )
    digest = hashlib.sha256(f"{variant}:{question_id}".encode()).hexdigest()
    return B3B4EvaluationResult.model_validate(
        {
            "run_id": f"run:m5:test:{variant.lower()}",
            "variant": variant,
            "question_id": question_id,
            "subset": question.subset,
            "conditions": (conditions or _conditions()).model_dump(mode="json"),
            "status": "succeeded" if called or variant == "B3" else "skipped",
            "quality_score": quality_score,
            "coverage_score": 0.75,
            "latency_seconds": 10 if variant == "B3" else 12,
            "tokens_visible_lower_bound": 100,
            "report_sha256": digest,
            "scholargraph": graph.model_dump(mode="json"),
        }
    )


def _paired(
    *, quality: bool = True
) -> tuple[list[B3B4EvaluationResult], list[B3B4EvaluationResult]]:
    b3 = [
        _result(question_id=question_id, variant="B3", quality_score=3 if quality else None)
        for question_id in QUESTIONS
    ]
    b4 = [
        _result(
            question_id=question_id,
            variant="B4",
            quality_score=(3.25 if question.subset == "scholargraph_eligible_eval" else 3)
            if quality
            else None,
        )
        for question_id, question in QUESTIONS.items()
    ]
    return b3, b4


def test_question_sets_match_upstream_frozen_hashes() -> None:
    assert len(QUESTIONS) == 12
    assert HASHES.eligible_sha256 == (
        "91d19770d3fee134036f88e0614f45b91d1a9d2642a00a5e5fae695482cb2a00"
    )
    assert HASHES.boundary_sha256 == (
        "6bc55ed002689ef165f8a6252c45dcee2f9583d05d43e55a6656f3405a0cf0fc"
    )


def test_paired_report_requires_human_review_after_positive_scored_delta() -> None:
    b3, b4 = _paired()
    report = B3B4Evaluator().compare(
        questions=QUESTIONS,
        b3_results=b3,
        b4_results=b4,
        question_set_hashes=HASHES,
    )
    assert report.question_count == 12
    assert report.eligible_count == 6
    assert report.boundary_count == 6
    assert report.eligible_quality_delta_mean == 0.25
    assert report.b4.scholargraph_calls == 6
    assert report.default_enable_decision == "human_review_required"


def test_missing_quality_keeps_scholargraph_disabled() -> None:
    b3, b4 = _paired(quality=False)
    report = B3B4Evaluator().compare(
        questions=QUESTIONS,
        b3_results=b3,
        b4_results=b4,
        question_set_hashes=HASHES,
    )
    assert report.eligible_quality_delta_mean is None
    assert report.default_enable_decision == "keep_disabled_insufficient_quality_evidence"


def test_mismatched_conditions_and_routing_fail_closed() -> None:
    b3, b4 = _paired()
    changed = copy.deepcopy(b4)
    changed[0] = changed[0].model_copy(
        update={"conditions": _conditions(report_length_limit=4000)}
    )
    with pytest.raises(EvaluationError, match="condition"):
        B3B4Evaluator().compare(
            questions=QUESTIONS,
            b3_results=b3,
            b4_results=changed,
            question_set_hashes=HASHES,
        )

    boundary_id = next(
        question_id
        for question_id, question in QUESTIONS.items()
        if question.subset == "boundary_eval"
    )
    boundary_called = [
        _result(
            question_id=item.question_id,
            variant="B4",
            graph_called=True if item.question_id == boundary_id else None,
        )
        for item in b4
    ]
    with pytest.raises(EvaluationError, match="boundary"):
        B3B4Evaluator().compare(
            questions=QUESTIONS,
            b3_results=b3,
            b4_results=boundary_called,
            question_set_hashes=HASHES,
        )


def test_zero_cost_fixture_smoke_proves_evaluator_not_quality() -> None:
    summary = run_fixture_smoke(eligible_path=ELIGIBLE, boundary_path=BOUNDARY)
    assert summary["passed"]
    assert summary["provider_calls"] == 0
    assert summary["model_provider_calls"] == 0
    assert summary["eligible_quality_delta_mean"] is None
    assert summary["default_enable_decision"] == (
        "keep_disabled_insufficient_quality_evidence"
    )
