"""Finalize an already completed paid pilot without making network calls."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from scholartrace.contracts import Verification
from scholartrace.scholargraph.blind_review import prepare_blind_review
from scholartrace.scholargraph.evaluation import load_question_sets
from scholartrace.scholargraph.experiment import (
    PrivateExperimentArchive,
    public_experiment_payload,
)
from scholartrace.scholargraph.pilot_input import load_m2_pilot_artifacts
from scholartrace.search.storage import write_json
from scholartrace.verification.models import ReportGateResult

ROOT = Path(__file__).resolve().parents[1]
ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"
M2_REPORT = ROOT / "artifacts" / "m2-evidence-live" / "evidence_report.json"
M2_PAPERS = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"
PRIVATE_DIR = ROOT / "agent" / "过程记录" / "M6-B3B4-paid-pilot"
PUBLIC_OUTPUT = ROOT / "artifacts" / "reports" / "m6_b3_b4_paid_pilot.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-dir", type=Path, default=PRIVATE_DIR)
    parser.add_argument("--public-output", type=Path, default=PUBLIC_OUTPUT)
    parser.add_argument("--question-id", default="sg-eligible-02")
    parser.add_argument("--approved-max-cost-cny", type=float, required=True)
    parser.add_argument(
        "--prior-attempt-reference-upper-bound-cny",
        type=float,
        required=True,
    )
    args = parser.parse_args()
    if not (0 < args.prior_attempt_reference_upper_bound_cny < args.approved_max_cost_cny):
        raise ValueError("finalizer requires a valid prior-attempt budget bound")

    private_input = json.loads((args.private_dir / "verified_input.json").read_text("utf-8"))
    archive = PrivateExperimentArchive.model_validate_json(
        (args.private_dir / "private_run.json").read_text("utf-8")
    )
    questions = load_question_sets(ELIGIBLE, BOUNDARY)
    question = questions[args.question_id]
    artifacts = load_m2_pilot_artifacts(
        report_path=M2_REPORT,
        paper_fixture_path=M2_PAPERS,
    )
    if (
        private_input["question_id"] != question.id
        or private_input["source_report_sha256"] != artifacts.source_report_sha256
        or private_input["paper_pool_sha256"] != artifacts.paper_pool_sha256
        or archive.manifest.paper_pool_sha256 != artifacts.paper_pool_sha256
    ):
        raise ValueError("pilot finalization inputs drifted")
    verifications = [Verification.model_validate(item) for item in private_input["verifications"]]
    gate = ReportGateResult.model_validate(private_input["report_gate"])
    statuses = Counter(item.status for item in verifications)
    review = prepare_blind_review(
        archive=archive,
        questions={question.id: question},
        review_path=args.private_dir / "blind_review.csv",
        mapping_path=args.private_dir / "private_mapping.json",
        seed=20260831,
    )
    report_cost = archive.b3_usage.reference_cost_cny + archive.b4_usage.reference_cost_cny
    total_upper = args.prior_attempt_reference_upper_bound_cny + report_cost
    public = public_experiment_payload(archive)
    public.update(
        {
            "pilot_kind": "one_question_paid_b3_b4_with_real_documind_evidence",
            "quality_scope": (
                "Paid protocol, semantic-verification, cost, and blind-review pilot; "
                "one question cannot establish default-enable quality benefit."
            ),
            "approved_total_reference_cost_cny": args.approved_max_cost_cny,
            "prior_attempt_reference_upper_bound_cny": round(
                args.prior_attempt_reference_upper_bound_cny, 6
            ),
            "provider_billed_cost_cny": None,
            "provider_billing_observability": "unavailable_in_response",
            "verifier": {
                "calls": len(verifications),
                "input_tokens": None,
                "output_tokens": None,
                "duration_seconds": None,
                "reference_cost_cny": None,
                "reference_cost_upper_bound_cny": round(
                    args.prior_attempt_reference_upper_bound_cny, 6
                ),
                "usage_observability": "not_persisted_before_first_report_failure",
                "status_counts": dict(sorted(statuses.items())),
            },
            "report_reference_cost_cny": round(report_cost, 6),
            "total_reference_cost_cny": None,
            "total_reference_cost_upper_bound_cny": round(total_upper, 6),
            "documind_source_report_sha256": artifacts.source_report_sha256,
            "paper_pool_sha256": artifacts.paper_pool_sha256,
            "deterministic_validation_outcome": artifacts.validation.outcome,
            "included_claim_count": sum(item.included for item in gate.dispositions),
            "evidence_count": len(private_input["allowed_evidence_ids"]),
            "blind_review": review,
            "unscored_comparison": {
                "question_count": 1,
                "eligible_count": 1,
                "boundary_count": 0,
                "quality_scores_present": False,
                "default_enable_decision": "keep_disabled_insufficient_quality_evidence",
                "limitations": [
                    "A one-question pilot is not the frozen 12-question comparison.",
                    "Blind scores have not been imported.",
                    "The full comparison contract requires eligible and boundary coverage.",
                ],
            },
            "raw_prompt_stored_publicly": False,
            "raw_response_stored_publicly": False,
            "passed": (
                total_upper <= args.approved_max_cost_cny
                and archive.b3_usage.provider_api_calls == 1
                and archive.b4_usage.provider_api_calls == 1
                and archive.b4_usage.scholargraph_calls == 1
                and all(row.result.status == "succeeded" for row in archive.b3 + archive.b4)
            ),
        }
    )
    write_json(args.public_output, public)
    print(
        json.dumps(
            {
                "passed": public["passed"],
                "verifier_calls": public["verifier"]["calls"],
                "report_calls": 2,
                "scholargraph_calls": archive.b4_usage.scholargraph_calls,
                "report_reference_cost_cny": public["report_reference_cost_cny"],
                "total_reference_cost_upper_bound_cny": public[
                    "total_reference_cost_upper_bound_cny"
                ],
                "default_enable_decision": public["unscored_comparison"]["default_enable_decision"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if public["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
