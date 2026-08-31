"""Fail-closed paired B3/B4 evaluation for optional ScholarGraph use."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scholartrace.contracts import Sha256, StableId
from scholartrace.scholargraph.models import QueryMethod, QueryStatus

EvaluationSubset = Literal["scholargraph_eligible_eval", "boundary_eval"]
RunVariant = Literal["B3", "B4"]
EvaluationStatus = Literal[
    "succeeded",
    "degraded",
    "failed",
    "timeout",
    "skipped",
    "rejected",
]
GraphDecision = Literal["disabled", "called", "skipped", "rejected"]


class EvaluationError(ValueError):
    """B3/B4 results are incomplete, unsafe, or incomparable."""


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EligibleQuestion(EvaluationModel):
    schema_version: Literal[1]
    id: StableId
    subset: Literal["scholargraph_eligible_eval"]
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    corpus_id: Literal["openalex-rag-abstracts-2020-2025-v1"]
    required_evidence_level: Literal["abstract"]
    allowed_methods: Annotated[list[Literal["basic"]], Field(min_length=1)]
    rationale: Annotated[str, Field(min_length=1, max_length=1000)]


class BoundaryQuestion(EvaluationModel):
    schema_version: Literal[1]
    id: StableId
    subset: Literal["boundary_eval"]
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    expected_decision: Literal["skip_or_reject"]
    reason_code: Annotated[str, Field(min_length=1, max_length=100)]
    rationale: Annotated[str, Field(min_length=1, max_length=1000)]


EvaluationQuestion = EligibleQuestion | BoundaryQuestion


class EvaluationConditions(EvaluationModel):
    model_profile: StableId
    paper_pool_sha256: Sha256
    budget_limit_cny: float = Field(ge=0, allow_inf_nan=False)
    report_length_limit: int = Field(ge=1, le=100_000)


class ScholarGraphEvaluationAudit(EvaluationModel):
    called: bool
    decision: GraphDecision
    method: QueryMethod | None = None
    status: QueryStatus | None = None

    @model_validator(mode="after")
    def validate_call_state(self) -> ScholarGraphEvaluationAudit:
        if self.called:
            if self.decision != "called" or self.method is None or self.status is None:
                raise ValueError("called ScholarGraph audit is incomplete")
        elif (
            self.decision not in {"disabled", "skipped", "rejected"}
            or self.method is not None
            or self.status is not None
        ):
            raise ValueError("non-called ScholarGraph audit is inconsistent")
        return self


class B3B4EvaluationResult(EvaluationModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: StableId
    variant: RunVariant
    question_id: StableId
    subset: EvaluationSubset
    conditions: EvaluationConditions
    status: EvaluationStatus
    quality_score: float | None = Field(default=None, ge=0, le=4, allow_inf_nan=False)
    coverage_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    latency_seconds: float = Field(ge=0, allow_inf_nan=False)
    tokens_visible_lower_bound: int = Field(ge=0)
    report_sha256: Sha256
    scholargraph: ScholarGraphEvaluationAudit


class VariantSummary(EvaluationModel):
    n: int = Field(ge=1)
    quality_n: int = Field(ge=0)
    quality_mean: float | None = Field(default=None, ge=0, le=4, allow_inf_nan=False)
    coverage_mean: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    latency_p50_seconds: float = Field(ge=0, allow_inf_nan=False)
    latency_p95_seconds: float = Field(ge=0, allow_inf_nan=False)
    failure_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    degraded_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    tokens_visible_lower_bound: int = Field(ge=0)
    scholargraph_calls: int = Field(ge=0)


class PairedResult(EvaluationModel):
    question_id: StableId
    subset: EvaluationSubset
    b3_status: EvaluationStatus
    b4_status: EvaluationStatus
    quality_delta_b4_minus_b3: float | None = Field(default=None, allow_inf_nan=False)
    latency_delta_seconds: float = Field(allow_inf_nan=False)
    scholargraph_decision: GraphDecision
    scholargraph_method: QueryMethod | None = None


class QuestionSetHashes(EvaluationModel):
    eligible_sha256: Sha256
    boundary_sha256: Sha256


class B3B4ComparisonReport(EvaluationModel):
    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["scholartrace-b3-b4-paired-comparison"] = (
        "scholartrace-b3-b4-paired-comparison"
    )
    comparability_pass: Literal[True] = True
    run_ids: dict[Literal["b3", "b4"], StableId]
    question_count: int = Field(ge=1)
    eligible_count: int = Field(ge=1)
    boundary_count: int = Field(ge=1)
    b3: VariantSummary
    b4: VariantSummary
    eligible_quality_delta_mean: float | None = Field(default=None, allow_inf_nan=False)
    boundary_routing_pass: Literal[True] = True
    paired_results: list[PairedResult]
    default_enable_decision: Literal[
        "keep_disabled_insufficient_quality_evidence",
        "keep_disabled_no_clear_benefit",
        "human_review_required",
    ]
    question_sets: QuestionSetHashes
    limitations: list[str]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def question_set_sha256(path: Path) -> str:
    """Hash JSONL question sets with stable line endings across platforms."""

    normalized = path.read_text("utf-8").replace("\n", "\r\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_question_sets(
    eligible_path: Path,
    boundary_path: Path,
) -> dict[str, EvaluationQuestion]:
    questions: dict[str, EvaluationQuestion] = {}
    for path, model in (
        (eligible_path, EligibleQuestion),
        (boundary_path, BoundaryQuestion),
    ):
        lines = path.read_text("utf-8").splitlines()
        if not lines:
            raise EvaluationError("M5 question set is empty")
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                raise EvaluationError(f"blank M5 JSONL line at {line_number}")
            try:
                question = model.model_validate_json(line)
            except ValueError as exc:
                raise EvaluationError(f"invalid M5 question at line {line_number}") from exc
            if question.id in questions:
                raise EvaluationError(f"duplicate M5 question: {question.id}")
            questions[question.id] = question
    return questions


class B3B4Evaluator:
    def compare(
        self,
        *,
        questions: Mapping[str, EvaluationQuestion],
        b3_results: list[B3B4EvaluationResult],
        b4_results: list[B3B4EvaluationResult],
        question_set_hashes: QuestionSetHashes,
    ) -> B3B4ComparisonReport:
        b3 = self._index(b3_results, "B3", questions)
        b4 = self._index(b4_results, "B4", questions)
        b3_run_id, b3_conditions = self._run_identity(b3.values(), "B3")
        b4_run_id, b4_conditions = self._run_identity(b4.values(), "B4")
        if b3_conditions != b4_conditions:
            raise EvaluationError("B3/B4 run conditions are not identical")

        pairs: list[PairedResult] = []
        eligible_deltas: list[float] = []
        for question_id in sorted(questions):
            question = questions[question_id]
            baseline = b3[question_id]
            enhanced = b4[question_id]
            if baseline.scholargraph.called or baseline.scholargraph.decision != "disabled":
                raise EvaluationError(f"{question_id}: B3 must disable ScholarGraph")
            if question.subset == "boundary_eval":
                if (
                    enhanced.scholargraph.called
                    or enhanced.scholargraph.decision not in {"skipped", "rejected"}
                ):
                    raise EvaluationError(
                        f"{question_id}: boundary B4 must skip or reject ScholarGraph"
                    )
            elif not enhanced.scholargraph.called:
                raise EvaluationError(f"{question_id}: eligible B4 did not call ScholarGraph")

            delta = None
            if (
                question.subset == "scholargraph_eligible_eval"
                and baseline.quality_score is not None
                and enhanced.quality_score is not None
            ):
                delta = round(enhanced.quality_score - baseline.quality_score, 6)
                eligible_deltas.append(delta)
            pairs.append(
                PairedResult(
                    question_id=question_id,
                    subset=question.subset,
                    b3_status=baseline.status,
                    b4_status=enhanced.status,
                    quality_delta_b4_minus_b3=delta,
                    latency_delta_seconds=round(
                        enhanced.latency_seconds - baseline.latency_seconds, 6
                    ),
                    scholargraph_decision=enhanced.scholargraph.decision,
                    scholargraph_method=enhanced.scholargraph.method,
                )
            )

        b3_summary = self._summary(b3.values())
        b4_summary = self._summary(b4.values())
        quality_delta = self._mean(eligible_deltas)
        decision: Literal[
            "keep_disabled_insufficient_quality_evidence",
            "keep_disabled_no_clear_benefit",
            "human_review_required",
        ]
        eligible_count = sum(
            question.subset == "scholargraph_eligible_eval"
            for question in questions.values()
        )
        if len(eligible_deltas) != eligible_count:
            decision = "keep_disabled_insufficient_quality_evidence"
        elif (
            quality_delta is None
            or quality_delta <= 0
            or b4_summary.failure_rate > b3_summary.failure_rate
        ):
            decision = "keep_disabled_no_clear_benefit"
        else:
            decision = "human_review_required"

        return B3B4ComparisonReport(
            run_ids={"b3": b3_run_id, "b4": b4_run_id},
            question_count=len(questions),
            eligible_count=eligible_count,
            boundary_count=len(questions) - eligible_count,
            b3=b3_summary,
            b4=b4_summary,
            eligible_quality_delta_mean=quality_delta,
            paired_results=pairs,
            default_enable_decision=decision,
            question_sets=question_set_hashes,
            limitations=[
                "Token totals are visible lower bounds, not complete usage.",
                "Automatic deltas do not replace blind review or cost acceptance.",
                (
                    "ScholarGraph answers are abstract-level auxiliary context, "
                    "not full-text Evidence."
                ),
            ],
        )

    @staticmethod
    def _index(
        results: list[B3B4EvaluationResult],
        variant: RunVariant,
        questions: Mapping[str, EvaluationQuestion],
    ) -> dict[str, B3B4EvaluationResult]:
        indexed: dict[str, B3B4EvaluationResult] = {}
        for result in results:
            if result.variant != variant:
                raise EvaluationError(f"{variant} result contains another variant")
            question = questions.get(result.question_id)
            if question is None or result.subset != question.subset:
                raise EvaluationError(f"{variant} result references an invalid question")
            if result.question_id in indexed:
                raise EvaluationError(f"duplicate {variant} result: {result.question_id}")
            indexed[result.question_id] = result
        if set(indexed) != set(questions):
            raise EvaluationError(f"{variant} question coverage is incomplete")
        return indexed

    @staticmethod
    def _run_identity(
        results: Iterable[B3B4EvaluationResult],
        variant: RunVariant,
    ) -> tuple[str, EvaluationConditions]:
        rows = list(results)
        run_ids = {item.run_id for item in rows}
        if len(run_ids) != 1:
            raise EvaluationError(f"{variant} must contain exactly one run ID")
        conditions = {
            json.dumps(
                item.conditions.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            )
            for item in rows
        }
        if len(conditions) != 1:
            raise EvaluationError(f"{variant} must use one condition set")
        return next(iter(run_ids)), rows[0].conditions

    @classmethod
    def _summary(cls, results: Iterable[B3B4EvaluationResult]) -> VariantSummary:
        rows = list(results)
        qualities = [item.quality_score for item in rows if item.quality_score is not None]
        coverages = [item.coverage_score for item in rows if item.coverage_score is not None]
        latencies = [item.latency_seconds for item in rows]
        return VariantSummary(
            n=len(rows),
            quality_n=len(qualities),
            quality_mean=cls._mean(qualities),
            coverage_mean=cls._mean(coverages),
            latency_p50_seconds=cls._percentile(latencies, 0.5),
            latency_p95_seconds=cls._percentile(latencies, 0.95),
            failure_rate=round(
                sum(item.status in {"failed", "timeout"} for item in rows) / len(rows),
                6,
            ),
            degraded_rate=round(
                sum(item.status == "degraded" for item in rows) / len(rows), 6
            ),
            tokens_visible_lower_bound=sum(
                item.tokens_visible_lower_bound for item in rows
            ),
            scholargraph_calls=sum(item.scholargraph.called for item in rows),
        )

    @staticmethod
    def _mean(values: Iterable[float]) -> float | None:
        numbers = list(values)
        return round(statistics.fmean(numbers), 6) if numbers else None

    @staticmethod
    def _percentile(values: Iterable[float], percentile: float) -> float:
        ordered = sorted(values)
        if not ordered:
            raise EvaluationError("cannot summarize an empty evaluation")
        index = max(0, math.ceil(percentile * len(ordered)) - 1)
        return round(ordered[index], 6)


M5_EVALUATION_CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        EvaluationConditions,
        ScholarGraphEvaluationAudit,
        B3B4EvaluationResult,
        B3B4ComparisonReport,
    )
}
