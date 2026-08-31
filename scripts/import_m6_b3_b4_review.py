"""Validate completed private scores and write a sanitized B3/B4 comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scholartrace.scholargraph.blind_review import import_blind_scores
from scholartrace.scholargraph.evaluation import (
    QuestionSetHashes,
    file_sha256,
    load_question_sets,
)
from scholartrace.scholargraph.experiment import (
    PrivateExperimentArchive,
    require_private_agent_path,
)
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-run", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    private_run = require_private_agent_path(args.private_run)
    archive = PrivateExperimentArchive.model_validate_json(
        private_run.read_text("utf-8")
    )
    questions = load_question_sets(ELIGIBLE, BOUNDARY)
    comparison = import_blind_scores(
        archive=archive,
        questions=questions,
        review_path=args.review,
        mapping_path=args.mapping,
        question_set_hashes=QuestionSetHashes(
            eligible_sha256=file_sha256(ELIGIBLE),
            boundary_sha256=file_sha256(BOUNDARY),
        ),
    )
    output = {
        "schema_version": "1.0",
        "purpose": "scholartrace-b3-b4-scored-comparison",
        "manifest_sha256": archive.manifest_sha256,
        "comparison": comparison.model_dump(mode="json"),
        "raw_answers_stored": False,
        "blind_mapping_stored": False,
    }
    write_json(args.output, output)
    print(
        json.dumps(
            {
                "question_count": comparison.question_count,
                "eligible_quality_delta_mean": comparison.eligible_quality_delta_mean,
                "default_enable_decision": comparison.default_enable_decision,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
