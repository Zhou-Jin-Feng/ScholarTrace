from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scholartrace.search.baseline import build_baselines
from scholartrace.search.manifest import build_run_manifest
from scholartrace.search.models import (
    PaperCandidate,
    SearchRequest,
    SourceRequestRecord,
    SourceSearchResult,
)
from scholartrace.search.pipeline import SearchPipeline, replay_snapshot

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 29, 8, tzinfo=UTC)


@dataclass
class FakeSource:
    name: str
    result: SourceSearchResult

    async def search(self, request: SearchRequest) -> SourceSearchResult:
        return self.result


def _candidate() -> PaperCandidate:
    raw = json.loads((ROOT / "evaluation/seeds/m1_dedup_gold.json").read_text("utf-8"))[
        "candidates"
    ][0]
    payload = dict(raw)
    payload.pop("expected_cluster")
    return PaperCandidate.model_validate(payload)


def _result(*, source: str, status: str, candidates: list[PaperCandidate]) -> SourceSearchResult:
    failed = status == "failed"
    return SourceSearchResult(
        source=source,
        request=SourceRequestRecord(
            request_id=f"request:{source}:fixture",
            source=source,
            public_url=f"https://{source}.example.test",
            public_params={"query": "CRAG"},
            started_at=NOW,
            completed_at=NOW,
            duration_seconds=0,
            cache_hit=False,
            attempts=1,
            status=status,
            http_status=503 if failed else 200,
            error_code="service_unavailable" if failed else None,
            public_reason="fixture failure" if failed else None,
            response_sha256=None if failed else "f" * 64,
            candidate_count=len(candidates),
        ),
        candidates=candidates,
    )


def test_pipeline_degrades_one_source_and_replays_identically() -> None:
    arxiv = FakeSource(
        "arxiv", _result(source="arxiv", status="succeeded", candidates=[_candidate()])
    )
    crossref = FakeSource("crossref", _result(source="crossref", status="failed", candidates=[]))
    snapshot = asyncio.run(
        SearchPipeline([arxiv, crossref]).run(SearchRequest(query="corrective retrieval"))  # type: ignore[list-item]
    )
    assert snapshot.outcome == "degraded"
    assert len(snapshot.ranked_papers) == 1
    assert replay_snapshot(snapshot) == snapshot.candidate_set_sha256


def test_pipeline_filters_weak_lexical_candidates() -> None:
    weak = _candidate().model_copy(
        update={"title": "Unrelated benchmark", "abstract": "One retrieval mention."}
    )
    source = FakeSource("arxiv", _result(source="arxiv", status="succeeded", candidates=[weak]))
    snapshot = asyncio.run(
        SearchPipeline([source], min_relevance_score=4).run(  # type: ignore[list-item]
            SearchRequest(query="corrective retrieval augmented generation")
        )
    )
    assert snapshot.ranked_papers == []
    assert snapshot.outcome == "failed"


def test_b0_b1_are_deterministic_and_state_their_evidence_limits() -> None:
    source = FakeSource(
        "arxiv", _result(source="arxiv", status="succeeded", candidates=[_candidate()])
    )
    snapshot = asyncio.run(
        SearchPipeline([source]).run(SearchRequest(query="corrective retrieval"))  # type: ignore[list-item]
    )
    first = build_baselines(snapshot, generated_at=NOW)
    second = build_baselines(snapshot, generated_at=NOW)
    assert first == second
    assert "metadata" in first[0].content_markdown.lower()
    assert any("full text" in limitation for limitation in first[1].limitations)


def test_manifest_records_snapshot_budget_and_dirty_source_tree() -> None:
    source = FakeSource(
        "arxiv", _result(source="arxiv", status="succeeded", candidates=[_candidate()])
    )
    snapshot = asyncio.run(
        SearchPipeline([source]).run(SearchRequest(query="corrective retrieval"))  # type: ignore[list-item]
    )
    manifest = build_run_manifest(
        root=ROOT,
        snapshot=snapshot,
        snapshot_path=ROOT / "artifacts" / "fixture" / "search_snapshot.json",
        snapshot_file_sha256="a" * 64,
        started_at=NOW,
        completed_at=NOW,
        git_commit="b" * 40,
        worktree_dirty=True,
        source_tree_sha256="c" * 64,
    )
    assert manifest.outcome == "succeeded"
    assert manifest.budget.usage.api_calls == 1
    assert manifest.budget.usage.model_calls == 0
    assert manifest.data_snapshot_refs[0].artifact_type == "search_snapshot"
    assert manifest.service_baselines[0].worktree_dirty is True
