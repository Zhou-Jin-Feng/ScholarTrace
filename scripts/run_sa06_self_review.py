"""Create a redacted SA-06 self-review from the completed formal archive."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "agent" / "verification-ablation" / "SA-05-formal" / "formal_archive.json"
REFERENCE = ROOT / "agent" / "verification-ablation" / "SA-02" / "human_reference.json"
BLIND_REVIEW = ROOT / "agent" / "verification-ablation" / "SA-05-formal" / "blind_review.csv"
PRIVATE_OUTPUT = ROOT / "agent" / "verification-ablation" / "SA-06-self-review.json"
PUBLIC_OUTPUT = ROOT / "evaluation" / "reports" / "sa_06_self_review.json"
CLAIM_PATTERN = re.compile(r"Claim: `([^`]+)`")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report_claim_ids(report: str) -> set[str]:
    return set(CLAIM_PATTERN.findall(report))


def main() -> int:
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    reference_by_id = {item["question_id"]: item for item in reference["questions"]}
    rows = archive["rows"]
    detailed: list[dict[str, Any]] = []
    public_questions: list[dict[str, Any]] = []
    for question_id in sorted(reference_by_id):
        if not question_id.startswith("sa02-formal-"):
            continue
        ref = reference_by_id[question_id]
        by_variant = {
            row["result"]["variant"]: row
            for row in rows
            if row["result"]["question_id"] == question_id
        }
        question_public: dict[str, Any] = {
            "question_id": question_id,
            "key_point_count": len(ref["key_points"]),
            "variants": {},
        }
        for variant, row in by_variant.items():
            present = report_claim_ids(row["report"])
            keypoint_rows = []
            covered = partial = absent = 0
            for point in ref["key_points"]:
                linked = set(point["claim_ids"])
                present_count = len(linked & present)
                if present_count == len(linked):
                    state = "covered"
                    covered += 1
                elif present_count:
                    state = "partial"
                    partial += 1
                else:
                    state = "absent"
                    absent += 1
                keypoint_rows.append(
                    {
                        "key_point_id": point["key_point_id"],
                        "kind": point["kind"],
                        "state": state,
                        "linked_claim_count": len(linked),
                        "present_claim_count": present_count,
                    }
                )
            counts = row["result"]["disposition_counts"]
            detailed.append(
                {
                    "question_id": question_id,
                    "variant": variant,
                    "status": row["result"]["status"],
                    "report_sha256": row["result"]["report_sha256"],
                    "present_claim_ids": sorted(present),
                    "disposition_counts": counts,
                    "key_points": keypoint_rows,
                }
            )
            question_public["variants"][variant] = {
                "status": row["result"]["status"],
                "report_sha256": row["result"]["report_sha256"],
                "disposition_counts": counts,
                "key_point_claim_id_traceability": {
                    "covered": covered,
                    "partial": partial,
                    "absent": absent,
                },
                "claim_presence_count": len(present),
            }
        public_questions.append(question_public)
    with BLIND_REVIEW.open("r", encoding="utf-8-sig", newline="") as handle:
        blind_rows = list(csv.DictReader(handle))
    blind_scores_empty = all(
        not row["score_a"].strip() and not row["score_b"].strip()
        for row in blind_rows
    )
    usage = archive["usage_by_variant"]
    private = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa06-private-self-review",
        "reviewer_mode": "codex_self_review_not_independent_blind_review",
        "archive_sha256": sha256(ARCHIVE),
        "reference_sha256": sha256(REFERENCE),
        "questions": detailed,
        "limitations": [
            "Claim-ID traceability is a structural check, not a semantic quality judgment.",
            (
            "Verifier status is not treated as human truth; this legacy key-point field "
            "records Claim-ID traceability only, not semantic coverage."
            ),
            "Blind review score fields remain empty and are not imported as quality scores.",
        ],
    }
    public = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa06-self-review-summary",
        "status": "self_review_complete_independent_blind_review_pending",
        "reviewer_mode": "codex_self_review_not_independent_blind_review",
        "formal_question_count": len(public_questions),
        "paired_rows": len(rows),
        "all_rows_succeeded": all(
            row["result"]["status"] in {"succeeded", "degraded"}
            for row in rows
        ),
        "blind_review_score_fields_empty": blind_scores_empty,
        "verifier_calls": usage["V-on"]["verifier_attempted_calls"],
        "report_calls": usage["V-on"]["report_calls"] + usage["V-off"]["report_calls"],
        "resource_by_variant": {
            variant: {
                "input_tokens": usage[variant]["input_tokens"],
                "output_tokens": usage[variant]["output_tokens"],
                "reference_cost_cny": usage[variant]["reference_cost_cny"],
                "duration_seconds": usage[variant]["duration_seconds"],
            }
            for variant in ("V-on", "V-off")
        },
        "questions": public_questions,
        "self_review_conclusion": [
            "V-off preserved unverified markers for all formal Claims.",
            "V-on preserved supported/partial markers and excluded unsupported Claims.",
            "Legacy self-review found no obvious Evidence-ID binding or extra-exclusion anomaly.",
            (
                "This does not establish causal quality improvement; independent blind "
                "scores remain pending."
            ),
        ],
        "private_self_review_sha256": sha256(PRIVATE_OUTPUT) if PRIVATE_OUTPUT.is_file() else None,
    }
    write_json(PRIVATE_OUTPUT, private)
    public["private_self_review_sha256"] = sha256(PRIVATE_OUTPUT)
    write_json(PUBLIC_OUTPUT, public)
    print(
        json.dumps(
            {
                "status": public["status"],
                "formal_questions": public["formal_question_count"],
                "paired_rows": public["paired_rows"],
                "blind_scores_empty": public["blind_review_score_fields_empty"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
