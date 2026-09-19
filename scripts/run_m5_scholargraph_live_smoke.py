"""Run one bounded ScholarGraph Basic query and persist sanitized metrics."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import TypeAdapter

from scholartrace.scholargraph.evaluation import load_question_sets
from scholartrace.scholargraph.live_smoke import run_live_smoke
from scholartrace.scholargraph.routing import ScholarGraphScope
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"
SCOPES = ROOT / "evaluation" / "seeds" / "m5_scholargraph_routing_scopes.json"
OUTPUT = ROOT / "artifacts" / "reports" / "m5_scholargraph_live_smoke.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--seed-id", default="sg-eligible-01")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    questions = load_question_sets(ELIGIBLE, BOUNDARY)
    scope_document = json.loads(SCOPES.read_text("utf-8"))
    scopes = TypeAdapter(dict[str, ScholarGraphScope]).validate_python(scope_document["scopes"])
    summary = asyncio.run(
        run_live_smoke(
            base_url=args.base_url,
            questions=questions,
            scopes=scopes,
            seed_id=args.seed_id,
        )
    )
    write_json(args.output, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
