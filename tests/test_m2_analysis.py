from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from scholartrace.contracts import (
    DocuMindBinding,
    ModelUsageRecord,
    Paper,
    PaperSource,
)
from scholartrace.evidence.analysis import OllamaPaperAnalyzer
from scholartrace.evidence.bindings import DocuMindBindingRepository
from scholartrace.evidence.client import RetrievalResult
from scholartrace.evidence.models import (
    DocuMindRetrieveResponse,
    EvidenceReportArtifact,
    PaperAnalysisDraft,
    RetrievalAudit,
)
from scholartrace.evidence.pipeline import M2EvidencePipeline, NoEvidenceError
from scholartrace.evidence.report import build_evidence_report

NOW = datetime(2026, 8, 29, 9, tzinfo=UTC)


def _ids(index: int) -> tuple[str, str, str, str]:
    values = ("a", "b", "c", "d", "e", "f", "0", "1", "2", "3", "4", "5")
    offset = index * 4
    return tuple(values[offset + item] * 64 for item in range(4))  # type: ignore[return-value]


def _paper(index: int = 0) -> Paper:
    return Paper(
        canonical_paper_id=f"doi:10.1000/m2-{index}",
        title=f"M2 Evidence Paper {index}",
        normalized_title=f"m2 evidence paper {index}",
        authors=[f"Author {index}"],
        publication_year=2024,
        doi=f"10.1000/m2-{index}",
        access_level="fulltext",
        sources=[
            PaperSource(
                source="crossref",
                source_id=f"10.1000/m2-{index}",
                retrieved_at=NOW,
                record_sha256=str(index + 6) * 64,
            )
        ],
    )


def _binding(index: int = 0) -> DocuMindBinding:
    document, active_index, source, _ = _ids(index)
    return DocuMindBinding(
        canonical_paper_id=_paper(index).canonical_paper_id,
        document_key=document,
        index_id=active_index,
        source_sha256=source,
        documind_version="2.2.0",
        retrieval_schema_version="1.0",
    )


def _retrieval(index: int = 0, *, empty: bool = False) -> RetrievalResult:
    document, active_index, source, chunk_id = _ids(index)
    content = f"Paper {index} states that retrieval is adaptive. Additional context follows."
    chunks = []
    if not empty:
        chunks = [
            {
                "chunk_id": chunk_id,
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "source": f"paper-{index}.pdf",
                "page_number": index + 1,
                "distance": 0.2,
                "rank": 1,
            }
        ]
    response = DocuMindRetrieveResponse(
        service_version="2.2.0",
        retrieval_version="dense-v1",
        retrieval_mode="dense",
        document_key=document,
        index_id=active_index,
        source_sha256=source,
        chunks=chunks,
    )
    audit = RetrievalAudit(
        retrieval_run_id=f"retrieval:m2:test-{index}",
        canonical_paper_id=_paper(index).canonical_paper_id,
        document_key=document,
        index_id=active_index,
        source_sha256=source,
        query_sha256="9" * 64,
        started_at=NOW,
        completed_at=NOW,
        duration_seconds=0,
        attempts=1,
        status="empty" if empty else "succeeded",
        service_version="2.2.0",
        retrieval_version="dense-v1",
        chunk_count=len(chunks),
    )
    return RetrievalResult(response=response, audit=audit)


def _draft(
    index: int = 0,
    *,
    chunk_ref: str = "chunk-1",
    quote_ref: str = "quote-1",
) -> dict:
    return {
        "summary": f"Paper {index} studies adaptive retrieval.",
        "contributions": ["It describes an adaptive retrieval mechanism."],
        "limitations": ["Only one supplied chunk was available."],
        "claims": [
            {
                "text": f"Paper {index} states that retrieval is adaptive.",
                "claim_type": "fact",
                "chunk_ref": chunk_ref,
                "quote_ref": quote_ref,
                "importance": "critical",
            }
        ],
    }


def _bundle(index: int = 0):
    return OllamaPaperAnalyzer._build_bundle(
        paper=_paper(index),
        binding=_binding(index),
        retrieval=_retrieval(index),
        question="How is retrieval adapted?",
        draft=PaperAnalysisDraft.model_validate(_draft(index)),
        generated_at=NOW,
        input_payload={"fixture": index},
    )


def _usage(index: int = 0) -> ModelUsageRecord:
    return ModelUsageRecord(
        node=f"node:m2:test-{index}",
        profile_id="model:local:qwen3-8b",
        provider="ollama",
        model_name="qwen3:8b",
        model_version="test",
        duration_seconds=0,
    )


