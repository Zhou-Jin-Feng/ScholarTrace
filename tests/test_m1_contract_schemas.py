from __future__ import annotations

import json
from pathlib import Path

from scholartrace.search.models import SEARCH_CONTRACT_MODELS

ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_search_schemas_match_pydantic_source() -> None:
    for name, model in SEARCH_CONTRACT_MODELS.items():
        checked_in = json.loads((ROOT / f"contracts/schemas/{name}.schema.json").read_text("utf-8"))
        assert checked_in == model.model_json_schema(mode="validation")
