"""Run bounded local ScholarGraph Basic repeats and persist sanitized metrics."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import TypeAdapter

from scholartrace.scholargraph.capacity import run_basic_repeat_capacity
from scholartrace.scholargraph.evaluation import load_question_sets
from scholartrace.scholargraph.routing import ScholarGraphScope
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"
SCOPES = ROOT / "evaluation" / "seeds" / "m5_scholargraph_routing_scopes.json"
OUTPUT = ROOT / "evaluation" / "reports" / "m6_scholargraph_basic_capacity.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    parser.add_argument("--seed-id", default="sg-eligible-01")
    parser.add_argument("--repeat-count", type=int, default=3)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    scope_document = json.loads(SCOPES.read_text("utf-8"))
    summary = asyncio.run(
        run_basic_repeat_capacity(
            base_url=args.base_url,
            questions=load_question_sets(ELIGIBLE, BOUNDARY),
            scopes=TypeAdapter(dict[str, ScholarGraphScope]).validate_python(
                scope_document["scopes"]
            ),
            seed_id=args.seed_id,
            repeat_count=args.repeat_count,
        )
    )
    write_json(args.output, summary)
    print(
        json.dumps(
            {
                "repeat_count": summary["repeat_count"],
                "success_rate": summary["success_rate"],
                "query_http_duration_p50_seconds": summary[
                    "query_http_duration_p50_seconds"
                ],
                "query_http_duration_p95_seconds": summary[
                    "query_http_duration_p95_seconds"
                ],
                "passed": summary["passed"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
