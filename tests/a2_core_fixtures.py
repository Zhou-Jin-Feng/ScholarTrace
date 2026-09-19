"""Offline transports for the assembled research flow; no socket access."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx

from scholartrace.search.models import PaperCandidate, SourceRequestRecord, SourceSearchResult
from scholartrace.verification.models import SemanticVerificationDraft

ROOT = Path(__file__).resolve().parents[1]


class OfflineResearch:
    name = "arxiv"
    verifier_kind = "fixture"
    profile_id = None
    model_profile = "fixture"
    model_identifier = "fixture"
    provider_protocol = "chat_completions"
    prompt_template_sha256 = "0" * 64

    def __init__(self):
        self.cases = json.loads(
            (ROOT / "tests/fixtures/documind/m2_three_papers.json").read_text("utf-8")
        )["papers"]
        self.events = []
        self.statuses = ["supported", "partially_supported", "unsupported"]
        self.pdfs = {}
        for case in self.cases:
            paper = case["paper"]
            pdf = b"%PDF-1.4\n" + paper["arxiv_id"].encode() + b"\n%%EOF\n"
            self.pdfs[paper["arxiv_id"]] = pdf
            digest = hashlib.sha256(pdf).hexdigest()
            case["binding"].update(source_sha256=digest, documind_version="3.0.0")
            case["retrieve_response"].update(source_sha256=digest, service_version="3.0.0")
        self.report_request = None

    async def search(self, request):
        self.events.append("search")
        now = datetime.now(UTC)
        candidates = [
            PaperCandidate(
                candidate_id=f"candidate:{i}",
                source="arxiv",
                source_id=f"http://arxiv.org/abs/{c['paper']['arxiv_id']}v1",
                title=c["paper"]["title"],
                authors=c["paper"]["authors"],
                publication_year=c["paper"]["publication_year"],
                doi=c["paper"]["doi"],
                arxiv_id=c["paper"]["arxiv_id"],
                access_level="abstract",
                abstract="Retrieval signals and classifications",
                retrieved_at=now,
                record_sha256=str(i + 1) * 64,
            )
            for i, c in enumerate(self.cases)
        ]
        return SourceSearchResult(
            source="arxiv",
            candidates=candidates,
            request=SourceRequestRecord(
                request_id="request:offline",
                source="arxiv",
                public_url="https://arxiv.org",
                public_params={},
                started_at=now,
                completed_at=now,
                duration_seconds=0,
                cache_hit=False,
                attempts=1,
                status="succeeded",
                candidate_count=len(candidates),
            ),
        )

    def transport(self, request):
        path = request.url.path
        if request.url.host == "arxiv.org":
            paper_id = path.rsplit("/", 1)[-1].removesuffix(".pdf").removesuffix("v1")
            self.events.append("acquire")
            return httpx.Response(
                200, content=self.pdfs[paper_id], headers={"content-type": "application/pdf"}
            )
        if path == "/api/v1/documents":
            if request.method == "GET":
                return httpx.Response(200, json={"items": []})
            case = next(c for c in self.cases if c["paper"]["arxiv_id"].encode() in request.content)
            self.events.append("ingest")
            b = case["binding"]
            return httpx.Response(200, json={**b, "chunk_count": 1, "status": "active"})
        if path.startswith("/api/v1/documents/"):
            b = next(c["binding"] for c in self.cases if c["binding"]["document_key"] in path)
            return httpx.Response(
                200,
                json={**b, "active_index_id": b["index_id"], "chunk_count": 1, "status": "active"},
            )
        body = json.loads(request.content)
        if "document_key" in body:
            self.events.append("retrieve")
            case = next(
                c for c in self.cases if c["binding"]["document_key"] == body["document_key"]
            )
            return httpx.Response(200, json=case["retrieve_response"])
        if path == "/api/chat":
            self.events.append("analyze")
            model_input = json.loads(body["messages"][1]["content"])
            paper_id = model_input["paper"]["canonical_paper_id"]
            case = next(c for c in self.cases if c["paper"]["canonical_paper_id"] == paper_id)
            return httpx.Response(
                200,
                json={
                    "message": {"role": "assistant", "content": json.dumps(case["analysis_draft"])},
                    "prompt_eval_count": 320,
                    "eval_count": 96,
                },
            )
        raise AssertionError(f"unexpected offline request: {request.method} {path}")

    async def verify(self, *, claim, evidence):
        index = self.events.count("verify")
        self.events.append("verify")
        status = self.statuses[index % len(self.statuses)]
        return SemanticVerificationDraft(
            status=status,
            reason="Offline engineering scenario",
            recommended_action="remove" if status == "unsupported" else "keep",
        )

    async def generate(self, request):
        from scholartrace.scholargraph.experiment import GeneratedReport

        self.events.append("synthesis")
        self.report_request = request
        return GeneratedReport(
            report="# Offline research\nEngineering fixture only.",
            status="succeeded",
            input_tokens=0,
            output_tokens=0,
            model_calls=0,
            provider_api_calls=0,
            duration_seconds=0,
            reference_cost_cny=0,
        )


def approved_plan(task_id="task:core:one"):
    from scholartrace.delivery.models import ResearchPlanView
    from scholartrace.delivery.plans import BudgetPlan, SourceScope, compute_plan_digest

    scope = SourceScope(providers=("arxiv",), year_from=2020, year_to=2025)
    budget = BudgetPlan(
        max_cny=0, max_api_calls=0, max_wall_clock_seconds=60, estimate_source="fixture"
    )
    questions = ("What signals determine retrieval?",)
    return ResearchPlanView(
        task_id=task_id,
        plan_version=1,
        plan_digest=compute_plan_digest(
            sub_questions=questions, source_scope=scope, exclusions=(), budget_plan=budget
        ),
        generated_by="fixture",
        generated_at="2026-09-17T00:00:00Z",
        approval_state="approved",
        supersedes_version=None,
        sub_questions=list(questions),
        exclusions=[],
        source_scope=scope,
        budget_plan=budget,
    )


@asynccontextmanager
async def offline_pipeline(tmp_path, backend=None):
    from scholartrace.delivery.research import ResearchPipeline
    from scholartrace.evidence.analysis import OllamaPaperAnalyzer
    from scholartrace.evidence.bindings import DocuMindBindingRepository
    from scholartrace.evidence.client import DocuMindClient
    from scholartrace.evidence.pipeline import M2EvidencePipeline
    from scholartrace.search.pipeline import SearchPipeline
    from scholartrace.verification.pipeline import M4ReliabilityPipeline
    from scholartrace.verification.verifier import VerifierRunner
    from scholartrace.workflow.storage import ArtifactStore, RuntimeLedger

    backend = backend or OfflineResearch()
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend.transport)) as http:
        artifacts = ArtifactStore(tmp_path / "artifacts.sqlite")
        ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
        try:
            yield (
                ResearchPipeline(
                    search=SearchPipeline([backend], min_relevance_score=0),
                    evidence=M2EvidencePipeline(
                        client=DocuMindClient(client=http),
                        bindings=DocuMindBindingRepository(tmp_path / "bindings.sqlite"),
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
                ),
                backend,
            )
        finally:
            artifacts.close()
            ledger.close()
