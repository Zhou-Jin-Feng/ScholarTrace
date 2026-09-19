"""Strict public models for the M6 delivery surface."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scholartrace.contracts import BudgetUsage
from scholartrace.delivery.authorization import ExecutionAuthorization, PlanningAuthorization
from scholartrace.delivery.plans import BudgetPlan, PlanDetails, SourceScope


class DeliveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskStatus(StrEnum):
    CREATED = "created"
    WAITING_APPROVAL = "waiting_approval"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    REJECTED = "rejected"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    #: Added in the A2 increment. A task that was running when the process
    #: stopped: distinct from `failed`, because nothing is known to be wrong
    #: with it and a human may be able to continue it. Detection at startup is
    #: T14 and is not implemented yet; the state exists so the SSE contract and
    #: the frontend can already name it.
    INTERRUPTED = "interrupted"


class TaskPhase(StrEnum):
    PLAN = "plan"
    SEARCH = "search"
    EVIDENCE = "evidence"
    CITATIONS = "citations"
    VERIFICATION = "verification"
    SYNTHESIS = "synthesis"
    DONE = "done"


class DemoMode(StrEnum):
    SUCCESS = "success"
    DEGRADED = "degraded"
    PRODUCTION_UNAVAILABLE = "production_unavailable"


class ExecutionMode(StrEnum):
    """Which executor owns a task. There is no implicit fallback between them."""

    DEMO = "demo"
    REAL = "real"


class TaskCreateRequest(DeliveryModel):
    question: str = Field(min_length=3, max_length=2000)
    title: str | None = Field(default=None, min_length=3, max_length=300)
    demo_mode: DemoMode = DemoMode.SUCCESS
    #: Real execution stays fail-closed until its dependencies are approved and
    #: configured; requesting it does not make it available.
    execution_mode: ExecutionMode = ExecutionMode.DEMO


class PlanModification(DeliveryModel):
    """Complete editable content of a delivery plan; no identity or approval fields."""

    sub_questions: list[str] = Field(min_length=1, max_length=20)
    source_scope: SourceScope
    exclusions: list[str] = Field(default_factory=list, max_length=20)
    budget_plan: BudgetPlan
    details: PlanDetails | None = None

    @model_validator(mode="after")
    def validate_content(self) -> PlanModification:
        self.source_scope.validate()
        if self.details is not None:
            self.details.validate_alignment(tuple(self.sub_questions), self.source_scope)
        if any(not value.strip() for value in self.sub_questions + self.exclusions):
            raise ValueError("plan text must not be blank")
        return self


class ApprovalRequest(DeliveryModel):
    """Decision bound to a delivery plan, distinct from the workflow M3 contract."""

    action: Literal["approve", "modify", "reject"]
    modified_plan: PlanModification | None = None
    reason: str | None = Field(default=None, max_length=2000)
    plan_version: int | None = Field(default=None, ge=1)
    plan_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_authorization: ExecutionAuthorization | None = None

    @model_validator(mode="after")
    def validate_modified_plan(self) -> ApprovalRequest:
        if self.execution_authorization is not None and self.action != "approve":
            raise ValueError("execution authorization is only valid for approve")
        if (self.action == "modify") != (self.modified_plan is not None):
            raise ValueError("only modify requires modified_plan")
        return self


class PlanCostAcknowledgement(DeliveryModel):
    acknowledged_max_cny: float = Field(ge=0, allow_inf_nan=False)
    authorization: PlanningAuthorization | None = None


class ResumeRequest(DeliveryModel):
    plan_version: int = Field(ge=1)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResearchPlanView(PlanModification):
    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    plan_version: int = Field(ge=1)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    generated_by: Literal["fixture", "api-strong", "manual"]
    generated_at: str
    approval_state: Literal["waiting_approval", "approved", "rejected", "superseded"]
    supersedes_version: int | None


class ReservationUsage(DeliveryModel):
    cny: float = Field(ge=0, allow_inf_nan=False)
    api_calls: int = Field(ge=0)


class ReservedUsage(ReservationUsage):
    wall_clock_seconds: int = Field(ge=0)


class SettledUsage(ReservationUsage):
    at: str
    reason: str


class ReservationView(DeliveryModel):
    task_id: str
    plan_version: int = Field(ge=0)
    plan_digest: str
    reserved: ReservedUsage
    estimate_source: str
    reserved_at: str
    settled: SettledUsage | None
    state: Literal["open", "settled", "reconciliation_required"]
    recorded_usage: ReservationUsage
    reconciliation_required: bool
    is_actual_bill: bool


class JournalAccounting(DeliveryModel):
    measured_cny: float = Field(ge=0, allow_inf_nan=False)
    known_cny: float = Field(ge=0, allow_inf_nan=False)
    held_cny: float = Field(ge=0, allow_inf_nan=False)
    committed_cny: float = Field(ge=0, allow_inf_nan=False)
    committed_calls: int = Field(ge=0)
    committed_local_calls: int = Field(ge=0)
    committed_external_requests: int = Field(ge=0)
    uncertain_effects: int = Field(ge=0)
    completed_effects: int = Field(ge=0)
    is_actual_bill: Literal[False] = False


class BudgetReport(DeliveryModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    reservation: ReservationView | None
    measured_usage: BudgetUsage | None
    journal_accounting: JournalAccounting | None = None
    projection_state: Literal["legacy", "pending", "synced", "reconciliation_required"] = "legacy"
    committed_cny_all_tasks: float | None = None
    note: str | None = None
    is_actual_bill: bool


class TaskSummary(DeliveryModel):
    task_id: str
    thread_id: str
    title: str
    question: str
    status: TaskStatus
    phase: TaskPhase
    demo_mode: DemoMode
    #: Added in the A2 increment. Older rows migrate to `demo`, which is what
    #: they were, so the field is never ambiguous.
    execution_mode: ExecutionMode = ExecutionMode.DEMO
    created_at: str
    updated_at: str
    event_count: int = Field(ge=0)
    artifact_count: int = Field(ge=0)
    metrics: dict[str, object] = Field(default_factory=dict)
    degradations: list[str] = Field(default_factory=list)


class ArtifactSummary(DeliveryModel):
    task_id: str
    artifact_id: str
    artifact_type: str
    content_sha256: str
    media_type: str
    size_bytes: int = Field(ge=0)
    created_at: str


class EvaluationPhase(DeliveryModel):
    phase: Literal["B0", "B1", "B2", "B3", "B4"]
    status: Literal["validated", "compatibility_only", "limited", "blocked"]
    evidence_report: str
    quality_evidence: Literal["measured", "limited", "missing"]
    failure_analysis: list[str]


class EvaluationMatrix(DeliveryModel):
    schema_version: Literal["1.0"] = "1.0"
    generated_at: str
    phases: list[EvaluationPhase]
    overall_status: Literal["delivery_ready_with_notes", "blocked"]
    blocking_reasons: list[str]
    limitations: list[str]


M6_DELIVERY_CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        TaskCreateRequest,
        ApprovalRequest,
        PlanCostAcknowledgement,
        ResumeRequest,
        ResearchPlanView,
        BudgetReport,
        TaskSummary,
        ArtifactSummary,
        EvaluationPhase,
        EvaluationMatrix,
    )
}
