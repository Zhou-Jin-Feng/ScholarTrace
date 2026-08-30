from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import respx

from scholartrace.citations.live_smoke import run_openalex_live_smoke
from scholartrace.citations.models import CitationSeed
from scholartrace.citations.provider import OpenAlexCitationProvider

NOW = datetime(2026, 8, 30, 7, tzinfo=UTC)


def _work(
    work_id: str,
    *,
    references: list[str],
    year: int = 2024,
) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": None,
        "title": f"Work {work_id}",
        "publication_year": year,
        "authorships": [{"author": {"display_name": "Test Author"}}],
        "ids": {"openalex": f"https://openalex.org/{work_id}"},
        "abstract_inverted_index": {"safe": [0], "fixture": [1]},
        "primary_location": {"landing_page_url": f"https://example.test/{work_id}"},
        "type": "article",
        "referenced_works": [f"https://openalex.org/{item}" for item in references],
    }


def test_online_smoke_uses_two_network_requests_then_cache_only_replay(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        filter_value = request.url.params["filter"]
        assert "api_key" not in request.url.params
        if "W100" in filter_value or "W101" in filter_value:
            results = [
                _work("W100", references=["W200"]),
                _work("W101", references=["W300"]),
            ]
        else:
            results = [
                _work("W200", references=[], year=2022),
                _work("W300", references=[], year=2023),
            ]
        return httpx.Response(200, json={"meta": {"cost_usd": 0.001}, "results": results})

    with respx.mock(assert_all_called=True) as router:
        route = router.get(OpenAlexCitationProvider.endpoint).mock(side_effect=handler)
        summary = asyncio.run(
            run_openalex_live_smoke(
                seeds=[
                    CitationSeed(canonical_paper_id="openalex:W100", openalex_id="W100"),
                    CitationSeed(canonical_paper_id="openalex:W101", openalex_id="W101"),
                ],
                cache_dir=tmp_path,
                api_key=None,
                max_discovered_papers=10,
            )
        )
        assert route.call_count == 2

    assert summary["passed"] is True
    assert summary["network_attempts"] == 2
    assert summary["citation_edge_count"] == 2
    assert summary["metadata_requested_count"] == 2
    assert summary["bounded_metadata_resolution_ratio"] == 1.0
    assert summary["deferred_due_to_bound_count"] == 0
    assert summary["requested_unresolved_count"] == 0
    assert summary["cached_replay_passed"] is True
    assert summary["credential_mode"] == "anonymous"
    assert "Work W100" not in json_text(summary)


def json_text(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)
