from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from scholartrace.search.models import PaperCandidate
from scholartrace.search.normalization import (
    candidate_set_sha256,
    normalize_candidates,
    pairwise_cluster_metrics,
    rank_papers,
)

ROOT = Path(__file__).resolve().parents[1]


def _gold() -> tuple[dict[str, object], list[PaperCandidate], dict[str, str]]:
    suite = json.loads((ROOT / "evaluation/seeds/m1_dedup_gold.json").read_text("utf-8"))
    candidates: list[PaperCandidate] = []
    expected: dict[str, str] = {}
    for raw in suite["candidates"]:
        payload = dict(raw)
        expected_cluster = payload.pop("expected_cluster")
        candidate = PaperCandidate.model_validate(payload)
        candidates.append(candidate)
        expected[candidate.candidate_id] = expected_cluster
    return suite, candidates, expected


def test_dedup_gold_reaches_exact_pairwise_thresholds() -> None:
    suite, candidates, expected = _gold()
    result = normalize_candidates(candidates)
    metrics = pairwise_cluster_metrics(
        expected_cluster_by_candidate=expected,
        predicted_paper_by_candidate=result.candidate_to_paper,
    )
    assert len(result.papers) == suite["expected_paper_count"]
    assert metrics == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert metrics["precision"] >= suite["thresholds"]["pairwise_precision"]
    assert metrics["recall"] >= suite["thresholds"]["pairwise_recall"]
    assert metrics["f1"] >= suite["thresholds"]["pairwise_f1"]


def test_preprint_and_formal_identifier_are_version_linked_not_deleted() -> None:
    suite, candidates, _ = _gold()
    papers = {paper.canonical_paper_id: paper for paper in normalize_candidates(candidates).papers}
    for expected in suite["expected_version_links"]:
        assert papers[expected["paper_id"]].version_of == expected["version_of"]
    assert "doi:10.48550/arxiv.2401.15884" in papers
    assert "doi:10.2139/ssrn.5267341" in papers
    assert any(
        decision.action == "version_linked" and decision.review_required
        for decision in normalize_candidates(candidates).decisions
    )


def test_conflicting_dois_with_same_title_are_kept_separate() -> None:
    _, candidates, _ = _gold()
    result = normalize_candidates(candidates)
    assert "doi:10.1000/conflict-a" in {paper.canonical_paper_id for paper in result.papers}
    assert "doi:10.1000/conflict-b" in {paper.canonical_paper_id for paper in result.papers}
    assert any(decision.action == "kept_separate" for decision in result.decisions)


def test_candidate_set_digest_is_independent_of_input_order() -> None:
    _, candidates, _ = _gold()
    first = rank_papers("corrective retrieval", normalize_candidates(candidates).papers)
    second = rank_papers(
        "corrective retrieval", normalize_candidates(list(reversed(candidates))).papers
    )
    assert candidate_set_sha256(first) == candidate_set_sha256(second)


def test_candidate_set_digest_excludes_collection_time() -> None:
    _, candidates, _ = _gold()
    shifted = [
        candidate.model_copy(update={"retrieved_at": candidate.retrieved_at + timedelta(days=1)})
        for candidate in candidates
    ]
    first = rank_papers("corrective retrieval", normalize_candidates(candidates).papers)
    second = rank_papers("corrective retrieval", normalize_candidates(shifted).papers)
    assert candidate_set_sha256(first) == candidate_set_sha256(second)
