from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from a2_core_fixtures import OfflineResearch, approved_plan


def test_offline_research_assembles_existing_modules_and_preserves_evidence_gate(tmp_path):
    from scholartrace.delivery.research import ResearchPipeline
    from scholartrace.evidence.analysis import OllamaPaperAnalyzer
    from scholartrace.evidence.bindings import DocuMindBindingRepository
    from scholartrace.evidence.client import DocuMindClient
    from scholartrace.evidence.pipeline import M2EvidencePipeline
    from scholartrace.search.pipeline import SearchPipeline
    from scholartrace.verification.pipeline import M4ReliabilityPipeline
    from scholartrace.verification.verifier import VerifierRunner
    from scholartrace.workflow.storage import ArtifactStore, RuntimeLedger

    backend = OfflineResearch()

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend.transport)) as http:
            bindings = DocuMindBindingRepository(tmp_path / "bindings.sqlite")
            artifacts = ArtifactStore(tmp_path / "artifacts.sqlite")
            ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
            try:
                pipeline = ResearchPipeline(
                    search=SearchPipeline([backend], min_relevance_score=0),
                    evidence=M2EvidencePipeline(
                        client=DocuMindClient(client=http),
                        bindings=bindings,
                        analyzer=OllamaPaperAnalyzer(client=http),
                    ),
                    reliability=M4ReliabilityPipeline(
                        verifier=VerifierRunner(backend=backend, policy=None, allow_fixture=True)
                    ),
                    synthesis=backend,
                    acquisition_client=http,
                    documind_client=http,
                    documind_url="http://documind.test",
                    download_dir=tmp_path / "pdfs",
                    artifacts=artifacts,
                    ledger=ledger,
                    fixture_mode=True,
                )
                result = await pipeline.run(
                    question="What determines retrieval?",
                    plan=approved_plan(),
                    cancel_event=threading.Event(),
                )
                assert result.execution_kind == "offline_fixture"
                assert len(result.papers) == len(result.claims) == len(result.evidence) == 3
                assert result.outcome == "degraded"
                assert {v.status for v in result.reliability.verifications} == {
                    "supported",
                    "partially_supported",
                    "unsupported",
                }
                assert len(result.reliability.follow_up_requests) <= 1
                assert "[PARTIALLY SUPPORTED]" in result.report_markdown
                unsupported = next(
                    v.claim_id
                    for v in result.reliability.verifications
                    if v.status == "unsupported"
                )
                assert unsupported not in backend.report_request.evidence_context
                assert len(backend.report_request.allowed_evidence_ids) == 2
                assert backend.events.count("acquire") == backend.events.count("ingest") == 3
                assert max(i for i, e in enumerate(backend.events) if e == "retrieve") < min(
                    i for i, e in enumerate(backend.events) if e == "analyze"
                )
                assert all(
                    e.source_sha256 in {c["binding"]["source_sha256"] for c in backend.cases}
                    for e in result.evidence
                )
                assert artifacts.get_ref(f"artifact:research:{result.task_id}:result") is not None
                return result
            finally:
                artifacts.close()
                ledger.close()

    asyncio.run(run())


def test_bounded_citation_expansion_keeps_lifecycle_order(tmp_path):
    from types import SimpleNamespace

    from a2_core_fixtures import offline_pipeline

    async def run():
        async with offline_pipeline(tmp_path) as (pipeline, backend):
            # Replace the external citation dependency, not the lifecycle/graph implementation.
            calls = []

            async def expand(papers):
                from datetime import UTC, datetime

                from scholartrace.citations.models import CitationEdge, CitationExpansionResult

                calls.append(1)
                return CitationExpansionResult(
                    seed_paper_ids=[p.canonical_paper_id for p in papers],
                    edges=[
                        CitationEdge(
                            edge_id="edge:offline",
                            citing_paper_id=papers[0].canonical_paper_id,
                            cited_paper_id=papers[1].canonical_paper_id,
                            source="openalex",
                            source_work_id="W1",
                            request_id="request:offline",
                            response_sha256="1" * 64,
                            retrieved_at=datetime.now(UTC),
                        )
                    ],
                    outcome="succeeded",
                )

            pipeline.citations = SimpleNamespace(expand=expand, max_discovered_papers=1)
            from scholartrace.delivery.plans import SourceScope, compute_plan_digest

            plan = approved_plan()
            scope = SourceScope(providers=("arxiv", "openalex"), year_from=2020, year_to=2025)
            plan = plan.model_copy(
                update={
                    "source_scope": scope,
                    "plan_digest": compute_plan_digest(
                        sub_questions=tuple(plan.sub_questions),
                        source_scope=scope,
                        exclusions=(),
                        budget_plan=plan.budget_plan,
                    ),
                }
            )
            result = await pipeline.run(
                question="What determines retrieval?", plan=plan, cancel_event=threading.Event()
            )
            assert calls == [1]
            assert len(result.citation_graph.edges) == 1
            assert {row.stage for row in result.lifecycle} == {"analyzed"}
            assert all(
                row.binding.source_sha256 == row.acquired_source_sha256 for row in result.lifecycle
            )

    asyncio.run(run())