def test_local_analyzer_retries_invalid_quote_and_builds_full_provenance() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        request_payload = json.loads(request.content)
        assert request_payload["options"]["num_predict"] == 2048
        model_chunk = json.loads(request_payload["messages"][1]["content"])["chunks"][0]
        assert model_chunk["chunk_ref"] == "chunk-1"
        assert model_chunk["quote_ref"] == "quote-1"
        assert "chunk_id" not in model_chunk
        payload = _draft(0, quote_ref="quote-2") if calls == 1 else _draft(0)
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": json.dumps(payload)},
                "prompt_eval_count": 250,
                "eval_count": 90,
            },
            request=request,
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            generated = await OllamaPaperAnalyzer(client=http).analyze(
                paper=_paper(),
                binding=_binding(),
                retrieval=_retrieval(),
                question="How is retrieval adapted?",
            )
        evidence = generated.bundle.evidence[0]
        assert evidence.evidence_level == "fulltext"
        assert evidence.document_key == _binding().document_key
        assert evidence.index_id == _binding().index_id
        assert evidence.chunk_content_sha256 == _retrieval().response.chunks[0].content_sha256
        assert evidence.char_start == 0
        assert evidence.char_end == len(evidence.quote)
        assert generated.usage.call_count == 2
        assert generated.usage.structured_repair_count == 1
        assert generated.usage.input_tokens == 500
        assert generated.usage.output_tokens == 180

    asyncio.run(scenario())
    assert calls == 2


def test_local_analyzer_fails_closed_on_unknown_chunk() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": json.dumps(_draft(0, chunk_ref="chunk-6")),
                }
            },
            request=request,
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(RuntimeError, match="failed after 2 attempts"):
                await OllamaPaperAnalyzer(client=http).analyze(
                    paper=_paper(),
                    binding=_binding(),
                    retrieval=_retrieval(),
                    question="How is retrieval adapted?",
                )

    asyncio.run(scenario())


def test_report_has_locators_and_rejects_cross_paper_evidence() -> None:
    bundles = [_bundle(index) for index in range(3)]
    report = build_evidence_report(
        question="How is retrieval adapted?",
        analyses=bundles,
        generated_at=NOW,
    )
    assert "page 1" in report.content_markdown
    assert f"chunk `{_ids(0)[3]}`" in report.content_markdown
    assert "independent semantic verification is deferred to M4" in report.content_markdown

    payload = report.model_dump(mode="json")
    payload["analyses"][0]["evidence"][0]["canonical_paper_id"] = _paper(1).canonical_paper_id
    with pytest.raises(ValidationError, match="cross-paper evidence"):
        EvidenceReportArtifact.model_validate(payload)


def test_pipeline_does_not_call_model_for_empty_retrieval(tmp_path: Path) -> None:
    repository = DocuMindBindingRepository(tmp_path / "bindings.sqlite3")
    for index in range(3):
        repository.put(_binding(index))

    class EmptyClient:
        async def retrieve(self, **kwargs):
            paper_id = kwargs["canonical_paper_id"]
            index = next(i for i in range(3) if _paper(i).canonical_paper_id == paper_id)
            return _retrieval(index, empty=True)

    class NeverAnalyzer:
        def __init__(self) -> None:
            self.calls = 0

        async def analyze(self, **kwargs):
            self.calls += 1
            raise AssertionError("analyzer must not run for empty retrieval")

    analyzer = NeverAnalyzer()
    pipeline = M2EvidencePipeline(
        client=EmptyClient(),  # type: ignore[arg-type]
        bindings=repository,
        analyzer=analyzer,  # type: ignore[arg-type]
    )

    with pytest.raises(NoEvidenceError):
        asyncio.run(
            pipeline.run(
                papers=[_paper(index) for index in range(3)],
                question="How is retrieval adapted?",
            )
        )
    assert analyzer.calls == 0


def test_pipeline_finishes_retrieval_phase_before_model_phase(tmp_path: Path) -> None:
    repository = DocuMindBindingRepository(tmp_path / "bindings.sqlite3")
    for index in range(3):
        repository.put(_binding(index))
    events: list[str] = []

    class OrderedClient:
        async def retrieve(self, **kwargs):
            paper_id = kwargs["canonical_paper_id"]
            index = next(i for i in range(3) if _paper(i).canonical_paper_id == paper_id)
            events.append(f"retrieve:{index}")
            return _retrieval(index)

    class OrderedAnalyzer:
        async def analyze(self, **kwargs):
            paper = kwargs["paper"]
            index = next(
                i for i in range(3) if _paper(i).canonical_paper_id == paper.canonical_paper_id
            )
            events.append(f"analyze:{index}")
            return type("Generated", (), {"bundle": _bundle(index), "usage": _usage(index)})()

    pipeline = M2EvidencePipeline(
        client=OrderedClient(),  # type: ignore[arg-type]
        bindings=repository,
        analyzer=OrderedAnalyzer(),  # type: ignore[arg-type]
        max_concurrency=2,
    )
    asyncio.run(
        pipeline.run(
            papers=[_paper(index) for index in range(3)],
            question="How is retrieval adapted?",
        )
    )

    assert all(event.startswith("retrieve:") for event in events[:3])
    assert all(event.startswith("analyze:") for event in events[3:])
