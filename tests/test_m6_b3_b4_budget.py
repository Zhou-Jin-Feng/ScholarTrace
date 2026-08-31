from __future__ import annotations

from scholartrace.scholargraph.budget import (
    build_b3_b4_budget_estimate,
    reference_cost_cny,
)


def test_reference_cost_matches_observed_smoke_arithmetic() -> None:
    assert reference_cost_cny(
        calls=1,
        input_tokens_per_call=5005,
        output_tokens_per_call=406,
    ) == 0.111615


def test_b3_b4_budget_separates_report_slice_from_other_model_calls() -> None:
    estimate = build_b3_b4_budget_estimate()
    assert estimate.full_report_call_count == 24
    assert estimate.local_scholargraph_basic_calls == 6
    assert [item.reference_cost_cny for item in estimate.scenarios] == [
        2.67876,
        7.128,
        13.392,
    ]
    assert estimate.pilot_reference_cap_cny == 3
    assert estimate.full_reference_cap_cny == 22.5
    assert any("Coordinator" in item for item in estimate.limitations)
