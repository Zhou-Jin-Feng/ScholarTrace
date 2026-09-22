"""Prepare the SA-04 paid pilot manifest without making network calls."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from scholartrace.model_provider.settings import (
    read_dotenv,
    resolve_provider_settings,
    resolved_structured_output_mode,
)
from scholartrace.verification_ablation import (
    ABLATION_REPORT_SCHEMA_SHA256,
    OpenAICompatibleAblationReportGenerator,
    load_frozen_inputs,
)

ROOT = Path(__file__).resolve().parents[1]
FROZEN_INPUTS = ROOT / "agent" / "verification-ablation" / "SA-02" / "frozen_inputs.json"
SA02_MANIFEST = ROOT / "evaluation" / "seeds" / "sa_02_verification_ablation_manifest.json"
OUTPUT = ROOT / "evaluation" / "reports" / "sa_04_pilot_preflight.json"
PILOT_IDS = ("sa02-pilot-01", "sa02-pilot-02")


def build_preflight() -> dict[str, object]:
    inputs = load_frozen_inputs(FROZEN_INPUTS)
    public_manifest = json.loads(SA02_MANIFEST.read_text(encoding="utf-8"))
    if set(PILOT_IDS) - set(inputs):
        raise ValueError("SA-02 pilot question coverage is incomplete")
    pilot = {question_id: inputs[question_id] for question_id in PILOT_IDS}
    dotenv = read_dotenv(ROOT / ".env")
    settings = resolve_provider_settings(
        cli_values={
            "base_url": None,
            "api_key": None,
            "timeout_seconds": None,
            "model": None,
        },
        environment=os.environ,
        dotenv_values=dotenv,
    )
    if (
        OpenAICompatibleAblationReportGenerator.prompt_template_sha256
        != public_manifest["experiment_contract"]["report_prompt_sha256"]
    ):
        raise ValueError("SA-02 report Prompt hash does not match the real adapter")
    question_rows = [
        {
            "question_id": question_id,
            "split": item.split,
            "claim_count": len(item.claims),
            "evidence_count": len(item.evidence),
            "input_sha256": item.input_sha256,
        }
        for question_id, item in pilot.items()
    ]
    return {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa04-paid-pilot-preflight",
        "status": "prepared",
        "generated_at": "2026-09-21T00:00:00+08:00",
        "baseline_commit": "3d095a79feff257b57b041de40b7b5ed73021f9e",
        "dataset_fingerprint_sha256": public_manifest["dataset_fingerprint_sha256"],
        "pilot_question_ids": list(PILOT_IDS),
        "pilot_questions": question_rows,
        "counts": {
            "question_count": len(pilot),
            "claim_count": sum(len(item.claims) for item in pilot.values()),
            "unique_evidence_count": len(
                {
                    evidence.evidence_id
                    for item in pilot.values()
                    for evidence in item.evidence
                }
            ),
            "v_on_verifier_calls": 12,
            "v_on_report_calls": 2,
            "v_off_verifier_calls": 0,
            "v_off_report_calls": 2,
            "total_expected_report_calls": 4,
            "total_expected_model_calls": 16,
        },
        "frozen_configuration": {
            "model_profile": "api-strong",
            "model_identifier_reference": "gpt-5.6-terra",
            "provider_protocol": "responses",
            "verifier_prompt_sha256": public_manifest["experiment_contract"][
                "verifier_prompt_sha256"
            ],
            "report_prompt_sha256": public_manifest["experiment_contract"][
                "report_prompt_sha256"
            ],
            "report_schema": public_manifest["experiment_contract"]["report_schema"],
            "report_schema_sha256": ABLATION_REPORT_SCHEMA_SHA256,
            "report_length_limit_chars": public_manifest["experiment_contract"][
                "report_length_limit_chars"
            ],
            "automatic_retry": False,
            "protocol_fallback": False,
            "model_upgrade": False,
            "provider_hostname": urlparse(settings.base_url).hostname,
            "structured_output_mode": resolved_structured_output_mode(settings),
            "request_timeout_seconds": 180,
            "max_response_bytes": 1_000_000,
            "budget_scope": "shared",
        },
        "reference_budget": {
            "planning_cap_cny": 3.0,
            "hard_failure_close_envelope_cny": 5.0,
            "input_usd_per_million_reference": 2.0,
            "output_usd_per_million_reference": 12.0,
            "usd_to_cny_reference": 7.5,
            "actual_provider_billing": "unknown_until_provider_exposes_billing",
            "prices_require_revalidation": True,
        },
        "local_provider_preflight": {
            "base_hostname": urlparse(settings.base_url).hostname,
            "configured_model": settings.model,
            "timeout_seconds": settings.timeout_seconds,
            "structured_output_mode": settings.structured_output_mode,
            "api_key_present": bool(settings.api_key.strip()),
            "network_probe_performed": False,
            "model_catalog_probe_performed": False,
            "paid_call_performed": False,
        },
        "execution_requirements": {
            "paid_calls_enabled": False,
            "external_calls_enabled": False,
            "formal_run_enabled": False,
            "required_before_first_call": [
                "explicit paid-call flag and budget",
                "current model catalog and Responses support check",
                "current input/output price and account multiplier confirmation",
                "current structure-output and usage observability check",
                "final private manifest hash and budget confirmation",
            ],
        },
        "privacy": {
            "api_key_stored": False,
            "raw_claims_public": False,
            "source_quotes_public": False,
            "raw_provider_response_public": False,
        },
        "limitations": [
            (
                "This preflight does not prove model availability, price, context, or "
                "billing behavior."
            ),
            "Historical M6 pilot evidence is not SA-04 evidence.",
            (
                "The pilot cannot establish formal quality benefit; it only validates the "
                "real adapter and resource ledger."
            ),
        ],
        "calls_made_during_preflight": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    payload = build_preflight()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "status": payload["status"],
                "question_count": payload["counts"]["question_count"],
                "claim_count": payload["counts"]["claim_count"],
                "unique_evidence_count": payload["counts"]["unique_evidence_count"],
                "calls_made": payload["calls_made_during_preflight"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
