"""Record SA-04 historical artifacts and correct known/unknown cost semantics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIR = ROOT / "agent" / "verification-ablation" / "SA-04"
PUBLIC_OUTPUT = ROOT / "evaluation" / "reports" / "sa_04_history_correction.json"
PRIVATE_OUTPUT = PRIVATE_DIR / "history_index.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    artifact_paths = [
        ROOT / "evaluation" / "reports" / "sa_04_verification_ablation_pilot_attempt1.json",
        ROOT / "evaluation" / "reports" / "sa_04_verification_ablation_pilot.json",
        PRIVATE_DIR / "pilot_archive_attempt1.json",
        PRIVATE_DIR / "pilot_archive.json",
        PRIVATE_DIR / "pilot_checkpoint_attempt1.json",
        PRIVATE_DIR / "pilot_checkpoint.json",
        PRIVATE_DIR / "pilot_retry_history.json",
    ]
    source_paths = [
        ROOT / "src" / "scholartrace" / "verification_ablation" / "models.py",
        ROOT / "src" / "scholartrace" / "verification_ablation" / "runner.py",
        ROOT / "src" / "scholartrace" / "verification_ablation" / "provider.py",
        ROOT / "scripts" / "run_sa_04_verification_ablation_pilot.py",
        ROOT / "scripts" / "prepare_sa_04_pilot.py",
    ]
    artifact_index = {
        path.relative_to(ROOT).as_posix(): sha256(path)
        for path in artifact_paths
        if path.is_file()
    }
    source_index = {
        path.relative_to(ROOT).as_posix(): sha256(path)
        for path in source_paths
        if path.is_file()
    }
    public = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa04-history-cost-correction",
        "status": "pilot_executed_acceptance_failed",
        "historical_inference_attempts": 17,
        "model_catalog_probe_attempts": 4,
        "successful_report_reference_cost_cny": 0.523275,
        "known_verifier_reference_cost_cny": 0.923880,
        "known_successful_cost_cny": 1.447155,
        "failed_report_attempts": 2,
        "failed_report_cost_cny": None,
        "complete_total_reference_cost_cny": None,
        "complete_upper_bound_reference_cost_cny": None,
        "superseded_incomplete_total_reference_cost_cny": 1.247775,
        "superseded_incomplete_upper_bound_cny": 2.163675,
        "cost_correction_reason": (
            "The previous total omitted two failed report attempts whose exact usage/cost "
            "was not persisted. Known successful and known Verifier costs are separated; "
            "the complete total remains unknown."
        ),
        "artifact_hashes": artifact_index,
        "source_hashes": source_index,
        "privacy": {
            "api_key_stored": False,
            "provider_response_body_stored_publicly": False,
            "raw_claims_or_quotes_stored_publicly": False,
        },
    }
    private = {
        **public,
        "purpose": "scholartrace-sa04-private-history-index",
        "private_artifact_hashes": artifact_index,
    }
    write_json(PRIVATE_OUTPUT, private)
    write_json(PUBLIC_OUTPUT, public)
    print(
        json.dumps(
            {
                "status": public["status"],
                "known_cost_cny": public["known_successful_cost_cny"],
                "failed_report_attempts": public["failed_report_attempts"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
