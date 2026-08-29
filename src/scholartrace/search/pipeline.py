"""Reproducible M1 multi-source search pipeline without Multi-Agent orchestration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from scholartrace.search.models import SearchRequest, SearchSnapshot
from scholartrace.search.normalization import (
    candidate_set_sha256,
    normalize_candidates,
    rank_papers,
)
from scholartrace.search.providers import AcademicSource


class SearchPipeline:
    def __init__(
        self,
        sources: list[AcademicSource],
        *,
        max_candidate_papers: int = 50,
        min_relevance_score: float = 4.0,
    ) -> None:
        if not sources:
            raise ValueError("search pipeline requires at least one source")
        if len({source.name for source in sources}) != len(sources):
            raise ValueError("search pipeline source names must be unique")
        self.sources = sources
        self.max_candidate_papers = max_candidate_papers
        self.min_relevance_score = min_relevance_score

    async def run(self, request: SearchRequest) -> SearchSnapshot:
        source_results = list(
            await asyncio.gather(*(source.search(request) for source in self.sources))
        )
        candidates = [
            candidate for source_result in source_results for candidate in source_result.candidates
        ]
        normalized = normalize_candidates(candidates)
        ranked = [
            ranked_paper
            for ranked_paper in rank_papers(request.query, normalized.papers)
            if ranked_paper.score >= self.min_relevance_score
        ][: self.max_candidate_papers]
        digest = candidate_set_sha256(ranked)
        failed_sources = sum(result.request.status == "failed" for result in source_results)
        outcome: Literal["succeeded", "degraded", "failed"]
        if not ranked:
            outcome = "failed"
        elif failed_sources:
            outcome = "degraded"
        else:
            outcome = "succeeded"
        return SearchSnapshot(
            snapshot_id=f"snapshot:search:{digest[:24]}",
            query=request.query,
            generated_at=datetime.now(UTC),
            source_results=source_results,
            ranked_papers=ranked,
            merge_decisions=normalized.decisions,
            candidate_set_sha256=digest,
            outcome=outcome,
        )


def replay_snapshot(snapshot: SearchSnapshot) -> str:
    candidates = [
        candidate
        for source_result in snapshot.source_results
        for candidate in source_result.candidates
    ]
    normalized = normalize_candidates(candidates)
    retained_ids = {ranked.paper.canonical_paper_id for ranked in snapshot.ranked_papers}
    ranked = [
        ranked_paper
        for ranked_paper in rank_papers(snapshot.query, normalized.papers)
        if ranked_paper.paper.canonical_paper_id in retained_ids
    ]
    return candidate_set_sha256(ranked)
