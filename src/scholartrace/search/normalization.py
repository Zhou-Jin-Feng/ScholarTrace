"""Conservative cross-source paper identity normalization and deterministic ranking."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import combinations

from scholartrace.contracts import Paper, PaperSource
from scholartrace.search.identifiers import (
    ARXIV_DOI_PREFIX,
    arxiv_id_from_doi,
    canonical_id,
    normalize_doi,
    normalize_openalex_id,
    normalize_person,
    normalize_semantic_scholar_id,
    normalize_title,
    parse_arxiv_id,
)
from scholartrace.search.models import MergeDecision, PaperCandidate, RankedPaper

SOURCE_PRIORITY = {
    "crossref": 4,
    "openalex": 3,
    "arxiv": 2,
    "semantic_scholar": 1,
}
QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, index: int) -> int:
        while self.parent[index] != index:
            self.parent[index] = self.parent[self.parent[index]]
            index = self.parent[index]
        return index

    def union(self, left: int, right: int) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return False
        lower, upper = sorted((left_root, right_root))
        self.parent[upper] = lower
        return True


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    papers: list[Paper]
    decisions: list[MergeDecision]
    candidate_to_paper: dict[str, str]


def _identity_keys(candidate: PaperCandidate) -> set[str]:
    keys: set[str] = set()
    doi = normalize_doi(candidate.doi)
    arxiv_id, _ = parse_arxiv_id(candidate.arxiv_id)
    arxiv_id = arxiv_id or arxiv_id_from_doi(doi)
    openalex_id = normalize_openalex_id(candidate.openalex_id)
    semantic_scholar_id = normalize_semantic_scholar_id(candidate.semantic_scholar_id)
    if doi:
        keys.add(f"doi:{doi}")
    if arxiv_id:
        keys.add(f"arxiv:{arxiv_id}")
    if openalex_id:
        keys.add(f"openalex:{openalex_id}")
    if semantic_scholar_id:
        keys.add(f"s2:{semantic_scholar_id}")
    return keys


def _cluster_values(cluster: list[PaperCandidate], field: str) -> set[str]:
    values: set[str] = set()
    for candidate in cluster:
        value = getattr(candidate, field)
        if field == "doi":
            normalized = normalize_doi(value)
        elif field == "arxiv_id":
            normalized, _ = parse_arxiv_id(value)
            normalized = normalized or arxiv_id_from_doi(candidate.doi)
        else:
            normalized = value
        if normalized:
            values.add(str(normalized))
    return values


def _strong_conflict(left: list[PaperCandidate], right: list[PaperCandidate]) -> bool:
    left_dois = _cluster_values(left, "doi")
    right_dois = _cluster_values(right, "doi")
    left_arxiv = _cluster_values(left, "arxiv_id")
    right_arxiv = _cluster_values(right, "arxiv_id")
    if left_dois and right_dois and left_dois.isdisjoint(right_dois):
        return True
    if left_arxiv and right_arxiv and left_arxiv.isdisjoint(right_arxiv):
        return True
    left_formal_doi = {doi for doi in left_dois if not doi.startswith(ARXIV_DOI_PREFIX)}
    right_formal_doi = {doi for doi in right_dois if not doi.startswith(ARXIV_DOI_PREFIX)}
    return bool((left_formal_doi and right_arxiv) or (right_formal_doi and left_arxiv))


def _decision_id(action: str, left_ids: list[str], right_ids: list[str]) -> str:
    payload = "\0".join((action, *sorted(left_ids), *sorted(right_ids))).encode()
    return f"decision:{hashlib.sha256(payload).hexdigest()[:24]}"


def normalize_candidates(candidates: list[PaperCandidate]) -> NormalizationResult:
    ordered = sorted(candidates, key=lambda candidate: candidate.candidate_id)
    if not ordered:
        return NormalizationResult(papers=[], decisions=[], candidate_to_paper={})
    disjoint = _DisjointSet(len(ordered))
    decisions: list[MergeDecision] = []

    for left_index, right_index in combinations(range(len(ordered)), 2):
        left_candidate = ordered[left_index]
        right_candidate = ordered[right_index]
        shared = _identity_keys(left_candidate) & _identity_keys(right_candidate)
        if shared and disjoint.union(left_index, right_index):
            decisions.append(
                MergeDecision(
                    decision_id=_decision_id(
                        "merged",
                        [left_candidate.candidate_id],
                        [right_candidate.candidate_id],
                    ),
                    action="merged",
                    left_ids=[left_candidate.candidate_id],
                    right_ids=[right_candidate.candidate_id],
                    reason=f"exact external identity: {sorted(shared)[0]}",
                )
            )

    clusters = _clusters(ordered, disjoint)
    roots = sorted(clusters)
    for left_root, right_root in combinations(roots, 2):
        left_cluster = clusters[left_root]
        right_cluster = clusters[right_root]
        if disjoint.find(left_root) == disjoint.find(right_root):
            continue
        if _fallback_key(left_cluster) != _fallback_key(right_cluster):
            continue
        left_ids = [candidate.candidate_id for candidate in left_cluster]
        right_ids = [candidate.candidate_id for candidate in right_cluster]
        if _strong_conflict(left_cluster, right_cluster):
            decisions.append(
                MergeDecision(
                    decision_id=_decision_id("kept_separate", left_ids, right_ids),
                    action="kept_separate",
                    left_ids=left_ids,
                    right_ids=right_ids,
                    reason="matching title/author/year but conflicting strong identifiers",
                )
            )
            continue
        if disjoint.union(left_root, right_root):
            decisions.append(
                MergeDecision(
                    decision_id=_decision_id("merged", left_ids, right_ids),
                    action="merged",
                    left_ids=left_ids,
                    right_ids=right_ids,
                    reason="exact normalized title, first author, and year without ID conflict",
                    review_required=True,
                )
            )

    final_clusters = _clusters(ordered, disjoint)
    papers: list[Paper] = []
    candidate_to_paper: dict[str, str] = {}
    cluster_candidates_by_paper: dict[str, list[PaperCandidate]] = {}
    for cluster in final_clusters.values():
        paper = _paper_from_cluster(cluster)
        papers.append(paper)
        cluster_candidates_by_paper[paper.canonical_paper_id] = cluster
        for candidate in cluster:
            candidate_to_paper[candidate.candidate_id] = paper.canonical_paper_id

    papers_by_id = {paper.canonical_paper_id: paper for paper in papers}
    for left_paper, right_paper in combinations(
        sorted(papers, key=lambda paper: paper.canonical_paper_id), 2
    ):
        left_cluster = cluster_candidates_by_paper[left_paper.canonical_paper_id]
        right_cluster = cluster_candidates_by_paper[right_paper.canonical_paper_id]
        if not _version_match(left_paper, right_paper, left_cluster, right_cluster):
            continue
        preferred, secondary = _preferred_version(left_paper, right_paper)
        papers_by_id[secondary.canonical_paper_id] = secondary.model_copy(
            update={"version_of": preferred.canonical_paper_id}
        )
        decisions.append(
            MergeDecision(
                decision_id=_decision_id(
                    "version_linked",
                    [secondary.canonical_paper_id],
                    [preferred.canonical_paper_id],
                ),
                action="version_linked",
                left_ids=[secondary.canonical_paper_id],
                right_ids=[preferred.canonical_paper_id],
                reason=(
                    "same normalized title and first author with an "
                    "arXiv/independent-DOI version boundary"
                ),
                review_required=True,
            )
        )

    result_papers = sorted(papers_by_id.values(), key=lambda paper: paper.canonical_paper_id)
    result_decisions = sorted(decisions, key=lambda decision: decision.decision_id)
    return NormalizationResult(
        papers=result_papers,
        decisions=result_decisions,
        candidate_to_paper=candidate_to_paper,
    )


def _clusters(
    candidates: list[PaperCandidate], disjoint: _DisjointSet
) -> dict[int, list[PaperCandidate]]:
    clusters: dict[int, list[PaperCandidate]] = {}
    for index, candidate in enumerate(candidates):
        clusters.setdefault(disjoint.find(index), []).append(candidate)
    return clusters


def _fallback_key(cluster: list[PaperCandidate]) -> tuple[str, str, int]:
    preferred = _preferred_candidate(cluster)
    return (
        normalize_title(preferred.title),
        normalize_person(preferred.authors[0]),
        preferred.publication_year,
    )


def _preferred_candidate(cluster: list[PaperCandidate]) -> PaperCandidate:
    return max(
        cluster,
        key=lambda candidate: (
            SOURCE_PRIORITY[candidate.source],
            bool(candidate.abstract),
            len(candidate.abstract or ""),
            candidate.candidate_id,
        ),
    )


def _paper_from_cluster(cluster: list[PaperCandidate]) -> Paper:
    preferred = _preferred_candidate(cluster)
    doi = next(
        (normalize_doi(candidate.doi) for candidate in cluster if normalize_doi(candidate.doi)),
        None,
    )
    arxiv_id = next(
        (
            parsed
            for candidate in cluster
            for parsed, _ in (parse_arxiv_id(candidate.arxiv_id),)
            if parsed
        ),
        arxiv_id_from_doi(doi),
    )
    openalex_id = next(
        (
            normalized
            for candidate in cluster
            for normalized in (normalize_openalex_id(candidate.openalex_id),)
            if normalized
        ),
        None,
    )
    semantic_scholar_id = next(
        (
            normalized
            for candidate in cluster
            for normalized in (normalize_semantic_scholar_id(candidate.semantic_scholar_id),)
            if normalized
        ),
        None,
    )
    abstract_candidate = max(cluster, key=lambda candidate: len(candidate.abstract or ""))
    authors_candidate = max(cluster, key=lambda candidate: len(candidate.authors))
    sources = sorted(
        (
            PaperSource(
                source=candidate.source,
                source_id=candidate.source_id,
                retrieved_at=candidate.retrieved_at,
                record_sha256=candidate.record_sha256,
            )
            for candidate in cluster
        ),
        key=lambda source: (source.source, source.source_id),
    )
    return Paper(
        canonical_paper_id=canonical_id(
            doi=doi,
            arxiv_id=arxiv_id,
            openalex_id=openalex_id,
            semantic_scholar_id=semantic_scholar_id,
        ),
        title=preferred.title,
        normalized_title=normalize_title(preferred.title),
        authors=authors_candidate.authors,
        publication_year=preferred.publication_year,
        doi=doi,
        arxiv_id=arxiv_id,
        openalex_id=openalex_id,
        semantic_scholar_id=semantic_scholar_id,
        abstract=abstract_candidate.abstract,
        access_level="abstract" if abstract_candidate.abstract else "metadata",
        sources=sources,
    )


def _version_match(
    left: Paper,
    right: Paper,
    left_cluster: list[PaperCandidate],
    right_cluster: list[PaperCandidate],
) -> bool:
    if left.normalized_title != right.normalized_title:
        return False
    if normalize_person(left.authors[0]) != normalize_person(right.authors[0]):
        return False
    if abs(left.publication_year - right.publication_year) > 2:
        return False
    left_arxiv = bool(_cluster_values(left_cluster, "arxiv_id"))
    right_arxiv = bool(_cluster_values(right_cluster, "arxiv_id"))
    left_non_arxiv_doi = any(
        not doi.startswith(ARXIV_DOI_PREFIX) for doi in _cluster_values(left_cluster, "doi")
    )
    right_non_arxiv_doi = any(
        not doi.startswith(ARXIV_DOI_PREFIX) for doi in _cluster_values(right_cluster, "doi")
    )
    return (left_arxiv and right_non_arxiv_doi) or (right_arxiv and left_non_arxiv_doi)


def _version_score(paper: Paper) -> tuple[int, int, str]:
    doi = normalize_doi(paper.doi)
    if doi and not doi.startswith(ARXIV_DOI_PREFIX):
        identity_score = 4
    elif doi:
        identity_score = 3
    elif paper.arxiv_id:
        identity_score = 2
    else:
        identity_score = 1
    return identity_score, paper.publication_year, paper.canonical_paper_id


def _preferred_version(left: Paper, right: Paper) -> tuple[Paper, Paper]:
    if _version_score(left) >= _version_score(right):
        return left, right
    return right, left


def rank_papers(query: str, papers: list[Paper]) -> list[RankedPaper]:
    query_terms = {
        term
        for term in normalize_title(query).split()
        if len(term) > 1 and term not in QUERY_STOPWORDS
    }
    query_phrase = normalize_title(query)
    ranked: list[RankedPaper] = []
    for paper in papers:
        title_terms = set(paper.normalized_title.split())
        abstract_terms = set(normalize_title(paper.abstract or "").split())
        title_matches = query_terms & title_terms
        abstract_matches = query_terms & abstract_terms
        phrase_bonus = 8.0 if query_phrase and query_phrase in paper.normalized_title else 0.0
        score = phrase_bonus + 4.0 * len(title_matches) + 0.5 * len(abstract_matches)
        ranked.append(
            RankedPaper(
                paper=paper,
                score=score,
                matched_query_terms=sorted(title_matches | abstract_matches),
            )
        )
    return sorted(
        ranked,
        key=lambda item: (-item.score, -item.paper.publication_year, item.paper.canonical_paper_id),
    )


def candidate_set_sha256(ranked_papers: list[RankedPaper]) -> str:
    payload: list[dict[str, object]] = []
    for ranked in ranked_papers:
        paper_payload = ranked.paper.model_dump(mode="json")
        for source in paper_payload["sources"]:
            source.pop("retrieved_at", None)
        payload.append(
            {
                "paper": paper_payload,
                "score": ranked.score,
                "matched_query_terms": ranked.matched_query_terms,
            }
        )
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def pairwise_cluster_metrics(
    *,
    expected_cluster_by_candidate: dict[str, str],
    predicted_paper_by_candidate: dict[str, str],
) -> dict[str, float]:
    candidate_ids = sorted(expected_cluster_by_candidate)
    if set(candidate_ids) != set(predicted_paper_by_candidate):
        raise ValueError("expected and predicted candidates must match")
    true_positive = false_positive = false_negative = 0
    for left, right in combinations(candidate_ids, 2):
        expected_same = expected_cluster_by_candidate[left] == expected_cluster_by_candidate[right]
        predicted_same = predicted_paper_by_candidate[left] == predicted_paper_by_candidate[right]
        if expected_same and predicted_same:
            true_positive += 1
        elif predicted_same:
            false_positive += 1
        elif expected_same:
            false_negative += 1
    precision = (
        true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    )
    recall = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}
