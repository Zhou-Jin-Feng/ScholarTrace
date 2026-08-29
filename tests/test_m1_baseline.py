from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from scholartrace.search.baseline import OllamaBaselineGenerator
from scholartrace.search.models import (
    PaperCandidate,
    SearchSnapshot,
    SourceRequestRecord,
    SourceSearchResult,
)
from scholartrace.search.normalization import normalize_candidates, rank_papers
from scholartrace.search.storage import write_text

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 29, 8, tzinfo=UTC)


def _snapshot() -> SearchSnapshot:
    raw = json.loads((ROOT / "evaluation/seeds/m1_dedup_gold.json").read_text("utf-8"))[
        "candidates"
    ][0]
    payload = dict(raw)
    payload.pop("expected_cluster")
    candidate = PaperCandidate.model_validate(payload)
    result = SourceSearchResult(
        source="arxiv",
        request=SourceRequestRecord(
            request_id="request:arxiv:baseline-fixture",
            source="arxiv",
            public_url="https://export.arxiv.org/api/query",
            public_params={"search_query": "corrective retrieval"},
            started_at=NOW,
            completed_at=NOW,
            duration_seconds=0,
            cache_hit=False,
            attempts=1,
            status="succeeded",
            http_status=200,
            response_sha256="f" * 64,
            candidate_count=1,
        ),
        candidates=[candidate],
    )
    papers = normalize_candidates([candidate]).papers
    return SearchSnapshot(
        snapshot_id="snapshot:baseline-fixture",
        query="corrective retrieval",
        generated_at=NOW,
        source_results=[result],
        ranked_papers=rank_papers("corrective retrieval", papers),
        merge_decisions=[],
        candidate_set_sha256="a" * 64,
        outcome="succeeded",
    )


def _response_content(paper_id: str) -> str:
    return json.dumps(
        {
            "answer": "CRAG evaluates retrieval quality before choosing a corrective action.",
            "cited_paper_ids": [paper_id],
            "limitations": ["Abstract-only evidence."],
        }
    )


def test_ollama_baseline_records_bounded_structured_usage() -> None:
    snapshot = _snapshot()
    allowed_id = snapshot.ranked_papers[0].paper.canonical_paper_id

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3:8b"
        assert payload["think"] is False
        assert payload["options"]["num_ctx"] == 8192
        supplied = json.loads(payload["messages"][1]["content"])["papers"]
        assert [paper["paper_id"] for paper in supplied] == [allowed_id]
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": _response_content(allowed_id)},
                "prompt_eval_count": 120,
                "eval_count": 35,
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await OllamaBaselineGenerator(client=client).generate(snapshot, baseline="B1")
        assert result.artifact.generator == "ollama_qwen3_8b"
        assert result.artifact.model_profile_id == "local-qwen3-8b"
        assert result.artifact.candidate_paper_ids == [allowed_id]
        assert result.usage.input_tokens == 120
        assert result.usage.output_tokens == 35
        assert result.usage.call_count == 1
        assert result.usage.retry_count == 0
        assert result.usage.billed_cost_cny == 0

    asyncio.run(scenario())


def test_ollama_baseline_retries_and_rejects_unknown_citation_ids() -> None:
    snapshot = _snapshot()
    allowed_id = snapshot.ranked_papers[0].paper.canonical_paper_id
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        cited_id = "doi:10.1000/not-supplied" if calls == 1 else allowed_id
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": _response_content(cited_id)},
                "prompt_eval_count": 100,
                "eval_count": 25,
            },
            request=request,
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await OllamaBaselineGenerator(client=client).generate(snapshot, baseline="B0")
        assert result.artifact.candidate_paper_ids == [allowed_id]
        assert result.usage.call_count == 2
        assert result.usage.retry_count == 1
        assert result.usage.structured_repair_count == 1

    asyncio.run(scenario())
    assert calls == 2


def test_ollama_baseline_fails_closed_after_invalid_citations() -> None:
    snapshot = _snapshot()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": _response_content("doi:10.1000/not-supplied"),
                }
            },
            request=request,
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match="failed after 2 attempts"):
                await OllamaBaselineGenerator(client=client).generate(snapshot, baseline="B1")

    asyncio.run(scenario())


def test_artifact_text_hash_matches_windows_file_bytes(tmp_path: Path) -> None:
    content = "first line\nsecond line\n"
    output = tmp_path / "baseline.md"

    stored_hash = write_text(output, content)

    assert output.read_bytes() == content.encode("utf-8")
    assert stored_hash == hashlib.sha256(output.read_bytes()).hexdigest()
