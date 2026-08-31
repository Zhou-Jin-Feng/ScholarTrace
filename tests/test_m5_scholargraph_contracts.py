from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from openapi_spec_validator import validate

from scholartrace.scholargraph.models import (
    SCHOLARGRAPH_CONTRACT_MODELS,
    CapabilitiesResponse,
)

ROOT = Path(__file__).resolve().parents[1]
PROVIDER = ROOT / "contracts" / "openapi" / "scholargraph-v1.openapi.json"
SCHEMAS = ROOT / "contracts" / "schemas"
CAPABILITIES = ROOT / "tests" / "fixtures" / "m5" / "scholargraph_capabilities.json"


def test_provider_contract_is_the_frozen_1_2_snapshot() -> None:
    provider = json.loads(PROVIDER.read_text("utf-8"))
    validate(provider)
    assert provider["info"]["version"] == "1.2.0"
    assert set(provider["paths"]) == {
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/api/v1/capabilities",
        "/api/v1/metrics",
        "/api/v1/query",
    }
    corpus = provider["components"]["schemas"]["CorpusCapability"]["properties"]
    assert corpus["corpus_id"]["const"] == "openalex-rag-abstracts-2020-2025-v1"
    assert corpus["document_count"]["const"] == 198
    assert corpus["evidence_level"]["const"] == "abstract"


def test_checked_in_consumer_schemas_match_pydantic_sources() -> None:
    for name, model in SCHOLARGRAPH_CONTRACT_MODELS.items():
        checked_in = json.loads((SCHEMAS / f"{name}.schema.json").read_text("utf-8"))
        Draft202012Validator.check_schema(checked_in)
        assert checked_in == model.model_json_schema(mode="validation")


def test_capability_fixture_freezes_method_and_corpus_boundaries() -> None:
    capabilities = CapabilitiesResponse.model_validate_json(CAPABILITIES.read_bytes())
    methods = {item.method.value: item for item in capabilities.methods}
    assert capabilities.corpus.document_count == 198
    assert capabilities.corpus.publication_years.model_dump() == {
        "start": 2020,
        "end": 2025,
    }
    assert methods["basic"].online_allowed
    assert methods["local"].online_allowed
    assert not methods["global"].online_allowed
    assert not methods["drift"].online_allowed
