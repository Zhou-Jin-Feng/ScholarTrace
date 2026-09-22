"""Rebuild a source-bound review summary without model or network calls."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from scholartrace.search.storage import write_json
from scholartrace.verification_ablation.review import (
    quality_view,
    summarize_review,
    validate_review,
)
from scholartrace.verification_ablation.validation import validation_status

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "agent" / "verification-ablation" / "release-review"
ARTIFACTS = {
    "formal_archive": ROOT / "agent/verification-ablation/SA-05-formal/formal_archive.json",
    "frozen_inputs": ROOT / "agent/verification-ablation/SA-02/frozen_inputs.json",
    "reference": ROOT / "agent/verification-ablation/SA-02/human_reference.json",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-input", type=Path, default=PRIVATE / "source_review.json")
    args = parser.parse_args()
    review = json.loads(args.review_input.read_text(encoding="utf-8"))
    inputs = {key: json.loads(path.read_text(encoding="utf-8")) for key, path in ARTIFACTS.items()}
    hashes = {key: digest(path) for key, path in ARTIFACTS.items()}
    archive = inputs["formal_archive"]
    validate_review(review, archive, inputs["frozen_inputs"], inputs["reference"], hashes)
    summary = summarize_review(review)
    by_question: dict[str, dict[str, Any]] = {}
    for row in archive["rows"]:
        by_question.setdefault(row["result"]["question_id"], {})[row["result"]["variant"]] = row
    if len(by_question) != 10 or any(set(v) != {"V-on", "V-off"} for v in by_question.values()):
        raise ValueError("formal paired coverage is incomplete")
    successful_pairs = sum(
        all(row["result"]["status"] == "succeeded" for row in rows.values())
        for rows in by_question.values()
    )
    summary["successful_pairs"] = successful_pairs
    if successful_pairs < 8:
        summary["protocol_conclusion"] = "INSUFFICIENT_VALID_PAIRS"
    rng = random.Random(20260922)
    views, mapping = [], []
    for question_id, rows in sorted(by_question.items()):
        order = ["V-on", "V-off"]
        rng.shuffle(order)
        rendered = {v: quality_view(rows[v]["report"]) for v in order}
        views.append(
            {
                "question_id": question_id,
                "answer_a": rendered[order[0]],
                "answer_b": rendered[order[1]],
                "review_notes": "",
            }
        )
        mapping.append(
            {
                "question_id": question_id,
                "a": order[0],
                "b": order[1],
                "source_sha256": {v: rows[v]["result"]["report_sha256"] for v in order},
                "view_sha256": {v: hashlib.sha256(rendered[v].encode()).hexdigest() for v in order},
            }
        )
    PRIVATE.mkdir(parents=True, exist_ok=True)
    with (PRIVATE / "quality_review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(views[0]))
        writer.writeheader()
        writer.writerows(views)
    write_json(PRIVATE / "quality_mapping.json", {"entries": mapping})
    labels = {(e["question_id"], e["variant"], e["claim_id"]): e for e in review["finding_reviews"]}
    claims = {
        q["question_id"]: {c["claim_id"]: c for c in q["claims"]}
        for q in inputs["frozen_inputs"]["questions"]
        if q["split"] == "formal"
    }
    relation_errors = 0
    from scholartrace.verification_ablation.review import parse_findings

    for q, rows in by_question.items():
        for v, row in rows.items():
            for finding in parse_findings(row["report"]):
                claim = claims[q][finding["claim_id"]]
                allowed = set(claim["evidence_ids"]) | set(claim.get("counter_evidence_ids", []))
                if not set(finding["evidence_ids"]) <= allowed:
                    relation_errors += 1
                if (q, v, finding["claim_id"]) not in labels:
                    raise ValueError("finding has no explicit source review")
    per_question = []
    for q in sorted(by_question):
        subset = dict(review)
        for key in ("claim_reviews", "finding_reviews", "key_point_reviews"):
            subset[key] = [e for e in review[key] if e["question_id"] == q]
        per_question.append({"question_id": q, **summarize_review(subset)})
    validation = validation_status(
        ROOT, ROOT / "evaluation/reports/verification_ablation_validation.json"
    )
    usage = archive["usage_by_variant"]
    total_cost = sum(
        float(usage[v][key])
        for v in usage
        for key in ("reference_cost_cny", "verifier_reference_cost_cny")
    )
    payload = {
        "schema_version": "2.0",
        "purpose": "verification-ablation-source-review",
        "status": validation["status"],
        "validation": validation,
        "artifact_sha256": {**hashes, "review_input": digest(args.review_input)},
        "review_method": {
            "mode": review["reviewer_mode"],
            "blinded": review["blinded"],
            "reviewed_at": review["reviewed_at"],
            "scope": review["scope"],
        },
        "formal_questions": len(by_question),
        "condition_rows": len(archive["rows"]),
        "claim_evidence_relation_violations": relation_errors,
        "quality": summary,
        "per_question": per_question,
        "cost": {
            "formal_logical_reference_cost_cny": round(total_cost, 6),
            "historical_debug_and_failed_attempts": "partially_known_separate_records",
            "provider_actual_billing": "unknown",
            "by_variant": usage,
        },
        "model_requests": {
            "report": sum(usage[v]["report_calls"] for v in usage),
            "verifier": sum(usage[v]["verifier_attempted_calls"] for v in usage),
        },
        "limitations": [
            "Ten questions derive from five source runs and are not independent retrieval samples.",
            (
                "Only supplied excerpts were reviewed; this is not full-paper or "
                "independent blind review."
            ),
            (
                "Two unsupported findings were removed, but V-off already warned about "
                "their bibliography evidence."
            ),
            "Removal counts do not establish newly detected errors or causal quality improvement.",
            "The actual fixed V-off/V-on order differs from the planned alternating order.",
            (
                "Historical successful reports remain unchanged; repaired recovery is "
                "not retrospective."
            ),
        ],
        "quality_view": {
            "rows": len(views),
            "blinded_review_claimed": False,
            "review_sha256": digest(PRIVATE / "quality_review.csv"),
            "mapping_sha256": digest(PRIVATE / "quality_mapping.json"),
        },
    }
    destination = ROOT / "evaluation/reports/verification_ablation_review.json"
    write_json(destination, payload)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "protocol_conclusion": summary["protocol_conclusion"],
                "output": destination.relative_to(ROOT).as_posix(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
