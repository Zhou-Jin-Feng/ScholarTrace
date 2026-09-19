"""Deterministic, reviewable execution of approved plan criteria.

Plan criteria are user-visible constraints, not an implicit classifier.  The
live runner therefore accepts a small explicit vocabulary and rejects an
unrecognised sentence before it can dispatch an external operation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scholartrace.contracts import Paper


class CriteriaError(ValueError):
    """An approved criterion cannot be evaluated deterministically."""


@dataclass(frozen=True, slots=True)
class CriteriaDecision:
    included: bool
    reason: str | None = None


def _fold(value: str) -> str:
    return " ".join(value.casefold().replace("-", " ").split())


def _contains(text: str, phrase: str) -> bool:
    return _fold(phrase) in _fold(text)


def _year_bound(value: str, operator: str) -> int | None:
    match = re.fullmatch(r"year\s*(>=|<=|>|<)\s*(\d{4})", _fold(value))
    if match is None:
        return None
    year = int(match.group(2))
    return year if match.group(1) == operator else None


def _matches_inclusion(criterion: str, paper: Paper) -> bool:
    folded = _fold(criterion)
    if folded in {"full text", "fulltext", "全文", "全文可获取"}:
        return paper.arxiv_id is not None
    if folded in {"arxiv", "arxiv preprint", "preprint", "公开预印本"}:
        return paper.arxiv_id is not None
    if folded in {"has abstract", "abstract available", "有摘要", "摘要可用"}:
        return bool(paper.abstract and paper.abstract.strip())
    if folded.startswith("title contains: "):
        return _contains(paper.title, criterion.split(":", 1)[1])
    if folded.startswith("abstract contains: "):
        return _contains(paper.abstract or "", criterion.split(":", 1)[1])
    for operator in (">=", "<=", ">", "<"):
        bound = _year_bound(criterion, operator)
        if bound is not None:
            return {
                ">=": paper.publication_year >= bound,
                "<=": paper.publication_year <= bound,
                ">": paper.publication_year > bound,
                "<": paper.publication_year < bound,
            }[operator]
    raise CriteriaError(f"unsupported inclusion criterion: {criterion}")


def _matches_exclusion(criterion: str, paper: Paper) -> bool:
    folded = _fold(criterion)
    if folded.startswith("title contains: "):
        return _contains(paper.title, criterion.split(":", 1)[1])
    if folded.startswith("abstract contains: "):
        return _contains(paper.abstract or "", criterion.split(":", 1)[1])
    # Preserve the previous reviewed-plan behaviour for short title exclusions.
    if ":" not in criterion:
        return _contains(paper.title, criterion)
    raise CriteriaError(f"unsupported exclusion criterion: {criterion}")


def evaluate_paper(
    paper: Paper,
    *,
    inclusion_criteria: tuple[str, ...],
    exclusion_criteria: tuple[str, ...],
) -> CriteriaDecision:
    """Evaluate every approved criterion without a semantic guess or network call."""

    for criterion in inclusion_criteria:
        if not _matches_inclusion(criterion, paper):
            return CriteriaDecision(False, f"inclusion failed: {criterion}")
    for criterion in exclusion_criteria:
        if _matches_exclusion(criterion, paper):
            return CriteriaDecision(False, f"exclusion matched: {criterion}")
    return CriteriaDecision(True)


def filter_papers(
    papers: list[Paper],
    *,
    inclusion_criteria: tuple[str, ...],
    exclusion_criteria: tuple[str, ...],
) -> tuple[list[Paper], dict[str, int]]:
    """Return retained papers and stable reason counts for the run audit."""

    retained: list[Paper] = []
    reasons: dict[str, int] = {}
    for paper in papers:
        decision = evaluate_paper(
            paper,
            inclusion_criteria=inclusion_criteria,
            exclusion_criteria=exclusion_criteria,
        )
        if decision.included:
            retained.append(paper)
        else:
            assert decision.reason is not None
            reasons[decision.reason] = reasons.get(decision.reason, 0) + 1
    return retained, reasons
