"""Typed control-plane models for the M3 research workflow."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scholartrace.contracts import ArtifactRef, ResearchPlan, StableId


class WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalDecision(WorkflowModel):
    action: Literal["approve", "modify", "reject"]
    modified_plan: ResearchPlan | None = None
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_modified_plan(self) -> ApprovalDecision:
        if self.action == "modify" and self.modified_plan is None:
            raise ValueError("modify approval requires modified_plan")
        if self.action != "modify" and self.modified_plan is not None:
            raise ValueError("modified_plan is only valid for modify approval")
        return self


class SearchBackendResult(WorkflowModel):
    candidate_paper_ids: list[StableId] = Field(default_factory=list, max_length=500)
    covered_subquestion_ids: list[StableId] = Field(default_factory=list, max_length=20)


class SearchRoundArtifact(WorkflowModel):
    task_id: StableId
    round_index: int = Field(ge=0, le=100)
    query: str = Field(min_length=1, max_length=8000)
    adjustment_reason: Literal["initial_plan", "uncovered_subquestion"]
    candidate_paper_ids: list[StableId] = Field(default_factory=list, max_length=500)
    new_unique_paper_ids: list[StableId] = Field(default_factory=list, max_length=500)
    covered_subquestion_ids: list[StableId] = Field(default_factory=list, max_length=20)
    new_unique_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)


class SearchAction(WorkflowModel):
    kind: Literal["search", "stop"]
    query: str | None = Field(default=None, max_length=8000)
    adjustment_reason: Literal["initial_plan", "uncovered_subquestion"] | None = None
    stop_reason: Literal[
        "coverage_complete",
        "saturated",
        "max_rounds",
        "query_budget_exhausted",
    ] | None = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> SearchAction:
        if self.kind == "search" and (not self.query or self.adjustment_reason is None):
            raise ValueError("search action requires query and adjustment_reason")
        if self.kind == "stop" and self.stop_reason is None:
            raise ValueError("stop action requires stop_reason")
        return self


class PaperWorkerArtifact(WorkflowModel):
    task_id: StableId
    canonical_paper_id: StableId
    status: Literal["succeeded", "failed", "timed_out"]
    output: dict[str, object] = Field(default_factory=dict)
    public_reason: str | None = Field(default=None, max_length=2000)


class PersistedEvent(WorkflowModel):
    event_id: str
    sequence: int = Field(ge=1)
    task_id: StableId
    node: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=100)
    artifact_id: str | None = Field(default=None, max_length=200)
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: str


def merge_unique_strings(left: list[str], right: list[str]) -> list[str]:
    """Merge concurrent IDs with deterministic ordering and no duplicates."""

    return sorted(set(left).union(right))


def merge_artifact_refs(left: list[ArtifactRef], right: list[ArtifactRef]) -> list[ArtifactRef]:
    """Merge refs by stable artifact ID and reject conflicting identities."""

    merged = {item.artifact_id: item for item in left}
    for item in right:
        existing = merged.get(item.artifact_id)
        if existing is not None and existing.content_sha256 != item.content_sha256:
            raise ValueError(f"conflicting artifact ref: {item.artifact_id}")
        merged[item.artifact_id] = item
    return [merged[key] for key in sorted(merged)]


class ResearchState(TypedDict, total=False):
    graph_schema_version: str
    task_id: str
    thread_id: str
    question: str
    started_at: str
    status: str
    plan_ref: ArtifactRef
    search_refs: Annotated[list[ArtifactRef], merge_artifact_refs]
    selected_paper_ids: Annotated[list[str], merge_unique_strings]
    paper_result_refs: Annotated[list[ArtifactRef], merge_artifact_refs]
    failed_paper_ids: Annotated[list[str], merge_unique_strings]
    round_count: int
    stop_reason: str | None
    approval_reason: str | None


class PaperWorkerState(TypedDict):
    task_id: str
    thread_id: str
    canonical_paper_id: str
    plan_ref: ArtifactRef
