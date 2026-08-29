from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from scholartrace.contracts import DocuMindBinding, Paper
from scholartrace.evidence.analysis import OllamaPaperAnalyzer
from scholartrace.evidence.bindings import DocuMindBindingRepository
from scholartrace.evidence.client import DocuMindClient, EvidenceScopeError
from scholartrace.evidence.models import DocuMindRetrieveResponse, PaperAnalysisDraft
from scholartrace.evidence.pipeline import M2EvidencePipeline

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"


def _fixture() -> dict[str, object]:
    payload = json.loads(FIXTURE_PATH.read_text("utf-8"))
    assert payload["fixture_kind"] == "contract_and_local_model_smoke"
    return payload


def _cases() -> list[dict[str, object]]:
    cases = _fixture()["papers"]
    assert isinstance(cases, list)
    return cases


def _paper(case: dict[str, object]) -> Paper:
    return Paper.model_validate(case["paper"])


def _binding(case: dict[str, object]) -> DocuMindBinding:
    return DocuMindBinding.model_validate(case["binding"])


def test_three_real_paper_identities_and_responses_validate() -> None:
    fixture = _fixture()
    baseline = fixture["provider_baseline"]
    assert isinstance(baseline, dict)
    assert baseline["git_commit"] == "212f60a"
    assert "not online retrieval quality" in str(fixture["quality_scope"])

    cases = _cases()
    papers = [_paper(case) for case in cases]
    assert {paper.arxiv_id for paper in papers} == {"2312.10997", "2401.15884", "2403.14403"}
    for case, paper in zip(cases, papers, strict=True):
        binding = _binding(case)
        response = DocuMindRetrieveResponse.model_validate(case["retrieve_response"])
        draft = PaperAnalysisDraft.model_validate(case["analysis_draft"])
        assert binding.canonical_paper_id == paper.canonical_paper_id
        assert response.document_key == binding.document_key
        assert response.index_id == binding.index_id
        assert draft.claims[0].chunk_ref == "chunk-1"
        assert draft.claims[0].quote_ref == "quote-1"


def test_three_paper_fixture_runs_through_real_consumer_and_analyzer(tmp_path: Path) -> None:
    fixture = _fixture()
    cases = _cases()
    repository = DocuMindBindingRepository(tmp_path / "bindings.sqlite3")
    by_document: dict[str, dict[str, object]] = {}
    by_paper: dict[str, dict[str, object]] = {}
    for case in cases:
        paper = _paper(case)
        binding = _binding(case)
        repository.put(binding)
        by_document[binding.document_key] = case
        by_paper[paper.canonical_paper_id] = case

    retrieval_calls: list[str] = []
    model_calls: list[str] = []

    def documind_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        case = by_document[body["document_key"]]
        retrieval_calls.append(body["document_key"])
        return httpx.Response(200, json=case["retrieve_response"], request=request)

    def ollama_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        model_input = json.loads(body["messages"][1]["content"])
        paper_id = model_input["paper"]["canonical_paper_id"]
        assert model_input["chunks"][0]["chunk_ref"] == "chunk-1"
        assert model_input["chunks"][0]["quote_ref"] == "quote-1"
        assert "chunk_id" not in model_input["chunks"][0]
        model_calls.append(paper_id)
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": json.dumps(by_paper[paper_id]["analysis_draft"]),
                },
                "prompt_eval_count": 320,
                "eval_count": 96,
            },
            request=request,
        )

    async def scenario():
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(documind_handler)) as documind_http,
            httpx.AsyncClient(transport=httpx.MockTransport(ollama_handler)) as ollama_http,
        ):
            pipeline = M2EvidencePipeline(
                client=DocuMindClient(client=documind_http),
                bindings=repository,
                analyzer=OllamaPaperAnalyzer(client=ollama_http),
                max_concurrency=2,
            )
            return await pipeline.run(
                papers=[_paper(case) for case in cases],
                question=str(fixture["question"]),
            )

    result = asyncio.run(scenario())
    assert len(result.report.analyses) == 3
    assert len(result.retrieval_audits) == 3
    assert len(result.model_usage) == 3
    assert set(retrieval_calls) == set(by_document)
    assert set(model_calls) == set(by_paper)
    assert all(item.evidence[0].evidence_level == "fulltext" for item in result.report.analyses)
    assert all(item.evidence[0].page_number == 1 for item in result.report.analyses)


def test_pipeline_fails_closed_when_provider_returns_another_paper(tmp_path: Path) -> None:
    cases = _cases()
    repository = DocuMindBindingRepository(tmp_path / "bindings.sqlite3")
    for case in cases:
        repository.put(_binding(case))

    wrong_response = cases[1]["retrieve_response"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=wrong_response, request=request)

    async def scenario() -> None:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(handler)) as documind_http,
            httpx.AsyncClient(transport=httpx.MockTransport(handler)) as ollama_http,
        ):
            pipeline = M2EvidencePipeline(
                client=DocuMindClient(client=documind_http),
                bindings=repository,
                analyzer=OllamaPaperAnalyzer(client=ollama_http),
            )
            with pytest.raises(EvidenceScopeError, match="another document"):
                await pipeline.run(
                    papers=[_paper(case) for case in cases],
                    question="What determines retrieval behavior?",
                )

    asyncio.run(scenario())


def test_analyzer_rejects_unsupplied_chunk_reference() -> None:
    cases = _cases()
    first_case = cases[0]
    contaminated = dict(first_case["analysis_draft"])  # type: ignore[arg-type]
    contaminated_claim = dict(contaminated["claims"][0])  # type: ignore[index]
    contaminated_claim["chunk_ref"] = "chunk-2"
    contaminated["claims"] = [contaminated_claim]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": json.dumps(contaminated)}},
            request=request,
        )

    response = DocuMindRetrieveResponse.model_validate(first_case["retrieve_response"])

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            analyzer = OllamaPaperAnalyzer(client=http)
            binding = _binding(first_case)
            from scholartrace.evidence.client import RetrievalResult
            from scholartrace.evidence.models import RetrievalAudit

            audit = RetrievalAudit(
                retrieval_run_id="retrieval:m2:fixture-contamination",
                canonical_paper_id=binding.canonical_paper_id,
                document_key=binding.document_key,
                index_id=binding.index_id,
                source_sha256=binding.source_sha256,
                query_sha256="9" * 64,
                started_at="2026-08-29T12:00:00Z",
                completed_at="2026-08-29T12:00:00Z",
                duration_seconds=0,
                attempts=1,
                status="succeeded",
                service_version="2.2.0",
                retrieval_version="dense-v1",
                chunk_count=1,
            )
            with pytest.raises(RuntimeError, match="failed after 2 attempts"):
                await analyzer.analyze(
                    paper=_paper(first_case),
                    binding=binding,
                    retrieval=RetrievalResult(response=response, audit=audit),
                    question="What determines retrieval behavior?",
                )

    asyncio.run(scenario())