def test_restart_replays_completed_research_without_network_or_generation(tmp_path):
    from a2_core_fixtures import offline_pipeline

    async def run():
        async with offline_pipeline(tmp_path) as (pipeline, backend):
            first = await pipeline.run(
                question="What determines retrieval?",
                plan=approved_plan(),
                cancel_event=threading.Event(),
            )
            assert backend.events
        async with offline_pipeline(tmp_path) as (pipeline, backend):
            second = await pipeline.run(
                question="What determines retrieval?",
                plan=approved_plan(),
                cancel_event=threading.Event(),
            )
            assert second == first
            assert backend.events == []

    asyncio.run(run())


def test_cancelled_or_unapproved_research_dispatches_nothing(tmp_path):
    from a2_core_fixtures import offline_pipeline

    from scholartrace.delivery.research import ResearchCancelledError

    async def run():
        async with offline_pipeline(tmp_path) as (pipeline, backend):
            cancel = threading.Event()
            cancel.set()
            with pytest.raises(ResearchCancelledError):
                await pipeline.run(
                    question="Research query", plan=approved_plan(), cancel_event=cancel
                )
            cancel.clear()
            with pytest.raises(ValueError, match="approved"):
                await pipeline.run(
                    question="Research query",
                    cancel_event=cancel,
                    plan=approved_plan().model_copy(update={"approval_state": "waiting_approval"}),
                )
            assert backend.events == []

    asyncio.run(run())


def test_cancel_during_dispatched_analysis_blocks_further_work_and_automatic_replay(tmp_path):
    from a2_core_fixtures import offline_pipeline

    from scholartrace.delivery.effects import (
        EffectCancelledError,
        EffectJournal,
        EffectUncertainError,
    )

    async def run():
        cancel = threading.Event()
        started = asyncio.Event()
        async with offline_pipeline(tmp_path) as (pipeline, backend):
            original = pipeline.evidence.analyzer

            class WaitingAnalyzer:
                async def analyze(self, **kwargs):
                    started.set()
                    await asyncio.sleep(60)
                    return await original.analyze(**kwargs)

            pipeline.evidence.analyzer = WaitingAnalyzer()
            running = asyncio.create_task(
                pipeline.run(
                    question="What determines retrieval?", plan=approved_plan(), cancel_event=cancel
                )
            )
            await started.wait()
            cancel.set()
            with pytest.raises(EffectCancelledError):
                await asyncio.wait_for(running, timeout=2)
            assert "verify" not in backend.events
        cancel.clear()
        async with offline_pipeline(tmp_path) as (pipeline, backend):
            with pytest.raises(EffectUncertainError):
                await pipeline.run(
                    question="What determines retrieval?", plan=approved_plan(), cancel_event=cancel
                )
            assert backend.events == []
        journal = EffectJournal(tmp_path / "research-effects.sqlite")
        try:
            assert journal.summary("task:core:one")["uncertain_effects"] == 1
        finally:
            journal.close()

    asyncio.run(run())


def test_dispatched_timeout_is_reported_as_uncertain_not_a_safe_retry(tmp_path):
    from a2_core_fixtures import offline_pipeline

    from scholartrace.delivery.effects import EffectUncertainError

    async def run():
        async with offline_pipeline(tmp_path) as (pipeline, backend):

            async def timeout(request):
                raise TimeoutError("synthetic response loss")

            backend.search = timeout
            with pytest.raises(EffectUncertainError):
                await pipeline.run(
                    question="What determines retrieval?",
                    plan=approved_plan(),
                    cancel_event=threading.Event(),
                )

    asyncio.run(run())
