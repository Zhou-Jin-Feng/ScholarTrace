"""Bounded research assembly over the existing search, evidence and verifier modules.

Dependencies are supplied by the composition root. Offline fixtures are explicit;
constructing this module never reads credentials or creates a network client.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, RootModel, TypeAdapter

from scholartrace.citations.graph import CitationGraphBuilder
from scholartrace.citations.lifecycle import CitationLifecycleGate
from scholartrace.citations.models import (
    CitationExpansionResult,
    CitationGraphArtifact,
    CitationPaperLifecycle,
)
from scholartrace.contracts import Budget, BudgetLimits, Claim, DocuMindBinding, Evidence, Paper
from scholartrace.delivery.authorization import CallContext
from scholartrace.delivery.criteria import filter_papers
from scholartrace.delivery.effects import EffectJournal
from scholartrace.delivery.models import ResearchPlanView
from scholartrace.delivery.plans import compute_plan_digest
from scholartrace.delivery.stages import ResearchStages
from scholartrace.evidence.live import PdfAcquisition, acquire_pdf, arxiv_pdf_url, ingest_papers
from scholartrace.evidence.models import RetrievalChunk
from scholartrace.evidence.pipeline import (
    EvidencePipelineResult,
    EvidenceRetrievalBatch,
    M2EvidencePipeline,
)
from scholartrace.scholargraph.experiment import (
    GeneratedReport,
    ReportGenerationRequest,
    ReportGenerator,
)
from scholartrace.search.models import SearchRequest, SearchSnapshot
from scholartrace.search.normalization import normalize_candidates, rank_papers
from scholartrace.search.pipeline import SearchPipeline
from scholartrace.search.providers import candidate_within_dates
from scholartrace.verification.models import M4ReliabilityResult
from scholartrace.verification.pipeline import M4ReliabilityPipeline
from scholartrace.workflow.models import PersistedEvent
from scholartrace.workflow.storage import ArtifactStore, RuntimeLedger


class ResearchCancelledError(RuntimeError):
    """Cancellation prevents starting the next operation, not a refund of dispatched calls."""


def _merge_evidence(target: dict[str, Evidence], item: Evidence) -> None:
    """Keep a stable source identity while allowing independent retrieval runs."""
    previous = target.get(item.evidence_id)
    if previous is not None:
        # Retrieval-run identity varies by question; document, location and
        # quote provenance must still describe exactly the same evidence.
        excluded = {"retrieval_run_id"}
        if previous.model_dump(exclude=excluded) != item.model_dump(exclude=excluded):
            raise ValueError("conflicting evidence identity across subquestions")
        return
    target[item.evidence_id] = item


class ResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    task_id: str
    plan_version: int = Field(ge=1)
    plan_digest: str
    execution_kind: Literal["offline_fixture", "live"]
    outcome: Literal["succeeded", "degraded", "failed"]
    papers: list[Paper]
    claims: list[Claim]
    evidence: list[Evidence]
    bindings: list[DocuMindBinding]
    citation_graph: CitationGraphArtifact
    lifecycle: list[CitationPaperLifecycle]
    timeline: list[PersistedEvent]
    reliability: M4ReliabilityResult
    report_markdown: str
    limitations: list[str]
    claim_subquestions: dict[str, list[str]] = Field(default_factory=dict)


class OfflineResearchRunner(Protocol):
    def __call__(
        self, question: str, plan: ResearchPlanView, cancel_event: threading.Event
    ) -> ResearchResult: ...


class CitationExpander(Protocol):
    max_discovered_papers: int

    async def expand(self, papers: list[Paper]) -> CitationExpansionResult: ...


class ResearchPipeline:
    def __init__(
        self,
        *,
        search: SearchPipeline,
        evidence: M2EvidencePipeline,
        reliability: M4ReliabilityPipeline,
        synthesis: ReportGenerator,
        acquisition_client: httpx.AsyncClient,
        documind_client: httpx.AsyncClient,
        documind_url: str,
        download_dir: Path,
        artifacts: ArtifactStore,
        ledger: RuntimeLedger,
        fixture_mode: bool = False,
        citations: CitationExpander | None = None,
        on_event: Callable[[PersistedEvent], None] | None = None,
        journal: EffectJournal | None = None,
        context_factory: Callable[[str], CallContext] | None = None,
        execution_kind: Literal["offline_fixture", "live"] = "offline_fixture",
    ) -> None:
        self.search = search
        self.evidence = evidence
        self.reliability = reliability
        self.synthesis = synthesis
        self.acquisition_client = acquisition_client
        self.documind_client = documind_client
        self.documind_url = documind_url.rstrip("/")
        self.download_dir = download_dir
        self.artifacts = artifacts
        self.ledger = ledger
        self.fixture_mode = fixture_mode
        self.citations = citations
        self.on_event = on_event
        self.journal = journal
        self.context_factory = context_factory
        self.execution_kind = execution_kind

    async def run(
        self,
        *,
        question: str,
        plan: ResearchPlanView,
        cancel_event: threading.Event,
    ) -> ResearchResult:
        # A live route must be explicitly assembled; it is never inferred from
        # a non-fixture flag or silently promoted from the offline route.
        if self.execution_kind == "live" and (
            self.fixture_mode or self.journal is None or self.context_factory is None
        ):
            raise RuntimeError("live research requires an approved metered composition")
        if self.execution_kind == "offline_fixture" and not self.fixture_mode:
            raise RuntimeError("non-fixture research requires an explicit live composition")
        if self.execution_kind == "offline_fixture" and (
            plan.generated_by != "fixture"
            or self.synthesis.model_profile != "fixture"
            or self.reliability.verifier.backend.verifier_kind != "fixture"
        ):
            raise ValueError("offline composition requires explicitly labelled fixture profiles")
        plan.source_scope.validate()
        digest = compute_plan_digest(
            sub_questions=tuple(plan.sub_questions),
            source_scope=plan.source_scope,
            exclusions=tuple(plan.exclusions),
            budget_plan=plan.budget_plan,
            details=plan.details,
        )
        if plan.approval_state != "approved" or plan.plan_digest != digest:
            raise ValueError("execution requires the exact approved plan")
        if not {s.name for s in self.search.sources} <= set(plan.source_scope.providers):
            raise ValueError("search provider is outside the approved scope")
        if self.citations is not None and (
            "openalex" not in plan.source_scope.providers
            or not 1 <= self.citations.max_discovered_papers <= 5
        ):
            raise ValueError("citation expansion must be approved and bounded to five candidates")
        self._check_cancel(cancel_event)
        journal = self.journal or EffectJournal(
            self.download_dir.parent / "research-effects.sqlite"
        )
        try:
            stages = ResearchStages(
                journal,
                plan=plan,
                question=question,
                cancel_event=cancel_event,
                context_factory=self.context_factory,
            )
            await stages.start()
            return await self._run(
                question=question, plan=plan, cancel_event=cancel_event, stages=stages
            )
        finally:
            if self.journal is None:
                journal.close()

    async def _run(
        self,
        *,
        question: str,
        plan: ResearchPlanView,
        cancel_event: threading.Event,
        stages: ResearchStages,
    ) -> ResearchResult:
        task_id = plan.task_id
        search_request = SearchRequest(
            query=question,
            max_results=plan.source_scope.max_papers,
            from_year=plan.source_scope.year_from,
            to_year=plan.source_scope.year_to,
            published_before=(plan.details.retrieval_cutoff if plan.details else None),
        )
        snapshot = await stages.run(
            "search",
            TypeAdapter(SearchSnapshot),
            lambda: self.search.run(search_request),
        )
        ranked = list(snapshot.ranked_papers)
        expansion = None
        if self.citations is not None:
            expansion = await stages.run(
                "citations",
                TypeAdapter(CitationExpansionResult),
                partial(self.citations.expand, [r.paper for r in ranked]),
            )
            candidates = expansion.discovered_candidates[: self.citations.max_discovered_papers]
            candidates = [c for c in candidates if candidate_within_dates(c, search_request)]
            known = {r.paper.canonical_paper_id for r in ranked}
            ranked.extend(
                r
                for r in rank_papers(question, normalize_candidates(candidates).papers)
                if r.score >= self.search.min_relevance_score
                and r.paper.canonical_paper_id not in known
            )
        lifecycle = CitationLifecycleGate(clock=lambda: snapshot.generated_at)
        candidate_papers = [
            r.paper.model_copy(update={"access_level": "fulltext"})
            for r in ranked
            if plan.source_scope.year_from <= r.paper.publication_year <= plan.source_scope.year_to
            and r.paper.arxiv_id is not None
        ]
        papers, criteria_exclusions = filter_papers(
            candidate_papers,
            inclusion_criteria=(
                plan.details.inclusion_criteria if plan.details else ("full text",)
            ),
            exclusion_criteria=tuple(plan.exclusions),
        )
        papers = papers[: plan.source_scope.max_papers]
        if len(papers) < plan.source_scope.min_papers:
            raise ValueError("insufficient accessible papers within the approved scope")
        # Resolve only the allowlisted, versioned arXiv access route. This is
        # acquisition authorization, not evidence that a PDF has been obtained.
        for paper in papers:
            url, _ = arxiv_pdf_url(paper)
            lifecycle.register(paper)
            lifecycle.mark_relevant(paper.canonical_paper_id)
            lifecycle.mark_access_resolved(paper.canonical_paper_id, access_url=url)
        self._save(task_id, "search", snapshot)
        self._event(task_id, "search", "search_completed")
        # All acquisition/ingestion precedes retrieval, all retrieval precedes generation.
        newly_created: list[str] = []
        for paper in papers:
            self._check_cancel(cancel_event)
            paper_key = hashlib.sha256(paper.canonical_paper_id.encode()).hexdigest()[:24]
            pdf = await stages.run(
                f"acquire:{paper_key}",
                TypeAdapter(PdfAcquisition),
                partial(
                    acquire_pdf, self.acquisition_client, paper=paper, output_dir=self.download_dir
                ),
            )
            self._check_cancel(cancel_event)
            lifecycle.mark_acquired(paper.canonical_paper_id, source_sha256=pdf.sha256)
            ingested = await stages.run(
                f"ingest:{paper_key}",
                TypeAdapter(tuple[list[DocuMindBinding], int]),
                partial(
                    ingest_papers,
                    client=self.documind_client,
                    base_url=self.documind_url,
                    papers=[paper],
                    document_paths={paper.canonical_paper_id: pdf.path},
                    repository=self.evidence.bindings,
                    newly_created=newly_created,
                    documind_version="3.0.0",
                    expected_source_sha256={paper.canonical_paper_id: pdf.sha256},
                    multipart_boundary=(
                        hashlib.sha256(
                            f"{task_id}:{plan.plan_digest}:{paper_key}".encode()
                        ).hexdigest()
                        if self.execution_kind == "live"
                        else None
                    ),
                ),
            )
            lifecycle.mark_ingested(paper.canonical_paper_id, binding=ingested[0][0])
        self._check_cancel(cancel_event)
        scopes = [("", question)]
        if self.execution_kind == "live":
            if plan.details is None:
                raise ValueError("live research requires full reviewed plan details")
            scopes = [(q.subquestion_id, q.question) for q in plan.details.subquestions]
        batches = []
        for subquestion_id, scoped_question in scopes:
            suffix = (
                ":" + hashlib.sha256(subquestion_id.encode()).hexdigest()[:24]
                if subquestion_id
                else ""
            )
            batch = await stages.run(
                "retrieve" + suffix,
                TypeAdapter(EvidenceRetrievalBatch),
                partial(self.evidence.retrieve, papers=papers, question=scoped_question),
            )
            batches.append((subquestion_id, suffix, batch))
        self._event(task_id, "retrieval", "retrieval_completed")
        analyses = []
        claim_subquestions: dict[str, list[str]] = {}
        for subquestion_id, suffix, batch in batches:
            self._check_cancel(cancel_event)
            analyzed = await stages.run(
                "analyze" + suffix,
                TypeAdapter(EvidencePipelineResult),
                partial(self.evidence.analyze, batch),
            )
            analyses.append(analyzed)
            for bundle in analyzed.report.analyses:
                for claim in bundle.claims:
                    if subquestion_id:
                        claim_subquestions.setdefault(claim.claim_id, []).append(subquestion_id)
        if self.execution_kind == "live":
            self._save(task_id, "analysis", RootModel(list(a.report for a in analyses)))
        else:
            self._save(task_id, "analysis", analyses[0].report)
        analysis_ref = self.artifacts.get_ref(f"artifact:research:{task_id}:analysis")
        assert analysis_ref is not None
        analysis_ref = analysis_ref.model_copy(
            update={"storage_uri": f"artifact://{analysis_ref.artifact_id}"}
        )
        for paper in papers:
            lifecycle.mark_analyzed(paper.canonical_paper_id, analysis_ref=analysis_ref)
        graph = CitationGraphBuilder().build(
            papers=papers,
            edges=expansion.edges if expansion is not None else [],
            seed_paper_ids=[r.paper.canonical_paper_id for r in snapshot.ranked_papers],
            lifecycle_records=lifecycle.records(),
            generated_at=snapshot.generated_at,
            warnings=[] if expansion is not None else ["Citation expansion was not requested."],
        )
        self._save(task_id, "citations", graph)
        self._event(task_id, "citations", "citations_completed")
        self._event(task_id, "evidence", "evidence_completed")
        claims_by_id: dict[str, Claim] = {}
        evidence_by_id: dict[str, Evidence] = {}
        chunks_by_paper: dict[str, dict[str, RetrievalChunk]] = {}
        for analysis in analyses:
            for bundle in analysis.report.analyses:
                for claim in bundle.claims:
                    if claim.claim_id in claims_by_id and claims_by_id[claim.claim_id] != claim:
                        raise ValueError("conflicting claim identity across subquestions")
                    claims_by_id[claim.claim_id] = claim
                for item in bundle.evidence:
                    _merge_evidence(evidence_by_id, item)
        for _, _, batch in batches:
            for row in batch.rows:
                chunks = chunks_by_paper.setdefault(row.paper.canonical_paper_id, {})
                for chunk in row.retrieval.response.chunks:
                    if chunk.chunk_id in chunks and chunks[chunk.chunk_id] != chunk:
                        raise ValueError("conflicting retrieved chunks across subquestions")
                    chunks[chunk.chunk_id] = chunk
        claims = list(claims_by_id.values())
        evidence = list(evidence_by_id.values())
        batch = batches[0][2]
        self._check_cancel(cancel_event)
        reliability = await stages.run(
            "verify",
            TypeAdapter(M4ReliabilityResult),
            lambda: self.reliability.run(
                task_id=task_id,
                claims=claims,
                evidence=evidence,
                papers=papers,
                bindings=[r.binding for r in batch.rows],
                chunks_by_paper={
                    paper_id: list(chunks.values()) for paper_id, chunks in chunks_by_paper.items()
                },
                claim_subquestions={
                    key: ids[0] for key, ids in claim_subquestions.items() if len(ids) == 1
                },
                budget=Budget(
                    limits=BudgetLimits(
                        max_cost_cny=plan.budget_plan.max_cny,
                        max_api_calls=plan.budget_plan.max_api_calls,
                    )
                ),
                completed_at=datetime.now(UTC),
                citation_graph=graph,
                lifecycle_records=lifecycle.records(),
            ),
        )
        self._save(task_id, "verification", reliability)
        self._event(task_id, "verification", "verification_completed")
        limitations = [
            *(
                []
                if self.execution_kind == "live"
                else ["Offline engineering fixture; not a live research-quality evaluation."]
            ),
            "Only accessible arXiv full texts in the approved scope are selected.",
            "ScholarGraph is disabled; citation expansion is at most one bounded round.",
        ]
        if criteria_exclusions:
            limitations.append(
                "Approved criteria excluded candidates: "
                + "; ".join(
                    f"{reason} ({count})" for reason, count in sorted(criteria_exclusions.items())
                )
            )
        if reliability.follow_up_requests:
            limitations.append("A bounded follow-up is proposed, not executed automatically.")
        if expansion is None:
            limitations.append("Citation expansion was not requested for this composition.")
        dispositions = [d for d in reliability.report_gate.dispositions if d.included]
        included_ids = {d.claim_id for d in dispositions}
        included_claims = [c for c in claims if c.claim_id in included_ids]
        evidence_ids = {e for c in included_claims for e in c.evidence_ids + c.counter_evidence_ids}
        context = json.dumps(
            {
                "claims": [c.model_dump(mode="json") for c in included_claims],
                "dispositions": [d.model_dump(mode="json") for d in dispositions],
                "evidence": [
                    e.model_dump(mode="json") for e in evidence if e.evidence_id in evidence_ids
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self._check_cancel(cancel_event)
        if reliability.report_gate.report_safe and dispositions:
            generated = await stages.run(
                "synthesis",
                TypeAdapter(GeneratedReport),
                lambda: self.synthesis.generate(
                    ReportGenerationRequest(
                        question_id=task_id,
                        subset="delivery",
                        question=question,
                        variant="B3",
                        paper_pool_sha256=hashlib.sha256(
                            "\n".join(p.canonical_paper_id for p in papers).encode()
                        ).hexdigest(),
                        report_length_limit=20000,
                        evidence_context=context,
                        allowed_evidence_ids=frozenset(evidence_ids),
                        scholargraph_context=None,
                    )
                ),
            )
            if generated.status not in {"succeeded", "degraded"}:
                raise ValueError("synthesis did not produce a usable report")
            markdown = generated.report
        else:
            markdown = "# Insufficient verified evidence\nNo substantive answer is available."
        # The deterministic gate is authoritative even if the narrative omits markers.
        markdown += "\n\n## Verified findings\n" + "\n".join(d.rendered_text for d in dispositions)
        markdown += "\n\n## Limitations\n" + "\n".join(f"- {s}" for s in limitations)
        outcome = reliability.outcome
        if outcome == "succeeded" and snapshot.outcome != "succeeded":
            outcome = "degraded"
        if expansion is not None and expansion.outcome != "succeeded" and outcome == "succeeded":
            outcome = "degraded"
        self._check_cancel(cancel_event)
        self._event(task_id, "synthesis", "synthesis_completed")
        result = ResearchResult(
            task_id=task_id,
            plan_version=plan.plan_version,
            plan_digest=plan.plan_digest,
            execution_kind=self.execution_kind,
            claim_subquestions=claim_subquestions,
            outcome=outcome,
            papers=papers,
            claims=claims,
            evidence=evidence,
            reliability=reliability,
            bindings=[row.binding for row in batch.rows],
            citation_graph=graph,
            lifecycle=lifecycle.records(),
            timeline=self.ledger.replay(task_id=task_id),
            report_markdown=markdown,
            limitations=limitations,
        )
        self._save(task_id, "result", result)
        return result

    def _save(self, task_id: str, stage: str, payload: BaseModel) -> None:
        self.artifacts.put(
            artifact_id=f"artifact:research:{task_id}:{stage}",
            artifact_type="report",
            payload=payload,
        )

    def _event(self, task_id: str, stage: str, kind: str) -> None:
        event = self.ledger.append_event(
            stable_key=f"event:research:{task_id}:{stage}",
            task_id=task_id,
            node=stage,
            kind=kind,
            payload={"execution_kind": self.execution_kind},
        )
        if self.on_event is not None:
            self.on_event(event)

    @staticmethod
    def _check_cancel(event: threading.Event) -> None:
        if event.is_set():
            raise ResearchCancelledError("research cancelled before the next operation")
