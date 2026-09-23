from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sa04_synthetic import make_synthetic_inputs

from scholartrace.verification_ablation.stage_two_questions import (
    StageTwoQuestionSpec,
    build_stage_two_inputs,
)

VALIDATED_AT = datetime(2026, 9, 22, tzinfo=UTC)


def test_stage_two_builder_creates_new_validated_input() -> None:
    source_inputs = make_synthetic_inputs()
    spec = StageTwoQuestionSpec(
        question_id="sp02-test-01",
        question="What does the synthetic evidence support about the measured result?",
        categories=["ordinary_fact"],
        source_question_claims={"synthetic-sa04-pilot": [0, 2]},
        key_points=["The result is tied to the supplied evidence."],
        review_focus="Keep the claim bounded to the supplied evidence.",
    )

    result = build_stage_two_inputs(
        source_inputs=source_inputs,
        specs=[spec],
        validated_at=VALIDATED_AT,
    )

    item = result["sp02-test-01"]
    assert item.input_sha256
    assert item.semantic_verification_results_included is False
    assert item.deterministic_validation.outcome == "succeeded"
    assert len(item.claims) == 2
    assert all(claim.claim_id.startswith("claim:sp02-test-01:") for claim in item.claims)


def test_stage_two_builder_rejects_unknown_source_question() -> None:
    source_inputs = make_synthetic_inputs()
    spec = StageTwoQuestionSpec(
        question_id="sp02-test-unknown",
        question="What does the missing source support?",
        categories=["evidence_incomplete"],
        source_question_claims={"missing-question": [0]},
        key_points=["The source must be present."],
        review_focus="Keep the source boundary explicit.",
    )

    with pytest.raises(ValueError, match="unknown source question"):
        build_stage_two_inputs(
            source_inputs=source_inputs,
            specs=[spec],
            validated_at=VALIDATED_AT,
        )


def test_stage_two_builder_rejects_claim_index_drift() -> None:
    source_inputs = make_synthetic_inputs()
    spec = StageTwoQuestionSpec(
        question_id="sp02-test-drift",
        question="What does the out-of-range selection support?",
        categories=["evidence_incomplete"],
        source_question_claims={"synthetic-sa04-pilot": [99]},
        key_points=["The selected Claim must exist."],
        review_focus="Stop on input drift.",
    )

    with pytest.raises(ValueError, match="claim index out of range"):
        build_stage_two_inputs(
            source_inputs=source_inputs,
            specs=[spec],
            validated_at=VALIDATED_AT,
        )
