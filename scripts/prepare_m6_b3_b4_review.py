"""Prepare a private blind-review CSV from a B3/B4 private run archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scholartrace.scholargraph.blind_review import prepare_blind_review
from scholartrace.scholargraph.evaluation import load_question_sets
from scholartrace.scholargraph.experiment import (
    PrivateExperimentArchive,
    require_private_agent_path,
)

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-run", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260830)
    args = parser.parse_args()
    private_run = require_private_agent_path(args.private_run)
    archive = PrivateExperimentArchive.model_validate_json(
        private_run.read_text("utf-8")
    )
    summary = prepare_blind_review(
        archive=archive,
        questions=load_question_sets(ELIGIBLE, BOUNDARY),
        review_path=args.review,
        mapping_path=args.mapping,
        seed=args.seed,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
