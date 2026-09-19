from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from scholartrace.search.cache import MemoryResponseCache
from scholartrace.search.http import AcademicHttpClient, HttpPayload
from scholartrace.search.models import SearchRequest, SourcePolicy
from scholartrace.search.providers import (
    ArxivSource,
    CrossrefSource,
    OpenAlexSource,
    SemanticScholarSource,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "academic"
NOW = datetime(2026, 8, 29, 8, tzinfo=UTC)


def _payload(path: Path) -> HttpPayload:
    body = path.read_bytes()
    return HttpPayload(
        status_code=200,
        headers={"content-type": "application/json"},
        body=body,
        retrieved_at=NOW,
        duration_seconds=0.1,
        cache_hit=False,
        attempts=1,
        response_sha256="f" * 64,
    )


def _http(source: str) -> AcademicHttpClient:
    return AcademicHttpClient(
        source=source,  # type: ignore[arg-type]
        client=httpx.AsyncClient(),
        cache=MemoryResponseCache(),
        policy=SourcePolicy(),
    )


def test_arxiv_atom_fixture_maps_versions_and_abstracts() -> None:
    source = ArxivSource(_http("arxiv"))
    candidates, cost = source.parse(_payload(FIXTURES / "arxiv.xml"))
    assert cost == 0
    assert [candidate.arxiv_id for candidate in candidates] == ["2401.15884", "2403.14403"]
    assert [candidate.arxiv_version for candidate in candidates] == [3, 2]
    assert all(candidate.access_level == "abstract" for candidate in candidates)


def test_openalex_fixture_rebuilds_abstract_and_records_reported_cost() -> None:
    test_credential = "private-" + "test-key"
    source = OpenAlexSource(_http("openalex"), api_key=test_credential)
    candidates, cost = source.parse(_payload(FIXTURES / "openalex.json"))
    assert cost == 0.001
    assert candidates[0].openalex_id == "W4391418506"
    assert candidates[0].arxiv_id == "2401.15884"
    assert candidates[0].abstract == "CRAG evaluates retrieval quality"
    public, private, _ = source.request_parts(SearchRequest(query="CRAG"))
    assert "api_key" not in public
    assert private == {"api_key": test_credential}


def test_openalex_search_text_drops_wildcard_characters() -> None:
    source = OpenAlexSource(_http("openalex"))
    public, _, _ = source.request_parts(SearchRequest(query="What about RAG?"))
    assert public["search"] == "What about RAG"
    public, _, _ = source.request_parts(SearchRequest(query="retrieval* ranking ?"))
    assert public["search"] == "retrieval ranking"


def test_openalex_wildcard_only_query_is_rejected() -> None:
    source = OpenAlexSource(_http("openalex"))
    with pytest.raises(ValueError, match="non-wildcard"):
        source.request_parts(SearchRequest(query="??"))


def test_openalex_wildcard_only_query_fails_without_network_call() -> None:
    async def scenario() -> str:
        async with httpx.AsyncClient() as client:
            source = OpenAlexSource(
                AcademicHttpClient(
                    source="openalex",
                    client=client,
                    cache=MemoryResponseCache(),
                    policy=SourcePolicy(max_network_requests=1, max_attempts=1),
                )
            )
            result = await source.search(SearchRequest(query="??"))
            return result.request.status

    with respx.mock(assert_all_called=False):
        assert asyncio.run(scenario()) == "failed"


def test_crossref_fixture_uses_valid_select_and_strips_jats_markup() -> None:
    source = CrossrefSource(_http("crossref"), contact_email="private@example.test")
    candidates, cost = source.parse(_payload(FIXTURES / "crossref.json"))
    assert cost == 0
    assert candidates[0].doi == "10.2139/ssrn.5267341"
    assert (
        candidates[0].abstract == "CRAG evaluates retrieval quality before corrective generation."
    )
    public, private, headers = source.request_parts(SearchRequest(query="CRAG"))
    assert "subtype" not in public["select"]
    assert "mailto" not in public
    assert private == {"mailto": "private@example.test"}
    assert "private@example.test" in headers["User-Agent"]


def test_semantic_scholar_fixture_maps_external_identifiers() -> None:
    test_credential = "private-" + "test-key"
    source = SemanticScholarSource(_http("semantic_scholar"), api_key=test_credential)
    candidates, cost = source.parse(_payload(FIXTURES / "semantic_scholar.json"))
    assert cost == 0
    assert candidates[0].semantic_scholar_id == "a" * 40
    assert candidates[0].doi == "10.48550/arxiv.2401.15884"
    public, private, headers = source.request_parts(SearchRequest(query="CRAG"))
    assert private == {}
    assert "x-api-key" not in public
    assert headers["x-api-key"] == test_credential


def test_fixture_documents_are_valid_json() -> None:
    for name in ("openalex.json", "crossref.json", "semantic_scholar.json"):
        assert isinstance(json.loads((FIXTURES / name).read_text("utf-8")), dict)


def test_empty_provider_response_is_successful_empty_not_failure() -> None:
    async def scenario() -> str:
        async with httpx.AsyncClient() as client:
            source = OpenAlexSource(
                AcademicHttpClient(
                    source="openalex",
                    client=client,
                    cache=MemoryResponseCache(),
                    policy=SourcePolicy(max_network_requests=1, max_attempts=1),
                )
            )
            result = await source.search(SearchRequest(query="no matching paper"))
            return result.request.status

    with respx.mock(assert_all_called=True) as router:
        router.get(OpenAlexSource.endpoint).mock(
            return_value=httpx.Response(200, json={"meta": {}, "results": []})
        )
        assert asyncio.run(scenario()) == "empty"
