from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

import httpx
import pytest
import respx

from scholartrace.citations.graph import CitationGraphBuilder
from scholartrace.citations.lifecycle import CitationLifecycleError, CitationLifecycleGate
from scholartrace.citations.models import CitationEdge, CitationExpansionResult
from scholartrace.citations.provider import OpenAlexCitationProvider
from scholartrace.contracts import ArtifactRef, DocuMindBinding, Paper, PaperSource
from scholartrace.search.cache import MemoryResponseCache
from scholartrace.search.http import AcademicHttpClient
from scholartrace.search.models import SourcePolicy

NOW = datetime(2026, 8, 30, 4, tzinfo=UTC)


def _paper(
    paper_id: str,
    openalex_id: str,
    *,
    title: str = "Seed paper",
    year: int = 2024,
) -> Paper:
    return Paper(
        canonical_paper_id=paper_id,
        title=title,
        normalized_title=title.casefold(),
        authors=["Test Author"],
        publication_year=year,
        openalex_id=openalex_id,
        access_level="abstract",
        sources=[
            PaperSource(
                source="openalex",
                source_id=f"https://openalex.org/{openalex_id}",
                retrieved_at=NOW,
                record_sha256="a" * 64,
            )
        ],
    )


def _work(
    work_id: str,
    title: str,
    year: int,
    *,
    references: list[str] | None = None,
) -> dict[str, object]:
    work: dict[str, object] = {
        "id": f"https://openalex.org/{work_id}",
        "doi": None,
        "title": title,
        "publication_year": year,
        "authorships": [{"author": {"display_name": "Test Author"}}],
        "ids": {"openalex": f"https://openalex.org/{work_id}"},
        "abstract_inverted_index": {"test": [0], "abstract": [1]},
        "primary_location": {"landing_page_url": f"https://example.test/{work_id}"},
        "type": "article",
    }
    if references is not None:
        work["referenced_works"] = [f"https://openalex.org/{item}" for item in references]
    return work


def _citation_http(client: httpx.AsyncClient) -> AcademicHttpClient:
    return AcademicHttpClient(
        source="openalex",
        client=client,
        cache=MemoryResponseCache(),
        policy=SourcePolicy(max_network_requests=2, max_attempts=1),
    )


def test_openalex_citation_expansion_preserves_provenance_and_degrades_on_missing_metadata(
) -> None:
    credential = "private-test-key"

    def handler(request: httpx.Request) -> httpx.Response:
        filter_value = request.url.params["filter"]
        assert request.url.params.get("api_key") == credential
        if "W100" in filter_value:
            results = [_work("W100", "Seed paper", 2024, references=["W200", "W300"])]
        else:
            results = [_work("W200", "Earlier paper", 2022, references=[])]
        return httpx.Response(200, json={"meta": {"cost_usd": 0.001}, "results": results})

    async def scenario() -> CitationExpansionResult:
        async with httpx.AsyncClient() as client:
            provider = OpenAlexCitationProvider(
                _citation_http(client),
                api_key=credential,
            )
            return await provider.expand([_paper("doi:10.1000/seed", "W100")])

    with respx.mock(assert_all_called=True) as router:
        route = router.get(OpenAlexCitationProvider.endpoint).mock(side_effect=handler)
        result = asyncio.run(scenario())
        assert route.call_count == 2

    assert result.outcome == "degraded"
    assert len(result.edges) == 2
    assert {edge.cited_paper_id for edge in result.edges} == {
        "openalex:W200",
        "openalex:W300",
    }
    assert result.unresolved_openalex_ids == ["W300"]
    assert result.edges[0].response_sha256
    assert all("api_key" not in record.public_params for record in result.request_records)
    assert credential not in str(result.model_dump(mode="json"))


def test_missing_reference_list_is_a_visible_degradation_not_an_empty_graph_success() -> None:
    async def scenario() -> CitationExpansionResult:
        async with httpx.AsyncClient() as client:
            provider = OpenAlexCitationProvider(_citation_http(client))
            return await provider.expand([_paper("openalex:W100", "W100")])

    with respx.mock(assert_all_called=True) as router:
        router.get(OpenAlexCitationProvider.endpoint).mock(
            return_value=httpx.Response(
                200,
                json={"meta": {}, "results": [_work("W100", "Seed", 2024)]},
            )
        )
        result = asyncio.run(scenario())
    assert result.outcome == "degraded"
    assert result.edges == []
    assert result.missing_reference_paper_ids == ["openalex:W100"]


