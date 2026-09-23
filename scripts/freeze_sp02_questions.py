"""Freeze the new stage-two question set and its pre-registered reference points."""

# ruff: noqa: E501

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scholartrace.search.storage import source_tree_sha256, write_json
from scholartrace.verification_ablation import load_frozen_inputs
from scholartrace.verification_ablation.models import canonical_sha256
from scholartrace.verification_ablation.stage_two_questions import (
    StageTwoQuestionSpec,
    build_stage_two_inputs,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_INPUTS = ROOT / "agent" / "verification-ablation" / "SA-02" / "frozen_inputs.json"
PRIVATE_DIR = ROOT / "agent" / "verification-ablation" / "SP-02"
PRIVATE_INPUTS = PRIVATE_DIR / "frozen_inputs.json"
PRIVATE_REFERENCE = PRIVATE_DIR / "human_reference.json"
PUBLIC_MANIFEST = ROOT / "evaluation" / "seeds" / "sp_02_new_question_manifest.json"
PUBLIC_REPORT = ROOT / "evaluation" / "reports" / "sp_02_question_freeze.json"

HYPOTHESIS = (
    "Strong semantic verification is most valuable for claims with condition or conclusion "
    "conflicts, cross-paper extrapolation, or incomplete evidence; ordinary low-risk claims "
    "can use the single-pass baseline if their unverified state remains explicit and auditable."
)
QUALITY_RULES = {
    "primary_metric": "weighted_key_point_coverage",
    "guardrail_unsupported_fact_delta": "no more than +1 across the new set versus the full-verification scheme",
    "guardrail_coverage_drop": "no more than 5 percentage points versus the full-verification scheme",
    "guardrail_state_integrity": "every skipped Claim remains unverified and traceable",
    "fallback": "if any guardrail fails, retain the full-verification scheme as the formal path and keep the candidate experimental",
}
RESOURCE_RULES = {
    "new_question_count": 8,
    "maximum_specialized_rounds": 1,
    "report_requests_per_scheme": 8,
    "strong_verifier_call_cap_for_candidate": 32,
    "unknown_usage_policy": "stop and preserve the checkpoint; no automatic resend",
    "cost_basis": "recalculate from the selected provider and actual input sizes before any external request",
}


def current_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).strip()


