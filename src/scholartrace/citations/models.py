"""Strict contracts for explicit citation provenance and graph artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scholartrace.contracts import ArtifactRef, DocuMindBinding, Paper, Sha256, StableId
from scholartrace.search.models import PaperCandidate, SourceRequestRecord


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CitationSeed(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    canonical_paper_id: StableId
    openalex_id: Annotated[str, Field(pattern=r"^W\d+$")]


class CitationEdge(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    edge_id: StableId
    citing_paper_id: StableId
    cited_paper_id: StableId
    source: Literal["openalex", "semantic_scholar"]
    relation: Literal["references"] = "references"
    source_work_id: Annotated[str, Field(pattern=r"^W\d+$")]
    request_id: StableId
    response_sha256: Sha256
    retrieved_at: datetime

    @model_validator(mode="after")
    def reject_self_citations(self) -> CitationEdge:
        if self.citing_paper_id == self.cited_paper_id:
            raise ValueError("citation edge cannot be a self-loop")
        return self


class CitationExpansionResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    seed_paper_ids: Annotated[list[StableId], Field(min_length=1, max_length=100)]
    edges: list[CitationEdge] = Field(default_factory=list, max_length=10_000)
    discovered_candidates: list[PaperCandidate] = Field(default_factory=list, max_length=1_000)
    request_records: list[SourceRequestRecord] = Field(default_factory=list, max_length=100)
    unresolved_openalex_ids: list[str] = Field(default_factory=list, max_length=1_000)
    missing_reference_paper_ids: list[StableId] = Field(default_factory=list, max_length=100)
    outcome: Literal["succeeded", "degraded", "failed"]

    @model_validator(mode="after")
    def verify_unique_provenance(self) -> CitationExpansionResult:
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("citation edge IDs must be unique")
        candidate_ids = [candidate.candidate_id for candidate in self.discovered_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("citation candidates must be unique")
        return self


LifecycleStage = Literal[
    "normalized",
    "relevance_passed",
    "access_resolved",
    "acquired",
    "ingested",
    "analyzed",
    "rejected",
    "failed",
]


class CitationPaperLifecycle(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    canonical_paper_id: StableId
    paper: Paper
    stage: LifecycleStage
    access_url: str | None = Field(default=None, max_length=2048)
    acquired_source_sha256: Sha256 | None = None
    binding: DocuMindBinding | None = None
    analysis_ref: ArtifactRef | None = None
    public_reason: str | None = Field(default=None, max_length=500)
    updated_at: datetime

    @model_validator(mode="after")
    def verify_stage_artifacts(self) -> CitationPaperLifecycle:
        if self.paper.canonical_paper_id != self.canonical_paper_id:
            raise ValueError("lifecycle paper ID mismatch")
        ordered = {
            "normalized": 0,
            "relevance_passed": 1,
            "access_resolved": 2,
            "acquired": 3,
            "ingested": 4,
            "analyzed": 5,
        }
        rank = ordered.get(self.stage)
        if rank is not None and rank >= 2 and self.access_url is None:
            raise ValueError("resolved access URL is required after the access gate")
        if rank is not None and rank >= 3 and self.acquired_source_sha256 is None:
            raise ValueError("acquired source hash is required after acquisition")
        if rank is not None and rank >= 4:
            if self.binding is None:
                raise ValueError("DocuMind binding is required after ingestion")
            if self.binding.canonical_paper_id != self.canonical_paper_id:
                raise ValueError("lifecycle binding paper ID mismatch")
            if self.binding.source_sha256 != self.acquired_source_sha256:
                raise ValueError("acquired source and DocuMind binding hashes differ")
        if self.stage == "analyzed" and self.analysis_ref is None:
            raise ValueError("analysis artifact is required after analysis")
        if self.stage in {"rejected", "failed"} and not self.public_reason:
            raise ValueError("terminal lifecycle failure requires a public reason")
        return self


class CitationNode(StrictModel):
    paper_id: StableId
    title: Annotated[str, Field(min_length=1, max_length=1000)]
    publication_year: int | None = Field(default=None, ge=1900, le=2100)
    seed: bool = False
    lifecycle_stage: LifecycleStage | Literal["external_metadata_missing"]
    analysis_ready: bool = False


class CitationNodeMetric(StrictModel):
    paper_id: StableId
    in_degree: int = Field(ge=0)
    out_degree: int = Field(ge=0)
    pagerank: float = Field(ge=0, le=1, allow_inf_nan=False)
    component_id: StableId
    community_id: StableId


class CitationTimelineCandidate(StrictModel):
    earlier_paper_id: StableId
    later_paper_id: StableId
    year_gap: int = Field(ge=0, le=200)
    citation_edge_id: StableId


class CitationGraphArtifact(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    graph_id: StableId
    nodes: Annotated[list[CitationNode], Field(min_length=1, max_length=10_000)]
    edges: list[CitationEdge] = Field(default_factory=list, max_length=50_000)
    metrics: Annotated[list[CitationNodeMetric], Field(min_length=1, max_length=10_000)]
    components: Annotated[list[list[StableId]], Field(min_length=1, max_length=10_000)]
    communities: Annotated[list[list[StableId]], Field(min_length=1, max_length=10_000)]
    timeline_candidates: list[CitationTimelineCandidate] = Field(
        default_factory=list, max_length=50_000
    )
    partial: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=1_000)
    generated_at: datetime
    content_sha256: Sha256

    @model_validator(mode="after")
    def verify_graph_integrity(self) -> CitationGraphArtifact:
        node_ids = [node.paper_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("citation graph node IDs must be unique")
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("citation graph edge IDs must be unique")
        if any(
            edge.citing_paper_id not in node_ids or edge.cited_paper_id not in node_ids
            for edge in self.edges
        ):
            raise ValueError("citation edge endpoint is missing from graph nodes")
        if {metric.paper_id for metric in self.metrics} != set(node_ids):
            raise ValueError("citation graph metrics must cover every node")
        if self.content_sha256 != citation_graph_sha256(self):
            raise ValueError("citation graph content hash mismatch")
        return self


def citation_graph_sha256(graph: CitationGraphArtifact) -> str:
    payload = graph.model_dump(mode="json", exclude={"content_sha256"})
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


CITATION_CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        CitationSeed,
        CitationEdge,
        CitationExpansionResult,
        CitationPaperLifecycle,
        CitationGraphArtifact,
    )
}
