"""Prepare a no-call SA-04 repair-round manifest and Verifier-cache decision."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scholartrace.search.storage import write_json
from scholartrace.verification_ablation import load_frozen_inputs

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "agent" / "verification-ablation" / "SA-02" / "frozen_inputs.json"
ARCHIVE = ROOT / "agent" / "verification-ablation" / "SA-04" / "pilot_archive.json"
OUTPUT = ROOT / "evaluation" / "reports" / "sa_04_repair_round_preflight.json"
PILOT_IDS = ("sa02-pilot-01", "sa02-pilot-02")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    inputs = load_frozen_inputs(INPUTS)
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    input_hashes = {question_id: inputs[question_id].input_sha256 for question_id in PILOT_IDS}
    v_on = {
        row["result"]["question_id"]: row
        for row in archive["rows"]
        if row["result"]["variant"] == "V-on"
    }
    cache_checks = {
        question_id: {
            "input_hash_matches": row["result"]["frozen_input_sha256"]
            == input_hashes[question_id],
            "status": row["result"]["status"],
            "verifier_count": len(row.get("verifications", [])),
            "verifier_records_complete": len(row.get("verifications", []))
            == len(inputs[question_id].claims),
            "report_is_reused": False,
        }
        for question_id, row in v_on.items()
    }
    reuse = all(
        item["input_hash_matches"]
        and item["status"] in {"succeeded", "degraded"}
        and item["verifier_records_complete"]
        for item in cache_checks.values()
    ) and set(cache_checks) == set(PILOT_IDS)
    payload = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa04-repair-round-preflight",
        "status": "prepared",
        "source_archive_sha256": file_sha256(ARCHIVE),
        "source_inputs_sha256": file_sha256(INPUTS),
        "repair_round_id": "sa04-repair-round-v2",
        "verifier_reuse_candidate": reuse,
        "verifier_reuse_decision": (
            "reuse_12_successful_verifications"
            if reuse
            else "do_not_reuse_verifier_cache"
        ),
        "cache_checks": cache_checks,
        "new_requests_if_reuse_accepted": {
            "verifier_calls": 0,
            "report_calls": 4,
            "total_inference_requests": 4,
        },
        "old_report_outputs_reused": False,
        "old_failed_report_outputs_reused": False,
        "calls_made_during_preflight": 0,
        "limitations": [
            (
                "Verifier reuse is a candidate decision based on preserved private inputs "
                "and successful Verification records; it is not a new quality claim."
            ),
            (
                "All four reports must be generated under the repaired report configuration; "
                "old reports are not mixed into the new round."
            ),
            (
                "A new report round requires a separate budget because the "
                "original 17 inference attempts are exhausted."
            ),
        ],
    }
    write_json(OUTPUT, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "verifier_reuse_candidate": payload["verifier_reuse_candidate"],
                "new_report_calls": payload["new_requests_if_reuse_accepted"]["report_calls"],
                "calls_made": payload["calls_made_during_preflight"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
