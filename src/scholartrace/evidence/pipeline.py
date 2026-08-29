"""M2 single-flow evidence pipeline over three to five bounded papers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from scholartrace.contracts import DocuMindBinding, ModelUsageRecord, Paper
from scholartrace.evidence.analysis import GeneratedPaperAnalysis
from scholartrace.evidence.bindings import DocuMindBindingRepository
from scholartrace.evidence.client import DocuMindClient, RetrievalResult
from scholartrace.evidence.models import EvidenceReportArtifact, RetrievalAudit
from scholartrace.evidence.report import build_evidence_report


class PaperAnalyzer(Protocol):
    async def analyze(
        self,
        *,
        paper: Paper,
        binding: DocuMindBinding,
        retrieval: RetrievalResult,
        question: str,
    ) -> GeneratedPaperAnalysis: ...


class MissingBindingError(RuntimeError):
    pass


class NoEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EvidencePipelineResult:
    report: EvidenceReportArtifact
    retrieval_audits: list[RetrievalAudit]
    model_usage: list[ModelUsageRecord]
    started_at: datetime
    completed_at: datetime


class M2EvidencePipeline:
    def __init__(
        self,
        *,
        client: DocuMindClient,
        bindings: DocuMindBindingRepository,
        analyzer: PaperAnalyzer,
        max_concurrency: int = 2,
    ) -> None:
        if max_concurrency < 1 or max_concurrency > 4:
            raise ValueError("M2 max_concurrency must be between 1 and 4")
        self.client = client
        self.bindings = bindings
        self.analyzer = analyzer
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def run(self, *, papers: list[Paper], question: str) -> EvidencePipelineResult:
        if len(papers) < 3 or len(papers) > 5:
            raise ValueError("M2 evidence pipeline requires three to five papers")
        paper_ids = [paper.canonical_paper_id for paper in papers]
        if len(paper_ids) != len(set(paper_ids)):
            raise ValueError("M2 evidence pipeline paper IDs must be unique")
        started_at = datetime.now(UTC)
        retrievals = await asyncio.gather(
            *(self._retrieve_paper(paper=paper, question=question) for paper in papers)
        )
        generated = await asyncio.gather(
            *(
                self._analyze_paper(
                    paper=paper,
                    binding=binding,
                    retrieval=retrieval,
                    question=question,
                )
                for paper, binding, retrieval in retrievals
            )
        )
        completed_at = datetime.now(UTC)
        report = build_evidence_report(
            question=question,
            analyses=[item.bundle for item in generated],
            generated_at=completed_at,
        )
        return EvidencePipelineResult(
            report=report,
            retrieval_audits=[item.bundle.retrieval_audit for item in generated],
            model_usage=[item.usage for item in generated],
            started_at=started_at,
            completed_at=completed_at,
        )

    async def _retrieve_paper(
        self,
        *,
        paper: Paper,
        question: str,
    ) -> tuple[Paper, DocuMindBinding, RetrievalResult]:
        binding = self.bindings.get(paper.canonical_paper_id)
        if binding is None:
            raise MissingBindingError(f"paper has no DocuMind binding: {paper.canonical_paper_id}")
        async with self.semaphore:
            retrieval = await self.client.retrieve(
                canonical_paper_id=paper.canonical_paper_id,
                binding=binding,
                query=question,
            )
            if not retrieval.response.chunks:
                raise NoEvidenceError(
                    f"DocuMind returned no evidence for paper: {paper.canonical_paper_id}"
                )
        return paper, binding, retrieval

    async def _analyze_paper(
        self,
        *,
        paper: Paper,
        binding: DocuMindBinding,
        retrieval: RetrievalResult,
        question: str,
    ) -> GeneratedPaperAnalysis:
        async with self.semaphore:
            return await self.analyzer.analyze(
                paper=paper,
                binding=binding,
                retrieval=retrieval,
                question=question,
            )
