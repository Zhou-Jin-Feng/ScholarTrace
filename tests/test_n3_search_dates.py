"""Reviewed date scope is enforced on returned metadata, not trusted to providers."""

import asyncio
from datetime import UTC, date, datetime

import httpx
import pytest

from scholartrace.search.cache import MemoryResponseCache
from scholartrace.search.http import AcademicHttpClient
from scholartrace.search.models import PaperCandidate, SearchRequest, SourcePolicy
from scholartrace.search.providers import ArxivSource, CrossrefSource, candidate_within_dates


def candidate(**changes):
    values = dict(
        candidate_id="candidate:one", source="arxiv", source_id="2609.00001v1",
        title="Research", authors=["Researcher"], publication_year=2026,
        arxiv_id="2609.00001", arxiv_version=1, access_level="metadata",
        publication_date=date(2026, 9, 18), retrieved_at=datetime.now(UTC),
        record_sha256="a" * 64,
    )
    return PaperCandidate(**(values | changes))


@pytest.mark.parametrize("changes,expected", [
    ({}, True),
    ({"publication_date": date(2026, 9, 19)}, False),
    ({"publication_date": None}, False),
    ({"publication_date": None, "publication_year": 2025}, True),
    ({"arxiv_version": 2, "version_date": None}, False),
    ({"arxiv_version": 2, "version_date": date(2026, 9, 18)}, True),
    ({"arxiv_version": 2, "version_date": date(2026, 9, 19)}, False),
    ({"publication_year": 2019, "publication_date": date(2019, 1, 1)}, False),
])
def test_cutoff_is_inclusive_and_unknown_precision_is_not_invented(changes, expected):
    request = SearchRequest(query="Research", from_year=2020, to_year=2026,
                            published_before=date(2026, 9, 18))
    assert candidate_within_dates(candidate(**changes), request) is expected


def test_year_only_metadata_can_pass_at_year_end():
    request = SearchRequest(query="Research", published_before=date(2026, 12, 31))
    assert candidate_within_dates(candidate(publication_date=None), request)


@pytest.mark.parametrize("parts,expected", [
    ([2026], None), ([2026, 9], None), ([2026, 2, 30], None),
    ([2026, 9, 18], date(2026, 9, 18)),
])
def test_crossref_partial_dates_are_not_completed(parts, expected):
    assert CrossrefSource._date({"date-parts": [parts]}) == expected


def test_actual_adapter_excludes_later_version_and_audits_count():
    async def run():
        entries = []
        for number, published, updated in [
            (1, "2026-09-18", "2026-09-18"),
            (2, "2026-09-17", "2026-09-19"),
            (3, "2026-09-19", "2026-09-19"),
        ]:
            entries.append(
                f"<entry><id>http://arxiv.org/abs/2609.0000{number}v2</id>"
                "<title>Research evidence</title><author><name>A</name></author>"
                f"<published>{published}T00:00:00Z</published>"
                f"<updated>{updated}T00:00:00Z</updated></entry>"
            )
        body = '<feed xmlns="http://www.w3.org/2005/Atom">' + ''.join(entries) + '</feed>'
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=body)
        )) as client:
            source = ArxivSource(AcademicHttpClient(
                source="arxiv", client=client, cache=MemoryResponseCache(),
                policy=SourcePolicy(max_network_requests=1, max_attempts=1),
            ))
            result = await source.search(SearchRequest(
                query="Research", published_before=date(2026, 9, 18),
            ))
            assert len(result.candidates) == result.request.candidate_count == 1
            assert result.candidates[0].arxiv_id == "2609.00001"
            assert result.candidates[0].version_date == date(2026, 9, 18)
            assert "excluded 2" in result.request.public_reason
            assert result.request.status == "succeeded"
    asyncio.run(run())


def test_failed_search_identity_still_binds_reviewed_cutoff():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(503)
        )) as client:
            source = ArxivSource(AcademicHttpClient(
                source="arxiv", client=client, cache=MemoryResponseCache(),
                policy=SourcePolicy(max_network_requests=2, max_attempts=1),
            ))
            first = await source.search(SearchRequest(
                query="Research", published_before=date(2026, 9, 18),
            ))
            second = await source.search(SearchRequest(
                query="Research", published_before=date(2026, 9, 19),
            ))
            assert first.request.status == second.request.status == "failed"
            assert first.request.request_id != second.request.request_id
            assert first.request.error_code == "service_unavailable"
    asyncio.run(run())
