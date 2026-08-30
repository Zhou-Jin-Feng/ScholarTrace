"""Reusable zero-cost M4 citation and verification fixture smoke."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from scholartrace.citations.graph import CitationGraphBuilder
from scholartrace.citations.models import CitationEdge
from scholartrace.contracts import (
    Budget,
    BudgetLimits,
    BudgetUsage,
    Claim,
    DocuMindBinding,
    Evidence,
    Paper,
)
from scholartrace.evidence.models import RetrievalChunk
from scholartrace.verification.models import (
    CitationRequirement,
    SemanticVerificationDraft,
)
from scholartrace.verification.pipeline import M4ReliabilityPipeline
from scholartrace.verification.verifier import VerifierKind, VerifierRunner


class ConflictFixtureVerifier:
    verifier_kind: VerifierKind = "fixture"
    profile_id: str | None = None

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def verify(
        self,
        *,
        claim: Claim,
        evidence: list[Evidence],
    ) -> SemanticVerificationDraft:
        if claim.claim_id != "claim:m4:conflicted" or len(evidence) != 2:
            raise ValueError("unexpected semantic verifier fixture input")
        self.calls.append(claim.claim_id)
        return SemanticVerificationDraft(
            status="conflicted",
            reason="The bounded fixture contains explicit support and counter-evidence.",
            recommended_action="follow_up",
        )


async def run_fixture_smoke(fixture_path: Path) -> dict[str, object]:
    fixture = json.loads(fixture_path.read_text("utf-8"))
    generated_at = datetime(2026, 8, 30, 6, tzinfo=UTC)
    paper = Paper.model_validate(fixture["paper"])
    citation_paper = Paper.model_validate(fixture["citation_paper"])
    binding = DocuMindBinding.model_validate(fixture["binding"])
    chunks = [RetrievalChunk.model_validate(item) for item in fixture["chunks"]]
    evidence = [Evidence.model_validate(item) for item in fixture["evidence"]]
    claims = [Claim.model_validate(item) for item in fixture["claims"]]
    edge = CitationEdge.model_validate(fixture["citation_edge"])
    requirement = CitationRequirement.model_validate(
        fixture["missing_citation_requirement"]
    )
    graph = CitationGraphBuilder().build(
        papers=[paper, citation_paper],
        edges=[edge],
        seed_paper_ids=[paper.canonical_paper_id, citation_paper.canonical_paper_id],
        lifecycle_records=[],
        generated_at=generated_at,
    )
    backend = ConflictFixtureVerifier()
    result = await M4ReliabilityPipeline(
        verifier=VerifierRunner(
            backend=backend,
            policy=None,
            allow_fixture=True,
        )
    ).run(
        task_id="task:m4:fixture",
        claims=claims,
        evidence=evidence,
        papers=[paper, citation_paper],
        bindings=[binding],
        chunks_by_paper={paper.canonical_paper_id: chunks},
        claim_subquestions={
            "claim:m4:conflicted": "subq:m4:conflict",
            "claim:m4:missing-citation": "subq:m4:citation",
        },
        budget=Budget(
            limits=BudgetLimits(
                max_rounds=2,
                max_queries=2,
                max_api_calls=2,
                max_cost_cny=0,
            ),
            usage=BudgetUsage(queries=1),
        ),
        completed_at=generated_at,
        citation_graph=graph,
        citation_requirements=[requirement],
        target_papers_by_claim={
            "claim:m4:conflicted": [paper.canonical_paper_id],
            "claim:m4:missing-citation": ["openalex:W300"],
        },
    )
    validation_codes = {
        item.claim_id: sorted({issue.code for issue in item.issues})
        for item in result.validation.results
    }
    dispositions = {item.claim_id: item for item in result.report_gate.dispositions}
    verifications = {item.claim_id: item for item in result.verifications}
    passed = all(
        (
            result.outcome == "degraded",
            result.report_gate.report_safe,
            validation_codes["claim:m4:missing-citation"] == ["citation_edge_missing"],
            verifications["claim:m4:conflicted"].status == "conflicted",
            dispositions["claim:m4:conflicted"].marker == "[CONFLICTED]",
            not dispositions["claim:m4:missing-citation"].included,
            result.report_gate.blocked_critical_claim_ids
            == ["claim:m4:missing-citation"],
            len(result.follow_up_requests) == 1,
            result.follow_up_requests[0].round_index == 1,
            result.follow_up_requests[0].max_additional_queries == 1,
            len(backend.calls) == 1,
        )
    )
    return {
        "schema_version": "1.0",
        "generated_at": generated_at.isoformat(),
        "fixture_kind": fixture["fixture_kind"],
        "provider_calls": 0,
        "model_provider_calls": 0,
        "fixture_verifier_calls": len(backend.calls),
        "citation_node_count": len(graph.nodes),
        "citation_edge_count": len(graph.edges),
        "citation_graph_sha256": graph.content_sha256,
        "pagerank_sum": round(sum(item.pagerank for item in graph.metrics), 12),
        "validation_outcome": result.validation.outcome,
        "validation_codes": validation_codes,
        "verification_statuses": {
            claim_id: item.status for claim_id, item in verifications.items()
        },
        "blocked_critical_claim_ids": result.report_gate.blocked_critical_claim_ids,
        "visible_conflict_marker": dispositions["claim:m4:conflicted"].marker,
        "report_safe": result.report_gate.report_safe,
        "follow_up_count": len(result.follow_up_requests),
        "follow_up_round": result.follow_up_requests[0].round_index,
        "outcome": result.outcome,
        "passed": passed,
        "notes": [
            "The fixture Verifier proves four-state handling, not semantic model quality.",
            "No academic provider, DocuMind service, local model, or paid API was called.",
            "The missing explicit citation edge blocks the critical claim from the report.",
        ],
    }
