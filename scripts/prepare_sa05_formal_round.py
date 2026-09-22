"""Prepare the repaired SA-05 formal manifest without Provider calls."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import run_sa05_formal as formal

from scholartrace.search.storage import write_json
from scholartrace.verification_ablation import load_frozen_inputs

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation" / "reports" / "sa_05_formal_preflight.json"


def main() -> int:
    inputs_all = load_frozen_inputs(formal.INPUTS)
    inputs = {
        question_id: item
        for question_id, item in inputs_all.items()
        if item.split == "formal"
    }
    if len(inputs) != 10:
        raise ValueError(f"expected 10 formal questions, got {len(inputs)}")
    settings = formal.load_luna_settings()
    manifest = formal.formal_manifest(inputs, settings)
    payload = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa05-formal-preflight",
        "status": "prepared",
        "question_count": len(inputs),
        "claim_count": sum(len(item.claims) for item in inputs.values()),
        "unique_evidence_count": len(
            {
                evidence.evidence_id
                for item in inputs.values()
                for evidence in item.evidence
            }
        ),
        "selected_model": manifest.model_identifier,
        "provider_hostname": urlparse(settings.base_url).hostname,
        "provider_source": "cc-switch:SuperBoy:Luna",
        "reasoning_effort": manifest.reasoning_effort,
        "provider_protocol": manifest.provider_protocol,
        "prompt_sha256": manifest.report_prompt_sha256,
        "verifier_prompt_sha256": manifest.verifier_prompt_sha256,
        "report_schema_sha256": manifest.report_schema_sha256,
        "dataset_fingerprint_sha256": manifest.dataset_fingerprint_sha256,
        "question_input_sha256": manifest.question_input_sha256,
        "manifest_sha256": manifest.stable_sha256(),
        "budget": manifest.budget.model_dump(mode="json"),
        "expected_calls": {
            "verifier": 56,
            "reports": 20,
            "total": 76,
        },
        "checkpoint": {
            "request_intent_before_dispatch": True,
            "attempt_state_after_dispatch": True,
            "unknown_usage_blocks_next_dispatch": True,
            "resume_requires_manifest_and_configuration_hash": True,
        },
        "calls_made_during_preflight": 0,
        "api_key_stored_publicly": False,
    }
    write_json(OUTPUT, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "question_count": payload["question_count"],
                "expected_calls": payload["expected_calls"],
                "manifest_sha256": payload["manifest_sha256"],
                "calls_made": payload["calls_made_during_preflight"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