def specs() -> list[StageTwoQuestionSpec]:
    return [
        StageTwoQuestionSpec(
            question_id="sp02-new-01",
            question=(
                "What task and corpus conditions are actually evidenced for GraphRAG gains "
                "over vector RAG, and what model context is reported?"
            ),
            categories=["ordinary_fact", "condition_conclusion_conflict"],
            source_question_claims={"sa02-formal-01": [0, 1, 2]},
            key_points=[
                "The evidence is tied to global sensemaking questions and a stated corpus scale.",
                "The reported comparison is against vector or conventional RAG under a specified model context.",
                "Root-level retrieval is described as an efficiency trade-off rather than an unrestricted quality claim.",
            ],
            review_focus="Separate directly evidenced conditions from broad superiority wording.",
        ),
        StageTwoQuestionSpec(
            question_id="sp02-new-02",
            question=(
                "What efficiency comparison does the supplied evidence support for HippoRAG "
                "versus iterative retrieval, and what remains unestablished?"
            ),
            categories=["ordinary_fact", "evidence_incomplete"],
            source_question_claims={"sa02-formal-02": [0, 1]},
            key_points=[
                "The supplied claims report relative cost and latency figures against iterative retrieval.",
                "The comparison also states comparable performance under the cited conditions.",
                "The evidence does not establish a universal advantage outside the cited tasks and baselines.",
            ],
            review_focus="Preserve numeric conditions and distinguish source-specific results from general claims.",
        ),
        StageTwoQuestionSpec(
            question_id="sp02-new-03",
            question=(
                "Which evaluation dimensions are explicitly defined across RGB, Ragas, and ARES, "
                "and where does the evidence stop short of separating retrieval from generation?"
            ),
            categories=["cross_paper_comparison", "evidence_incomplete"],
            source_question_claims={
                "sa02-formal-03": [0, 1, 4, 5, 6],
                "sa02-formal-04": [1, 2, 3],
            },
            key_points=[
                "RGB and related material cover multiple robustness and retrieval-augmented evaluation dimensions.",
                "ARES explicitly names context relevance, answer faithfulness, and answer relevance.",
                "The supplied excerpts do not by themselves prove a clean causal separation of retrieval and generation quality.",
            ],
            review_focus="Audit whether a generated comparison preserves scope and uncertainty across papers.",
        ),
        StageTwoQuestionSpec(
            question_id="sp02-new-04",
            question=(
                "What security attack surfaces and mitigation limitations are supported for poisoned "
                "or adversarial retrieved documents?"
            ),
            categories=["cross_paper_comparison", "evidence_incomplete"],
            source_question_claims={
                "sa02-formal-05": [1, 2, 3],
                "sa02-formal-06": [0, 1, 2, 3],
            },
            key_points=[
                "Knowledge databases and retrieved documents are described as attack surfaces.",
                "PoisonedRAG and indirect prompt injection represent distinct but related threat mechanisms.",
                "The cited material calls for mitigation and does not establish a complete defense guarantee.",
            ],
            review_focus="Check that threat mechanisms, evidence limits, and mitigation directions are not merged into one claim.",
        ),
        StageTwoQuestionSpec(
            question_id="sp02-new-05",
            question=(
                "How do classifier or confidence signals map a query to no-retrieval, single-step, "
                "recursive, or multi-step retrieval?"
            ),
            categories=["ordinary_fact", "condition_conclusion_conflict"],
            source_question_claims={"sa02-pilot-02": [0, 1, 2, 3, 5, 6, 7]},
            key_points=[
                "Query complexity or confidence is used to select a retrieval strategy.",
                "The described choices include no retrieval, one retrieval step, and multi-step retrieval.",
                "Classifier quality affects downstream strategy selection and answer performance.",
            ],
            review_focus="Retain the distinction between confidence signals, classifier behavior, and reported outcomes.",
        ),
        StageTwoQuestionSpec(
            question_id="sp02-new-06",
            question=(
                "Across CRAG, HippoRAG, and LightRAG, which claims about correction, latency, "
                "adaptability, and resource trade-offs are actually supported?"
            ),
            categories=["cross_paper_comparison", "condition_conclusion_conflict"],
            source_question_claims={"sa02-formal-08": [0, 3, 4, 5, 6, 7]},
            key_points=[
                "CRAG is associated with corrective retrieval actions.",
                "HippoRAG evidence emphasizes retrieval efficiency and latency under cited comparisons.",
                "LightRAG evidence emphasizes adaptation and complex-query handling under its stated setup.",
            ],
            review_focus="Prevent cross-paper results from being presented as one shared benchmark outcome.",
        ),
        StageTwoQuestionSpec(
            question_id="sp02-new-07",
            question=(
                "Which graph construction and hybrid retrieval mechanisms are described for global, "
                "multi-hop, and entity-focused questions?"
            ),
            categories=["cross_paper_comparison"],
            source_question_claims={"sa02-formal-09": [0, 1, 2, 3, 4, 7]},
            key_points=[
                "GraphRAG is associated with global sensemaking and community-level processing.",
                "HippoRAG constructs and traverses a knowledge graph for multi-hop retrieval.",
                "Hybrid approaches combine lower-level matching with higher-level relationship or community retrieval.",
            ],
            review_focus="Verify that mechanism descriptions stay tied to the named system and query type.",
        ),
        StageTwoQuestionSpec(
            question_id="sp02-new-08",
            question=(
                "What resource or scalability limitations are explicitly evidenced for graph-enhanced retrieval systems?"
            ),
            categories=["evidence_incomplete", "condition_conclusion_conflict"],
            source_question_claims={"sa02-formal-10": [0, 1, 2]},
            key_points=[
                "The supplied chunks explicitly lack a complete cost discussion for some approaches.",
                "HippoRAG reports online efficiency gains in the cited comparison.",
                "Potential scalability work is identified without a complete measured cost profile.",
            ],
            review_focus="Reward explicit evidence limits and reject invented resource estimates.",
        ),
    ]


