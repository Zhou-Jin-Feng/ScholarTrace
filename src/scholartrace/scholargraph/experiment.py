"""Reproducible B3/B4 execution with private answers and public-safe audits."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scholartrace.contracts import Budget, BudgetLimits, Sha256, StableId
from scholartrace.scholargraph.evaluation import (
    B3B4EvaluationResult,
    EvaluationConditions,
    EvaluationQuestion,
    EvaluationStatus,
    QuestionSetHashes,
    RunVariant,
    ScholarGraphEvaluationAudit,
)
from scholartrace.scholargraph.models import QueryMethod, QueryStatus
from scholartrace.scholargraph.routing import (
    CapabilityDecision,
    ScholarGraphScope,
    ScholarGraphToolResult,
)
from scholartrace.search.storage import write_json

GenerationStatus = Literal["succeeded", "degraded", "failed", "timeout"]
ProviderProtocol = Literal["responses", "chat_completions", "fixture"]


class ExperimentError(ValueError):
    """The paired experiment is unsafe, incomplete, or not reproducible."""


class ExperimentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunBudgetEnvelope(ExperimentModel):
    """Identical per-variant limits applied to B3 and B4."""

    max_model_calls: int = Field(ge=0, le=500)
    max_provider_api_calls: int = Field(ge=0, le=500)
    max_scholargraph_calls: int = Field(ge=0, le=100)
    max_llm_input_tokens: int = Field(ge=0)
    max_llm_output_tokens: int = Field(ge=0)
    max_cost_cny: float = Field(ge=0, allow_inf_nan=False)
    max_duration_seconds: int = Field(ge=30, le=86_400)


class B3B4ExecutionManifest(ExperimentModel):
    """Frozen conditions that are stricter than the upstream comparison contract."""

    schema_version: Literal["1.0"] = "1.0"
    experiment_id: StableId
    model_profile: StableId
    model_identifier: StableId
    provider_protocol: ProviderProtocol
    prompt_template_sha256: Sha256
    paper_pool_sha256: Sha256
    question_input_sha256: dict[StableId, Sha256] = Field(min_length=1, max_length=100)
    report_length_limit: int = Field(ge=1, le=100_000)
    budget: RunBudgetEnvelope
    question_sets: QuestionSetHashes
    scholargraph_service_version: Literal["1.2.0"] = "1.2.0"
    scholargraph_corpus_manifest_sha256: Literal[
        "168671c6f9f68ed2c8017e8d3d14396a455559a6a55628a7b0c40d32a80ea36c"
    ] = "168671c6f9f68ed2c8017e8d3d14396a455559a6a55628a7b0c40d32a80ea36c"

    def comparison_conditions(self) -> EvaluationConditions:
        return EvaluationConditions(
            model_profile=self.model_profile,
            paper_pool_sha256=self.paper_pool_sha256,
            budget_limit_cny=self.budget.max_cost_cny,
            report_length_limit=self.report_length_limit,
        )

    def stable_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class ReportGenerationRequest:
    question_id: str
    subset: str
    question: str
    variant: RunVariant
    paper_pool_sha256: str
    report_length_limit: int
    evidence_context: str
    allowed_evidence_ids: frozenset[str]
    scholargraph_context: str | None


@dataclass(frozen=True, slots=True)
class GeneratedReport:
    report: str
    status: GenerationStatus
    input_tokens: int
    output_tokens: int
    model_calls: int
    provider_api_calls: int
    duration_seconds: float
    reference_cost_cny: float

    def __post_init__(self) -> None:
        integers = (
            self.input_tokens,
            self.output_tokens,
            self.model_calls,
            self.provider_api_calls,
        )
        if any(value < 0 for value in integers):
            raise ValueError("generation usage counters must be non-negative")
        if (
            not math.isfinite(self.duration_seconds)
            or not math.isfinite(self.reference_cost_cny)
            or self.duration_seconds < 0
            or self.reference_cost_cny < 0
        ):
            raise ValueError("generation duration and cost must be finite and non-negative")
        if self.status in {"succeeded", "degraded"} and not self.report.strip():
            raise ValueError("successful generation must contain a report")


class ReportGenerator(Protocol):
    """Injectable report generator; production adapters must enforce preflight limits."""

    model_profile: str
    model_identifier: str
    provider_protocol: ProviderProtocol
    prompt_template_sha256: str

    async def generate(self, request: ReportGenerationRequest) -> GeneratedReport: ...


class ScholarGraphExecutor(Protocol):
    async def execute(
        self,
        *,
        question: str,
        scope: ScholarGraphScope,
        budget: Budget,
        traceparent: str | None = None,
    ) -> ScholarGraphToolResult: ...


class PrivateEvaluationRow(ExperimentModel):
    """Raw model output. This model must only be serialized below ``agent/``."""

    result: B3B4EvaluationResult
    report: str = Field(max_length=100_000)
    model_calls: int = Field(ge=0)
    provider_api_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reference_cost_cny: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_report_hash(self) -> PrivateEvaluationRow:
        digest = hashlib.sha256(self.report.encode("utf-8")).hexdigest()
        if digest != self.result.report_sha256:
            raise ValueError("private report does not match the public report hash")
        return self


class VariantRunUsage(ExperimentModel):
    model_calls: int = Field(ge=0)
    provider_api_calls: int = Field(ge=0)
    scholargraph_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reference_cost_cny: float = Field(ge=0, allow_inf_nan=False)
    duration_seconds: float = Field(ge=0, allow_inf_nan=False)


class PrivateExperimentArchive(ExperimentModel):
    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["scholartrace-b3-b4-private-answers"] = (
        "scholartrace-b3-b4-private-answers"
    )
    manifest: B3B4ExecutionManifest
    manifest_sha256: Sha256
    b3_usage: VariantRunUsage
    b4_usage: VariantRunUsage
    b3: list[PrivateEvaluationRow]
    b4: list[PrivateEvaluationRow]

    @model_validator(mode="after")
    def validate_archive(self) -> PrivateExperimentArchive:
        if self.manifest.stable_sha256() != self.manifest_sha256:
            raise ValueError("execution manifest hash mismatch")
        conditions = self.manifest.comparison_conditions()
        for variant, rows in (("B3", self.b3), ("B4", self.b4)):
            if not rows:
                raise ValueError(f"{variant} private run is empty")
            if any(
                row.result.variant != variant or row.result.conditions != conditions
                for row in rows
            ):
                raise ValueError(f"{variant} private rows drifted from the manifest")
        if {row.result.question_id for row in self.b3} != {
            row.result.question_id for row in self.b4
        }:
            raise ValueError("B3/B4 private question coverage differs")
        return self


@dataclass(slots=True)
class _MutableUsage:
    model_calls: int = 0
    provider_api_calls: int = 0
    scholargraph_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reference_cost_cny: float = 0
    duration_seconds: float = 0

    @classmethod
    def from_frozen(cls, usage: VariantRunUsage) -> _MutableUsage:
        return cls(**usage.model_dump())

    def add_generation(self, generated: GeneratedReport) -> None:
        self.model_calls += generated.model_calls
        self.provider_api_calls += generated.provider_api_calls
        self.input_tokens += generated.input_tokens
        self.output_tokens += generated.output_tokens
        self.reference_cost_cny += generated.reference_cost_cny

    def add_existing(self, row: PrivateEvaluationRow) -> None:
        self.model_calls += row.model_calls
        self.provider_api_calls += row.provider_api_calls
        self.scholargraph_calls += int(row.result.scholargraph.called)
        self.input_tokens += row.input_tokens
        self.output_tokens += row.output_tokens
        self.reference_cost_cny += row.reference_cost_cny
        self.duration_seconds += row.result.latency_seconds

    def freeze(self) -> VariantRunUsage:
        return VariantRunUsage(
            model_calls=self.model_calls,
            provider_api_calls=self.provider_api_calls,
            scholargraph_calls=self.scholargraph_calls,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            reference_cost_cny=round(self.reference_cost_cny, 6),
            duration_seconds=round(self.duration_seconds, 6),
        )


class B3B4ExperimentRunner:
    """Run paired reports sequentially so both variants share frozen conditions."""

    def __init__(
        self,
        *,
        report_generator: ReportGenerator,
        scholargraph: ScholarGraphExecutor,
    ) -> None:
        self.report_generator = report_generator
        self.scholargraph = scholargraph

    async def run(
        self,
        *,
        manifest: B3B4ExecutionManifest,
        questions: Mapping[str, EvaluationQuestion],
        scopes: Mapping[str, ScholarGraphScope],
        evidence_contexts: Mapping[str, str],
        allowed_evidence_ids: Mapping[str, frozenset[str]],
        b3_run_id: str,
        b4_run_id: str,
        existing_b3: list[PrivateEvaluationRow] | None = None,
        existing_b4: list[PrivateEvaluationRow] | None = None,
        existing_b3_usage: VariantRunUsage | None = None,
        existing_b4_usage: VariantRunUsage | None = None,
        on_checkpoint: Callable[
            [RunVariant, PrivateEvaluationRow, VariantRunUsage, VariantRunUsage],
            None,
        ]
        | None = None,
    ) -> PrivateExperimentArchive:
        self._validate_preflight(
            manifest,
            questions,
            scopes,
            evidence_contexts,
            allowed_evidence_ids,
        )
        b3_by_question = self._index_existing(
            rows=existing_b3 or [],
            variant="B3",
            run_id=b3_run_id,
            manifest=manifest,
            questions=questions,
        )
        b4_by_question = self._index_existing(
            rows=existing_b4 or [],
            variant="B4",
            run_id=b4_run_id,
            manifest=manifest,
            questions=questions,
        )
        b3_usage = (
            _MutableUsage.from_frozen(existing_b3_usage)
            if existing_b3_usage is not None
            else _MutableUsage()
        )
        b4_usage = (
            _MutableUsage.from_frozen(existing_b4_usage)
            if existing_b4_usage is not None
            else _MutableUsage()
        )
        if existing_b3_usage is None:
            for row in b3_by_question.values():
                b3_usage.add_existing(row)
        if existing_b4_usage is None:
            for row in b4_by_question.values():
                b4_usage.add_existing(row)
        self._check_usage(manifest.budget, b3_usage)
        self._check_usage(manifest.budget, b4_usage)
        for question_id in sorted(questions):
            question = questions[question_id]
            if question_id not in b3_by_question:
                row = await self._run_one(
                    manifest=manifest,
                    question=question,
                    scope=scopes[question_id],
                    evidence_context=evidence_contexts[question_id],
                    allowed_evidence_ids=allowed_evidence_ids[question_id],
                    variant="B3",
                    run_id=b3_run_id,
                    usage=b3_usage,
                )
                b3_by_question[question_id] = row
                if on_checkpoint is not None:
                    on_checkpoint(
                        "B3",
                        row,
                        b3_usage.freeze(),
                        b4_usage.freeze(),
                    )
            if question_id not in b4_by_question:
                row = await self._run_one(
                    manifest=manifest,
                    question=question,
                    scope=scopes[question_id],
                    evidence_context=evidence_contexts[question_id],
                    allowed_evidence_ids=allowed_evidence_ids[question_id],
                    variant="B4",
                    run_id=b4_run_id,
                    usage=b4_usage,
                )
                b4_by_question[question_id] = row
                if on_checkpoint is not None:
                    on_checkpoint(
                        "B4",
                        row,
                        b3_usage.freeze(),
                        b4_usage.freeze(),
                    )
        return PrivateExperimentArchive(
            manifest=manifest,
            manifest_sha256=manifest.stable_sha256(),
            b3_usage=b3_usage.freeze(),
            b4_usage=b4_usage.freeze(),
            b3=[b3_by_question[key] for key in sorted(b3_by_question)],
            b4=[b4_by_question[key] for key in sorted(b4_by_question)],
        )

    @staticmethod
    def _index_existing(
        *,
        rows: list[PrivateEvaluationRow],
        variant: RunVariant,
        run_id: str,
        manifest: B3B4ExecutionManifest,
        questions: Mapping[str, EvaluationQuestion],
    ) -> dict[str, PrivateEvaluationRow]:
        indexed: dict[str, PrivateEvaluationRow] = {}
        conditions = manifest.comparison_conditions()
        for row in rows:
            result = row.result
            question = questions.get(result.question_id)
            if question is None:
                raise ExperimentError("existing row references an unknown question")
            if result.question_id in indexed:
                raise ExperimentError("existing rows contain a duplicate question")
            if (
                result.variant != variant
                or result.run_id != run_id
                or result.subset != question.subset
                or result.conditions != conditions
            ):
                raise ExperimentError("existing row drifted from the execution manifest")
            indexed[result.question_id] = row
        return indexed

    def _validate_preflight(
        self,
        manifest: B3B4ExecutionManifest,
        questions: Mapping[str, EvaluationQuestion],
        scopes: Mapping[str, ScholarGraphScope],
        evidence_contexts: Mapping[str, str],
        allowed_evidence_ids: Mapping[str, frozenset[str]],
    ) -> None:
        identity = (
            self.report_generator.model_profile,
            self.report_generator.model_identifier,
            self.report_generator.provider_protocol,
            self.report_generator.prompt_template_sha256,
        )
        expected = (
            manifest.model_profile,
            manifest.model_identifier,
            manifest.provider_protocol,
            manifest.prompt_template_sha256,
        )
        if identity != expected:
            raise ExperimentError("report generator identity drifted from the manifest")
        if set(scopes) != set(questions):
            raise ExperimentError("routing scopes do not cover the frozen question set")
        if set(evidence_contexts) != set(questions):
            raise ExperimentError("Evidence inputs do not cover the frozen question set")
        if set(allowed_evidence_ids) != set(questions):
            raise ExperimentError("Evidence ID allowlists do not cover the question set")
        actual_input_hashes = {
            question_id: evidence_input_sha256(
                context,
                allowed_evidence_ids[question_id],
            )
            for question_id, context in evidence_contexts.items()
        }
        if actual_input_hashes != manifest.question_input_sha256:
            raise ExperimentError("Evidence inputs drifted from the execution manifest")
        eligible_count = sum(
            question.subset == "scholargraph_eligible_eval"
            for question in questions.values()
        )
        limits = manifest.budget
        if limits.max_scholargraph_calls < eligible_count:
            raise ExperimentError("B4 ScholarGraph call budget cannot cover eligible questions")

    async def _run_one(
        self,
        *,
        manifest: B3B4ExecutionManifest,
        question: EvaluationQuestion,
        scope: ScholarGraphScope,
        evidence_context: str,
        allowed_evidence_ids: frozenset[str],
        variant: RunVariant,
        run_id: str,
        usage: _MutableUsage,
    ) -> PrivateEvaluationRow:
        self._check_usage(manifest.budget, usage)
        started = time.perf_counter()
        graph_result: ScholarGraphToolResult | None = None
        graph_audit = ScholarGraphEvaluationAudit(called=False, decision="disabled")
        graph_duration = 0.0
        graph_context: str | None = None
        if variant == "B4":
            graph_result = await self.scholargraph.execute(
                question=question.question,
                scope=scope,
                budget=Budget(
                    limits=BudgetLimits(
                        max_api_calls=1,
                        max_cost_cny=0,
                        max_duration_seconds=manifest.budget.max_duration_seconds,
                    )
                ),
            )
            graph_audit = self._graph_audit(question, graph_result)
            graph_duration = graph_result.duration_seconds
            if graph_audit.called:
                usage.scholargraph_calls += 1
            if graph_result.answer and not graph_result.fallback_to_b3:
                graph_context = graph_result.answer

        request = ReportGenerationRequest(
            question_id=question.id,
            subset=question.subset,
            question=question.question,
            variant=variant,
            paper_pool_sha256=manifest.paper_pool_sha256,
            report_length_limit=manifest.report_length_limit,
            evidence_context=evidence_context,
            allowed_evidence_ids=allowed_evidence_ids,
            scholargraph_context=graph_context,
        )
        generated = await self.report_generator.generate(request)
        if len(generated.report) > manifest.report_length_limit:
            raise ExperimentError(f"{question.id}: report exceeded the frozen length limit")
        usage.add_generation(generated)
        usage.duration_seconds += time.perf_counter() - started
        self._check_usage(manifest.budget, usage)

        status: EvaluationStatus = generated.status
        if (
            variant == "B4"
            and graph_result is not None
            and graph_result.decision.action == "call"
            and (
                graph_result.fallback_to_b3
                or graph_result.query_status == QueryStatus.DEGRADED
            )
            and generated.status == "succeeded"
        ):
            status = "degraded"
        report_sha256 = hashlib.sha256(generated.report.encode("utf-8")).hexdigest()
        result = B3B4EvaluationResult(
            run_id=run_id,
            variant=variant,
            question_id=question.id,
            subset=question.subset,
            conditions=manifest.comparison_conditions(),
            status=status,
            quality_score=None,
            coverage_score=None,
            latency_seconds=round(graph_duration + generated.duration_seconds, 6),
            tokens_visible_lower_bound=generated.input_tokens + generated.output_tokens,
            report_sha256=report_sha256,
            scholargraph=graph_audit,
        )
        return PrivateEvaluationRow(
            result=result,
            report=generated.report,
            model_calls=generated.model_calls,
            provider_api_calls=generated.provider_api_calls,
            input_tokens=generated.input_tokens,
            output_tokens=generated.output_tokens,
            reference_cost_cny=generated.reference_cost_cny,
        )

    @staticmethod
    def _graph_audit(
        question: EvaluationQuestion,
        result: ScholarGraphToolResult,
    ) -> ScholarGraphEvaluationAudit:
        action = result.decision.action
        eligible = question.subset == "scholargraph_eligible_eval"
        if eligible and action != "call":
            raise ExperimentError(f"{question.id}: eligible B4 did not call ScholarGraph")
        if not eligible and action == "call":
            raise ExperimentError(f"{question.id}: boundary B4 reached ScholarGraph")
        if action == "call":
            if result.decision.method != QueryMethod.BASIC:
                raise ExperimentError(f"{question.id}: first B4 must use Basic")
            return ScholarGraphEvaluationAudit(
                called=True,
                decision="called",
                method=result.decision.method,
                status=result.query_status or QueryStatus.FAILED,
            )
        decision: Literal["skipped", "rejected"] = (
            "rejected" if action == "reject" else "skipped"
        )
        return ScholarGraphEvaluationAudit(called=False, decision=decision)

    @staticmethod
    def _check_usage(limits: RunBudgetEnvelope, usage: _MutableUsage) -> None:
        checks = (
            (usage.model_calls, limits.max_model_calls, "model call"),
            (usage.provider_api_calls, limits.max_provider_api_calls, "provider API call"),
            (usage.scholargraph_calls, limits.max_scholargraph_calls, "ScholarGraph call"),
            (usage.input_tokens, limits.max_llm_input_tokens, "input token"),
            (usage.output_tokens, limits.max_llm_output_tokens, "output token"),
        )
        for actual, maximum, label in checks:
            if actual > maximum:
                raise ExperimentError(f"per-variant {label} budget exceeded")
        if usage.reference_cost_cny > limits.max_cost_cny:
            raise ExperimentError("per-variant reference CNY budget exceeded")
        if usage.duration_seconds > limits.max_duration_seconds:
            raise ExperimentError("per-variant wall-time budget exceeded")


class DeterministicFixtureReportGenerator:
    """No-model generator used only to validate orchestration and review plumbing."""

    model_profile = "fixture-no-model"
    model_identifier = "fixture-deterministic-v1"
    provider_protocol: ProviderProtocol = "fixture"
    prompt_template = (
        "Create a bounded evidence report from the frozen paper pool and optional "
        "abstract-only ScholarGraph context. Never treat graph context as full-text Evidence."
    )
    prompt_template_sha256 = hashlib.sha256(prompt_template.encode("utf-8")).hexdigest()

    async def generate(self, request: ReportGenerationRequest) -> GeneratedReport:
        evidence_sha = hashlib.sha256(request.evidence_context.encode("utf-8")).hexdigest()
        context_sha = (
            hashlib.sha256(request.scholargraph_context.encode("utf-8")).hexdigest()
            if request.scholargraph_context is not None
            else "none"
        )
        report = (
            f"Fixture report for {request.question_id}. "
            f"Evidence input hash: {evidence_sha}. "
            f"Auxiliary context hash: {context_sha}. No quality claim."
        )
        return GeneratedReport(
            report=report,
            status="succeeded",
            input_tokens=0,
            output_tokens=0,
            model_calls=0,
            provider_api_calls=0,
            duration_seconds=0,
            reference_cost_cny=0,
        )


def evidence_input_sha256(
    evidence_context: str,
    allowed_evidence_ids: frozenset[str],
) -> str:
    payload = json.dumps(
        {
            "evidence_context": evidence_context,
            "allowed_evidence_ids": sorted(allowed_evidence_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class DeterministicFixtureScholarGraph:
    """Exercise the real capability router without network or model calls."""

    def __init__(self, *, router: object, capabilities: object) -> None:
        self.router = router
        self.capabilities = capabilities
        self.simulated_query_calls = 0

    async def execute(
        self,
        *,
        question: str,
        scope: ScholarGraphScope,
        budget: Budget,
        traceparent: str | None = None,
    ) -> ScholarGraphToolResult:
        del traceparent
        decide = getattr(self.router, "decide", None)
        if not callable(decide):
            raise TypeError("fixture ScholarGraph router has no decide method")
        decision = decide(scope=scope, capabilities=self.capabilities, budget=budget)
        if not isinstance(decision, CapabilityDecision):
            raise TypeError("fixture ScholarGraph router returned an invalid decision")
        if decision.action != "call":
            return ScholarGraphToolResult(decision=decision, fallback_to_b3=True)
        self.simulated_query_calls += 1
        return ScholarGraphToolResult(
            decision=decision,
            query_status=QueryStatus.SUCCEEDED,
            answer=f"Fixture abstract context for {hashlib.sha256(question.encode()).hexdigest()}",
            fallback_to_b3=False,
            attempts=1,
            duration_seconds=0,
            provider_duration_seconds=0,
        )


def require_private_agent_path(path: Path) -> Path:
    resolved = path.resolve()
    if "agent" not in {part.lower() for part in resolved.parts}:
        raise ExperimentError("raw B3/B4 answers must be written below agent/")
    return resolved


def public_experiment_payload(archive: PrivateExperimentArchive) -> dict[str, object]:
    """Return the complete audit surface without report text or blind mappings."""

    return {
        "schema_version": "1.0",
        "purpose": "scholartrace-b3-b4-sanitized-run-audit",
        "quality_scope": (
            "Run metadata only; quality requires a completed blind review and score import."
        ),
        "manifest": archive.manifest.model_dump(mode="json"),
        "manifest_sha256": archive.manifest_sha256,
        "runs": {
            "b3": {
                "usage": archive.b3_usage.model_dump(mode="json"),
                "results": [row.result.model_dump(mode="json") for row in archive.b3],
            },
            "b4": {
                "usage": archive.b4_usage.model_dump(mode="json"),
                "results": [row.result.model_dump(mode="json") for row in archive.b4],
            },
        },
        "raw_answers_stored": False,
        "blind_mapping_stored": False,
        "notes": [
            "Raw answers and blind mappings are restricted to the ignored agent directory.",
            "ScholarGraph context is abstract-only auxiliary material, never full-text Evidence.",
            "Fixture runs and unscored runs cannot establish B4 quality benefit.",
        ],
    }


def write_experiment_artifacts(
    *,
    archive: PrivateExperimentArchive,
    private_path: Path,
    public_path: Path,
) -> None:
    private_destination = require_private_agent_path(private_path)
    if private_destination == public_path.resolve():
        raise ExperimentError("private and public experiment outputs must differ")
    write_json(private_destination, archive.model_dump(mode="json"))
    write_json(public_path, public_experiment_payload(archive))


M6_EXPERIMENT_CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        RunBudgetEnvelope,
        B3B4ExecutionManifest,
        VariantRunUsage,
    )
}
