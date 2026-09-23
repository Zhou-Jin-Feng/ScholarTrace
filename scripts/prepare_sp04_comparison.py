"""Prepare the SP-04 three-scheme resource and authorization preflight."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from scholartrace.search.storage import source_tree_sha256, write_json
from scholartrace.verification_ablation.models import AblationFrozenInput

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_INPUTS = ROOT / "agent" / "verification-ablation" / "SP-02" / "frozen_inputs.json"
PRIVATE_SELECTION = ROOT / "agent" / "verification-ablation" / "SP-03" / "selection_plan.json"
PUBLIC_OUTPUT = ROOT / "evaluation" / "reports" / "sp_04_comparison_preflight.json"
MODEL = "gpt-5.6-luna"


def current_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).strip()


def load_inputs() -> dict[str, AblationFrozenInput]:
    payload = json.loads(PRIVATE_INPUTS.read_text(encoding="utf-8"))
    return {
        item["input"]["question_id"]: AblationFrozenInput.model_validate(item["input"])
        for item in payload["questions"]
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reasoning-effort",
        choices=("high", "max"),
        default="high",
    )
    args = parser.parse_args()
    inputs = load_inputs()
    selection = json.loads(PRIVATE_SELECTION.read_text(encoding="utf-8"))
    selected_claim_count = sum(
        len(item["selected_claim_ids"]) for item in selection["plans"]
    )
    total_claim_count = sum(len(item.claims) for item in inputs.values())
    question_count = len(inputs)
    input_chars = sum(
        len(item.question) + sum(len(claim.text) for claim in item.claims)
        for item in inputs.values()
    )
    schemes: list[dict[str, Any]] = [
        {
            "scheme_id": "simple-baseline",
            "role": "single-pass-synthesis-baseline",
            "report_calls": question_count,
            "strong_verifier_calls": 0,
            "run_id": "sp04-simple-baseline-production-v1",
        },
        {
            "scheme_id": "full-verification",
            "role": "original-full-verification-scheme",
            "report_calls": question_count,
            "strong_verifier_calls": total_claim_count,
            "run_id": "sp04-full-verification-production-v1",
        },
        {
            "scheme_id": "selective-verification",
            "role": "stage-two-candidate",
            "report_calls": question_count,
            "strong_verifier_calls": selected_claim_count,
            "run_id": "sp04-selective-verification-production-v1",
        },
    ]
    for scheme in schemes:
        scheme["total_provider_requests"] = (
            scheme["report_calls"] + scheme["strong_verifier_calls"]
        )
    total_requests = sum(item["total_provider_requests"] for item in schemes)
    preflight = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sp04-three-scheme-comparison-preflight",
        "status": "preflight_only",
        "baseline_commit": current_commit(),
        "source_tree_sha256": source_tree_sha256(ROOT),
        "question_count": question_count,
        "total_claim_count": total_claim_count,
        "selected_claim_count": selected_claim_count,
        "skipped_claim_count": total_claim_count - selected_claim_count,
        "input_character_count_lower_bound": input_chars,
        "schemes": schemes,
        "total_provider_requests": total_requests,
        "provider": {
            "model": MODEL,
            "protocol": "responses",
            "reasoning_effort": args.reasoning_effort,
            "configuration_status": "must_reconfirm_before_dispatch",
            "max_boundary": (
                "max is a separate runtime identity; a difficult max probe previously returned "
                "HTTP 524 and streaming produced no terminal event."
            ),
            "price_status": "must_reconfirm_before_dispatch",
        },
        "budget": {
            "reference_cost_cny": None,
            "cost_status": (
                "not_estimated_until_provider_rate_and_actual_token_limits_are_confirmed"
            ),
            "unknown_usage_policy": "stop_and_preserve_checkpoint",
            "automatic_resend": False,
        },
        "authorization": {
            "required": True,
            "reason": (
                "This is a new external-call range for the stage-two three-scheme comparison."
            ),
            "calls_requested": total_requests,
            "reports": sum(item["report_calls"] for item in schemes),
            "strong_verifier_calls": sum(item["strong_verifier_calls"] for item in schemes),
            "no_calls_sent_by_this_preflight": True,
        },
        "quality_scope": {
            "primary_metric": "weighted_key_point_coverage",
            "audit_all_skipped_claims": True,
            "formal_workflow_changed": False,
        },
        "next_action": (
            "Confirm provider, model, rate, hard cap, and call range "
            "before dispatching any scheme."
        ),
    }
    write_json(PUBLIC_OUTPUT, preflight)
    print(json.dumps(preflight["authorization"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
