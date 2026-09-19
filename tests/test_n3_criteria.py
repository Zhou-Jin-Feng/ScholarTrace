from datetime import UTC, datetime

import pytest

from scholartrace.contracts import Paper
from scholartrace.delivery.criteria import CriteriaError, evaluate_paper, filter_papers


def paper(
    *, title: str = "Retrieval evaluation", abstract: str | None = "Empirical results"
) -> Paper:
    return Paper(
        canonical_paper_id="paper:criteria",
        title=title,
        normalized_title=title.casefold(),
        authors=["Researcher"],
        publication_year=2024,
        arxiv_id="2401.00001",
        abstract=abstract,
        access_level="fulltext",
        sources=[
            {
                "source": "arxiv",
                "source_id": "2401.00001",
                "retrieved_at": datetime(2024, 1, 1, tzinfo=UTC),
                "record_sha256": "1" * 64,
            }
        ],
    )


def test_inclusion_and_exclusion_are_both_applied() -> None:
    decision = evaluate_paper(
        paper(),
        inclusion_criteria=("full text", "abstract contains: Empirical"),
        exclusion_criteria=("title contains: opinion",),
    )
    assert decision.included and decision.reason is None

    decision = evaluate_paper(
        paper(title="Opinion on retrieval"),
        inclusion_criteria=("full text",),
        exclusion_criteria=("title contains: opinion",),
    )
    assert not decision.included and decision.reason == "exclusion matched: title contains: opinion"


def test_filter_reports_reason_counts() -> None:
    retained, reasons = filter_papers(
        [paper(), paper(title="Opinion on retrieval")],
        inclusion_criteria=("full text",),
        exclusion_criteria=("title contains: opinion",),
    )
    assert len(retained) == 1
    assert reasons == {"exclusion matched: title contains: opinion": 1}


def test_unsupported_criteria_fail_closed() -> None:
    with pytest.raises(CriteriaError, match="unsupported inclusion criterion"):
        evaluate_paper(
            paper(),
            inclusion_criteria=("peer reviewed",),
            exclusion_criteria=(),
        )
