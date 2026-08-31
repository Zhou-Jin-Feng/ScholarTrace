"""Strict public models for the M6 delivery surface."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scholartrace.workflow.models import ApprovalDecision


class DeliveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskStatus(StrEnum):
    CREATED = "created"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    DEGRADED = "degraded"
    REJECTED = "rejected"
    FAILED = "failed"


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


class TaskCreateRequest(DeliveryModel):
    question: str = Field(min_length=3, max_length=2000)
    title: str | None = Field(default=None, min_length=3, max_length=300)
    demo_mode: DemoMode = DemoMode.SUCCESS


class ApprovalRequest(ApprovalDecision):
    """M3 approval contract exposed by the M6 API."""


class TaskSummary(DeliveryModel):
    task_id: str
    thread_id: str
    title: str
    question: str
    status: TaskStatus
    phase: TaskPhase
    demo_mode: DemoMode
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
        TaskSummary,
        ArtifactSummary,
        EvaluationPhase,
        EvaluationMatrix,
    )
}
