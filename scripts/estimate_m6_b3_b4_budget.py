"""Write the zero-call B3/B4 report-generation reference budget."""

from __future__ import annotations

import json
from pathlib import Path

from scholartrace.scholargraph.budget import build_b3_b4_budget_estimate
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation" / "reports" / "m6_b3_b4_budget_estimate.json"


def main() -> int:
    estimate = build_b3_b4_budget_estimate()
    write_json(OUTPUT, estimate.model_dump(mode="json"))
    print(
        json.dumps(
            {
                "report_calls": estimate.full_report_call_count,
                "planning_reference_cny": estimate.scenarios[1].reference_cost_cny,
                "hard_report_envelope_cny": estimate.scenarios[2].reference_cost_cny,
                "pilot_reference_cap_cny": estimate.pilot_reference_cap_cny,
                "provider_billing_observed": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
