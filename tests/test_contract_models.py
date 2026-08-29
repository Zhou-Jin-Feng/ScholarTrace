from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scholartrace.contracts import CONTRACT_MODELS, Evidence

ROOT = Path(__file__).resolve().parents[1]


def test_all_checked_in_examples_validate() -> None:
    bundle = json.loads((ROOT / "contracts/examples/m0_bundle.json").read_text("utf-8"))
    assert set(bundle) == set(CONTRACT_MODELS)
    for name, payload in bundle.items():
        CONTRACT_MODELS[name].model_validate(payload)


def test_unknown_fields_fail_closed() -> None:
    bundle = json.loads((ROOT / "contracts/examples/m0_bundle.json").read_text("utf-8"))
    payload = {**bundle["Claim"], "unexpected": "contract drift"}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CONTRACT_MODELS["Claim"].model_validate(payload)


def test_fulltext_evidence_requires_documind_provenance() -> None:
    bundle = json.loads((ROOT / "contracts/examples/m0_bundle.json").read_text("utf-8"))
    payload = {**bundle["Evidence"], "evidence_level": "fulltext", "chunk_id": None}
    with pytest.raises(ValidationError, match="DocuMind provenance"):
        Evidence.model_validate(payload)


def test_checked_in_json_schemas_match_pydantic_source() -> None:
    for name, model in CONTRACT_MODELS.items():
        checked_in = json.loads((ROOT / f"contracts/schemas/{name}.schema.json").read_text("utf-8"))
        assert checked_in == model.model_json_schema(mode="validation")