def main() -> int:
    source_inputs = load_frozen_inputs(SOURCE_INPUTS)
    question_specs = specs()
    frozen = build_stage_two_inputs(
        source_inputs=source_inputs,
        specs=question_specs,
        validated_at=datetime(2026, 9, 22, tzinfo=UTC),
    )
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(
        PRIVATE_INPUTS,
        {
            "schema_version": "1.0",
            "purpose": "scholartrace-sp02-private-frozen-inputs",
            "frozen_at": "2026-09-22T00:00:00+00:00",
            "questions": [
                {
                    "spec": spec.model_dump(mode="json"),
                    "input": frozen[spec.question_id].model_dump(mode="json"),
                }
                for spec in question_specs
            ],
        },
    )
    write_json(
        PRIVATE_REFERENCE,
        {
            "schema_version": "1.0",
            "purpose": "scholartrace-sp02-private-human-reference",
            "questions": [
                {
                    "question_id": spec.question_id,
                    "key_points": spec.key_points,
                    "review_focus": spec.review_focus,
                    "source_question_claims": spec.source_question_claims,
                }
                for spec in question_specs
            ],
        },
    )
    public_questions: list[dict[str, Any]] = [
        {
            "question_id": spec.question_id,
            "question": spec.question,
            "categories": spec.categories,
            "source_question_ids": sorted(spec.source_question_claims),
            "claim_count": len(frozen[spec.question_id].claims),
            "evidence_count": len(frozen[spec.question_id].evidence),
            "paper_count": len(frozen[spec.question_id].papers),
            "key_point_count": len(spec.key_points),
            "input_sha256": frozen[spec.question_id].input_sha256,
        }
        for spec in question_specs
    ]
    question_fingerprint = canonical_sha256([item["input_sha256"] for item in public_questions])
    manifest = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sp02-new-question-manifest",
        "status": "frozen",
        "experiment_scope": "stage-two-new-question-set",
        "baseline_commit": current_commit(),
        "source_tree_sha256": source_tree_sha256(ROOT),
        "source_manifest": "evaluation/seeds/sa_02_verification_ablation_manifest.json",
        "question_set_fingerprint_sha256": question_fingerprint,
        "question_count": len(public_questions),
        "questions": public_questions,
        "hypothesis": HYPOTHESIS,
        "quality_rules": QUALITY_RULES,
        "resource_rules": RESOURCE_RULES,
        "privacy": {
            "raw_claims_public": False,
            "source_quotes_public": False,
            "human_key_points_public": False,
            "private_inputs": "agent/verification-ablation/SP-02/frozen_inputs.json",
            "private_reference": "agent/verification-ablation/SP-02/human_reference.json",
        },
        "notes": [
            "The set is derived from previously validated Evidence packets with new question identities and a separate manifest.",
            "The set is not added to the historical SA-02 formal result and is not used to tune the candidate rules before freezing.",
            "No model or external Provider request is made by this freeze command.",
        ],
    }
    write_json(PUBLIC_MANIFEST, manifest)
    write_json(
        PUBLIC_REPORT,
        {
            "schema_version": "1.0",
            "purpose": "scholartrace-sp02-question-freeze-report",
            "passed": True,
            "question_count": len(public_questions),
            "claim_count": sum(item["claim_count"] for item in public_questions),
            "evidence_count_sum": sum(item["evidence_count"] for item in public_questions),
            "question_set_fingerprint_sha256": question_fingerprint,
            "model_calls": 0,
            "provider_api_calls": 0,
            "raw_inputs_public": False,
            "hypothesis_frozen": True,
            "quality_rules_frozen": True,
            "resource_rules_frozen": True,
        },
    )
    print(
        json.dumps(
            {
                "passed": True,
                "question_count": len(public_questions),
                "claim_count": sum(item["claim_count"] for item in public_questions),
                "question_set_fingerprint_sha256": question_fingerprint,
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
