"""Repeated quotes cannot silently change their source during question merging."""

import hashlib

import pytest

from scholartrace.contracts import Evidence
from scholartrace.delivery.research import _merge_evidence, _merge_retrieved_chunks
from scholartrace.evidence.models import RetrievalChunk


def evidence():
    return Evidence(
        evidence_id="evidence:shared", canonical_paper_id="paper:one",
        quote="A supported finding", evidence_level="fulltext",
        content_sha256=hashlib.sha256(b"A supported finding").hexdigest(),
        chunk_content_sha256="a" * 64, document_key="b" * 64,
        index_id="c" * 64, chunk_id="d" * 64, source_sha256="e" * 64,
        retrieval_run_id="retrieval:first", page_number=1,
    )


@pytest.mark.parametrize("field,value", [
    ("canonical_paper_id", "paper:other"), ("document_key", "f" * 64),
    ("index_id", "f" * 64), ("chunk_id", "f" * 64),
    ("source_sha256", "f" * 64), ("page_number", 2),
    ("parser_version", "changed"), ("char_start", 10),
    ("source_url", "https://example.org/other"),
])
def test_identical_text_does_not_authorize_provenance_replacement(field, value):
    original = evidence()
    merged = {original.evidence_id: original}
    changed = Evidence.model_validate({**original.model_dump(), field: value})
    with pytest.raises(ValueError, match="conflicting evidence identity"):
        _merge_evidence(merged, changed)
    assert merged[original.evidence_id] is original


def test_independent_retrieval_preserves_first_validated_source():
    original = evidence()
    merged = {}
    _merge_evidence(merged, original)
    _merge_evidence(merged, original.model_copy(update={"retrieval_run_id": "retrieval:next"}))
    assert list(merged.values()) == [original]
    assert merged[original.evidence_id].retrieval_run_id == "retrieval:first"


def chunk(
    content: str = "A stable chunk", *, rank: int = 1, distance: float = 0.1
) -> RetrievalChunk:
    return RetrievalChunk(
        chunk_id="a" * 64,
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        source="paper.pdf",
        page_number=1,
        distance=distance,
        rank=rank,
    )


def test_rank_and_distance_differences_do_not_conflict() -> None:
    merged: dict[str, RetrievalChunk] = {}
    first = chunk(rank=1, distance=0.1)
    _merge_retrieved_chunks(merged, [first])
    _merge_retrieved_chunks(merged, [chunk(rank=4, distance=0.6)])

    assert list(merged.values()) == [first]


def test_changed_chunk_content_is_rejected() -> None:
    merged: dict[str, RetrievalChunk] = {}
    _merge_retrieved_chunks(merged, [chunk()])

    with pytest.raises(ValueError, match="conflicting retrieved chunks"):
        _merge_retrieved_chunks(merged, [chunk("A different chunk")])
