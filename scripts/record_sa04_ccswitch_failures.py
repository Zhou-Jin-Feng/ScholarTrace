"""Record cc-switch GPT provider repair-round failures without new calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / "agent" / "verification-ablation"
OUTPUT = ROOT / "evaluation" / "reports" / "sa_04_ccswitch_provider_failures.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    rounds = []
    for label, model, directory in (
        ("sol", "gpt-5.6-sol", PRIVATE_ROOT / "SA-04-repair-v2"),
        ("luna", "gpt-5.6-luna", PRIVATE_ROOT / "SA-04-repair-luna-v3"),
    ):
        checkpoint = directory / "pilot_checkpoint.json"
        if not checkpoint.is_file():
            continue
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        row = next(
            item
            for item in payload.get("rows", [])
            if item["result"]["status"] == "failed"
        )
        rounds.append(
            {
                "round": label,
                "model": model,
                "provider_profile": "cc-switch:SuperBoy:GPT",
                "provider_hostname": "share-api.top",
                "question_id": row["result"]["question_id"],
                "variant": row["result"]["variant"],
                "status": row["result"]["status"],
                "error_code": row["result"]["error_code"],
                "error_diagnostics": row["result"]["error_diagnostics"],
                "report_attempts": 1,
                "verifier_attempts": 0,
                "checkpoint_sha256": sha256(checkpoint),
            }
        )
    public = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa04-ccswitch-provider-failure-record",
        "status": "blocked_provider_inference_unavailable",
        "provider_profile": "cc-switch:SuperBoy:GPT",
        "provider_hostname": "share-api.top",
        "models_tested": ["gpt-5.6-sol", "gpt-5.6-luna"],
        "model_catalog_probe": "both_models_visible",
        "additional_inference_attempts": 2,
        "historical_inference_attempts_before_switch": 17,
        "total_inference_attempts_recorded": 19,
        "known_successful_cost_cny": 1.447155,
        "additional_failed_report_cost_cny": None,
        "rounds": rounds,
        "raw_response_stored_publicly": False,
        "api_key_stored_publicly": False,
        "next_required_action": (
            "Obtain a functioning provider/quota or explicitly choose another provider; "
            "do not retry this 503 route automatically."
        ),
    }
    write_json(OUTPUT, public)
    print(
        json.dumps(
            {
                "status": public["status"],
                "rounds": len(rounds),
                "total_attempts": public["total_inference_attempts_recorded"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