def test_citation_lifecycle_cannot_skip_documind_ingestion() -> None:
    paper = _paper("openalex:W200", "W200", title="Earlier paper", year=2022)
    gate = CitationLifecycleGate(clock=lambda: NOW)
    gate.register(paper)
    with pytest.raises(CitationLifecycleError, match="expected ingested"):
        gate.mark_analyzed(
            paper.canonical_paper_id,
            analysis_ref=ArtifactRef(
                artifact_id="artifact:m4:analysis:W200",
                artifact_type="paper_card",
                content_sha256="d" * 64,
                storage_uri="artifacts/m4/W200.json",
                created_at=NOW,
            ),
        )

    gate.mark_relevant(paper.canonical_paper_id)
    gate.mark_access_resolved(
        paper.canonical_paper_id,
        access_url="https://example.test/W200.pdf",
    )
    gate.mark_acquired(paper.canonical_paper_id, source_sha256="b" * 64)
    binding = DocuMindBinding(
        canonical_paper_id=paper.canonical_paper_id,
        document_key="c" * 64,
        index_id="d" * 64,
        source_sha256="b" * 64,
        documind_version="2.2.0",
        retrieval_schema_version="1.0",
    )
    gate.mark_ingested(paper.canonical_paper_id, binding=binding)
    assert gate.can_analyze(paper.canonical_paper_id)
    completed = gate.mark_analyzed(
        paper.canonical_paper_id,
        analysis_ref=ArtifactRef(
            artifact_id="artifact:m4:analysis:W200",
            artifact_type="paper_card",
            content_sha256="e" * 64,
            storage_uri="artifacts/m4/W200.json",
            created_at=NOW,
        ),
    )
    assert completed.stage == "analyzed"
    assert gate.can_verify(paper.canonical_paper_id)


def test_citation_graph_metrics_and_hash_are_deterministic() -> None:
    seed = _paper("openalex:W100", "W100", year=2024)
    cited = _paper("openalex:W200", "W200", title="Earlier paper", year=2022)
    edge = CitationEdge(
        edge_id="citation:openalex:test",
        citing_paper_id=seed.canonical_paper_id,
        cited_paper_id=cited.canonical_paper_id,
        source="openalex",
        source_work_id="W100",
        request_id="request:openalex:citation:test",
        response_sha256=hashlib.sha256(b"fixture").hexdigest(),
        retrieved_at=NOW,
    )
    reverse_edge = CitationEdge(
        edge_id="citation:openalex:reverse-test",
        citing_paper_id=cited.canonical_paper_id,
        cited_paper_id=seed.canonical_paper_id,
        source="openalex",
        source_work_id="W200",
        request_id="request:openalex:citation:reverse-test",
        response_sha256=hashlib.sha256(b"reverse-fixture").hexdigest(),
        retrieved_at=NOW,
    )
    gate = CitationLifecycleGate(clock=lambda: NOW)
    gate.register(cited)
    gate.mark_relevant(cited.canonical_paper_id)
    gate.mark_access_resolved(cited.canonical_paper_id, access_url="https://x.test/p.pdf")
    gate.mark_acquired(cited.canonical_paper_id, source_sha256="b" * 64)
    gate.mark_ingested(
        cited.canonical_paper_id,
        binding=DocuMindBinding(
            canonical_paper_id=cited.canonical_paper_id,
            document_key="c" * 64,
            index_id="d" * 64,
            source_sha256="b" * 64,
            documind_version="2.2.0",
            retrieval_schema_version="1.0",
        ),
    )
    gate.mark_analyzed(
        cited.canonical_paper_id,
        analysis_ref=ArtifactRef(
            artifact_id="artifact:m4:analysis:W200",
            artifact_type="paper_card",
            content_sha256="e" * 64,
            storage_uri="artifacts/m4/W200.json",
            created_at=NOW,
        ),
    )

    builder = CitationGraphBuilder()
    first = builder.build(
        papers=[seed, cited],
        edges=[edge, reverse_edge],
        seed_paper_ids=[seed.canonical_paper_id],
        lifecycle_records=gate.records(),
        generated_at=NOW,
    )
    second = builder.build(
        papers=[cited, seed],
        edges=[reverse_edge, edge],
        seed_paper_ids=[seed.canonical_paper_id],
        lifecycle_records=list(reversed(gate.records())),
        generated_at=NOW,
    )
    assert first == second
    assert sum(item.pagerank for item in first.metrics) == pytest.approx(1.0)
    assert first.timeline_candidates[0].earlier_paper_id == cited.canonical_paper_id
    assert {node.paper_id: node.analysis_ready for node in first.nodes} == {
        seed.canonical_paper_id: True,
        cited.canonical_paper_id: True,
    }
