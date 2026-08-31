"""Reference-only B3/B4 report-generation budget estimates."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BudgetEstimateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportBudgetScenario(BudgetEstimateModel):
    name: str
    call_count: int = Field(ge=1)
    input_tokens_per_call: int = Field(ge=0)
    output_tokens_per_call: int = Field(ge=0)
    reference_cost_cny: float = Field(ge=0, allow_inf_nan=False)
    interpretation: str


class B3B4BudgetEstimate(BudgetEstimateModel):
    schema_version: str = "1.0"
    purpose: str = "scholartrace-b3-b4-report-generation-reference-budget"
    model_identifier: str
    full_report_call_count: int = Field(ge=1)
    local_scholargraph_basic_calls: int = Field(ge=0)
    reference_rates: dict[str, float]
    scenarios: list[ReportBudgetScenario]
    pilot_reference_cap_cny: float = Field(gt=0, allow_inf_nan=False)
    full_reference_cap_cny: float = Field(gt=0, allow_inf_nan=False)
    limitations: list[str]


def reference_cost_cny(
    *,
    calls: int,
    input_tokens_per_call: int,
    output_tokens_per_call: int,
    input_usd_per_million: float = 2.0,
    output_usd_per_million: float = 12.0,
    usd_to_cny: float = 7.5,
) -> float:
    if calls < 1 or input_tokens_per_call < 0 or output_tokens_per_call < 0:
        raise ValueError("budget estimate counts must be non-negative and calls positive")
    usd = calls * (
        input_tokens_per_call * input_usd_per_million
        + output_tokens_per_call * output_usd_per_million
    ) / 1_000_000
    return round(usd * usd_to_cny, 6)


def build_b3_b4_budget_estimate() -> B3B4BudgetEstimate:
    full_calls = 24
    rates = {
        "input_usd_per_million": 2.0,
        "output_usd_per_million": 12.0,
        "usd_to_cny": 7.5,
    }
    scenarios = [
        ReportBudgetScenario(
            name="observed_plan_smoke_shape",
            call_count=full_calls,
            input_tokens_per_call=5005,
            output_tokens_per_call=406,
            reference_cost_cny=reference_cost_cny(
                calls=full_calls,
                input_tokens_per_call=5005,
                output_tokens_per_call=406,
            ),
            interpretation=(
                "Arithmetic projection of the earlier one-call plan smoke; report inputs "
                "will usually be larger, so this is not a safe cap."
            ),
        ),
        ReportBudgetScenario(
            name="planning_shape",
            call_count=full_calls,
            input_tokens_per_call=15_000,
            output_tokens_per_call=800,
            reference_cost_cny=reference_cost_cny(
                calls=full_calls,
                input_tokens_per_call=15_000,
                output_tokens_per_call=800,
            ),
            interpretation="Planning estimate for a bounded Evidence packet and short report.",
        ),
        ReportBudgetScenario(
            name="hard_report_envelope",
            call_count=full_calls,
            input_tokens_per_call=30_000,
            output_tokens_per_call=1200,
            reference_cost_cny=reference_cost_cny(
                calls=full_calls,
                input_tokens_per_call=30_000,
                output_tokens_per_call=1200,
            ),
            interpretation=(
                "Maximum reference envelope for the 24 final report calls before "
                "Coordinator or Verifier usage."
            ),
        ),
    ]
    return B3B4BudgetEstimate(
        model_identifier="gpt-5.6-terra",
        full_report_call_count=full_calls,
        local_scholargraph_basic_calls=6,
        reference_rates=rates,
        scenarios=scenarios,
        pilot_reference_cap_cny=3.0,
        full_reference_cap_cny=22.5,
        limitations=[
            "These are official-reference arithmetic estimates, not Provider bills.",
            "The Provider does not expose an account multiplier or billed-cost field.",
            "The full cap covers final report generation only.",
            "api-strong Coordinator and critical Verifier calls require a separate ledger.",
            "A two-call B3/B4 pilot must precede the 24-call run to validate actual usage.",
            "The user-approved execution caps include a 50 percent contingency.",
        ],
    )
