"""M4 reliability pipeline: validate, verify, gate, then plan one follow-up."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from scholartrace.citations.models import CitationGraphArtifact, CitationPaperLifecycle
from scholartrace.contracts import (
    Budget,
    Claim,
    DocuMindBinding,
    Evidence,
    FollowUpRequest,
    Paper,
)
from scholartrace.evidence.models import RetrievalChunk
from scholartrace.verification.followup import FollowUpPolicy
from scholartrace.verification.gate import VerificationReportGate
from scholartrace.verification.models import (
    CitationRequirement,
    M4ReliabilityResult,
)
from scholartrace.verification.validator import EvidenceValidator
from scholartrace.verification.verifier import VerifierRunner


class M4ReliabilityPipeline:
    def __init__(
        self,
        *,
        verifier: VerifierRunner,
        validator: EvidenceValidator | None = None,
        report_gate: VerificationReportGate | None = None,
        follow_up_policy: FollowUpPolicy | None = None,
    ) -> None:
        self.verifier = verifier
        self.validator = validator or EvidenceValidator()
        self.report_gate = report_gate or VerificationReportGate()
        self.follow_up_policy = follow_up_policy or FollowUpPolicy()

    async def run(
        self,
        *,
        task_id: str,
        claims: list[Claim],
        evidence: list[Evidence],
        papers: list[Paper],
        bindings: list[DocuMindBinding],
        chunks_by_paper: Mapping[str, list[RetrievalChunk]],
        claim_subquestions: Mapping[str, str],
        budget: Budget,
        completed_at: datetime,
        citation_graph: CitationGraphArtifact | None = None,
        citation_requirements: list[CitationRequirement] | None = None,
        lifecycle_records: list[CitationPaperLifecycle] | None = None,
        existing_follow_ups: list[FollowUpRequest] | None = None,
        target_papers_by_claim: Mapping[str, list[str]] | None = None,
    ) -> M4ReliabilityResult:
        validation = self.validator.validate(
            claims=claims,
            evidence=evidence,
            papers=papers,
            bindings=bindings,
            chunks_by_paper=chunks_by_paper,
            citation_graph=citation_graph,
            citation_requirements=citation_requirements,
            lifecycle_records=lifecycle_records,
            validated_at=completed_at,
        )
        verifications = await self.verifier.verify(
            claims=claims,
            evidence=evidence,
            validations=validation.results,
            verified_at=completed_at,
        )
        report_gate = self.report_gate.apply(
            claims=claims,
            verifications=verifications,
        )
        follow_ups = self.follow_up_policy.select(
            task_id=task_id,
            claims=claims,
            verifications=verifications,
            claim_subquestions=claim_subquestions,
            budget=budget,
            existing_requests=existing_follow_ups,
            target_papers_by_claim=target_papers_by_claim,
        )
        statuses = {item.status for item in verifications}
        outcome: Literal["succeeded", "degraded", "failed"]
        if not report_gate.report_safe:
            outcome = "failed"
        elif statuses <= {"supported"}:
            outcome = "succeeded"
        else:
            outcome = "degraded"
        return M4ReliabilityResult(
            validation=validation,
            verifications=verifications,
            report_gate=report_gate,
            follow_up_requests=follow_ups,
            outcome=outcome,
        )
