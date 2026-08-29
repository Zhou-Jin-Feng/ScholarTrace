from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scholartrace.contracts import Evidence
from scholartrace.evidence.models import (
    DocuMindRetrieveRequest,
    DocuMindRetrieveResponse,
)

ROOT = Path(__file__).resolve().parents[1]


def _chunk(content: str = "A retrieved evidence chunk.") -> dict[str, object]:
    return {
        "chunk_id": "d" * 64,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "source": "paper.pdf",
        "page_number": 3,
        "distance": 0.42,
        "rank": 1,
    }


def _response(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "service_version": "2.2.0",
        "retrieval_version": "dense-v1",
        "retrieval_mode": "dense",
        "document_key": "a" * 64,
        "index_id": "b" * 64,
        "source_sha256": "c" * 64,
        "chunks": [_chunk()],
    }
    payload.update(updates)
    return payload


def test_retrieve_request_is_single_document_and_strict() -> None:
    request = DocuMindRetrieveRequest(
        query="  What supports this claim?  ",
        document_key="a" * 64,
        expected_index_id="b" * 64,
    )
    assert request.query == "What supports this claim?"
    with pytest.raises(ValidationError, match="Extra inputs"):
        DocuMindRetrieveRequest.model_validate({**request.model_dump(), "documents": ["c" * 64]})


def test_retrieve_response_verifies_hash_unique_ids_and_contiguous_ranks() -> None:
    response = DocuMindRetrieveResponse.model_validate(_response())
    assert response.chunks[0].rank == 1

    bad_hash = _chunk()
    bad_hash["content_sha256"] = "e" * 64
    with pytest.raises(ValidationError, match="content hash mismatch"):
        DocuMindRetrieveResponse.model_validate(_response(chunks=[bad_hash]))

    duplicate = {**_chunk(), "rank": 2}
    with pytest.raises(ValidationError, match="must be unique"):
        DocuMindRetrieveResponse.model_validate(_response(chunks=[_chunk(), duplicate]))

    wrong_rank = {**_chunk(), "chunk_id": "e" * 64, "rank": 3}
    with pytest.raises(ValidationError, match="contiguous"):
        DocuMindRetrieveResponse.model_validate(_response(chunks=[_chunk(), wrong_rank]))


def test_fulltext_evidence_requires_quote_and_chunk_integrity() -> None:
    quote = "A retrieved evidence chunk."
    payload = {
        "evidence_id": "evidence:test:1",
        "canonical_paper_id": "doi:10.1000/test",
        "quote": quote,
        "evidence_level": "fulltext",
        "content_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "chunk_content_sha256": hashlib.sha256(quote.encode()).hexdigest(),
        "document_key": "a" * 64,
        "index_id": "b" * 64,
        "chunk_id": "d" * 64,
        "source_sha256": "c" * 64,
        "retrieval_run_id": "retrieval:test:1",
    }
    assert Evidence.model_validate(payload).evidence_level == "fulltext"
    with pytest.raises(ValidationError, match="must match quote"):
        Evidence.model_validate({**payload, "content_sha256": "f" * 64})
    with pytest.raises(ValidationError, match="DocuMind provenance"):
        Evidence.model_validate({**payload, "chunk_content_sha256": None})


def test_checked_in_m2_schemas_match_source_after_export() -> None:
    from scholartrace.evidence.models import EVIDENCE_CONTRACT_MODELS

    for name, model in EVIDENCE_CONTRACT_MODELS.items():
        path = ROOT / "contracts" / "schemas" / f"{name}.schema.json"
        if path.exists():
            assert json.loads(path.read_text("utf-8")) == model.model_json_schema(mode="validation")
