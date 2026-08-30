from __future__ import annotations

import json
from pathlib import Path

from scholartrace.citations.models import CITATION_CONTRACT_MODELS
from scholartrace.verification.models import VERIFICATION_CONTRACT_MODELS

ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_m4_schemas_match_pydantic_sources() -> None:
    models = {**CITATION_CONTRACT_MODELS, **VERIFICATION_CONTRACT_MODELS}
    for name, model in models.items():
        checked_in = json.loads(
            (ROOT / "contracts" / "schemas" / f"{name}.schema.json").read_text("utf-8")
        )
        assert checked_in == model.model_json_schema(mode="validation")
