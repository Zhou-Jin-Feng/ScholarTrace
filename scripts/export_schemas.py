"""Export deterministic JSON Schemas from the Pydantic contract source."""

from __future__ import annotations

import json
from pathlib import Path

from scholartrace.contracts import CONTRACT_MODELS
from scholartrace.evidence.models import EVIDENCE_CONTRACT_MODELS
from scholartrace.search.models import SEARCH_CONTRACT_MODELS

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "contracts" / "schemas"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    models = {**CONTRACT_MODELS, **SEARCH_CONTRACT_MODELS, **EVIDENCE_CONTRACT_MODELS}
    for name, model in models.items():
        schema = model.model_json_schema(mode="validation")
        destination = OUTPUT / f"{name}.schema.json"
        destination.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
