"""Build the deterministic SP-03 selection plan without external calls."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from scholartrace.search.storage import source_tree_sha256, write_json
from scholartrace.verification_ablation.models import AblationFrozenInput, canonical_sha256
from scholartrace.verification_ablation.stage_two_selection import (
    RULE_ID,
    SELECTION_RULE_SHA256,
    build_selection_plan,
)

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_INPUTS = ROOT / "agent" / "verification-ablation" / "SP-02" / "frozen_inputs.json"
PRIVATE_DIR = ROOT / "agent" / "verification-ablation" / "SP-03"
PRIVATE_PLAN = PRIVATE_DIR / "selection_plan.json"
PUBLIC_PLAN = ROOT / "evaluation" / "seeds" / "sp_03_selection_plan.json"


def current_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).strip()


def load_inputs() -> dict[str, AblationFrozenInput]:
    payload = json.loads(PRIVATE_INPUTS.read_text(encoding="utf-8"))
    inputs = {
        item["input"]["question_id"]: AblationFrozenInput.model_validate(item["input"])
        for item in payload["questions"]
    }
    if len(inputs) != 8:
        raise ValueError("SP-03 requires the frozen eight-question SP-02 set")
    return dict(sorted(inputs.items()))


def main() -> int:
    inputs = load_inputs()
    plans = [build_selection_plan(item) for item in inputs.values()]
    selected_count = sum(len(item.selected_claim_ids) for item in plans)
    skipped_count = sum(len(item.skipped_claim_ids) for item in plans)
    if selected_count == 0 or skipped_count == 0:
        raise ValueError("selection rule must select and skip at least one Claim")
    if selected_count > 32:
        raise ValueError(f"candidate selection exceeds the frozen call cap: {selected_count}")

    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(
        PRIVATE_PLAN,
        {
            "schema_version": "1.0",
            "purpose": "scholartrace-sp03-private-selection-plan",
            "rule_id": RULE_ID,
            "rule_sha256": SELECTION_RULE_SHA256,
            "plans": [item.model_dump(mode="json") for item in plans],
        },
    )
    public_questions: list[dict[str, Any]] = []
    for plan in plans:
        public_questions.append(
            {
                "question_id": plan.question_id,
                "frozen_input_sha256": plan.frozen_input_sha256,
                "total_claim_count": plan.total_claim_count,
                "selected_claim_count": len(plan.selected_claim_ids),
                "skipped_claim_count": len(plan.skipped_claim_ids),
                "selection_ratio": round(
                    len(plan.selected_claim_ids) / plan.total_claim_count, 6
                ),
            }
        )
    public = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sp03-selection-plan",
        "status": "candidate_only",
        "experiment_scope": "stage-two-selective-verification-candidate",
        "baseline_commit": current_commit(),
        "source_tree_sha256": source_tree_sha256(ROOT),
        "rule_id": RULE_ID,
        "rule_sha256": SELECTION_RULE_SHA256,
        "question_count": len(public_questions),
        "total_claim_count": selected_count + skipped_count,
        "selected_claim_count": selected_count,
        "skipped_claim_count": skipped_count,
        "selected_claim_ratio": round(
            selected_count / (selected_count + skipped_count), 6
        ),
        "question_set_fingerprint_sha256": canonical_sha256(
            [item["frozen_input_sha256"] for item in public_questions]
        ),
        "questions": public_questions,
        "unselected_state": "unverified",
        "formal_workflow_changed": False,
        "model_calls": 0,
        "provider_api_calls": 0,
        "private_plan": "agent/verification-ablation/SP-03/selection_plan.json",
        "notes": [
            "Selection is deterministic and uses Claim metadata only; no model judgment is added.",
            "The candidate is isolated from the formal M4 path and is not an adoption decision.",
            (
                "Every skipped Claim remains auditable and must remain unverified "
                "until a later explicit policy changes it."
            ),
        ],
    }
    write_json(PUBLIC_PLAN, public)
    print(
        json.dumps(
            {
                "passed": True,
                "rule_id": RULE_ID,
                "question_count": len(public_questions),
                "total_claim_count": selected_count + skipped_count,
                "selected_claim_count": selected_count,
                "skipped_claim_count": skipped_count,
                "model_calls": 0,
                "provider_api_calls": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
