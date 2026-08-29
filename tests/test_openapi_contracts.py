from __future__ import annotations

import json
from pathlib import Path

from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "contracts" / "openapi"


def _load(name: str) -> dict[str, object]:
    return json.loads((OPENAPI / name).read_text("utf-8"))


def test_openapi_documents_are_valid() -> None:
    validate(_load("documind-v1.openapi.json"))
    validate(_load("scholargraph-v1.openapi.json"))


def test_documind_contract_matches_frozen_provider_constraints() -> None:
    spec = _load("documind-v1.openapi.json")
    schemas = spec["components"]["schemas"]  # type: ignore[index]
    request = schemas["RetrieveRequest"]  # type: ignore[index]
    response = schemas["RetrieveResponse"]  # type: ignore[index]

    assert request["properties"]["top_k"]["maximum"] == 20
    assert request["properties"]["retrieval_mode"]["const"] == "dense"
    assert response["properties"]["retrieval_version"]["const"] == "dense-v1"
    assert "query" not in response["properties"]
    error_codes = schemas["ErrorDetail"]["properties"]["code"]["enum"]  # type: ignore[index]
    assert "retrieval_capacity_exceeded" in error_codes
    assert "retrieval_timeout" in error_codes
    health = schemas["HealthResponse"]  # type: ignore[index]
    assert "components" in health["properties"]
    assert "page_number" not in schemas["RetrievalChunk"]["required"]  # type: ignore[index]
    assert "request_id" not in schemas["ErrorDetail"]["required"]  # type: ignore[index]


def test_scholargraph_contract_declares_fixed_abstract_corpus() -> None:
    spec = _load("scholargraph-v1.openapi.json")
    schema = spec["components"]["schemas"]["CorpusCapability"]  # type: ignore[index]
    properties = schema["properties"]

    assert properties["corpus_id"]["const"] == "rag-openalex-2020-2025-198-v1"
    assert properties["document_count"]["const"] == 198
    assert properties["evidence_level"]["const"] == "abstract"
    assert properties["default_method"]["const"] == "basic"
