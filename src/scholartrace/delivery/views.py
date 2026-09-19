"""Task-scoped UI views derived from persisted, verified research artifacts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from scholartrace.delivery.models import ResearchPlanView
from scholartrace.delivery.research import ResearchResult


class ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimVerificationView(ViewModel):
    status: Literal["supported", "partially_supported", "unsupported", "conflicted"]
    verifier_kind: Literal["deterministic", "model", "human", "fixture"]
    marker: str | None
    validated: bool


class ClaimView(ViewModel):
    claim_id: str
    text: str
    sub_question: str | None
    verification: ClaimVerificationView
    evidence_ids: list[str]
    included_in_report: bool


class ClaimsResponse(ViewModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    claims: list[ClaimView]
    excluded_count: int
    exclusion_reasons: dict[str, int]


class PaperView(ViewModel):
    paper_id: str
    title: str
    source: str
    access_level: str
    version: str | None


class BindingView(ViewModel):
    document_key: str
    index_id: str
    source_sha256: str
    documind_version: str


class ChunkView(ViewModel):
    chunk_id: str | None
    content_sha256: str | None
    page_number: int | None
    char_start: int | None
    char_end: int | None


class EvidenceView(ViewModel):
    schema_version: Literal["1.0"] = "1.0"
    evidence_id: str
    task_id: str
    claim_ids: list[str]
    evidence_level: Literal["fulltext", "abstract", "metadata"]
    paper: PaperView
    binding: BindingView | None
    chunk: ChunkView
    excerpt: str
    excerpt_is_verbatim: bool
    inference_note: str | None


def claims_view(result: ResearchResult, plan: ResearchPlanView) -> ClaimsResponse:
    verifications = {v.claim_id: v for v in result.reliability.verifications}
    validations = {v.claim_id: v for v in result.reliability.validation.results}
    dispositions = {d.claim_id: d for d in result.reliability.report_gate.dispositions}
    items = []
    for claim in result.claims:
        verification = verifications[claim.claim_id]
        disposition = dispositions[claim.claim_id]
        items.append(
            ClaimView(
                claim_id=claim.claim_id,
                text=claim.text,
                sub_question=(
                    "；".join(
                        q.question
                        for q in plan.details.subquestions
                        if q.subquestion_id in result.claim_subquestions.get(claim.claim_id, [])
                    )
                    or None
                )
                if plan.details
                else (plan.sub_questions[0] if len(plan.sub_questions) == 1 else None),
                verification=ClaimVerificationView(
                    status=verification.status,
                    verifier_kind=verification.verifier,
                    marker=disposition.marker,
                    validated=validations[claim.claim_id].passed,
                ),
                evidence_ids=sorted(set(claim.evidence_ids + claim.counter_evidence_ids)),
                included_in_report=disposition.included,
            )
        )
    excluded = sum(not item.included_in_report for item in items)
    return ClaimsResponse(
        task_id=result.task_id,
        claims=items,
        excluded_count=excluded,
        exclusion_reasons={"unsupported": excluded} if excluded else {},
    )


def evidence_view(result: ResearchResult, evidence_id: str) -> EvidenceView:
    item = next((e for e in result.evidence if e.evidence_id == evidence_id), None)
    if item is None:
        raise KeyError(evidence_id)
    paper = next(p for p in result.papers if p.canonical_paper_id == item.canonical_paper_id)
    binding = next(
        (b for b in result.bindings if b.canonical_paper_id == item.canonical_paper_id), None
    )
    source = next((s for s in paper.sources if s.source == "arxiv"), paper.sources[0])
    return EvidenceView(
        task_id=result.task_id,
        evidence_id=item.evidence_id,
        claim_ids=[
            c.claim_id
            for c in result.claims
            if evidence_id in c.evidence_ids + c.counter_evidence_ids
        ],
        evidence_level=item.evidence_level,
        paper=PaperView(
            paper_id=paper.canonical_paper_id,
            title=paper.title,
            source=source.source,
            access_level=paper.access_level,
            version=source.source_id if source.source == "arxiv" else None,
        ),
        binding=None
        if binding is None
        else BindingView(
            document_key=binding.document_key,
            index_id=binding.index_id,
            source_sha256=binding.source_sha256,
            documind_version=binding.documind_version,
        ),
        chunk=ChunkView(
            chunk_id=item.chunk_id,
            content_sha256=item.chunk_content_sha256,
            page_number=item.page_number,
            char_start=item.char_start,
            char_end=item.char_end,
        ),
        excerpt=item.quote,
        excerpt_is_verbatim=True,
        inference_note=None,
    )


VIEW_CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    "ClaimsResponse": ClaimsResponse,
    "EvidenceView": EvidenceView,
    "ResearchResult": ResearchResult,
}
