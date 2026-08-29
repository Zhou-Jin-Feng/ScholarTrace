from __future__ import annotations

import json
from pathlib import Path

from scholartrace.contracts import Budget, ModelRoutingPolicy

ROOT = Path(__file__).resolve().parents[1]


def _bundle() -> dict[str, object]:
    return json.loads((ROOT / "contracts/examples/m0_bundle.json").read_text("utf-8"))


def test_local_model_is_exact_and_paid_routes_fail_closed() -> None:
    policy = ModelRoutingPolicy.model_validate(_bundle()["ModelRoutingPolicy"])
    profiles = {profile.profile_id: profile for profile in policy.profiles}

    assert profiles["local-qwen3-8b"].model_name == "qwen3:8b"
    assert profiles["local-qwen3-8b"].model_version == "500a1f067a9f"
    assert profiles["local-qwen3-8b"].quantization == "Q4_K_M"
    assert profiles["local-qwen3-8b"].context_window == 40960
    assert profiles["api-strong"].enabled is False
    assert policy.paid_routes_enabled is False
    assert policy.no_automatic_price_tier_upgrade is True


def test_m0_budget_has_hard_cny_and_call_limits() -> None:
    budget = Budget.model_validate(_bundle()["Budget"])

    assert budget.limits.max_llm_input_tokens == 160_000
    assert budget.limits.max_llm_output_tokens == 40_000
    assert budget.limits.max_total_tokens == 200_000
    assert budget.limits.max_api_calls == 16
    assert budget.limits.max_model_calls == 60
    assert budget.limits.max_cost_cny == 10.0
    assert budget.limits.max_duration_seconds == 1800


def test_model_comparison_suite_is_fixed_and_bounded() -> None:
    suite = json.loads((ROOT / "evaluation/seeds/model_comparison_cases.json").read_text("utf-8"))

    assert len(suite["cases"]) == 3
    assert {case["node"] for case in suite["cases"]} == {
        "query_rewrite",
        "claim_extraction",
        "abstract_screening",
    }
    assert suite["thresholds"]["unsupported_critical_claim_rate"] == 0.0


def test_local_model_smoke_report_passed_without_paid_calls() -> None:
    report = json.loads((ROOT / "evaluation/reports/m0_local_model_smoke.json").read_text("utf-8"))

    assert report["passed"] is True
    assert report["summary"]["structured_success_rate"] == 1.0
    assert report["summary"]["semantic_rule_pass_rate"] == 1.0
    assert report["summary"]["api_cost_cny"] == 0.0
    assert report["profile"]["model_id"] == "500a1f067a9f"
