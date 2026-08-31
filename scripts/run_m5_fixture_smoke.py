"""Run the deterministic M5 routing/comparability smoke."""

from __future__ import annotations

import json
from pathlib import Path

from scholartrace.scholargraph.fixture_smoke import run_fixture_smoke
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"
OUTPUT = ROOT / "evaluation" / "reports" / "m5_scholargraph_fixture_smoke.json"


def main() -> int:
    summary = run_fixture_smoke(eligible_path=ELIGIBLE, boundary_path=BOUNDARY)
    write_json(OUTPUT, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
